"""Independent feed/scanner workers, single-owner execution, durable deadline (or none: hours=0 runs until
`quantos stop`; `quantos pause` / `resume` hold and allow new entries)."""
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

from contextlib import ExitStack

from .adaptive import AdaptiveSizer
from .dashboard import build_status, recent_events, write_status
from .health import Alerter, FeedMonitor, Heartbeat
from .live_inputs import OpenInterestCache, quarter_start_ms, quarterly_cutoff
from .selfcheck import self_check
from .sentiment import SentimentShadow
from .strategies import live_library
from .strategies.candidate_features import ALT_SOURCE_UNIVERSE
from .strategies.crash_flush_e2 import MAJORS
from .strategies.usdc_mapped import usdt_twin
from .engine import PaperEngine
from .journal import Journal
from .market import PublicMarket, RateLimited
from .stream import QuoteStream, UserStream
from .report import atomic_json, write_reports
from .risk import Cap200M5Policy, Cap200M10Policy, Demo200Policy, LivePolicy, LiveR5Policy, Policy, usable_quote
from .strategies import library
from .announcements import BinanceAnnouncements
from .types import D, Instrument, Snapshot, dumps


# Profile the demo mirror follows (one per account). pD = combined $200 demo run (owner 2026-11-02).
DEMO_PROFILE = os.environ.get("QUANTOS_DEMO_PROFILE", "pL")
# Gap between healthy batches before the feed counts as down. Polls run every 2s and each batch is
# validated on its own; at 5s, ordinary slow polls (p99 ~7s) marked it down ~55 times/hour (11-03).
FEED_TIMEOUT_MS = 10_000
# Dashboard status/heartbeat cadence. Was 10s; with the 5s page poll the display ran up to 15s behind.
MARKET_KEEP_DAYS = 3  # raw market recordings kept per state dir (~1.2 GB/day on the Mac, 11-07)
STATUS_EVERY_MS = 2_000
# Messages waiting in the main queue. Each feed batch is ~2s, so 15 queued ~ 30s behind live (11-05: 31 min).
QUEUE_LAG_WARN = 15
if DEMO_PROFILE not in ("pL", "pD"): raise ValueError("QUANTOS_DEMO_PROFILE must be pL or pD")


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


def dispatch(engines, kind, payload, now, *, strategies=(), active_seconds=0, sentiment=None,
             sentiment_fail_closed=False, sentiment_trace=None, external_trace=None):
    """Identical state transitions for recorded input replay and natural runtime."""
    for e in engines: shadow_call(e, "advance", now, kind, payload, now)
    if kind == "feed":
        quotes = [v for event, v in payload if event == "quote"]
        healthy = bool(quotes) and all(usable_quote(q, now) for q in quotes)
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
                if external_trace is not None and strategy.strategy_id == "ml1h_pattern":
                    external_trace.setdefault(strategy.strategy_id, {})[snapshot.instrument.symbol] = (
                        asdict(proposal) if proposal else None)
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
                if sentiment is not None:
                    # Shadow for most profiles; a profile's sentiment_gate lists rules a WOULD_BLOCK verdict blocks
                    # (event rules stay exempt: crashes with bad news did not do worse, edge_vault/crash_cause_v1).
                    verdict = sentiment.judge(proposal, now)
                    if sentiment_trace is not None:
                        sentiment_trace[proposal.signal_id] = verdict
                    with e.journal.transaction():
                        e.journal.event(now, "SENTIMENT_SHADOW", verdict)
                        unavailable = verdict.get("verdict") in {"UNAVAILABLE", "NO_SCORE"}
                        if ((verdict.get("verdict") == "WOULD_BLOCK" or sentiment_fail_closed and unavailable)
                                and proposal.strategy_id in getattr(e.policy, "sentiment_gate", ())):
                            e.journal.event(now, "REJECTED_RISK", {"signal_id": proposal.signal_id, "approved": False,
                                                                   "reason": "SENTIMENT_UNAVAILABLE" if unavailable else "SENTIMENT_BLOCK"})
                            shadow_call(e, "propose", now, proposal, snapshot.instrument, q, now,
                                        "SENTIMENT_UNAVAILABLE" if unavailable else "SENTIMENT_BLOCK")
                            continue
                oid = e.propose(proposal, snapshot.instrument, q, now)
                reason = "TAKEN"
                if oid is None:
                    row = e.journal.db.execute("SELECT payload FROM events WHERE kind='REJECTED_RISK' "
                        "AND json_extract(payload,'$.signal_id')=? ORDER BY id DESC LIMIT 1", (proposal.signal_id,)).fetchone()
                    reason = json.loads(row[0])["reason"] if row else "DUPLICATE_OR_UNAVAILABLE"
                shadow_call(e, "propose", now, proposal, snapshot.instrument, q, now, reason, oid)
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
    elif kind == "exchange":
        from .replay_effects import apply_effects
        for e in engines:
            if e.journal.profile == payload["profile"]: apply_effects(e, payload)
    elif kind == "control":
        for e in engines:
            if "manual_pause" in payload: e.manual_pause = bool(payload["manual_pause"])
            if payload.get("kill"): e.kill(now)
    else:
        raise ValueError(f"unknown input kind: {kind}")
    return False


