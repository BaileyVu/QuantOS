"""Independent feed/scanner workers, single-owner execution, durable 72h deadline."""
from dataclasses import asdict, replace
import math
import fcntl
import gzip
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .adaptive import AdaptiveSizer
from .engine import PaperEngine
from .journal import Journal
from .market import PublicMarket, RateLimited
from .report import atomic_json, write_reports
from .risk import Policy, valid_quote
from .strategies import library
from .announcements import BinanceAnnouncements
from .types import D, Instrument, Snapshot, dumps


def milliseconds():
    return time.time_ns() // 1000000


def arbitrate(signals, adaptive=None):
    """No opposing proposals on one symbol; deterministic priority otherwise."""
    sides = {}
    for s in signals: sides.setdefault(s.symbol, set()).add(s.side)
    conflicts = {symbol for symbol, choices in sides.items() if len(choices) > 1}
    def rank(s):
        if adaptive is None:
            return (s.symbol, s.strategy_id, s.signal_id)
        edge = adaptive.edge(s.strategy_id)
        mu, m2 = edge.mu(), edge.m2()
        score = mu/math.sqrt(m2) if math.isfinite(mu) and math.isfinite(m2) and m2 > 0 else float('-inf')
        return (-score, s.maximum_hold_seconds, s.strategy_id, s.symbol, s.signal_id)
    return sorted((s for s in signals if s.symbol not in conflicts), key=rank), conflicts


def dispatch(engines, kind, payload, now, *, strategies=(), active_seconds=0):
    """Identical state transitions for recorded input replay and natural runtime."""
    if kind == "feed":
        quotes = [v for event, v in payload if event == "quote"]
        healthy = bool(quotes) and all(valid_quote(q, now) for q in quotes)
        for e in engines:
            if not healthy:
                e.connection(False, now, "STALE_FEED_BATCH")
                continue
            if not e.connected: e.connection(True, now)
            for event, value in payload:
                getattr(e, event)(value, now)
            with e.journal.transaction():
                a = e.account(now)
                e.journal.set("equity_last_observed", str(a.equity))
                e.journal.event(now, "OBSERVATION", {"active_seconds": active_seconds,
                    "equity": str(a.equity), "drawdown": str(1-a.equity/a.high_water)})
        return healthy
    if kind == "funding":
        for value in payload:
            for e in engines: e.funding(value, now)
    elif kind == "diagnostic":
        for e in engines:
            with e.journal.transaction():
                e.journal.event(now, payload["status"], payload)
    elif kind == "scan":
        from .regime import regime_tags
        signals, lookup = [], {}
        for snapshot in payload:
            lookup[snapshot.instrument.symbol] = snapshot
            for e in engines:
                e.channel_stops(snapshot, now)
            tags = regime_tags(snapshot)
            if not snapshot.entry_eligible:
                continue  # Exit maintenance above remains active outside the entry universe.
            for strategy in strategies:
                proposal = strategy.evaluate(snapshot)
                if proposal: proposal = replace(proposal, **tags)
                if proposal: signals.append(proposal)
                for e in engines:
                    with e.journal.transaction():
                        e.journal.event(now, "SCAN", {"strategy": strategy.strategy_id,
                            "symbol": snapshot.instrument.symbol,
                            "candle_ms": snapshot.candles_5m[-1].close_ms if snapshot.candles_5m else None})
                        if proposal is None:
                            e.journal.event(now, "NO_SIGNAL", {"strategy": strategy.strategy_id})
                        else:
                            e.journal.event(now, "CANDIDATE", asdict(proposal))
        for e in engines:
            with e.journal.transaction():
                if e.adaptive is not None:
                    e.adaptive.learn(now)
                candidates, conflicts = arbitrate(signals, e.adaptive)
                for symbol in conflicts: e.journal.event(now, "CONFLICT", {"symbol": symbol})
            for proposal in candidates:
                snapshot = lookup[proposal.symbol]
                q = max((snapshot.quote, e.quotes.get(proposal.symbol, snapshot.quote)), key=lambda q:q.time_ms)
                e.propose(proposal, snapshot.instrument, q, now)
    elif kind == "restart":
        for e in engines:
            e.connected = False
            e.recover(now)
    elif kind == "tick":
        for e in engines: e.tick(now, finish=payload["finish"])
    elif kind == "disconnected":
        for e in engines:
            e.connection(False, now, payload["error"])
            with e.journal.transaction(): e.journal.event(now, "ERROR", payload)
    else:
        raise ValueError(f"unknown input kind: {kind}")
    return False


