"""Continuous paper orchestration; Execution alone owns economic state."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
import logging
import math
from typing import Callable, Protocol

from quantos.application.evaluation import _TradeBook, _marked_state, _validate_datasets
from quantos.application.risk_execution import TradingStep
from quantos.domain.common import require_decimal, require_non_empty, require_utc, require_v1_symbol
from quantos.domain.evaluation import AlphaEvaluation, AlphaDecisionFunction, EquityPoint
from quantos.domain.execution import OrderSide
from quantos.domain.execution.core import ExecutionEngine, ExecutionLedger, PaperFillProvider
from quantos.domain.features import FEATURE_VERSION, MIN_HISTORY, compute_feature_vector
from quantos.domain.market_data import Candle, DatasetIdentity, MarketEvent, validate_candle_sequence
from quantos.domain.risk.engine import MarketState, RiskContext, RiskEngine, RiskPolicy
from quantos.domain.runtime_contracts import AccountSnapshot, arithmetic, canonical, identity, primitive


class PaperRuntimeError(ValueError):
    """A fatal condition requiring explicit operator recovery; never auto-resume."""


@dataclass(frozen=True, slots=True)
class PaperRuntimePolicy:
    symbols: tuple[str, ...]
    initial_capital: Decimal
    risk: RiskPolicy
    synchronization_timeout_ms: int
    alpha_implementation_id: str
    provider_clock_skew_tolerance_ms: int
    mode: str = "paper"

    def __post_init__(self):
        if self.mode != "paper":
            raise ValueError("only paper runtime is enabled")
        if type(self.symbols) is not tuple or not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("symbols must be a nonempty unique tuple")
        for symbol in self.symbols:
            require_v1_symbol(symbol)
        object.__setattr__(self, "symbols", tuple(sorted(self.symbols)))
        require_decimal(self.initial_capital, "initial_capital")
        if self.initial_capital <= 0:
            raise ValueError("initial capital must be positive")
        self.risk.__post_init__()
        if type(self.synchronization_timeout_ms) is not int or self.synchronization_timeout_ms <= 0:
            raise ValueError("synchronization_timeout_ms must be a positive integer")
        if (type(self.provider_clock_skew_tolerance_ms) is not int
                or not 0 <= self.provider_clock_skew_tolerance_ms <= 1000):
            raise ValueError("provider_clock_skew_tolerance_ms must be an integer from 0 to 1000")
        require_non_empty(self.alpha_implementation_id, "alpha_implementation_id")


class RuntimeStore(Protocol):
    def acquire(self) -> None: ...
    def release(self) -> None: ...
    def load(self) -> dict | None: ...
    def save(self, state: dict) -> None: ...
    def evidence_head(self) -> tuple[int, str | None]: ...
    def latest_evidence(self) -> dict | None: ...
    def append(self, evidence: dict) -> tuple[int, str]: ...


class StopSignal:
    """Event-loop-local stop signal. It never requests liquidation."""
    def __init__(self):
        self.event = asyncio.Event()
        self.reason = "requested shutdown"

    def request(self, reason: str = "requested shutdown") -> None:
        require_non_empty(reason, "stop reason")
        self.reason = reason
        self.event.set()

    @property
    def active(self) -> bool:
        return self.event.is_set()


def _decode_candle(value: dict) -> Candle:
    fields = dict(value)
    for name in ("open_time", "close_time"):
        fields[name] = datetime.fromisoformat(fields[name])
    for name in ("open", "high", "low", "close", "volume", "quote_volume"):
        fields[name] = Decimal(fields[name])
    return Candle(**fields)


class PaperRuntime:
    """Single consumer, bounded histories, one durable commit per completed bundle.

    Alpha must be reproducible from its FeatureVector and stable implementation;
    private mutable Alpha state cannot be recovered by this narrow boundary.
    The caller owns neither account mutation nor fill simulation.
    """
    def __init__(self, *, policy: PaperRuntimePolicy, alpha: AlphaDecisionFunction,
                 ledger: ExecutionLedger, store: RuntimeStore, clock: Callable[[], datetime],
                 stop: StopSignal | None = None, logger: logging.Logger | None = None,
                 monotonic: Callable[[], float] | None = None):
        policy.__post_init__()
        if not callable(alpha) or not callable(clock):
            raise ValueError("explicit Alpha and UTC clock callables required")
        self.policy, self.alpha, self.ledger, self.store, self.clock = policy, alpha, ledger, store, clock
        self.stop = stop if stop is not None else StopSignal()
        self.monotonic = monotonic
        self._last_tick = None
        self.logger = logger or logging.getLogger("quantos")
        self.runtime_id = identity(("paper-runtime-v1", "1m", policy, FEATURE_VERSION, MIN_HISTORY))
        self.execution: ExecutionEngine | None = None
        self.histories = {symbol: () for symbol in policy.symbols}
        self.last_minute = None
        self.day = None
        self.day_equity = self.peak = policy.initial_capital
        self.versions = None
        self.book = _TradeBook()
        self.totals = {"fees": Decimal("0"), "slippage": Decimal("0"),
                       "realized_pnl": Decimal("0"), "trade_count": 0}
        self.last_equity = None
        self.last_observation = None
        self.evidence_count, self.evidence_id = 0, None
        self._started = self._initialized = self._processing = False

    def _log(self, event, context):
        self.logger.info(event, extra={"event": event, "context": primitive(context)})

    def _now(self):
        now = self.clock()
        require_utc(now, "observation time")
        if self.last_observation is not None and now < self.last_observation:
            raise PaperRuntimeError("UTC observation clock moved backwards")
        self.last_observation = now
        return now

    def _fresh(self, candles):
        now = self._now()
        for candle in candles:
            age = now - candle.close_time
            if (age < -timedelta(milliseconds=self.policy.provider_clock_skew_tolerance_ms)
                    or age > timedelta(seconds=self.policy.risk.stale_seconds)):
                raise PaperRuntimeError("stale or future live candle at actual observation time")

    def _tick(self):
        tick = self.monotonic() if self.monotonic is not None else asyncio.get_running_loop().time()
        if not math.isfinite(tick) or (self._last_tick is not None and tick < self._last_tick):
            raise PaperRuntimeError("invalid or backward monotonic clock")
        self._last_tick = tick
        return tick

    def _checkpoint(self, status="ready"):
        return primitive({
            "schema": "paper-runtime-v1", "runtime_id": self.runtime_id, "status": status,
            "initial": self.initial, "last_minute": self.last_minute,
            "histories": self.histories, "day": self.day, "day_equity": self.day_equity,
            "peak": self.peak, "versions": self.versions, "entry_fees": dict(self.book._entry_fees),
            "totals": self.totals, "last_equity": self.last_equity,
            "last_observation": self.last_observation,
            "execution_id": identity(self.execution.snapshot), "ledger_id": identity(self.ledger.read()),
            "evidence_count": self.evidence_count, "evidence_id": self.evidence_id,
        })

    def _open(self):
        saved = self.store.load()
        head = self.store.evidence_head()
        if saved is None:
            if self.ledger.read() or head != (0, None):
                raise PaperRuntimeError("missing runtime checkpoint for existing ledger/evidence")
            now = self._now()
            self.initial = AccountSnapshot(now-timedelta(seconds=self.policy.risk.stale_seconds),
                {"USDT": self.policy.initial_capital, "BTC": Decimal("0"), "ETH": Decimal("0")}, ())
        else:
            if saved["status"] != "ready" or saved["runtime_id"] != self.runtime_id:
                raise PaperRuntimeError("blocked/unfinished runtime or incompatible configuration/Alpha identity")
            if (saved["evidence_count"], saved["evidence_id"]) != head:
                raise PaperRuntimeError("runtime evidence/checkpoint mismatch")
            initial = saved["initial"]
            if initial["positions"]:
                raise PaperRuntimeError("initial runtime account must be flat")
            self.initial = AccountSnapshot(datetime.fromisoformat(initial["timestamp"]),
                {asset: Decimal(value) for asset, value in initial["balances"].items()}, ())
            if self.initial.balances["USDT"] != self.policy.initial_capital:
                raise PaperRuntimeError("initial capital differs from configuration")
            if not self.ledger.read():
                raise PaperRuntimeError("runtime checkpoint exists but execution ledger is missing")
        self.execution = ExecutionEngine(self.initial, self.policy.risk.costs, self.ledger,
                                         PaperFillProvider(), self.logger)
        self.step = TradingStep(RiskEngine(self.policy.risk), self.execution, self.logger)
        if saved is not None:
            if saved["execution_id"] != identity(self.execution.snapshot) or saved["ledger_id"] != identity(self.ledger.read()):
                raise PaperRuntimeError("runtime/Execution ledger state mismatch")
            if set(saved["histories"]) != set(self.policy.symbols):
                raise PaperRuntimeError("runtime history symbol mismatch")
            self.histories = {s: tuple(_decode_candle(c) for c in saved["histories"][s]) for s in self.policy.symbols}
            self.last_minute = None if saved["last_minute"] is None else datetime.fromisoformat(saved["last_minute"])
            if self.last_minute is not None:
                histories = []
                for symbol, candles in self.histories.items():
                    if not 1 <= len(candles) <= MIN_HISTORY or candles[-1].open_time != self.last_minute:
                        raise PaperRuntimeError("invalid bounded runtime history")
                    meta = DatasetIdentity(symbol, "1m", candles[0].open_time, candles[-1].open_time,
                                           "runtime", "v1", "v1")
                    histories.append(validate_candle_sequence(meta, candles))
                _validate_datasets(tuple(histories))
                if len({tuple(c.open_time for c in h) for h in self.histories.values()}) != 1:
                    raise PaperRuntimeError("inconsistent cross-symbol histories")
            elif any(self.histories.values()):
                raise PaperRuntimeError("history exists without committed minute")
            self.day = None if saved["day"] is None else datetime.fromisoformat(saved["day"])
            self.day_equity, self.peak = Decimal(saved["day_equity"]), Decimal(saved["peak"])
            for value in (self.day_equity, self.peak):
                require_decimal(value, "equity reference")
                if value <= 0:
                    raise PaperRuntimeError("invalid equity reference")
            self.versions = None if saved["versions"] is None else tuple(saved["versions"])
            if self.versions is not None:
                if len(self.versions) != 2:
                    raise PaperRuntimeError("invalid Alpha versions")
                for value in self.versions:
                    require_non_empty(value, "Alpha version")
            for symbol, value in saved["entry_fees"].items():
                if symbol not in self.policy.symbols:
                    raise PaperRuntimeError("unsupported entry fee symbol")
                self.book._entry_fees[symbol] = require_decimal(Decimal(value), "entry fee", non_negative=True)
            self.totals = {key: Decimal(saved["totals"][key]) for key in ("fees", "slippage", "realized_pnl")}
            self.totals["trade_count"] = saved["totals"]["trade_count"]
            if type(self.totals["trade_count"]) is not int or self.totals["trade_count"] < 0:
                raise PaperRuntimeError("invalid completed trade count")
            for key in ("fees", "slippage", "realized_pnl"):
                require_decimal(self.totals[key], key, non_negative=key != "realized_pnl")
            self.last_equity = saved["last_equity"]
            self.last_observation = datetime.fromisoformat(saved["last_observation"])
            require_utc(self.last_observation, "last observation")
            self.evidence_count, self.evidence_id = head
            if canonical(self._checkpoint()) != canonical(saved):
                raise PaperRuntimeError("invalid runtime checkpoint fields or canonical representation")
            latest = self.store.latest_evidence()
            if latest is not None:
                if self.last_minute is None or any(len(h) != min(self.evidence_count, MIN_HISTORY) for h in self.histories.values()):
                    raise PaperRuntimeError("history differs from committed evidence count")
                if any(latest[key] != saved[key] for key in
                       ("runtime_id", "execution_id", "ledger_id", "totals", "entry_fees")) or latest["equity"] != saved["last_equity"]:
                    raise PaperRuntimeError("runtime checkpoint/evaluation evidence mismatch")
            elif (self.last_minute is not None or self.last_equity is not None or self.day is not None
                  or self.versions is not None or self.book._entry_fees or any(self.totals.values())
                  or self.day_equity != self.policy.initial_capital or self.peak != self.policy.initial_capital):
                raise PaperRuntimeError("runtime state claims observations without evidence")
            if self.last_minute is not None:
                marks = {s: h[-1].close for s, h in self.histories.items()}
                equity, exposure = _marked_state(self.execution.snapshot, marks)
                timestamp = next(iter(self.histories.values()))[-1].close_time
                if self.day != timestamp.replace(hour=0, minute=0, second=0, microsecond=0) or self.peak < max(equity, self.day_equity):
                    raise PaperRuntimeError("inconsistent persisted equity references")
                expected_point = EquityPoint(timestamp, equity, self.execution.snapshot.balances["USDT"],
                                             exposure, self.day_equity, self.peak)
                if primitive(expected_point) != self.last_equity:
                    raise PaperRuntimeError("persisted equity differs from Execution marks")
        else:
            self.store.save(self._checkpoint())
        self.execution.reconcile()
        self._initialized = True
        self._log("paper_runtime_recovered" if saved else "paper_runtime_started", {"runtime_id": self.runtime_id})

    def _validate_event(self, event):
        if type(event) is not MarketEvent or type(event.candle) is not Candle:
            raise PaperRuntimeError("canonical MarketEvent required")
        MarketEvent.__post_init__(event)
        candle = event.candle
        Candle.__post_init__(candle)
        if candle.symbol not in self.policy.symbols:
            raise PaperRuntimeError("unsubscribed symbol")
        end = candle.open_time + timedelta(minutes=1)
        if candle.open_time.second or candle.open_time.microsecond or not end-timedelta(milliseconds=1) <= candle.close_time <= end:
            raise PaperRuntimeError("invalid completed one-minute candle boundary")
        if candle.close <= 0:
            raise PaperRuntimeError("invalid executable price")
        previous = self.histories[candle.symbol]
        if previous:
            if candle.open_time == previous[-1].open_time and candle == previous[-1]:
                return None
            if candle.open_time != previous[-1].open_time + timedelta(minutes=1):
                raise PaperRuntimeError("conflicting duplicate, backward time or skipped minute")
            if candle.close_time - previous[-1].close_time != timedelta(minutes=1):
                raise PaperRuntimeError("inconsistent completion grid")
        self._fresh((candle,))
        # Provider clock disagreement never changes the local observation clock.
        if event.timestamp - self.last_observation > timedelta(milliseconds=self.policy.provider_clock_skew_tolerance_ms):
            raise PaperRuntimeError("future event observation timestamp")
        return candle

    def _process(self, pending):
        candles = tuple(pending[s] for s in self.policy.symbols)
        self._fresh(candles)
        if self.stop.active:
            return
        self.store.save(self._checkpoint("processing"))
        self._processing = True
        for candle in candles:
            self.histories[candle.symbol] = (self.histories[candle.symbol]+(candle,))[-MIN_HISTORY:]
        timestamp = candles[0].close_time
        marks = {c.symbol: c.close for c in candles}
        equity, _ = _marked_state(self.execution.snapshot, marks)
        day = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        if self.day != day:
            self.day, self.day_equity = day, equity
        self.peak = max(self.peak, equity)
        decisions = []
        for candle in candles:
            if self.stop.active:
                break
            self._fresh(candles)
            feature = compute_feature_vector(self.histories[candle.symbol], decision_time=timestamp)
            if feature is None:
                self._log("paper_runtime_warmup", {"symbol": candle.symbol, "timestamp": timestamp,
                                                  "candles": len(self.histories[candle.symbol])})
                continue
            evaluated = self.alpha(feature)
            if type(evaluated) is not AlphaEvaluation:
                raise PaperRuntimeError("Alpha must return AlphaEvaluation")
            evaluated.__post_init__()
            alpha = evaluated.decision
            if (alpha.timestamp, alpha.symbol, alpha.feature_version) != (timestamp, feature.symbol, feature.feature_version):
                raise PaperRuntimeError("Alpha/causal FeatureVector mismatch")
            versions = (alpha.strategy_version, alpha.model_version)
            if self.versions is not None and self.versions != versions:
                raise PaperRuntimeError("Alpha versions changed")
            self.versions = versions
            self._fresh(candles)
            if self.stop.active:
                decisions.append({"alpha": alpha, "risk": None, "execution": None, "stopped": True})
                break
            before = self.execution.snapshot
            equity, _ = _marked_state(before, marks)
            self.peak = max(self.peak, equity)
            context = RiskContext(timestamp, tuple(MarketState(c, True) for c in candles), before,
                                  evaluated.gross_edge_rate, day, self.day_equity, self.peak, evaluated.volatility)
            result = self.step.run(alpha, context)
            decisions.append({"alpha": alpha, "risk": result.risk, "execution": result.execution})
            if result.execution is not None:
                self.book.observe(before=before, result=result.execution, side=OrderSide(alpha.action.value), symbol=alpha.symbol)
                with localcontext(arithmetic()):
                    self.totals["fees"] += result.execution.fee
                    self.totals["slippage"] += result.execution.slippage_cost
            equity, _ = _marked_state(self.execution.snapshot, marks)
            self.peak = max(self.peak, equity)
        equity, exposure = _marked_state(self.execution.snapshot, marks)
        point = EquityPoint(timestamp, equity, self.execution.snapshot.balances["USDT"], exposure,
                            self.day_equity, max(self.peak, equity))
        with localcontext(arithmetic()):
            self.totals["realized_pnl"] += sum((t.net_pnl for t in self.book.completed), Decimal("0"))
        self.totals["trade_count"] += len(self.book.completed)
        evidence = primitive({"runtime_id": self.runtime_id, "equity": point, "decisions": decisions,
                              "completed_trades": tuple(self.book.completed), "totals": self.totals,
                              "entry_fees": dict(self.book._entry_fees),
                              "execution_id": identity(self.execution.snapshot),
                              "ledger_id": identity(self.ledger.read()), "stop_reason": self.stop.reason if self.stop.active else None})
        self.execution.reconcile()
        self.evidence_count, self.evidence_id = self.store.append(evidence)
        self.last_minute = candles[0].open_time
        self.last_equity = primitive(point)
        self.book.completed.clear()
        self.store.save(self._checkpoint())
        self._processing = False
        self._log("paper_runtime_minute", evidence)

    async def run(self, feed):
        if self._started:
            raise PaperRuntimeError("runtime instances are single-use")
        self._started = True
        receiver = stopper = None
        acquired = False
        pending = {}
        deadline = None
        try:
            self.store.acquire()
            acquired = True
            self._open()
            stopper = asyncio.create_task(self.stop.event.wait())
            while not self.stop.active:
                receiver = asyncio.create_task(anext(feed))
                timeout = None if deadline is None else max(0, deadline-self._tick())
                done, _ = await asyncio.wait((receiver, stopper), timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                if stopper in done:
                    break
                if not done or (deadline is not None and self._tick() >= deadline):
                    raise PaperRuntimeError("completed-minute synchronization timeout")
                try:
                    event = receiver.result()
                except StopAsyncIteration:
                    if pending:
                        raise PaperRuntimeError("feed ended with missing required symbol")
                    break
                receiver = None
                candle = self._validate_event(event)
                if candle is None:
                    continue
                if pending:
                    first = next(iter(pending.values()))
                    if (candle.open_time, candle.close_time) != (first.open_time, first.close_time):
                        raise PaperRuntimeError("cross-symbol minute mismatch")
                if candle.symbol in pending:
                    if candle != pending[candle.symbol]:
                        raise PaperRuntimeError("conflicting pending duplicate")
                    continue
                pending[candle.symbol] = candle
                if deadline is None:
                    deadline = self._tick()+self.policy.synchronization_timeout_ms/1000
                if len(pending) == len(self.policy.symbols):
                    self._process(pending)
                    pending, deadline = {}, None
            return self.last_equity
        except asyncio.CancelledError:
            self._log("paper_runtime_cancelled", {"processing": self._processing})
            raise
        except Exception as error:
            if self._initialized:
                try:
                    self.store.save(self._checkpoint("blocked"))
                except Exception:
                    self._log("paper_runtime_checkpoint_failure", {"recovery": "operator inspection required"})
            self._log("paper_runtime_failed", {"reason": str(error)})
            raise PaperRuntimeError(str(error)) from error
        finally:
            tasks = [task for task in (receiver, stopper) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await feed.aclose()
            finally:
                if acquired:
                    self.store.release()
                self._log("paper_runtime_stopped", {"reason": self.stop.reason if self.stop.active else "feed end or shutdown"})
