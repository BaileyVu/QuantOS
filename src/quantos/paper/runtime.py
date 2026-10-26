"""Independent feed/scanner workers, single-owner execution, durable 72h deadline."""
from dataclasses import asdict
import fcntl
import gzip
import json
import os
from pathlib import Path
import queue
import signal
import threading
import time

from .engine import PaperEngine
from .journal import Journal
from .market import PublicMarket, RateLimited
from .report import atomic_json, write_reports
from .risk import Policy, valid_quote
from .strategies import EmaAtrLimitStrategy, DonchianAtrStopStrategy
from .types import D, Snapshot, dumps


def milliseconds():
    return time.time_ns() // 1000000


def arbitrate(signals):
    """No opposing proposals on one symbol; deterministic priority otherwise."""
    sides = {}
    for s in signals: sides.setdefault(s.symbol, set()).add(s.side)
    conflicts = {symbol for symbol, choices in sides.items() if len(choices) > 1}
    return sorted((s for s in signals if s.symbol not in conflicts),
                  key=lambda s: (s.symbol, s.strategy_id, s.signal_id)), conflicts


class Runtime:
    def __init__(self, state: Path, hours=72, market_factory=PublicMarket):
        if not 0 < hours <= 72: raise ValueError("duration must be >0 and <=72 hours")
        self.state, self.hours, self.market_factory = state, hours, market_factory
        self.shutdown = threading.Event()
        self.messages = queue.Queue(maxsize=1000)
        self.strategies = (EmaAtrLimitStrategy(), DonchianAtrStopStrategy())
        self.instrument_lock = threading.Lock()
        self.instruments = {}
        self.stop_requested = False

    def emit(self, kind, payload):
        try: self.messages.put((kind, payload), timeout=1)
        except queue.Full:
            self.stop_requested = True

    def scanner(self):
        market = self.market_factory()
        next_scan = time.monotonic()
        while not self.shutdown.is_set():
            try:
                now = milliseconds()
                offset = market.server_time() - milliseconds()
                if abs(offset) > 2000: raise ValueError(f"CLOCK_DRIFT:{offset}")
                instruments = market.exchange_info()
                symbols = [s for s in ("BTCUSDC", "ETHUSDC") if s in instruments and instruments[s].status == "TRADING"]
                if not symbols: raise ValueError("NO_COMPATIBLE_USDC_SYMBOLS")
                with self.instrument_lock:
                    self.instruments = {s: instruments[s] for s in symbols}
                snapshots = []
                for symbol in symbols:
                    at = milliseconds()
                    one = market.candles(symbol, "1m", at)
                    five = market.candles(symbol, "5m", at)
                    q = market.quote(symbol)
                    snapshots.append(Snapshot(milliseconds(), instruments[symbol], q, one, five))
                self.emit("scan", snapshots)
            except Exception as error:
                self.emit("error", {"worker": "scanner", "error": str(error)})
                if isinstance(error, RateLimited):
                    self.shutdown.wait(error.retry_after_seconds)
            # Align after each scan to next UTC 30-second boundary using monotonic wait.
            next_scan = time.monotonic() + (30 - time.time() % 30)
            self.shutdown.wait(max(0, next_scan - time.monotonic()))

    def feed(self, started_ms, requested_symbol):
        market = self.market_factory()
        cursors, funding_since = {}, {}
        next_funding, backoff = 0, 2
        while not self.shutdown.is_set():
            start = time.monotonic()
            with self.instrument_lock:
                symbols = (requested_symbol,) if requested_symbol in self.instruments else ()
            if not symbols:
                self.shutdown.wait(1); continue
            try:
                records = []
                now = milliseconds()
                for symbol in symbols:
                    trades = market.trades(symbol, from_id=cursors[symbol]) if symbol in cursors else market.trades(symbol, start_ms=now-2000)
                    if len(trades) == 1000:
                        # Backlog is uncertain, not a reason to award stale fills.
                        cursors.pop(symbol, None)
                        raise ValueError("TRADE_BACKLOG")
                    if trades: cursors[symbol] = trades[-1].trade_id + 1
                    records.extend(("trade", t) for t in trades)
                    if now >= next_funding:
                        fund = market.funding(symbol, funding_since.get(symbol, started_ms), now)
                        records.extend(("funding", f) for f in fund)
                        if fund: funding_since[symbol] = fund[-1].time_ms + 1
                    # Quote last so trade/funding request latency cannot stale it.
                    records.insert(0, ("quote", market.quote(symbol)))
                if now >= next_funding: next_funding = now + 60000
                self.emit("feed", records)
                backoff = 2
            except Exception as error:
                self.emit("disconnected", {"worker": "feed", "error": str(error)})
                cursors.clear()
                delay = error.retry_after_seconds if isinstance(error, RateLimited) else backoff
                self.shutdown.wait(delay)
                backoff = min(60, backoff * 2)
            self.shutdown.wait(max(0, 2 - (time.monotonic() - start)))

    def run(self):
        self.state.mkdir(parents=True, exist_ok=True)
        lock = (self.state / "engine.lock").open("a+")
        try: fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close(); raise RuntimeError("paper state already has a running owner")
        journals = []
        try:
            now = milliseconds()
            run_file = self.state / "run.json"
            run = json.loads(run_file.read_text()) if run_file.exists() else {
                "started_ms": now, "deadline_ms": now+int(self.hours*3600000),
                "evidence": "natural", "status": "starting", "restarts": -1,
                "hours": self.hours, "reported_days": []}
            if run["evidence"] != "natural": raise ValueError("natural runtime cannot reuse synthetic state")
            if run["status"] == "completed": return 0
            if (self.state / "stop.request").exists():
                (self.state / "stop.request").unlink()
            run.update(status="running", pid=os.getpid(), restarts=run["restarts"]+1)
            atomic_json(run_file, run)
            engines = []
            for profile, fraction in (("p1", ".01"), ("p5", ".05")):
                j = Journal(self.state / f"journal_{profile}.db", profile)
                journals.append(j)
                e = PaperEngine(j, Policy(risk_fraction=D(fraction)))
                e.recover(now); engines.append(e)
                with j.transaction(): j.event(now, "START" if run["restarts"] == 0 else "RESTART", {"pid": os.getpid()})
            workers = [threading.Thread(target=self.scanner, daemon=True)] + [
                threading.Thread(target=self.feed, args=(run["started_ms"], symbol), daemon=True)
                for symbol in ("BTCUSDC", "ETHUSDC")]
            for worker in workers: worker.start()
            previous_handlers = {}
            if threading.current_thread() is threading.main_thread():
                for sig in (signal.SIGTERM, signal.SIGINT):
                    previous_handlers[sig] = signal.signal(sig, lambda *_: setattr(self, "stop_requested", True))
            last_wall, last_mono = milliseconds(), time.monotonic()
            last_feed, last_status, last_report = 0, 0, 0
            finishing_since = None
            while True:
                now, mono = milliseconds(), time.monotonic()
                gap = (now-last_wall)/1000
                if gap > 5 or abs(gap-(mono-last_mono)) > 2:
                    for e in engines:
                        e.connection(False, now, "SLEEP_OR_CLOCK_GAP")
                        with e.journal.transaction():
                            e.journal.event(now, "OBSERVATION_GAP", {"seconds": gap, "missed_scan_estimate": int(gap//30)})
                last_wall, last_mono = now, mono
                self.stop_requested = self.stop_requested or (self.state / "stop.request").exists()
                finishing = self.stop_requested or now >= run["deadline_ms"]
                if finishing:
                    finishing_since = finishing_since or now
                    run["status"] = "closing"
                try: kind, payload = self.messages.get(timeout=.25)
                except queue.Empty: kind, payload = "tick", None
                now = milliseconds()
                if kind == "feed":
                    quotes = [value for event, value in payload if event == "quote"]
                    archive = self.state / "market"; archive.mkdir(exist_ok=True)
                    day = time.strftime("%Y-%m-%d", time.gmtime(now/1000))
                    with gzip.open(archive / f"{day}.jsonl.gz", "at") as f:
                        f.write(dumps({"received_ms": now, "records": payload}) + "\n")
                    healthy = quotes and all(valid_quote(q, now) for q in quotes)
                    if healthy:
                        interval = (now-last_feed)/1000 if last_feed else 0
                        active = interval if 0 <= interval <= 5 else 0
                        last_feed = now
                        for e in engines:
                            if not e.connected: e.connection(True, now)
                            for event, value in payload:
                                getattr(e, event)(value, now)
                            with e.journal.transaction():
                                a = e.account(now)
                                e.journal.set("equity_last_observed", str(a.equity))
                                e.journal.event(now, "OBSERVATION", {"active_seconds": active,
                                    "equity": str(a.equity), "drawdown": str(1-a.equity/a.high_water)})
                    else:
                        for e in engines: e.connection(False, now, "STALE_FEED_BATCH")
                elif kind == "scan" and not finishing:
                    archive = self.state / "market"; archive.mkdir(exist_ok=True)
                    day = time.strftime("%Y-%m-%d", time.gmtime(now/1000))
                    with gzip.open(archive / f"{day}.jsonl.gz", "at") as f:
                        f.write(dumps({"received_ms": now, "records": [("snapshots", payload)]}) + "\n")
                    signals, lookup = [], {}
                    for snapshot in payload:
                        lookup[snapshot.instrument.symbol] = snapshot
                        for strategy in self.strategies:
                            proposal = strategy.evaluate(snapshot)
                            if proposal: signals.append(proposal)
                            for j in journals:
                                with j.transaction():
                                    j.event(now, "SCAN", {"strategy": strategy.strategy_id, "symbol": snapshot.instrument.symbol,
                                            "candle_ms": snapshot.candles_5m[-1].close_ms})
                                    if proposal is None: j.event(now, "NO_SIGNAL", {"strategy": strategy.strategy_id})
                    candidates, conflicts = arbitrate(signals)
                    for j in journals:
                        with j.transaction():
                            for symbol in conflicts: j.event(now, "CONFLICT", {"symbol": symbol})
                    for proposal in candidates:
                        snap = lookup[proposal.symbol]
                        for e in engines:
                            freshest = max((snap.quote, e.quotes.get(proposal.symbol, snap.quote)), key=lambda q: q.time_ms)
                            e.propose(proposal, snap.instrument, freshest, now)
                elif kind in {"error", "disconnected"}:
                    archive = self.state / "market"; archive.mkdir(exist_ok=True)
                    day = time.strftime("%Y-%m-%d", time.gmtime(now/1000))
                    with gzip.open(archive / f"{day}.jsonl.gz", "at") as f:
                        f.write(dumps({"received_ms": now, "records": [("disconnected", payload)]}) + "\n")
                    for e in engines:
                        e.connection(False, now, payload["error"])
                        with e.journal.transaction(): e.journal.event(now, "ERROR", payload)
                if last_feed and now-last_feed > 5000:
                    for e in engines:
                        if e.connected: e.connection(False, now, "FEED_TIMEOUT")
                for e in engines: e.tick(now, finish=finishing)
                if now-last_status >= 10000:
                    atomic_json(self.state / "status.json", {"mode": "paper", "run": run, "updated_ms": now,
                        "feed_age_ms": now-last_feed if last_feed else None,
                        "next_scan_ms": (now//30000+1)*30000,
                        "profiles": {e.journal.profile: {"cash": str(e.journal.cash()), "active": e.active(),
                            "paused": e.journal.get("paused", False)} for e in engines}})
                    last_status = now
                if now-last_report >= 3600000:
                    write_reports(self.state, run); last_report = now
                for day in (1, 2, 3):
                    if now >= run["started_ms"] + day*86400000 and day not in run["reported_days"]:
                        write_reports(self.state, run, day=day)
                        run["reported_days"].append(day); atomic_json(run_file, run)
                if finishing and not any(e.active() for e in engines):
                    # Finish funding reconciliation before final accounting publication.
                    market = self.market_factory()
                    try:
                        with self.instrument_lock: symbols = tuple(self.instruments)
                        for symbol in symbols:
                            for funding in market.funding(symbol, run["started_ms"], now):
                                for e in engines: e.funding(funding, now)
                        run.update(status="stopped" if self.stop_requested else "completed", finished_ms=now)
                        atomic_json(run_file, run)
                        write_reports(self.state, run, final=not self.stop_requested)
                        return 0
                    except Exception as error:
                        for j in journals:
                            with j.transaction(): j.event(now, "ERROR", {"worker": "final_funding", "error": str(error)})
                if finishing_since and now-finishing_since > 25000 and self.stop_requested:
                    run.update(status="stopped_with_protected_exposure", stopped_ms=now)
                    atomic_json(run_file, run); write_reports(self.state, run)
                    return 0
        finally:
            self.shutdown.set()
            for j in journals: j.close()
            if "previous_handlers" in locals():
                for sig, handler in previous_handlers.items(): signal.signal(sig, handler)
            lock.close()