class Runtime:
    def __init__(self, state: Path, hours=72, market_factory=PublicMarket,
                 min_quote_volume=D("20000000"), announcement_factory=BinanceAnnouncements):
        if not 0 < hours <= 72: raise ValueError("duration must be >0 and <=72 hours")
        self.state, self.hours, self.market_factory = state, hours, market_factory
        self.shutdown = threading.Event()
        self.messages = queue.Queue(maxsize=1000)
        self.strategies = library()
        self.min_quote_volume = D(min_quote_volume)
        if not self.min_quote_volume.is_finite() or self.min_quote_volume < 0:
            raise ValueError("invalid quote volume floor")
        self.announcement_factory = announcement_factory
        self.watched_symbols = {"BTCUSDC", "ETHUSDC"}
        self.instrument_lock = threading.Lock()
        self.instruments = {}
        self.stop_requested = False
        self.last_final_attempt = 0

    def emit(self, kind, payload):
        try: self.messages.put((kind, payload), timeout=1)
        except queue.Full:
            self.stop_requested = True
            return False
        return True

    def record(self, kind, payload, now, active_seconds=0):
        folder = self.state / "market"
        folder.mkdir(exist_ok=True)
        day = time.strftime("%Y-%m-%d", time.gmtime(now/1000))
        with gzip.open(folder / f"{day}.jsonl.gz", "at") as f:
            f.write(dumps({"schema": 2, "received_ms": now, "kind": kind,
                           "payload": payload, "active_seconds": active_seconds}) + "\n")

    def scanner(self):
        market = self.market_factory()
        announcements = self.announcement_factory()
        instruments, cache = {}, {}
        next_universe = next_announcements = 0
        frames = {"1m": (60000, 101), "5m": (300000, 101),
                  "1h": (3600000, 201), "4h": (14400000, 202), "1d": (86400000, 151)}
        def load(symbol):
            result = {}
            for frame, (width, count) in frames.items():
                if self.shutdown.is_set():
                    return symbol, result
                at = milliseconds()
                key, expected = (symbol, frame), at//width*width-1
                bars = cache.get(key)
                if not bars or bars[-1].close_ms != expected:
                    bars = market.candles(symbol, frame, at, limit=count)
                    cache[key] = bars
                result[frame] = bars
            return symbol, result
        next_scan = time.monotonic()
        while not self.shutdown.is_set():
            try:
                now = milliseconds()
                offset = market.server_time() - milliseconds()
                if abs(offset) > 2000: raise ValueError(f"CLOCK_DRIFT:{offset}")
                if now >= next_universe:
                    instruments = market.eligible_instruments(self.min_quote_volume)
                    next_universe = now+300000
                with self.instrument_lock:
                    # Keep held contracts available to their feed even if liquidity eligibility changes.
                    self.instruments = {**{s: i for s, i in self.instruments.items() if s in self.watched_symbols},
                                        **instruments}
                    scan_instruments = dict(self.instruments)
                symbols = list(scan_instruments)
                if not symbols: raise ValueError("NO_COMPATIBLE_USDC_SYMBOLS")
                with ThreadPoolExecutor(max_workers=4) as pool:
                    series = dict(pool.map(load, symbols))
                if self.shutdown.is_set():
                    break
                events = ()
                if milliseconds() >= next_announcements:
                    try:
                        poll = announcements.poll(instruments)
                        events = poll.events
                        for diagnostic in poll.diagnostics:
                            self.emit("diagnostic", asdict(diagnostic))
                        next_announcements = milliseconds()+30000
                    except Exception as error:
                        delay = error.retry_after_seconds if isinstance(error, RateLimited) else 30
                        next_announcements = milliseconds()+int(delay*1000)
                        self.emit("diagnostic", {"status": "ANNOUNCEMENT_UNAVAILABLE", "error": str(error)})
                recent = {}
                for symbol in {e.symbol for e in events}:
                    at = milliseconds()
                    trades = market.trades(symbol, start_ms=at//60000*60000)
                    trades = [t for t in trades if t.time_ms//60000 == at//60000 and t.time_ms <= at]
                    if trades: recent[symbol] = (max(t.time_ms for t in trades), sum((t.quantity for t in trades), D(0)))
                quotes = market.quotes(symbols, skip_invalid=True)
                snapshots = []
                for symbol in symbols:
                    q = quotes.get(symbol)
                    eligible = symbol in instruments and q is not None and (q.ask-q.bid)/q.bid <= Policy().max_spread
                    if not eligible:
                        self.emit("diagnostic", {"status": "SKIPPED_MARKET_QUALITY", "symbol": symbol})
                    if q is None:
                        continue
                    bars = series[symbol]
                    trade_ms, volume = recent.get(symbol, (None, D(0)))
                    snapshots.append(Snapshot(milliseconds(), scan_instruments[symbol], q, bars["1m"], bars["5m"],
                        bars["1h"], bars["4h"], bars["1d"], tuple(e for e in events if e.symbol == symbol),
                        trade_ms, volume, eligible))
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
                choices = sorted(self.watched_symbols.intersection(self.instruments))
                selected = (choices[requested_symbol] if requested_symbol < len(choices) else None) if isinstance(requested_symbol, int) else requested_symbol
                symbols = (selected,) if selected in choices else ()
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
                        # Funding is valid independently of a later quote failure.
                        if fund and self.emit("funding", fund):
                            funding_since[symbol] = fund[-1].time_ms + 1
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
        caffeine = None
        try:
            now = milliseconds()
            run_file = self.state / "run.json"
            run = json.loads(run_file.read_text()) if run_file.exists() else {
                "started_ms": now, "deadline_ms": now+int(self.hours*3600000),
                "evidence": "natural", "status": "starting", "restarts": -1,
                "hours": self.hours, "reported_days": []}
            if run["evidence"] != "natural": raise ValueError("natural runtime cannot reuse synthetic state")
            if run["status"] == "completed": return 0
            if D(run.get("min_quote_volume", str(self.min_quote_volume))) != self.min_quote_volume:
                raise ValueError("existing run has a different quote volume floor")
            run["min_quote_volume"] = str(self.min_quote_volume)
            if (self.state / "stop.request").exists():
                (self.state / "stop.request").unlink()
            # A resumed run retains its deadline, but is no longer finished.
            run.pop("finished_ms", None)
            run.update(status="running", pid=os.getpid(), restarts=run["restarts"]+1)
            atomic_json(run_file, run)
            if sys.platform == "darwin":
                caffeine = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-s", "-w", str(os.getpid())],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            engines = []
            for profile, fraction in (("p1", ".01"), ("p5", ".05"), ("pA", ".05")):
                j = Journal(self.state / f"journal_{profile}.db", profile)
                journals.append(j)
                # pA learns from p1, which takes every approved signal at a fixed 1%.
                sizer = AdaptiveSizer(j, journals[0]) if profile == "pA" else None
                e = PaperEngine(j, Policy(risk_fraction=D(fraction)), adaptive=sizer)
                e.recover(now); engines.append(e)
                for order in e.active():
                    fields = order["instrument"]
                    ins = Instrument(**{k: v if k in {"symbol", "margin_asset", "status", "base_asset"} else D(v)
                                        for k, v in fields.items()})
                    self.instruments[ins.symbol] = ins
                with j.transaction(): j.event(now, "START" if run["restarts"] == 0 else "RESTART", {"pid": os.getpid()})
            self.record("restart", {}, now)
            self.watched_symbols.update(o["signal"]["symbol"] for e in engines for o in e.active())
            workers = [threading.Thread(target=self.scanner, daemon=True)] + [
                threading.Thread(target=self.feed, args=(run["started_ms"], slot), daemon=True)
                for slot in range(5)]  # Two baseline symbols plus at most three distinct held symbols.
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
                with self.instrument_lock:
                    self.watched_symbols = {"BTCUSDC", "ETHUSDC"} | {
                        o["signal"]["symbol"] for e in engines for o in e.active()}
                gap = (now-last_wall)/1000
                if gap > 5 or abs(gap-(mono-last_mono)) > 2:
                    detail = {"worker": "supervisor", "error": "SLEEP_OR_CLOCK_GAP"}
                    self.record("disconnected", detail, now)
                    dispatch(engines, "disconnected", detail, now)
                    for e in engines:
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
                    interval = (now-last_feed)/1000 if last_feed else 0
                    active = interval if 0 <= interval <= 5 else 0
                    self.record(kind, payload, now, active)
                    if dispatch(engines, kind, payload, now, active_seconds=active):
                        last_feed = now
                elif kind in {"funding", "diagnostic"} or (kind == "scan" and not finishing):
                    self.record(kind, payload, now)
                    dispatch(engines, kind, payload, now, strategies=self.strategies)
                elif kind in {"error", "disconnected"}:
                    self.record("disconnected", payload, now)
                    dispatch(engines, "disconnected", payload, now)
                if last_feed and now-last_feed > 5000:
                    if any(e.connected for e in engines):
                        detail = {"worker": "supervisor", "error": "FEED_TIMEOUT"}
                        self.record("disconnected", detail, now)
                        dispatch(engines, "disconnected", detail, now)
                due = any((D(o["remaining"]) > 0 and now >= o["signal"]["expires_ms"]) or
                          (D(o["filled"]) > 0 and o["state"] != "CLOSING" and
                           now >= o["first_fill_ms"] + o["signal"]["maximum_hold_seconds"]*1000)
                          for e in engines for o in e.active())
                if due or (finishing and any(e.active() for e in engines)):
                    detail = {"finish": finishing}
                    self.record("tick", detail, now)
                    dispatch(engines, "tick", detail, now)
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
                if finishing and not any(e.active() for e in engines) and now-self.last_final_attempt >= 60000:
                    self.last_final_attempt = now
                    # Finish funding reconciliation before final accounting publication.
                    market = self.market_factory()
                    try:
                        with self.instrument_lock: symbols = tuple(self.instruments)
                        for symbol in symbols:
                            observations = market.funding(symbol, run["started_ms"], now)
                            self.record("funding", observations, now)
                            dispatch(engines, "funding", observations, now)
                        run.update(status="stopped" if self.stop_requested else "completed", finished_ms=now)
                        atomic_json(run_file, run)
                        for day in run["reported_days"]:
                            write_reports(self.state, run, day=day)
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
            if caffeine is not None:
                caffeine.terminate()
                try: caffeine.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    caffeine.kill(); caffeine.wait()
            for j in journals: j.close()
            if "previous_handlers" in locals():
                for sig, handler in previous_handlers.items(): signal.signal(sig, handler)
            lock.close()
