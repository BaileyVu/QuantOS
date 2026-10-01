"""Exchange-authoritative HFT execution contracts and safety authority."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import time
from typing import Any, Protocol

from quantos.domain.alpha.hft import HftSide
from quantos.domain.risk.hft import HftRiskDecision


class HftExecutionError(RuntimeError):
    pass


class HftExecutionEnvironment(str, Enum):
    PAPER = "PAPER"
    TESTNET = "TESTNET"
    MAINNET = "MAINNET"


class HftExchangeOrderStatus(str, Enum):
    PENDING_SUBMIT = "PENDING_SUBMIT"
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    EXPIRED_IN_MATCH = "EXPIRED_IN_MATCH"

    @property
    def terminal(self) -> bool:
        return self in {
            self.FILLED, self.CANCELED, self.EXPIRED, self.EXPIRED_IN_MATCH,
        }


@dataclass(frozen=True, slots=True)
class HftCommission:
    maker_rate: Decimal
    taker_rate: Decimal
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class HftExchangePosition:
    symbol: str
    quantity: Decimal
    entry_price: Decimal
    leverage: int
    isolated: bool


@dataclass(frozen=True, slots=True)
class HftExchangeOrder:
    symbol: str
    client_order_id: str
    exchange_order_id: int | None
    side: HftSide
    price: Decimal
    original_quantity: Decimal
    executed_quantity: Decimal
    status: HftExchangeOrderStatus
    reduce_only: bool
    update_time: datetime


@dataclass(frozen=True, slots=True)
class HftActualFill:
    client_order_id: str
    trade_id: int
    side: HftSide
    price: Decimal
    quantity: Decimal
    commission: Decimal
    commission_asset: str
    maker: bool
    realized_pnl: Decimal
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class HftReadiness:
    authenticated_preflight: bool = False
    account_reconciled: bool = False
    clock_healthy: bool = False
    market_data_ready: bool = False
    user_data_ready: bool = False
    risk_ready: bool = False
    state_ready: bool = False

    @property
    def ready(self) -> bool:
        return all((
            self.authenticated_preflight,
            self.account_reconciled,
            self.clock_healthy,
            self.market_data_ready,
            self.user_data_ready,
            self.risk_ready,
            self.state_ready,
        ))


class HftExecutionGateway(Protocol):
    def new_limit_gtx(self, **parameters: Any) -> dict[str, Any]: ...
    def query_order(self, symbol: str, client_order_id: str) -> dict[str, Any] | None: ...
    def cancel_order(self, symbol: str, client_order_id: str) -> dict[str, Any]: ...
    def open_orders(self, symbol: str) -> list[dict[str, Any]]: ...
    def emergency_market_exit(self, **parameters: Any) -> dict[str, Any]: ...
    def position_risk(self, symbol: str | None = None) -> list[dict[str, Any]]: ...
    @property
    def order_creation_suppressed(self) -> bool: ...


class HftExecutionStore(Protocol):
    def snapshot(self) -> dict[str, Any]: ...
    def persist_intent(self, intent: dict[str, Any]) -> None: ...
    def persist_order(self, order: dict[str, Any]) -> None: ...
    def persist_fill(self, fill: dict[str, Any]) -> None: ...
    def set_disabled(self, disabled: bool, reason: str) -> None: ...


class HftAuthenticatedExecution:
    """The only authority allowed to submit authenticated HFT orders."""

    APPROVAL_TOKEN = "I_ACCEPT_REAL_MONEY_RISK"

    def __init__(
        self,
        *,
        environment: HftExecutionEnvironment,
        gateway: HftExecutionGateway,
        store: HftExecutionStore,
        session_id: str,
        enable_mainnet: bool = False,
        approval_token: str | None = None,
        minimum_size_only: bool = False,
        symbol: str = "BTCUSDC",
        maximum_quotes_per_second: int = 5,
    ) -> None:
        if environment is HftExecutionEnvironment.PAPER:
            raise HftExecutionError(
                "PAPER cannot construct authenticated execution authority"
            )
        if not session_id or len(session_id) > 64:
            raise HftExecutionError("invalid authenticated session identity")
        if symbol not in {"BTCUSDC", "BTCUSDT"}:
            raise HftExecutionError("unsupported authenticated HFT symbol")
        if maximum_quotes_per_second < 1:
            raise HftExecutionError("quote-rate ceiling must be positive")
        self.environment = environment
        self.gateway = gateway
        self.store = store
        self.session_id = session_id
        self.enable_mainnet = enable_mainnet
        self.approval_token = approval_token
        self.minimum_size_only = minimum_size_only
        self.symbol = symbol
        self.maximum_quotes_per_second = maximum_quotes_per_second
        self.readiness = HftReadiness()
        self._sequence = int(store.snapshot().get("order_sequence", 0))
        self._quote_times: list[float] = []

    @property
    def mainnet_armed(self) -> bool:
        return (
            self.environment is HftExecutionEnvironment.MAINNET
            and self.enable_mainnet
            and self.approval_token == self.APPROVAL_TOKEN
        )

    @property
    def submission_enabled(self) -> bool:
        state = self.store.snapshot()
        environment_gate = (
            self.environment is HftExecutionEnvironment.TESTNET
            or self.mainnet_armed
        )
        return (
            environment_gate
            and self.readiness.ready
            and not bool(state.get("disabled", True))
            and not self.gateway.order_creation_suppressed
        )

    def set_readiness(self, readiness: HftReadiness) -> None:
        self.readiness = readiness

    def client_order_id(self, side: HftSide, sequence: int) -> str:
        environment = "t" if self.environment is HftExecutionEnvironment.TESTNET else "m"
        side_code = "b" if side is HftSide.BUY else "s"
        session = sha256(self.session_id.encode("utf-8")).hexdigest()[:10]
        value = f"qoh1-{environment}-{session}-{side_code}-{sequence:08x}"
        if len(value) > 36:
            raise HftExecutionError("client order ID exceeds exchange limit")
        return value

    def submit_post_only(
        self,
        decision: HftRiskDecision,
        side: HftSide,
        price: Decimal,
        *,
        minimum_quantity: Decimal | None = None,
    ) -> HftExchangeOrder:
        if not decision.approved:
            raise HftExecutionError("Risk approval is required")
        if not self.submission_enabled:
            raise HftExecutionError("authenticated order submission is not armed and READY")
        now = time.monotonic()
        self._quote_times = [value for value in self._quote_times if now - value < 1]
        if len(self._quote_times) >= self.maximum_quotes_per_second:
            raise HftExecutionError("hard quote-rate ceiling reached")
        state = self.store.snapshot()
        active = [
            order for order in state.get("orders", {}).values()
            if order.get("status") in {"PENDING_SUBMIT", "NEW", "PARTIALLY_FILLED"}
        ]
        if active:
            raise HftExecutionError("one working QuantOS order maximum")
        quantity = decision.quantity
        if self.minimum_size_only:
            if minimum_quantity is None:
                raise HftExecutionError("minimum-size mode requires exchange minimum quantity")
            quantity = minimum_quantity
        if quantity <= 0 or price <= 0:
            raise HftExecutionError("order price and quantity must be positive")
        self._sequence += 1
        client_order_id = self.client_order_id(side, self._sequence)
        intent = {
            "client_order_id": client_order_id,
            "environment": self.environment.value,
            "session_id": self.session_id,
            "sequence": self._sequence,
            "symbol": self.symbol,
            "side": side.value,
            "price": str(price),
            "quantity": str(quantity),
            "status": HftExchangeOrderStatus.PENDING_SUBMIT.value,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.store.persist_intent(intent)
        try:
            response = self.gateway.new_limit_gtx(
                symbol=self.symbol,
                side=side.value,
                quantity=quantity,
                price=price,
                client_order_id=client_order_id,
            )
        except BaseException as error:
            if not bool(getattr(error, "ambiguous", False)):
                raise
            response = self.gateway.query_order(self.symbol, client_order_id)
            if response is None:
                response = self.gateway.new_limit_gtx(
                    symbol=self.symbol,
                    side=side.value,
                    quantity=quantity,
                    price=price,
                    client_order_id=client_order_id,
                )
            elif not response:
                raise HftExecutionError("order state remains uncertain") from error
        order = self._order_from_payload(response, side, price, quantity)
        self.store.persist_order(self._order_payload(order))
        self._quote_times.append(now)
        return order

    def submit_testnet_protocol_quote(
        self,
        side: HftSide,
        price: Decimal,
        minimum_quantity: Decimal,
    ) -> HftExchangeOrder:
        """Exercise the transport on testnet without representing Alpha approval."""
        if self.environment is not HftExecutionEnvironment.TESTNET:
            raise HftExecutionError(
                "protocol-only quote is forbidden outside TESTNET"
            )
        if not self.submission_enabled:
            raise HftExecutionError("TESTNET protocol submission is not READY")
        if price <= 0 or minimum_quantity <= 0:
            raise HftExecutionError("protocol quote values must be positive")
        if self.gateway.open_orders(self.symbol):
            raise HftExecutionError("protocol smoke requires no open orders")
        self._sequence += 1
        client_order_id = self.client_order_id(side, self._sequence)
        intent = {
            "client_order_id": client_order_id,
            "environment": self.environment.value,
            "session_id": self.session_id,
            "sequence": self._sequence,
            "symbol": self.symbol,
            "side": side.value,
            "price": str(price),
            "quantity": str(minimum_quantity),
            "status": HftExchangeOrderStatus.PENDING_SUBMIT.value,
            "transport_only": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.store.persist_intent(intent)
        response = self.gateway.new_limit_gtx(
            symbol=self.symbol,
            side=side.value,
            quantity=minimum_quantity,
            price=price,
            client_order_id=client_order_id,
        )
        order = self._order_from_payload(
            response, side, price, minimum_quantity
        )
        self.store.persist_order(self._order_payload(order))
        return order

    def submit_post_only_exit(
        self,
        side: HftSide,
        price: Decimal,
        quantity: Decimal,
    ) -> HftExchangeOrder:
        """Submit one reduce-only maker exit for exchange-confirmed inventory."""
        if not self.submission_enabled:
            raise HftExecutionError("authenticated exit submission is not READY")
        if price <= 0 or quantity <= 0:
            raise HftExecutionError("exit price and quantity must be positive")
        if self.gateway.open_orders(self.symbol):
            raise HftExecutionError("one working order maximum")
        self._sequence += 1
        client_id = self.client_order_id(side, self._sequence)
        intent = {
            "client_order_id": client_id,
            "environment": self.environment.value,
            "session_id": self.session_id,
            "sequence": self._sequence,
            "symbol": self.symbol,
            "side": side.value,
            "price": str(price),
            "quantity": str(quantity),
            "reduce_only": True,
            "status": HftExchangeOrderStatus.PENDING_SUBMIT.value,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.store.persist_intent(intent)
        try:
            response = self.gateway.new_limit_gtx(
                symbol=self.symbol,
                side=side.value,
                quantity=quantity,
                price=price,
                client_order_id=client_id,
                reduce_only=True,
            )
        except BaseException as error:
            if not bool(getattr(error, "ambiguous", False)):
                raise
            response = self.gateway.query_order(self.symbol, client_id)
            if response is None:
                response = self.gateway.new_limit_gtx(
                    symbol=self.symbol,
                    side=side.value,
                    quantity=quantity,
                    price=price,
                    client_order_id=client_id,
                    reduce_only=True,
                )
        order = self._order_from_payload(response, side, price, quantity)
        self.store.persist_order(self._order_payload(order))
        return order

    def cancel_owned_orders(self) -> tuple[str, ...]:
        cancelled = []
        prefix = "qoh1-"
        for payload in self.gateway.open_orders(self.symbol):
            client_id = str(payload.get("clientOrderId", ""))
            if not client_id.startswith(prefix):
                continue
            try:
                response = self.gateway.cancel_order(self.symbol, client_id)
            except BaseException as error:
                recovered = self.gateway.query_order(self.symbol, client_id)
                if recovered is None:
                    cancelled.append(client_id)
                    continue
                if str(recovered.get("status")) not in {
                    "FILLED", "CANCELED", "EXPIRED", "EXPIRED_IN_MATCH",
                }:
                    raise error
                response = recovered
            side = HftSide(str(response.get("side", payload.get("side"))))
            order = self._order_from_payload(
                response,
                side,
                Decimal(str(response.get("price", payload.get("price", "0")))),
                Decimal(str(response.get("origQty", payload.get("origQty", "0")))),
            )
            self.store.persist_order(self._order_payload(order))
            cancelled.append(client_id)
        return tuple(cancelled)

    def emergency_flatten(self, reason: str) -> dict[str, Any]:
        self.store.set_disabled(True, reason)
        self.cancel_owned_orders()
        positions = self.gateway.position_risk(self.symbol)
        position = next((row for row in positions if row.get("symbol") == self.symbol), None)
        if position is None:
            raise HftExecutionError("cannot prove exchange BTCUSDC position")
        quantity = Decimal(str(position.get("positionAmt", "0")))
        if quantity == 0:
            return {"status": "FLAT", "reason": reason}
        side = HftSide.SELL if quantity > 0 else HftSide.BUY
        self._sequence += 1
        client_id = self.client_order_id(side, self._sequence)
        self.store.persist_intent({
            "client_order_id": client_id,
            "environment": self.environment.value,
            "session_id": self.session_id,
            "sequence": self._sequence,
            "symbol": self.symbol,
            "side": side.value,
            "quantity": str(abs(quantity)),
            "reduce_only": True,
            "status": HftExchangeOrderStatus.PENDING_SUBMIT.value,
            "reason": reason,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        try:
            response = self.gateway.emergency_market_exit(
                symbol=self.symbol,
                side=side.value,
                quantity=abs(quantity),
                client_order_id=client_id,
            )
        except BaseException as error:
            if not bool(getattr(error, "ambiguous", False)):
                raise
            recovered = self.gateway.query_order(self.symbol, client_id)
            if recovered is None:
                raise HftExecutionError(
                    "CRITICAL emergency exit submission is uncertain; no retry permitted"
                ) from error
            response = recovered
        order = self._order_from_payload(
            response, side, Decimal("0"), abs(quantity)
        )
        self.store.persist_order(self._order_payload(order))
        remaining = None
        for _ in range(5):
            after = self.gateway.position_risk(self.symbol)
            remaining = next(
                (row for row in after if row.get("symbol") == self.symbol), None
            )
            if (
                remaining is not None
                and Decimal(str(remaining.get("positionAmt", "0"))) == 0
            ):
                break
            time.sleep(.2)
        if remaining is None or Decimal(str(remaining.get("positionAmt", "0"))) != 0:
            raise HftExecutionError("CRITICAL emergency flatten did not reconcile flat")
        return {"status": "FLAT", "reason": reason, "order": response}

    def kill(self, *, flatten: bool) -> dict[str, Any]:
        self.store.set_disabled(True, "operator_kill")
        cancelled = self.cancel_owned_orders()
        flattened = self.emergency_flatten("operator_kill") if flatten else None
        return {"disabled": True, "cancelled": cancelled, "flatten": flattened}

    @staticmethod
    def _order_from_payload(
        payload: dict[str, Any], side: HftSide,
        price: Decimal, quantity: Decimal,
    ) -> HftExchangeOrder:
        if not isinstance(payload, dict):
            raise HftExecutionError("malformed exchange order response")
        status = HftExchangeOrderStatus(str(payload.get("status", "NEW")))
        milliseconds = int(payload.get("updateTime", payload.get("time", 0)))
        updated = (
            datetime.fromtimestamp(milliseconds / 1000, timezone.utc)
            if milliseconds else datetime.now(timezone.utc)
        )
        return HftExchangeOrder(
            symbol=str(payload.get("symbol", "BTCUSDC")),
            client_order_id=str(payload["clientOrderId"]),
            exchange_order_id=(
                int(payload["orderId"]) if payload.get("orderId") is not None else None
            ),
            side=HftSide(str(payload.get("side", side.value))),
            price=Decimal(str(payload.get("price", price))),
            original_quantity=Decimal(str(payload.get("origQty", quantity))),
            executed_quantity=Decimal(str(payload.get("executedQty", "0"))),
            status=status,
            reduce_only=bool(payload.get("reduceOnly", False)),
            update_time=updated,
        )

    @staticmethod
    def _order_payload(order: HftExchangeOrder) -> dict[str, Any]:
        return {
            "symbol": order.symbol,
            "client_order_id": order.client_order_id,
            "exchange_order_id": order.exchange_order_id,
            "side": order.side.value,
            "price": str(order.price),
            "original_quantity": str(order.original_quantity),
            "executed_quantity": str(order.executed_quantity),
            "status": order.status.value,
            "reduce_only": order.reduce_only,
            "update_time": order.update_time.isoformat(),
        }
