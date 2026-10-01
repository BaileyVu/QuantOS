"""Transactional SQLite checkpoint for the autonomous Futures paper account."""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from dataclasses import asdict, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import hashlib
import json
from pathlib import Path
import sqlite3

from quantos.application.futures_trader import (
    AutonomousFuturesPaperTrader, FuturesTraderConfig,
)
from quantos.domain.alpha.futures import Direction, MarketRegime, RegimeState
from quantos.domain.execution.futures_paper import (
    ExecutionIntent, ExitReason, LiquidityRole, PaperPosition, PaperTrade,
    PositionManagementState,
)
from quantos.domain.market_data import Candle
from quantos.domain.market_data.futures import FuturesSymbolRules
from quantos.domain.market_data.timeframes import SignalCandle
from quantos.domain.risk.futures import ConsecutiveLossBreakerState
from quantos.domain.runtime_contracts import identity


SCHEMA_VERSION = 2


class FuturesPaperStateError(RuntimeError):
    """Raised when durable paper state cannot be trusted."""


_DATACLASSES = {
    item.__name__: item
    for item in (
        Candle, SignalCandle, RegimeState, PaperPosition, PaperTrade,
        ConsecutiveLossBreakerState,
    )
}
_ENUMS = {
    item.__name__: item
    for item in (
        Direction, MarketRegime, PositionManagementState, ExitReason,
        ExecutionIntent, LiquidityRole,
    )
}


def _encode(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise FuturesPaperStateError("non-finite Decimal in paper state")
        return {"$decimal": str(value)}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, Enum):
        return {"$enum": value.__class__.__name__, "value": value.value}
    if is_dataclass(value):
        return {
            "$dataclass": value.__class__.__name__,
            "fields": {
                field.name: _encode(getattr(value, field.name))
                for field in fields(value)
            },
        }
    if isinstance(value, tuple):
        return {"$tuple": [_encode(item) for item in value]}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise FuturesPaperStateError("paper state keys must be strings")
        return {key: _encode(item) for key, item in value.items()}
    if value is None or type(value) in (str, int, bool):
        return value
    raise FuturesPaperStateError(
        f"unsupported paper state value: {type(value).__name__}"
    )


def _decode(value):
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if not isinstance(value, dict):
        if value is None or type(value) in (str, int, bool):
            return value
        raise FuturesPaperStateError("invalid paper state primitive")
    if "$decimal" in value:
        if set(value) != {"$decimal"}:
            raise FuturesPaperStateError("ambiguous Decimal state")
        result = Decimal(value["$decimal"])
        if not result.is_finite():
            raise FuturesPaperStateError("non-finite Decimal state")
        return result
    if "$datetime" in value:
        if set(value) != {"$datetime"}:
            raise FuturesPaperStateError("ambiguous datetime state")
        return datetime.fromisoformat(value["$datetime"])
    if "$date" in value:
        if set(value) != {"$date"}:
            raise FuturesPaperStateError("ambiguous date state")
        return date.fromisoformat(value["$date"])
    if "$tuple" in value:
        if set(value) != {"$tuple"} or not isinstance(value["$tuple"], list):
            raise FuturesPaperStateError("invalid tuple state")
        return tuple(_decode(item) for item in value["$tuple"])
    if "$enum" in value:
        if set(value) != {"$enum", "value"}:
            raise FuturesPaperStateError("ambiguous enum state")
        try:
            return _ENUMS[value["$enum"]](value["value"])
        except (KeyError, ValueError) as error:
            raise FuturesPaperStateError("unknown enum state") from error
    if "$dataclass" in value:
        if set(value) != {"$dataclass", "fields"}:
            raise FuturesPaperStateError("ambiguous dataclass state")
        try:
            contract = _DATACLASSES[value["$dataclass"]]
        except KeyError as error:
            raise FuturesPaperStateError("unknown dataclass state") from error
        raw_fields = value["fields"]
        if not isinstance(raw_fields, dict):
            raise FuturesPaperStateError("invalid dataclass fields")
        expected = {field.name for field in fields(contract)}
        if set(raw_fields) != expected:
            raise FuturesPaperStateError("incompatible dataclass state")
        try:
            return contract(**{
                key: _decode(item) for key, item in raw_fields.items()
            })
        except (TypeError, ValueError) as error:
            raise FuturesPaperStateError("invalid dataclass state") from error
    return {key: _decode(item) for key, item in value.items()}


