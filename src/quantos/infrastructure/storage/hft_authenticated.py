"""Durable environment-bound state for authenticated HFT execution."""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable

from quantos.domain.execution.hft_authenticated import HftExecutionEnvironment


class HftAuthenticatedStateError(RuntimeError):
    pass


SCHEMA_VERSION = 1


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    )


class HftAuthenticatedStateStore:
    def __init__(
        self,
        path: Path,
        environment: HftExecutionEnvironment,
        identity: str,
    ) -> None:
        if environment is HftExecutionEnvironment.PAPER:
            raise HftAuthenticatedStateError(
                "authenticated state cannot use PAPER environment"
            )
        self.path = path
        self.environment = environment
        self.identity = identity

    def initialize(self) -> dict[str, Any]:
        if self.path.exists():
            raise HftAuthenticatedStateError(
                "authenticated state already exists; reuse or choose a new path"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        state = {
            "environment": self.environment.value,
            "identity": self.identity,
            "created_at": now,
            "updated_at": now,
            "disabled": True,
            "disabled_reason": "new_authenticated_state_requires_rearm",
            "order_sequence": 0,
            "commission": None,
            "preflight": None,
            "reconciliation": None,
            "orders": {},
            "intents": {},
            "fills": [],
            "markouts": [],
        }
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    "CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), "
                    "schema_version INTEGER NOT NULL, environment TEXT NOT NULL, "
                    "identity TEXT NOT NULL, payload TEXT NOT NULL, "
                    "digest TEXT NOT NULL, updated_at TEXT NOT NULL)"
                )
                self._write(connection, state)
        except sqlite3.Error as error:
            raise HftAuthenticatedStateError(
                f"cannot initialize authenticated state: {error}"
            ) from error
        return deepcopy(state)

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self._load())

    def persist_intent(self, intent: dict[str, Any]) -> None:
        client_id = str(intent["client_order_id"])
        def mutate(state: dict[str, Any]) -> None:
            if client_id in state["intents"]:
                if state["intents"][client_id] != intent:
                    raise HftAuthenticatedStateError(
                        "client order intent identity collision"
                    )
                return
            state["intents"][client_id] = deepcopy(intent)
            state["order_sequence"] = max(
                int(state["order_sequence"]), int(intent["sequence"])
            )
        self._mutate(mutate)

    def persist_order(self, order: dict[str, Any]) -> None:
        client_id = str(order["client_order_id"])
        def mutate(state: dict[str, Any]) -> None:
            if client_id not in state["intents"]:
                raise HftAuthenticatedStateError(
                    "exchange order has no durable pre-submit intent"
                )
            state["orders"][client_id] = deepcopy(order)
        self._mutate(mutate)

    def persist_fill(self, fill: dict[str, Any]) -> None:
        key = (str(fill["client_order_id"]), int(fill["trade_id"]))
        def mutate(state: dict[str, Any]) -> None:
            existing = {
                (str(item["client_order_id"]), int(item["trade_id"]))
                for item in state["fills"]
            }
            if key not in existing:
                state["fills"].append(deepcopy(fill))
        self._mutate(mutate)

    def persist_markout(self, markout: dict[str, Any]) -> None:
        key = (str(markout["fill_id"]), int(markout["horizon_seconds"]))
        def mutate(state: dict[str, Any]) -> None:
            existing = {
                (str(item["fill_id"]), int(item["horizon_seconds"]))
                for item in state["markouts"]
            }
            if key not in existing:
                state["markouts"].append(deepcopy(markout))
        self._mutate(mutate)

    def persist_preflight(self, payload: dict[str, Any]) -> None:
        def mutate(state: dict[str, Any]) -> None:
            state["preflight"] = deepcopy(payload)
            state["commission"] = deepcopy(payload.get("commission"))
        self._mutate(mutate)

    def persist_reconciliation(self, payload: dict[str, Any]) -> None:
        self._mutate(lambda state: state.__setitem__(
            "reconciliation", deepcopy(payload)
        ))

    def set_disabled(self, disabled: bool, reason: str) -> None:
        if not reason:
            raise HftAuthenticatedStateError("disabled-state reason is required")
        def mutate(state: dict[str, Any]) -> None:
            state["disabled"] = bool(disabled)
            state["disabled_reason"] = reason
        self._mutate(mutate)

    def rearm(self, operator_confirmation: str) -> None:
        if operator_confirmation != "REARM_AUTHENTICATED_HFT":
            raise HftAuthenticatedStateError(
                "explicit authenticated HFT re-arm confirmation is required"
            )
        self.set_disabled(False, "explicit_operator_rearm")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            raise HftAuthenticatedStateError(
                "authenticated state does not exist; initialize explicitly"
            )
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT schema_version,environment,identity,payload,digest "
                    "FROM state WHERE id=1"
                ).fetchone()
        except sqlite3.Error as error:
            raise HftAuthenticatedStateError(
                f"cannot load authenticated state: {error}"
            ) from error
        if row is None or row[0] != SCHEMA_VERSION:
            raise HftAuthenticatedStateError(
                "missing or incompatible authenticated state"
            )
        if row[1] != self.environment.value:
            raise HftAuthenticatedStateError(
                "authenticated state belongs to a different environment"
            )
        if row[2] != self.identity:
            raise HftAuthenticatedStateError(
                "authenticated state identity differs from configuration"
            )
        if sha256(row[3].encode("ascii")).hexdigest() != row[4]:
            raise HftAuthenticatedStateError("authenticated state digest mismatch")
        try:
            state = json.loads(row[3])
        except (TypeError, json.JSONDecodeError) as error:
            raise HftAuthenticatedStateError(
                "authenticated state payload is invalid"
            ) from error
        if (
            state.get("environment") != self.environment.value
            or state.get("identity") != self.identity
        ):
            raise HftAuthenticatedStateError(
                "authenticated state environment/identity mismatch"
            )
        return state

    def _mutate(self, callback: Callable[[dict[str, Any]], None]) -> None:
        state = self._load()
        callback(state)
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            with closing(self._connect()) as connection, connection:
                self._write(connection, state)
        except sqlite3.Error as error:
            raise HftAuthenticatedStateError(
                f"cannot persist authenticated state: {error}"
            ) from error

    def _write(
        self, connection: sqlite3.Connection, state: dict[str, Any]
    ) -> None:
        text = _canonical(state)
        digest = sha256(text.encode("ascii")).hexdigest()
        connection.execute(
            "INSERT INTO state(id,schema_version,environment,identity,payload,digest,updated_at) "
            "VALUES(1,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "schema_version=excluded.schema_version,"
            "environment=excluded.environment,identity=excluded.identity,"
            "payload=excluded.payload,digest=excluded.digest,"
            "updated_at=excluded.updated_at",
            (
                SCHEMA_VERSION, self.environment.value, self.identity,
                text, digest, state["updated_at"],
            ),
        )