def shadow_call(engine, method, now, *args):
    book = getattr(engine, "shadow", None)
    if book is None: return
    try:
        getattr(book, method)(*args)
    except Exception as error:
        # Evidence failures are visible, but cannot change execution or risk decisions.
        with engine.journal.transaction():
            engine.journal.event(now, "SHADOW_ERROR", {"method": method, "error": str(error)})
        engine.shadow_error = str(error)
        try: book.close()
        except Exception: pass
        engine.shadow = None


class Runtime:
    def __init__(self, state: Path, hours=72, market_factory=PublicMarket,
                 min_quote_volume=D("20000000"), announcement_factory=BinanceAnnouncements,
                 portfolio="legacy", sentiment_path=None, notify=True, execution="paper", client_factory=None,
                 stream=True, sentiment_fail_closed=False):
        if not (hours == 0 or 0 < hours <= 72): raise ValueError("duration must be 0 (until stopped) or 0-72 hours")
        self.state, self.hours, self.market_factory = state, hours, market_factory
        self.shutdown = threading.Event()
        self.messages = queue.Queue(maxsize=1000)
        self.dropped = {}  # kind -> messages dropped on a full queue
        if portfolio not in ("legacy", "live50"): raise ValueError("portfolio must be legacy or live50")
        self.portfolio = portfolio
        self.strategies = live_library() if portfolio == "live50" else library()
        # Live WebSocket quotes for the live50 feed; REST polling remains the fallback. Real market only.
        self.stream = (QuoteStream(self._stream_symbols) if stream and portfolio == "live50"
                       and market_factory is PublicMarket else None)
        self.sentiment = SentimentShadow(sentiment_path) if sentiment_path or sentiment_fail_closed else None
        self.sentiment_fail_closed = sentiment_fail_closed
        self.notify = notify
        if execution not in ("paper", "dry-run", "testnet"):
            raise ValueError("execution must be paper, dry-run or testnet (mainnet is not enabled here)")
        if execution != "paper" and portfolio != "live50":
            raise ValueError("exchange execution requires --portfolio live50")
        self.execution, self.client_factory = execution, client_factory
        self.min_quote_volume = D(min_quote_volume)
        if not self.min_quote_volume.is_finite() or self.min_quote_volume < 0:
            raise ValueError("invalid quote volume floor")
        self.announcement_factory = announcement_factory
        self.watched_symbols = {"BTCUSDC", "ETHUSDC"}
        self.instrument_lock = threading.Lock()
        self.instruments = {}
        self.stop_requested = False
        self.suspend_requested = False  # live50: SIGTERM/SIGINT leaves protected positions for the restart
        self.last_final_attempt = 0

    def _client(self):
        if self.client_factory is not None:
            return self.client_factory(self.execution)
        from ..live.client import SignedClient
        if self.execution == "dry-run":
            return SignedClient("dry-run", None, log_path=self.state / "dry_run_orders.jsonl")
        from ..live.keys import load_keys
        path = os.environ.get("QUANTOS_KEY_FILE")
        if not path: raise ValueError("QUANTOS_KEY_FILE must name the testnet key file")
        return SignedClient(self.execution, load_keys(path))

    def _stream_symbols(self):
        with self.instrument_lock:
            return set(self.watched_symbols).intersection(self.instruments)

    def emit(self, kind, payload):
        """A full queue means the main loop is behind. Quote batches are dropped (the next one supersedes
        them); other messages wait longer. Never flatten for it: that used to close every position."""
        try: self.messages.put((kind, payload), timeout=1 if kind == "feed" else 30)
        except queue.Full:
            self.dropped[kind] = self.dropped.get(kind, 0) + 1
            return False
        return True

    def record(self, kind, payload, now, active_seconds=0, *, context=None):
        folder = self.state / "market"
        folder.mkdir(exist_ok=True)
        day = time.strftime("%Y-%m-%d", time.gmtime(now/1000))
        if day != getattr(self, "_record_day", day):  # a non-stop run must not fill the disk
            for old in sorted(folder.glob("*.jsonl.gz"))[:-MARKET_KEEP_DAYS]:
                old.unlink(missing_ok=True)
        self._record_day = day
        with gzip.open(folder / f"{day}.jsonl.gz", "at") as f:
            row = {"schema": 2, "received_ms": now, "kind": kind,
                   "payload": payload, "active_seconds": active_seconds}
            if context is not None:
                row["context"] = context
            f.write(dumps(row) + "\n")

    def scanner(self):
        market = self.market_factory()
        announcements = self.announcement_factory()
        instruments, cache = {}, {}
        next_universe = next_announcements = next_cutoff = 0
        live = self.portfolio == "live50"
        oi = OpenInterestCache(market, sorted(MAJORS)) if live else None
        cutoff = (None, None)
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
                if live:
                    oi.refresh(now)
                    if now >= next_cutoff:
                        try:
                            threshold, end, detail = quarterly_cutoff(market, self.state, now)
                        except Exception as error:
                            threshold, end, detail = None, None, {"error": str(error)}
                        cutoff = (threshold, end)
                        # Retry hourly until calibrated, then at the next quarter boundary.
                        next_cutoff = now + 3600000 if threshold is None else quarter_start_ms(now) + 93 * 86400000
                        self.emit("diagnostic", {"status": "ALT_DIP_CALIBRATION" if threshold is not None
                                                 else "ALT_DIP_CALIBRATION_UNAVAILABLE", **detail})
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
                    extra, twin = {}, usdt_twin(symbol)
                    if live and twin in MAJORS:
                        extra["oi_values"] = oi.values(twin)
                    if live and cutoff[0] is not None and twin in ALT_SOURCE_UNIVERSE:
                        extra.update(quarterly_return_cutoff=cutoff[0], calibration_end_ms=cutoff[1])
                    snapshots.append(Snapshot(milliseconds(), scan_instruments[symbol], q, bars["1m"], bars["5m"],
                        bars["1h"], bars["4h"], bars["1d"], tuple(e for e in events if e.symbol == symbol),
                        trade_ms, volume, eligible, **extra))
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
                if self.portfolio == "live50":
                    # One batched quote call for every watched symbol (two profiles can hold more symbols
                    # than per-symbol workers); same request weight as five single-symbol workers.
                    symbols = tuple(choices) if requested_symbol == 0 else ()
            if not symbols:
                self.shutdown.wait(1); continue
            streamed = False
            try:
                records = []
                now = milliseconds()
                for symbol in symbols:
                    # Trade prints only fill LIMIT entries; live50 has none, and aggTrades (weight 20 per
                    # call every 2s) pushed two runs on one IP past Binance's request-weight limit.
                    if self.portfolio == "live50": trades = ()
                    else: trades = market.trades(symbol, from_id=cursors[symbol]) if symbol in cursors else market.trades(symbol, start_ms=now-2000)
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
                    if self.portfolio != "live50":
                        records.insert(0, ("quote", market.quote(symbol)))
                if self.portfolio == "live50":
                    batch = self.stream.quotes(symbols, now) if self.stream else None
                    streamed = batch is not None
                    if not streamed:
                        batch = market.quotes(symbols, skip_invalid=True)
                        if self.stream: self.stream.seed_books(batch)
                    # One quiet thin book must not blank the whole batch (11-02 run: ~4.2k feed errors).
                    if not batch: raise ValueError("invalid or stale quote")
                    records[0:0] = [("quote", batch[s]) for s in symbols if s in batch]
                if now >= next_funding: next_funding = now + 60000
                self.emit("feed", records)
                backoff = 2
            except Exception as error:
                self.emit("disconnected", {"worker": "feed", "error": str(error)})
                cursors.clear()
                delay = error.retry_after_seconds if isinstance(error, RateLimited) else backoff
                self.shutdown.wait(delay)
                backoff = min(60, backoff * 2)
            # Streamed quotes are already live, so hand them over every 1s; REST polls stay at 2s.
            self.shutdown.wait(max(0, (1 if streamed else 2) - (time.monotonic() - start)))

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
                "started_ms": now, "deadline_ms": now+int(self.hours*3600000) if self.hours else None,
                "evidence": "natural", "status": "starting", "restarts": -1,
                "hours": self.hours, "reported_days": [], "portfolio": self.portfolio,
                **({"profiles": ["pL"]} if self.portfolio == "live50" else {})}
            if run["evidence"] != "natural": raise ValueError("natural runtime cannot reuse synthetic state")
            if run["status"] == "completed": return 0
            if D(run.get("min_quote_volume", str(self.min_quote_volume))) != self.min_quote_volume:
                raise ValueError("existing run has a different quote volume floor")
            if run.get("portfolio", "legacy") != self.portfolio:
                raise ValueError("existing run has a different portfolio")
            if self.portfolio == "live50":
                run["profiles"] = ["pL", "pL5", "pM10", "pM5"] if self.execution == "paper" else [DEMO_PROFILE]
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
            # The demo mirror can only follow one profile (positions would net on one account).
            profiles = (((("pL", None), ("pL5", None), ("pM10", None), ("pM5", None)) if self.execution == "paper"
                         else ((DEMO_PROFILE, None),))
                        if self.portfolio == "live50"
                        else (("p1", ".01"), ("p5", ".05"), ("pA", ".05")))
            for profile, fraction in profiles:
                if profile in ("pL", "pL5", "pM10", "pM5", "pD"):
                    j = Journal(self.state / f"journal_{profile}.db", profile,
                                initial_balance="200" if profile in ("pM10", "pM5", "pD") else "50")
                    journals.append(j)
                    e = PaperEngine(j, {"pL": LivePolicy, "pL5": LiveR5Policy, "pM10": Cap200M10Policy,
                                         "pM5": Cap200M5Policy, "pD": Demo200Policy}[profile]())
                    e.recover(now); engines.append(e)
                    with j.transaction(): j.event(now, "START" if run["restarts"] == 0 else "RESTART", {"pid": os.getpid()})
                    continue
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
            self.record("restart", {}, now, context={"execution": self.execution, "exchange_effects": True})
            from .shadow import ShadowBook
            for e in engines:
                try: e.shadow = ShadowBook(self.state / f"shadow_{e.journal.profile}.db", e, now)
                except Exception as error:
                    e.shadow_error = str(error)
                    with e.journal.transaction(): e.journal.event(now, "SHADOW_ERROR", {"error": str(error)})
            self.watched_symbols.update(o["signal"]["symbol"] for e in engines for o in e.active())
            workers = [threading.Thread(target=self.scanner, daemon=True)] + [
                threading.Thread(target=self.feed, args=(run["started_ms"], slot), daemon=True)
                for slot in range(5)]  # Two baseline symbols plus at most three distinct held symbols.
            if self.stream is not None:
                workers.append(threading.Thread(target=self.stream.run, args=(self.shutdown,), daemon=True))
            for worker in workers: worker.start()
            previous_handlers = {}
            if threading.current_thread() is threading.main_thread():
                for sig in (signal.SIGTERM, signal.SIGINT):
                    # A redeploy or launchd stop must not realise trades in a supervised live50 run;
                    # an explicit `quantos stop` (stop.request), the deadline or KILL still close them.
                    flag = "suspend_requested" if self.portfolio == "live50" else "stop_requested"
                    previous_handlers[sig] = signal.signal(sig, lambda *_, f=flag: setattr(self, f, True))
            alerter, heartbeat = Alerter(self.state, notify=self.notify), Heartbeat(self.state)
            monitor = FeedMonitor(stale_seconds=60, startup_grace_seconds=120)
            # A 4h-close scan refreshes 1h+4h candles for every symbol and takes ~65s; prices keep the 60s
            # rule, scans get 150s (five missed 30s cycles), which still catches a real stall quickly.
            scan_monitor = FeedMonitor(stale_seconds=150, startup_grace_seconds=180)
            monitor.expect("feed", now); scan_monitor.expect("scan", now)
            next_check, killed, last_mirror, mirror, user = 0, False, 0, None, None
            if self.execution != "paper":
                from ..live.bridge import ExchangeMirror
                def mirror_alert(level, key, message):
                    if level == "recovered": alerter.clear(key, milliseconds())
                    else: alerter.alert(level, key, message, milliseconds())
                mirror = ExchangeMirror(self._client(), f"q{run['started_ms'] // 1000}", self.state, mirror_alert)
                if getattr(mirror.client, "ws_host", None) and mirror.client.mode != "dry-run":
                    user = UserStream(mirror.client)
                    threading.Thread(target=user.run, args=(self.shutdown,), daemon=True).start()
            last_wall, last_mono = milliseconds(), time.monotonic()
            last_feed, last_status, last_report = 0, 0, 0
            finishing_since = None
            while True:
                now, mono = milliseconds(), time.monotonic()
                with self.instrument_lock:
                    self.watched_symbols = {"BTCUSDC", "ETHUSDC"} | {
                        o["signal"]["symbol"] for e in engines for o in e.active()}
                    for e in engines:
                        if getattr(e, "shadow", None): self.watched_symbols.update(e.shadow.symbols())
                gap = (now-last_wall)/1000
                if gap > 5 or abs(gap-(mono-last_mono)) > 2:
                    detail = {"worker": "supervisor", "error": "SLEEP_OR_CLOCK_GAP"}
                    self.record("disconnected", detail, now)
                    dispatch(engines, "disconnected", detail, now)
                    for e in engines:
                        with e.journal.transaction():
                            e.journal.event(now, "OBSERVATION_GAP", {"seconds": gap, "missed_scan_estimate": int(gap//30)})
                last_wall, last_mono = now, mono
                if self.suspend_requested:
                    run.update(status="suspended", suspended_ms=now)
                    atomic_json(run_file, run)
                    for e in engines:
                        with e.journal.transaction():
                            e.journal.event(now, "SUSPENDED", {"active": len(e.active())})
                    return 0
                self.stop_requested = self.stop_requested or (self.state / "stop.request").exists()
                finishing = self.stop_requested or (run["deadline_ms"] is not None and now >= run["deadline_ms"])
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
                        monitor.note("feed", now)
                elif kind in {"funding", "diagnostic"} or (kind == "scan" and not finishing):
                    sentiment_trace = {} if kind == "scan" else None
                    external_trace = {} if kind == "scan" else None
                    dispatch(engines, kind, payload, now, strategies=self.strategies, sentiment=self.sentiment,
                             sentiment_fail_closed=self.sentiment_fail_closed,
                             sentiment_trace=sentiment_trace, external_trace=external_trace)
                    self.record(kind, payload, now, context=(
                        {"sentiment_enabled": self.sentiment is not None,
                         "sentiment_fail_closed": self.sentiment_fail_closed,
                         "external_proposals": external_trace,
                         "sentiment_verdicts": sentiment_trace}
                        if sentiment_trace is not None else None))
                    if kind == "scan":
                        scan_monitor.note("scan", now)
                        if now >= next_check and payload:
                            checks = self_check(self.strategies, payload)
                            for e in engines:
                                with e.journal.transaction(): e.journal.event(now, "SELF_CHECK", checks)
                            for sid, c in checks.items():
                                if c["ok"]: alerter.clear(f"selfcheck:{sid}", now)
                                else: alerter.alert(c["level"], f"selfcheck:{sid}", f"{sid}: {c['detail']}", now)
                            healthy = all(c["ok"] for c in checks.values())
                            next_check = now + (86400000 if healthy else 3600000)
                elif kind in {"error", "disconnected"}:
                    self.record("disconnected", payload, now)
                    dispatch(engines, "disconnected", payload, now)
                if last_feed and now-last_feed > FEED_TIMEOUT_MS:
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
                if mirror is not None and now - last_mirror >= 2000:
                    last_mirror = now
                    try:
                        from .replay_effects import TraceEngine
                        traced = TraceEngine(engines[0])
                        with engines[0].journal.transaction():
                            result = mirror.sync(traced, now, user.drain() if user else ())
                            if not result.get("ok", True):
                                engines[0].journal.event(now, "EXCHANGE_RECONCILE_FAILED", result)
                        self.record("exchange", {"profile": engines[0].journal.profile,
                            "effects": traced.events, "exchange_hold": getattr(engines[0], "exchange_hold", False)}, now)
                    except Exception as error:
                        alerter.alert("critical", "mirror", f"exchange mirror error: {error}", now)
                for e in engines:
                    shadow_call(e, "advance", now, "tick", {"finish": finishing}, now)
                if (self.state / "KILL").exists() and not killed:
                    killed = True
                    self.record("control", {"kill": True}, now)
                    for e in engines: e.kill(now)
                    alerter.alert("critical", "kill_switch", "KILL file present: entries paused, positions closing", now)
                if now-last_status >= STATUS_EVERY_MS:
                    paused = (self.state / "PAUSE").exists()
                    if any(getattr(e, "manual_pause", False) != paused for e in engines):
                        self.record("control", {"manual_pause": paused}, now)
                    for e in engines: e.manual_pause = paused
                    for e in engines:
                        if getattr(e, "shadow_error", None):
                            alerter.alert("critical", f"shadow:{e.journal.profile}", e.shadow_error, now)
                    stale = {**dict(monitor.stale(now)), **dict(scan_monitor.stale(now))}
                    for name in ("feed", "scan"):
                        if name in stale:
                            alerter.alert("critical", f"stale:{name}", f"{name} stale for {stale[name]:.0f}s", now)
                        else:
                            alerter.clear(f"stale:{name}", now)
                    if user is not None and user.down_since and now - user.down_since > 120_000:
                        alerter.alert("warn", "user_stream", "account stream down >2min; mirror falls back to 30s REST checks", now)
                    elif user is not None and user.up:
                        alerter.clear("user_stream", now)
                    if self.dropped:
                        alerter.alert("critical", "queue_full", f"main loop fell behind; dropped {self.dropped}", now)
                        self.dropped = {}
                    if self.sentiment is not None and now - getattr(self, "_sentiment_checked", 0) >= 60_000:
                        self._sentiment_checked = now
                        feed = self.sentiment.read(now)
                        if feed["available"]: alerter.clear("sentiment", now)
                        else:
                            effect = "trend entries blocked" if self.sentiment_fail_closed else "trend rules trade ungated"
                            alerter.alert("warn", "sentiment", f"sentiment unavailable ({effect}): {feed['reason']}", now)
                    backlog = self.messages.qsize()
                    if backlog >= QUEUE_LAG_WARN:
                        alerter.alert("critical", "queue_lag", f"main loop {backlog} messages behind the feed", now)
                    else:
                        alerter.clear("queue_lag", now)
                    heartbeat.beat(now, {"status": run["status"], "feed_age_ms": now-last_feed if last_feed else None,
                                         "queue": backlog, "paused": paused,
                                         **({"stream": {"live": self.stream.healthy(now), "messages": self.stream.messages,
                                                        "reconnects": self.stream.reconnects}} if self.stream else {}),
                                         **({"user_stream": {"live": user.up, "messages": user.messages,
                                                             "reconnects": user.reconnects}} if user else {}),
                                         "stale": sorted(stale)})
                    try:
                        with ExitStack() as stack:
                            for e in engines: stack.enter_context(e.journal.transaction())
                            status = build_status("paper", run, engines, {"ages_s": {**monitor.ages(now), **scan_monitor.ages(now)}, "stale": stale},
                                                  killed, now, recent_events(engines[0].journal),
                                                  recent_events(engines[0].journal, ("FILLED", "PARTIAL", "CLOSED")))
                        write_status(self.state, status)
                    except Exception as error:
                        alerter.alert("warn", "dashboard", f"status write failed: {error}", now)
                    atomic_json(self.state / "status.json", {"mode": "paper", "run": run, "updated_ms": now, "paused": paused,
                        "feed_age_ms": now-last_feed if last_feed else None,
                        "next_scan_ms": (now//30000+1)*30000,
                        "profiles": {e.journal.profile: {"cash": str(e.journal.cash()), "active": e.active(),
                            "paused": e.journal.get("paused", False)} for e in engines}})
                    last_status = now
                if now-last_report >= 3600000:
                    last_report = now
                    try: write_reports(self.state, run)
                    except Exception as error:  # reporting must never stop observation
                        alerter.alert("warn", "report", f"hourly report failed: {error}", now)
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
            for e in locals().get("engines", []):
                if getattr(e, "shadow", None): e.shadow.close()
            if caffeine is not None:
                caffeine.terminate()
                try: caffeine.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    caffeine.kill(); caffeine.wait()
            for j in journals: j.close()
            if "previous_handlers" in locals():
                for sig, handler in previous_handlers.items(): signal.signal(sig, handler)
            lock.close()
