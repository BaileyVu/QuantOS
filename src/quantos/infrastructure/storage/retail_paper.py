"""Separate, checksummed retail paper state with explicit new-account creation."""
from __future__ import annotations

from dataclasses import asdict
from contextlib import contextmanager
import fcntl
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

from quantos.domain.alpha.hft import HftSide
from quantos.domain.execution.retail_paper import (
    RetailFill, RetailOrder, RetailPaperAccount, RetailPosition, ProtectedOrder, ProtectedPosition,
)
from quantos.domain.risk.hft import HftFeeSchedule
from quantos.domain.risk.retail import RetailRiskPolicy


class RetailPaperStateError(RuntimeError):
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


class RetailPaperStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def reset(
        self, identity: str, fees: HftFeeSchedule, policy: RetailRiskPolicy,
    ) -> RetailPaperAccount:
        if self.path.exists():
            raise RetailPaperStateError("retail paper state already exists")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        account = RetailPaperAccount(fees, policy)
        self._write(identity, account, {}, create=True)
        return account

    def save(self, identity: str, account: RetailPaperAccount, runtime: dict) -> None:
        if not self.path.exists():
            raise RetailPaperStateError("run retail-paper-reset for a new account")
        self._write(identity, account, runtime, create=False)

    def _write(
        self, identity: str, account: RetailPaperAccount, runtime: dict,
        *, create: bool,
    ) -> None:
        validate_account(account)
        payload = account_payload(account, runtime)
        serialized = json.dumps(payload, default=_encode, sort_keys=True, separators=(",", ":"))
        wrapper = json.dumps({
            "schema_version": 2,
            "identity": identity,
            "digest": sha256(serialized.encode("ascii")).hexdigest(),
            "payload": serialized,
        }, sort_keys=True, separators=(",", ":"))
        if create:
            with self.path.open("x", encoding="ascii") as stream:
                stream.write(wrapper)
                stream.flush()
                os.fsync(stream.fileno())
            return
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="ascii", dir=self.path.parent,
                prefix=self.path.name + ".", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(wrapper)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def load(
        self, identity: str, fees: HftFeeSchedule, policy: RetailRiskPolicy,
    ) -> tuple[RetailPaperAccount, dict]:
        if not self.path.exists():
            raise RetailPaperStateError("run retail-paper-reset for a new account")
        try:
            wrapper = json.loads(self.path.read_text(encoding="ascii"))
            if wrapper["schema_version"] != 2 or wrapper["identity"] != identity:
                raise RetailPaperStateError("retail state identity differs from configuration")
            serialized = wrapper["payload"]
            if sha256(serialized.encode("ascii")).hexdigest() != wrapper["digest"]:
                raise RetailPaperStateError("retail state digest mismatch")
            payload = json.loads(serialized, object_hook=_decode)
            if payload["starting_equity"] != Decimal("200"):
                raise RetailPaperStateError("retail state capital differs from 200")
            if payload["fees"] != asdict(fees) or payload["policy"] != asdict(policy):
                raise RetailPaperStateError("retail fees or risk policy changed")
            account = restore_account(payload, fees, policy)
            if account.position is not None:
                account.position.opened_ns = 0
            runtime = payload["runtime"]
        except RetailPaperStateError:
            raise
        except (OSError, KeyError, TypeError, ValueError, ArithmeticError) as error:
            raise RetailPaperStateError(f"invalid retail paper state: {error}") from error
        if account.pending is not None:
            account.cancel_pending("restart_fail_closed", datetime.now(timezone.utc))
            self.save(identity, account, runtime)
        return account, runtime


def account_payload(account, runtime=None):
    return {
        "risk_off": account.risk_off, "day": account.day,
        "daily_start_equity": account.daily_start_equity,
        "total_funding": account.total_funding,
        "funding_ledger": account.funding_ledger,
        "last_funding_time": account.last_funding_time,
        "funding_due": account.funding_due, "funding_rate": account.funding_rate,
        "funding_mark": account.funding_mark,
        "starting_equity": account.starting_equity,
        "balance": account.balance,
        "peak_equity": account.peak_equity,
        "fees": asdict(account.fees),
        "policy": asdict(account.policy),
        "position": asdict(account.position) if account.position else None,
        "pending": asdict(account.pending) if account.pending else None,
        "fills": [asdict(fill) for fill in account.fills],
        "lifecycle": account.lifecycle,
        "orders_submitted": account.orders_submitted,
        "orders_cancelled": account.orders_cancelled,
        "realized_gross_pnl": account.realized_gross_pnl,
        "realized_net_pnl": account.realized_net_pnl,
        "total_fees": account.total_fees,
        "order_sequence": account._order_sequence,
        "fill_sequence": account._fill_sequence,
        "runtime": runtime or {},
    }


