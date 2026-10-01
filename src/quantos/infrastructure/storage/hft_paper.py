"""Durable fail-closed SQLite state for the separate HFT paper account."""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from quantos.domain.alpha.hft import HftSide
from quantos.domain.evaluation.hft import HftEvaluation, Markout
from quantos.domain.execution.hft_paper import (
    HftFill,
    HftInventory,
    HftPaperAccount,
    HftWorkingOrder,
)
from quantos.domain.risk.hft import HftFeeSchedule


HFT_STATE_SCHEMA_VERSION = 1


class HftPaperStateError(RuntimeError):
    pass


def _encode(value):
    if isinstance(value, Decimal):
        return {"__decimal__": str(value)}
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, HftSide):
        return value.value
    raise TypeError(type(value).__name__)


def _decode(value):
    if "__decimal__" in value:
        return Decimal(value["__decimal__"])
    if "__datetime__" in value:
        return datetime.fromisoformat(value["__datetime__"])
    return value


def _canonical(payload: dict) -> str:
    return json.dumps(
        payload, default=_encode, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    )


class HftPaperStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def reset(
        self,
        identity: str,
        symbol: str,
        starting_equity: Decimal,
        fees: HftFeeSchedule,
        now: datetime,
    ) -> tuple[HftPaperAccount, HftEvaluation, dict]:
        if self.path.exists():
            raise HftPaperStateError(
                "HFT paper state already exists; choose a new path explicitly"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        account = HftPaperAccount(starting_equity, fees)
        evaluation = HftEvaluation(starting_equity)
        runtime = {
            "symbol": symbol,
            "created_at": now,
            "last_event_at": None,
            "last_update_id": None,
            "book_valid": False,
            "reconstruction_identity": None,
            "alpha_state": {},
            "breakers": {},
        }
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "CREATE TABLE state (id INTEGER PRIMARY KEY CHECK (id = 1), "
                "schema_version INTEGER NOT NULL, identity TEXT NOT NULL, "
                "payload TEXT NOT NULL, digest TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
        self.save(account, evaluation, runtime, identity, now)
        return account, evaluation, runtime

    def save(
        self,
        account: HftPaperAccount,
        evaluation: HftEvaluation,
        runtime: dict,
        identity: str,
        now: datetime,
    ) -> None:
        if not self.path.exists():
            raise HftPaperStateError(
                "HFT paper state does not exist; run hft-paper-reset"
            )
        payload = {
            "account": self._account_payload(account),
            "evaluation": {
                "latencies": evaluation.latencies,
                "markouts": [asdict(item) for item in evaluation.markouts],
                "equity_curve": evaluation.equity_curve,
                "equity_points": evaluation.equity_points,
                "kill_switch_events": evaluation.kill_switch_events,
                "directional_alpha_pnl": evaluation.directional_alpha_pnl,
                "gross_spread_capture": evaluation.gross_spread_capture,
                "alpha_bps_observations": evaluation.alpha_bps_observations,
                "normal_hurdle_bps_observations": (
                    evaluation.normal_hurdle_bps_observations
                ),
                "quote_funnel": evaluation.quote_funnel,
                "latest_quote_evaluation": evaluation.latest_quote_evaluation,
            },
            "runtime": runtime,
        }
        text = _canonical(payload)
        digest = sha256(text.encode("ascii")).hexdigest()
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    "INSERT INTO state(id,schema_version,identity,payload,digest,updated_at) "
                    "VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "schema_version=excluded.schema_version,identity=excluded.identity,"
                    "payload=excluded.payload,digest=excluded.digest,"
                    "updated_at=excluded.updated_at",
                    (
                        HFT_STATE_SCHEMA_VERSION, identity, text, digest,
                        now.isoformat(),
                    ),
                )
        except sqlite3.Error as error:
            raise HftPaperStateError(f"cannot save HFT paper state: {error}") from error

    def load(
        self, identity: str, fees: HftFeeSchedule
    ) -> tuple[HftPaperAccount, HftEvaluation, dict]:
        if not self.path.exists():
            raise HftPaperStateError(
                "HFT paper state does not exist; run hft-paper-reset"
            )
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT schema_version,identity,payload,digest FROM state WHERE id=1"
                ).fetchone()
        except sqlite3.Error as error:
            raise HftPaperStateError(f"cannot load HFT paper state: {error}") from error
        if row is None or row[0] != HFT_STATE_SCHEMA_VERSION:
            raise HftPaperStateError("missing or incompatible HFT state")
        if row[1] != identity:
            raise HftPaperStateError("HFT state identity differs from configuration")
        if sha256(row[2].encode("ascii")).hexdigest() != row[3]:
            raise HftPaperStateError("HFT state digest mismatch")
        try:
            payload = json.loads(row[2], object_hook=_decode)
            account = self._restore_account(payload["account"], fees)
            evaluation = HftEvaluation(account.starting_equity)
            saved = payload["evaluation"]
            evaluation.latencies = saved["latencies"]
            evaluation.markouts = [Markout(**item) for item in saved["markouts"]]
            evaluation.equity_curve = saved["equity_curve"]
            evaluation.equity_points = [
                tuple(item) for item in saved.get("equity_points", [])
            ]
            evaluation.kill_switch_events = saved["kill_switch_events"]
            evaluation.directional_alpha_pnl = saved["directional_alpha_pnl"]
            evaluation.gross_spread_capture = saved["gross_spread_capture"]
            evaluation.alpha_bps_observations = saved.get(
                "alpha_bps_observations", []
            )
            evaluation.normal_hurdle_bps_observations = saved.get(
                "normal_hurdle_bps_observations", []
            )
            evaluation.quote_funnel.update(saved.get("quote_funnel", {}))
            evaluation.latest_quote_evaluation = saved.get(
                "latest_quote_evaluation"
            )
            runtime = payload["runtime"]
        except (KeyError, TypeError, ValueError, ArithmeticError) as error:
            raise HftPaperStateError(f"invalid HFT state payload: {error}") from error
        # A persisted quote is historical evidence only. Restart always cancels it
        # and requires a fresh REST/WebSocket reconstruction before new quoting.
        if account.working_order is not None:
            account.cancel("restart_fail_closed")
        runtime["book_valid"] = False
        runtime["last_update_id"] = None
        runtime["reconstruction_identity"] = None
        return account, evaluation, runtime

    @staticmethod
    def _account_payload(account: HftPaperAccount) -> dict:
        working_order = (
            asdict(account.working_order)
            if account.working_order else None
        )
        if working_order is not None:
            working_order["submitted_monotonic"] = None
            working_order["acknowledged_monotonic"] = None
        inventory = asdict(account.inventory) if account.inventory else None
        if inventory is not None:
            inventory["opened_monotonic"] = None
        fills = []
        for fill in account.fills:
            payload = asdict(fill)
            payload["monotonic_timestamp"] = None
            fills.append(payload)
        return {
            "starting_equity": account.starting_equity,
            "balance": account.balance,
            "fees": asdict(account.fees),
            # Monotonic values are process/boot-local and intentionally are not
            # durable authorities across restart.
            "working_order": working_order,
            "inventory": inventory,
            "fills": fills,
            "maker_fees": account.maker_fees,
            "taker_fees": account.taker_fees,
            "realized_pnl": account.realized_pnl,
            "funding": account.funding,
            "quotes_submitted": account.quotes_submitted,
            "quotes_cancelled": account.quotes_cancelled,
            "cancelled_before_fill": account.cancelled_before_fill,
            "taker_exits": account.taker_exits,
            "order_sequence": account._order_sequence,
            "fill_sequence": account._fill_sequence,
        }

    @staticmethod
    def _restore_account(payload: dict, fees: HftFeeSchedule) -> HftPaperAccount:
        if payload["fees"] != asdict(fees):
            raise HftPaperStateError("configured HFT fees differ from durable state")
        account = HftPaperAccount(payload["starting_equity"], fees)
        for name in (
            "balance", "maker_fees", "taker_fees", "realized_pnl", "funding",
            "quotes_submitted", "quotes_cancelled", "cancelled_before_fill",
            "taker_exits",
        ):
            setattr(account, name, payload[name])
        account._order_sequence = payload["order_sequence"]
        account._fill_sequence = payload["fill_sequence"]
        inventory = payload["inventory"]
        if inventory is not None:
            account.inventory = HftInventory(**inventory)
        order = payload["working_order"]
        if order is not None:
            order["side"] = HftSide(order["side"])
            account.working_order = HftWorkingOrder(**order)
        account.fills = []
        for item in payload["fills"]:
            item["side"] = HftSide(item["side"])
            account.fills.append(HftFill(**item))
        return account