def _canonical(value) -> str:
    return json.dumps(
        _encode(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    )


def _checksum(payload: str, runtime_json: str) -> str:
    return hashlib.sha256(
        (payload + "\n" + runtime_json).encode("ascii")
    ).hexdigest()


def _config_id(config: FuturesTraderConfig) -> str:
    return identity(asdict(config))


def _rules_id(rules: FuturesSymbolRules) -> str:
    return identity(asdict(rules))


_TRADER_FIELDS = (
    "histories", "latest_regimes", "equity_curve", "rejections",
    "breaker_state", "day", "day_start_equity",
    "daily_loss_breaker_trigger_count", "daily_loss_active_day",
    "last_candle", "no_valid_signal_count", "risk_rejection_count",
    "exchange_rule_rejection_count", "position_already_open_rejections",
    "candidate_count", "selected_trade_count", "rejected_candidate_count",
    "economic_rejections", "economic_rejections_per_timeframe",
    "strategy_funnel", "timeframe_funnel", "regime_funnel",
    "regime_evaluation_periods", "regime_evaluation_periods_by_timeframe",
    "funnel_rejection_matrix",
    "projected_opportunities",
)


def _snapshot(trader: AutonomousFuturesPaperTrader) -> dict:
    execution = trader.execution
    aggregators = {
        timeframe: {
            "start": item._start,
            "parts": list(item._parts),
            "incomplete_bucket_count": item.incomplete_bucket_count,
        }
        for timeframe, item in trader.aggregator._aggregators.items()
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "trader": {
            key: getattr(trader, key) for key in _TRADER_FIELDS
        },
        "last_decision": trader.decisions[-1] if trader.decisions else None,
        "aggregators": aggregators,
        "execution": {
            "balance": execution.balance,
            "position": execution.position,
            "trades": execution.trades,
            "audit_events": execution.audit_events,
            "total_fees": execution.total_fees,
            "total_slippage_cost": execution.total_slippage_cost,
            "total_funding": execution.total_funding,
            "maker_fills": execution.maker_fills,
            "taker_fills": execution.taker_fills,
            "maker_attempts_not_filled": execution.maker_attempts_not_filled,
            "maker_fees": execution.maker_fees,
            "taker_fees": execution.taker_fees,
            "order_ids": sorted(execution._order_ids),
            "funding_ids": sorted(execution._funding_ids),
        },
    }


def _restore_nested_rejections(raw: dict) -> defaultdict:
    result = defaultdict(
        lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    )
    for strategy, timeframes in raw.items():
        for timeframe, regimes in timeframes.items():
            for regime, reasons in regimes.items():
                result[strategy][timeframe][regime].update(reasons)
    return result


def _restore(
    snapshot: dict,
    config: FuturesTraderConfig,
    rules: FuturesSymbolRules,
) -> AutonomousFuturesPaperTrader:
    expected = {
        "schema_version", "trader", "last_decision", "aggregators", "execution"
    }
    if not isinstance(snapshot, dict) or set(snapshot) != expected:
        raise FuturesPaperStateError("incompatible Futures paper checkpoint")
    if snapshot["schema_version"] != SCHEMA_VERSION:
        raise FuturesPaperStateError("unsupported Futures paper state schema")
    trader = AutonomousFuturesPaperTrader(config, rules)
    state = snapshot["trader"]
    if not isinstance(state, dict) or set(state) != set(_TRADER_FIELDS):
        raise FuturesPaperStateError("incomplete Futures trader checkpoint")
    for key in _TRADER_FIELDS:
        setattr(trader, key, state[key])
    trader.rejections = defaultdict(int, trader.rejections)
    trader.economic_rejections = defaultdict(int, trader.economic_rejections)
    trader.economic_rejections_per_timeframe = {
        key: defaultdict(int, value)
        for key, value in trader.economic_rejections_per_timeframe.items()
    }
    for collection in (
        trader.strategy_funnel, trader.timeframe_funnel, trader.regime_funnel
    ):
        for bucket in collection.values():
            bucket["rejection_reasons"] = defaultdict(
                int, bucket["rejection_reasons"]
            )
    trader.funnel_rejection_matrix = _restore_nested_rejections(
        trader.funnel_rejection_matrix
    )
    trader.decisions = (
        [snapshot["last_decision"]]
        if snapshot["last_decision"] is not None else []
    )
    if set(snapshot["aggregators"]) != set(trader.aggregator._aggregators):
        raise FuturesPaperStateError("aggregation checkpoint does not match config")
    for timeframe, item in snapshot["aggregators"].items():
        target = trader.aggregator._aggregators[timeframe]
        if set(item) != {"start", "parts", "incomplete_bucket_count"}:
            raise FuturesPaperStateError("invalid aggregation checkpoint")
        target._start = item["start"]
        target._parts = list(item["parts"])
        target.incomplete_bucket_count = item["incomplete_bucket_count"]
    execution = snapshot["execution"]
    expected_execution = {
        "balance", "position", "trades", "audit_events", "total_fees",
        "total_slippage_cost", "total_funding", "maker_fills", "taker_fills",
        "maker_attempts_not_filled", "maker_fees", "taker_fees", "order_ids",
        "funding_ids",
    }
    if not isinstance(execution, dict) or set(execution) != expected_execution:
        raise FuturesPaperStateError("incomplete execution checkpoint")
    target = trader.execution
    target.balance = execution["balance"]
    target.position = execution["position"]
    target.trades = list(execution["trades"])
    target.audit_events = list(execution["audit_events"])
    target.total_fees = execution["total_fees"]
    target.total_slippage_cost = execution["total_slippage_cost"]
    target.total_funding = execution["total_funding"]
    target.maker_fills = execution["maker_fills"]
    target.taker_fills = execution["taker_fills"]
    target.maker_attempts_not_filled = execution["maker_attempts_not_filled"]
    target.maker_fees = execution["maker_fees"]
    target.taker_fees = execution["taker_fees"]
    target._order_ids = set(execution["order_ids"])
    target._funding_ids = set(execution["funding_ids"])
    if not isinstance(target.balance, Decimal) or not target.balance.is_finite():
        raise FuturesPaperStateError("invalid restored paper balance")
    if target.position is not None:
        if not isinstance(target.position, PaperPosition):
            raise FuturesPaperStateError("invalid restored paper position")
        if target.position.client_order_id not in target._order_ids:
            raise FuturesPaperStateError("open position idempotency state is missing")
    if trader.last_candle is not None and not isinstance(trader.last_candle, Candle):
        raise FuturesPaperStateError("invalid last completed candle")
    return trader


class FuturesPaperStateStore:
    """One checksummed account snapshot, committed atomically per completed candle."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.revision: int | None = None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _create(connection: sqlite3.Connection) -> None:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS futures_paper_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL,
                config_id TEXT NOT NULL,
                rules_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                payload TEXT NOT NULL,
                checksum TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                runtime_json TEXT NOT NULL
            )"""
        )

    def reset(
        self,
        config: FuturesTraderConfig,
        rules: FuturesSymbolRules,
        now: datetime,
    ) -> AutonomousFuturesPaperTrader:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        trader = AutonomousFuturesPaperTrader(config, rules)
        payload = _canonical(_snapshot(trader))
        runtime = _canonical({
            "last_healthy_runtime_timestamp": None,
            "last_runtime_timestamp": now,
            "retry_count": 0,
        })
        try:
            with closing(self._connect()) as connection, connection:
                self._create(connection)
                connection.execute("DELETE FROM futures_paper_state")
                connection.execute(
                    """INSERT INTO futures_paper_state VALUES
                       (1, ?, ?, ?, 0, ?, ?, ?, ?)""",
                    (
                        SCHEMA_VERSION, _config_id(config), _rules_id(rules),
                        payload, _checksum(payload, runtime), now.isoformat(), runtime,
                    ),
                )
        except sqlite3.Error as error:
            raise FuturesPaperStateError(
                f"cannot reset Futures paper state: {error}"
            ) from error
        self.revision = 0
        return trader

    def load(
        self,
        config: FuturesTraderConfig,
        rules: FuturesSymbolRules,
    ) -> tuple[AutonomousFuturesPaperTrader, dict]:
        if not self.path.is_file():
            raise FuturesPaperStateError(
                "paper state does not exist; run the explicit paper-reset command"
            )
        try:
            with closing(self._connect()) as connection, connection:
                if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise FuturesPaperStateError("SQLite paper state failed integrity check")
                self._create(connection)
                rows = connection.execute(
                    "SELECT schema_version, config_id, rules_id, revision, payload, "
                    "checksum, runtime_json FROM futures_paper_state"
                ).fetchall()
        except (sqlite3.Error, OSError) as error:
            raise FuturesPaperStateError(
                f"cannot read Futures paper state: {error}"
            ) from error
        if len(rows) != 1:
            raise FuturesPaperStateError("Futures paper state is missing or ambiguous")
        schema, config_id, rules_id, revision, payload, checksum, runtime_json = rows[0]
        if schema != SCHEMA_VERSION:
            raise FuturesPaperStateError("unsupported Futures paper database schema")
        if config_id != _config_id(config) or rules_id != _rules_id(rules):
            raise FuturesPaperStateError("paper state config or exchange rules changed")
        if checksum != _checksum(payload, runtime_json):
            raise FuturesPaperStateError("corrupt Futures paper state checksum")
        try:
            snapshot = _decode(json.loads(payload))
            runtime = _decode(json.loads(runtime_json))
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise FuturesPaperStateError("corrupt Futures paper state payload") from error
        if not isinstance(runtime, dict) or set(runtime) != {
            "last_healthy_runtime_timestamp", "last_runtime_timestamp", "retry_count"
        }:
            raise FuturesPaperStateError("incompatible Futures runtime metadata")
        self.revision = revision
        return _restore(snapshot, config, rules), runtime

    def save(
        self,
        trader: AutonomousFuturesPaperTrader,
        runtime: dict,
        now: datetime,
    ) -> None:
        if self.revision is None:
            raise FuturesPaperStateError("paper state must be loaded before saving")
        payload = _canonical(_snapshot(trader))
        runtime_json = _canonical(runtime)
        try:
            with closing(self._connect()) as connection, connection:
                cursor = connection.execute(
                    """UPDATE futures_paper_state
                       SET revision = revision + 1, payload = ?, checksum = ?,
                           updated_at = ?, runtime_json = ?
                       WHERE singleton = 1 AND revision = ?""",
                    (
                        payload, _checksum(payload, runtime_json), now.isoformat(),
                        runtime_json,
                        self.revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise FuturesPaperStateError(
                        "paper state changed concurrently; stopping fail closed"
                    )
        except sqlite3.Error as error:
            raise FuturesPaperStateError(
                f"cannot commit Futures paper state: {error}"
            ) from error
        self.revision += 1