def restore_account(payload, fees, policy):
    if payload["starting_equity"] != Decimal("200"):
        raise RetailPaperStateError("retail state capital differs from 200")
    account = RetailPaperAccount(fees, policy)
    for name in (
        "balance", "peak_equity", "orders_submitted", "orders_cancelled",
        "realized_gross_pnl", "realized_net_pnl", "total_fees",
        "risk_off", "day", "daily_start_equity", "total_funding",
        "funding_ledger", "funding_due", "funding_rate", "funding_mark", "last_funding_time",
    ):
        setattr(account, name, payload[name])
    account._order_sequence = payload["order_sequence"]
    account._fill_sequence = payload["fill_sequence"]
    account.lifecycle = payload["lifecycle"]
    if payload["position"] is not None:
        payload["position"]["side"] = HftSide(payload["position"]["side"])
        account.position = (ProtectedPosition if "stop_price" in payload["position"] else RetailPosition)(**payload["position"])
    if payload["pending"] is not None:
        payload["pending"]["side"] = HftSide(payload["pending"]["side"])
        account.pending = (ProtectedOrder if "stop_price" in payload["pending"] else RetailOrder)(**payload["pending"])
    for row in payload["fills"]:
        row["side"] = HftSide(row["side"])
        account.fills.append(RetailFill(**row))
    validate_account(account)
    return account


def validate_account(account):
    from quantos.domain.risk.retail import FullEquityRiskPolicy
    if isinstance(account.policy, FullEquityRiskPolicy) and account.position is not None:
        p = account.position
        if not isinstance(p, ProtectedPosition) or any(not x.is_finite() or x <= 0 for x in (p.stop_price, p.target_price, p.entry_price, p.quantity)):
            raise RetailPaperStateError("missing or invalid protective state")
        direction = 1 if p.side is HftSide.BUY else -1
        if (p.entry_price - p.stop_price) * direction <= 0 or (p.target_price - p.entry_price) * direction <= 0:
            raise RetailPaperStateError("inconsistent protective state")
    values = (account.balance, account.peak_equity, account.total_fees,
              account.realized_gross_pnl, account.realized_net_pnl, account.total_funding)
    if any(not value.is_finite() for value in values):
        raise RetailPaperStateError("nonfinite account state")
    if account.starting_equity != Decimal("200") or account.peak_equity < 200:
        raise RetailPaperStateError("invalid capital or peak equity")
    fees = sum((fill.fee for fill in account.fills), Decimal("0"))
    gross = sum((fill.gross_pnl for fill in account.fills), Decimal("0"))
    net = sum((fill.net_pnl for fill in account.fills), Decimal("0"))
    funding = sum((row["cost"] for row in account.funding_ledger), Decimal("0"))
    if fees != account.total_fees or gross != account.realized_gross_pnl or net != account.realized_net_pnl or funding != account.total_funding:
        raise RetailPaperStateError("account ledger totals do not reconcile")
    if account.balance != 200 + gross - fees - funding:
        raise RetailPaperStateError("account balance does not reconcile")
    quantity = sum((fill.quantity if fill.kind == "ENTRY" else -fill.quantity for fill in account.fills), Decimal("0"))
    if quantity != (account.position.quantity if account.position else Decimal("0")) or quantity < 0:
        raise RetailPaperStateError("account inventory does not reconcile")
    ids = [fill.fill_id for fill in account.fills]
    if len(set(ids)) != len(ids) or account._fill_sequence != len(ids):
        raise RetailPaperStateError("ambiguous fill identity")
    if account._order_sequence != account.orders_submitted:
        raise RetailPaperStateError("ambiguous order identity")
    if account.pending and not 0 < account.pending.remaining <= account.pending.quantity:
        raise RetailPaperStateError("invalid pending quantity")


@contextmanager
def exclusive_account(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RetailPaperStateError("paper account already in use") from error
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
