"""Authenticated HFT preflight, reconciliation, and exchange-event handling."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import asyncio
import time
from typing import Any, Mapping

from quantos.application.hft_trader import _clock_calibration
from quantos.domain.alpha.hft import HftIntentAction, HftSide, VampOrderFlowAlpha
from quantos.domain.evaluation.hft import HftEvaluation
from quantos.domain.execution.hft_paper import HftFill
from quantos.domain.features.hft import HftFeatureEngine
from quantos.domain.market_data.hft import (
    AggregateTrade, BookTicker, DepthDelta, HftBookError, L2OrderBook,
    MarkPriceEvent,
)
from quantos.domain.market_data.hft_clock import HftClockHealth, HftClockMonitor
from quantos.domain.execution.hft_authenticated import HftExecutionEnvironment
from quantos.domain.market_data.futures import (
    FuturesRuleError, FuturesSymbolRules, parse_usdm_exchange_info,
)
from quantos.domain.risk.hft import (
    HftFeeSchedule, HftRiskEngine, HftRiskState, cost_hurdle,
)


class HftAuthenticatedPreflightError(RuntimeError):
    def __init__(
        self, message: str, *, diagnostic: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.diagnostic = dict(diagnostic or {})


def _diagnostic_call(
    client: Any, method_name: str
) -> tuple[dict[str, Any], str]:
    method = getattr(client, method_name, None)
    if not callable(method):
        return {}, "UNAVAILABLE"
    try:
        payload = method()
    except Exception as error:
        return {}, f"UNAVAILABLE_{type(error).__name__}"
    if not isinstance(payload, Mapping):
        return {}, "MALFORMED"
    return dict(payload), "AVAILABLE"


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _balance_fields(
    balances: list[Mapping[str, Any]], asset: str
) -> tuple[str | None, str | None]:
    row = next((item for item in balances if item.get("asset") == asset), None)
    if row is None:
        return None, None
    wallet = row.get("balance", row.get("walletBalance"))
    available = row.get("availableBalance")
    return (
        None if wallet is None else str(wallet),
        None if available is None else str(available),
    )


def _permission_failure_type(diagnostic: Mapping[str, Any]) -> str:
    if diagnostic.get("futures_account_enabled") is False:
        return "FUTURES_NOT_ENABLED"
    status = diagnostic.get("account_status")
    if (
        diagnostic.get("api_trading_locked") is True
        or (isinstance(status, str) and status.casefold() != "normal")
    ):
        return "ACCOUNT_RESTRICTED"
    if (
        diagnostic.get("api_key_enable_reading") is False
        or diagnostic.get("api_key_enable_futures") is False
    ):
        return "API_KEY_PERMISSION"
    return "ACCOUNT_PERMISSION"


def _order_test_failure_type(error: Exception) -> str:
    message = str(error).casefold()
    if any(token in message for token in ("timestamp", "signature", "recvwindow")):
        return "AUTHENTICATION_OR_TRANSPORT"
    if any(token in message for token in ("permission", "api-key", "api key")):
        return "API_KEY_PERMISSION"
    if any(token in message for token in (
        "filter", "symbol", "precision", "price", "quantity",
    )):
        return "SYMBOL_FILTER"
    return "ACCOUNT_PERMISSION"


def _preflight_failure(
    diagnostic: Mapping[str, Any],
    *,
    environment: HftExecutionEnvironment,
    failure_code: str,
    failure_type: str,
    message: str,
) -> HftAuthenticatedPreflightError:
    payload = {
        "event": "hft_authenticated_preflight",
        "passed": False,
        "environment": environment.value,
        "failure_code": failure_code,
        "failure_type": failure_type,
        "failure_message": message,
        **diagnostic,
    }
    return HftAuthenticatedPreflightError(message, diagnostic=payload)


def _decimal(value: Any, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as error:
        raise HftAuthenticatedPreflightError(f"invalid {name}") from error
    if not result.is_finite():
        raise HftAuthenticatedPreflightError(f"invalid {name}")
    return result


def _symbol_metadata(exchange_info: Mapping[str, Any], symbol: str) -> Mapping[str, Any]:
    rows = exchange_info.get("symbols")
    if not isinstance(rows, list):
        raise HftAuthenticatedPreflightError("exchangeInfo symbols are missing")
    item = next(
        (row for row in rows if isinstance(row, Mapping) and row.get("symbol") == symbol),
        None,
    )
    if item is None:
        raise HftAuthenticatedPreflightError(f"{symbol} is unavailable")
    return item


def minimum_gtx_order(
    rules: FuturesSymbolRules, ticker: Mapping[str, Any]
) -> tuple[Decimal, Decimal]:
    bid = _decimal(ticker.get("bidPrice"), "best bid")
    price = rules.round_price(bid)
    quantity = rules.round_quantity_up(max(
        rules.minimum_quantity,
        rules.minimum_notional / price,
    ))
    rules.validate_market_order(quantity, price)
    return price, quantity


def authenticated_preflight(
    client: Any,
    store: Any,
    *,
    environment: HftExecutionEnvironment,
    normalize_account: bool,
    perform_test_order: bool,
    allow_testnet_btcusdt: bool = False,
) -> tuple[dict[str, Any], FuturesSymbolRules, HftFeeSchedule]:
    if environment is HftExecutionEnvironment.PAPER:
        raise HftAuthenticatedPreflightError(
            "authenticated preflight requires TESTNET or MAINNET"
        )
    clock = client.calibrate_clock()
    exchange_info = client.exchange_info()
    symbol = "BTCUSDC"
    try:
        metadata = _symbol_metadata(exchange_info, symbol)
        rules = parse_usdm_exchange_info(exchange_info, symbol)
    except (FuturesRuleError, HftAuthenticatedPreflightError):
        if environment is not HftExecutionEnvironment.TESTNET or not allow_testnet_btcusdt:
            raise
        symbol = "BTCUSDT"
        metadata = _symbol_metadata(exchange_info, symbol)
        rules = parse_usdm_exchange_info(exchange_info, symbol)
    if (
        rules.status != "TRADING"
        or metadata.get("contractType") != "PERPETUAL"
        or metadata.get("quoteAsset") not in {"USDC", "USDT"}
    ):
        raise HftAuthenticatedPreflightError(
            "selected symbol is not an eligible USD-M perpetual"
        )
    if environment is HftExecutionEnvironment.MAINNET and symbol != "BTCUSDC":
        raise HftAuthenticatedPreflightError("MAINNET HFT requires BTCUSDC")

    account = client.account()
    account_config = client.account_config()
    balances = client.balances()
    positions = client.position_risk()
    open_orders = client.open_orders()
    position_mode = client.position_mode()
    symbol_configs = client.symbol_config(symbol)
    order_rate_limits = client.order_rate_limits()
    commission_raw = client.commission_rate(symbol)
    account_can_trade = _optional_bool(account.get("canTrade"))
    account_can_trade_source = (
        "fapi_v3_account.canTrade"
        if account_can_trade is not None else None
    )
    account_info: dict[str, Any] = {}
    account_status: dict[str, Any] = {}
    api_permissions: dict[str, Any] = {}
    api_trading_status: dict[str, Any] = {}
    diagnostic_query_status = {
        "account_info": "NOT_APPLICABLE",
        "account_status": "NOT_APPLICABLE",
        "api_key_permissions": "NOT_APPLICABLE",
        "api_trading_status": "NOT_APPLICABLE",
    }
    if environment is HftExecutionEnvironment.MAINNET:
        account_info, diagnostic_query_status["account_info"] = (
            _diagnostic_call(client, "account_info")
        )
        account_status, diagnostic_query_status["account_status"] = (
            _diagnostic_call(client, "account_status")
        )
        api_permissions, diagnostic_query_status["api_key_permissions"] = (
            _diagnostic_call(client, "api_key_permissions")
        )
        api_trading_status, diagnostic_query_status["api_trading_status"] = (
            _diagnostic_call(client, "api_trading_status")
        )
    trading_status_data = api_trading_status.get("data")
    if not isinstance(trading_status_data, Mapping):
        trading_status_data = {}
    usdt_wallet, usdt_available = _balance_fields(balances, "USDT")
    usdc_wallet, usdc_available = _balance_fields(balances, "USDC")
    dual_side_value = position_mode.get("dualSidePosition")
    multi_assets_value = account_config.get(
        "multiAssetsMargin", account.get("multiAssetsMargin")
    )
    diagnostic = {
        "account_can_trade": account_can_trade,
        "account_can_trade_source": account_can_trade_source,
        "account_can_trade_required": account_can_trade is not None,
        "futures_account_enabled": _optional_bool(
            account_info.get("isFutureEnabled")
        ),
        "api_key_enable_reading": _optional_bool(
            api_permissions.get("enableReading")
        ),
        "api_key_enable_futures": _optional_bool(
            api_permissions.get("enableFutures")
        ),
        "api_key_enable_spot_and_margin_trading": _optional_bool(
            api_permissions.get("enableSpotAndMarginTrading")
        ),
        "api_trading_permission": _optional_bool(
            api_permissions.get("enableFutures")
        ),
        "account_status": account_status.get("data"),
        "api_trading_locked": _optional_bool(
            trading_status_data.get("isLocked")
        ),
        "usdt_futures_wallet_balance": usdt_wallet,
        "usdt_futures_available_balance": usdt_available,
        "usdc_futures_wallet_balance": usdc_wallet,
        "usdc_futures_available_balance": usdc_available,
        "position_mode": (
            "HEDGE" if dual_side_value is True
            else "ONE_WAY" if dual_side_value is False
            else "UNKNOWN"
        ),
        "account_mode": (
            "MULTI_ASSET" if multi_assets_value is True
            else "SINGLE_ASSET" if multi_assets_value is False
            else "UNKNOWN"
        ),
        "btcusdc_status": metadata.get("status"),
        "diagnostic_query_status": diagnostic_query_status,
        "account_config_query_status": "AVAILABLE",
        "symbol_config_query_status": "AVAILABLE",
        "order_test_attempted": False,
        "order_test_passed": False,
        "order_test_error_code": None,
        "order_test_error_message": None,
        "effective_trade_permission": None,
    }
    diagnostic["effective_trade_permission_evidence"] = [
        (
            "fapi_v3_account.canTrade="
            f"{account_can_trade if account_can_trade is not None else 'UNKNOWN'}"
        ),
        (
            "sapi_account_info.isFutureEnabled="
            f"{diagnostic['futures_account_enabled']}"
        ),
        (
            "sapi_api_restrictions.enableFutures="
            f"{diagnostic['api_key_enable_futures']}"
        ),
        (
            "sapi_api_trading_status.isLocked="
            f"{diagnostic['api_trading_locked']}"
        ),
        f"sapi_account_status={diagnostic['account_status']}",
    ]
    if account_can_trade is False:
        raise _preflight_failure(
            diagnostic,
            environment=environment,
            failure_code="ACCOUNT_PERMISSION",
            failure_type="ACCOUNT_PERMISSION",
            message="ACCOUNT_PERMISSION: Futures account canTrade=false",
        )
    if environment is HftExecutionEnvironment.MAINNET:
        if diagnostic["futures_account_enabled"] is not True:
            raise _preflight_failure(
                diagnostic,
                environment=environment,
                failure_code="FUTURES_NOT_ENABLED",
                failure_type="FUTURES_NOT_ENABLED",
                message="FUTURES_NOT_ENABLED: account is not Futures-enabled",
            )
        if (
            diagnostic["api_key_enable_reading"] is not True
            or diagnostic["api_key_enable_futures"] is not True
        ):
            raise _preflight_failure(
                diagnostic,
                environment=environment,
                failure_code="API_KEY_PERMISSION",
                failure_type="API_KEY_PERMISSION",
                message="API_KEY_PERMISSION: Futures API permission is unavailable",
            )
        if diagnostic["api_trading_locked"] is not False:
            raise _preflight_failure(
                diagnostic,
                environment=environment,
                failure_code="ACCOUNT_RESTRICTED",
                failure_type="ACCOUNT_RESTRICTED",
                message="ACCOUNT_RESTRICTED: API trading status is locked or unknown",
            )
        if diagnostic["account_status"] != "Normal":
            raise _preflight_failure(
                diagnostic,
                environment=environment,
                failure_code="ACCOUNT_RESTRICTED",
                failure_type="ACCOUNT_RESTRICTED",
                message="ACCOUNT_RESTRICTED: account status is not Normal",
            )
    if (
        account_config.get("multiAssetsMargin") is True
        or account.get("multiAssetsMargin") is True
    ):
        raise HftAuthenticatedPreflightError(
            "multi-assets margin mode is incompatible with canonical HFT"
        )
    asset = "USDC" if symbol == "BTCUSDC" else "USDT"
    balance = next((row for row in balances if row.get("asset") == asset), None)
    if balance is None:
        raise _preflight_failure(
            diagnostic,
            environment=environment,
            failure_code="INSUFFICIENT_FUTURES_COLLATERAL",
            failure_type="BALANCE_ONLY",
            message=f"INSUFFICIENT_FUTURES_COLLATERAL: {asset} balance is unavailable",
        )
    wallet_balance = _decimal(balance.get("balance", "0"), f"{asset} balance")
    available_balance = _decimal(
        balance.get("availableBalance", "0"), f"{asset} available balance"
    )
    if wallet_balance <= 0 or available_balance <= 0:
        raise _preflight_failure(
            diagnostic,
            environment=environment,
            failure_code="INSUFFICIENT_FUTURES_COLLATERAL",
            failure_type="BALANCE_ONLY",
            message=(
                "INSUFFICIENT_FUTURES_COLLATERAL: "
                f"{asset} wallet or available balance is zero"
            ),
        )

    nonflat = [
        row for row in positions
        if _decimal(row.get("positionAmt", "0"), "position amount") != 0
    ]
    unrelated_positions = [row for row in nonflat if row.get("symbol") != symbol]
    unrelated_orders = [row for row in open_orders if row.get("symbol") != symbol]
    if unrelated_positions or unrelated_orders:
        raise HftAuthenticatedPreflightError(
            "unrelated positions or open orders prevent safe account normalization"
        )

    dual_side = position_mode.get("dualSidePosition") is True
    symbol_position = next(
        (row for row in positions if row.get("symbol") == symbol), None
    )
    if symbol_position is None:
        # Binance USDⓈ-M /fapi/v3/account intentionally omits symbols
        # that have neither a position nor an open order. Therefore an
        # absent BTCUSDC position row is normal when the account is flat.
        #
        # Fail closed if Binance simultaneously reports an open order for
        # the symbol, because then the missing position state is inconsistent.
        symbol_open_orders = [
            row for row in open_orders if row.get("symbol") == symbol
        ]
        if symbol_open_orders:
            raise HftAuthenticatedPreflightError(
                "symbol position state is missing while open orders exist"
            )
        symbol_position = {
            "symbol": symbol,
            "positionAmt": "0",
            "positionSide": "BOTH",
        }
    symbol_config = next(
        (row for row in symbol_configs if row.get("symbol") == symbol), None
    )
    if symbol_config is None:
        raise HftAuthenticatedPreflightError("symbol account configuration is missing")
    isolated = str(symbol_config.get("marginType", "")).upper() == "ISOLATED"
    leverage = int(symbol_config.get("leverage", 0))
    can_normalize = not nonflat and not open_orders
    changes: list[str] = []
    if dual_side:
        if not normalize_account or not can_normalize:
            raise HftAuthenticatedPreflightError("one-way position mode is required")
        client.set_one_way_mode()
        changes.append("one_way")
        dual_side = False
    if not isolated:
        if not normalize_account or not can_normalize:
            raise HftAuthenticatedPreflightError("isolated margin is required")
        client.set_isolated_margin(symbol)
        changes.append("isolated_margin")
        isolated = True
    if leverage != 5:
        if not normalize_account or not can_normalize:
            raise HftAuthenticatedPreflightError("exchange leverage must equal 5")
        client.set_leverage(symbol, 5)
        changes.append("leverage_5")
        leverage = 5

    maker = _decimal(commission_raw.get("makerCommissionRate"), "maker commission")
    taker = _decimal(commission_raw.get("takerCommissionRate"), "taker commission")
    commission_time = datetime.now(timezone.utc)
    fees = HftFeeSchedule(maker, taker)
    ticker = client.book_ticker(symbol)
    test_price, test_quantity = minimum_gtx_order(rules, ticker)
    test_order_passed = False
    if perform_test_order:
        diagnostic["order_test_attempted"] = True
        try:
            client.test_order(
                symbol=symbol,
                side="BUY",
                quantity=test_quantity,
                price=test_price,
                client_order_id="qoh1-preflight-test",
            )
        except Exception as error:
            failure_type = _order_test_failure_type(error)
            diagnostic["order_test_error_code"] = getattr(error, "code", None)
            diagnostic["order_test_error_message"] = str(error)
            diagnostic["effective_trade_permission"] = False
            diagnostic["effective_trade_permission_evidence"].append(
                f"fapi_v1_order_test=REJECTED:{failure_type}"
            )
            raise _preflight_failure(
                diagnostic,
                environment=environment,
                failure_code=failure_type,
                failure_type=failure_type,
                message=f"{failure_type}: non-executing order test rejected",
            ) from error
        test_order_passed = True
        diagnostic["order_test_passed"] = True
        diagnostic["effective_trade_permission"] = True
        diagnostic["effective_trade_permission_evidence"].append(
            "fapi_v1_order_test=PASS"
        )
    payload = {
        "event": "hft_authenticated_preflight",
        "passed": True,
        "environment": environment.value,
        "auth_health": "HEALTHY",
        "credential_health": client.credential_health(),
        "clock": clock,
        **diagnostic,
        "symbol": symbol,
        "strategy_validation_symbol": symbol == "BTCUSDC",
        "transport_only_fallback": symbol != "BTCUSDC",
        "contract_type": metadata.get("contractType"),
        "symbol_status": rules.status,
        "rules": {key: str(value) for key, value in asdict(rules).items()},
        "balance_asset": asset,
        "wallet_balance": str(wallet_balance),
        "available_margin": str(available_balance),
        "position_amount": str(_decimal(
            symbol_position.get("positionAmt", "0"), "position amount"
        )),
        "open_order_count": len(open_orders),
        "one_way_position_mode": not dual_side,
        "isolated_margin": isolated,
        "leverage": leverage,
        "multi_assets_margin": bool(
            account_config.get("multiAssetsMargin", False)
        ),
        "account_configuration_changes": changes,
        "commission": {
            "maker_rate": str(maker),
            "taker_rate": str(taker),
            "observed_at": commission_time.isoformat(),
        },
        "test_order": {
            "performed": perform_test_order,
            "passed": test_order_passed,
            "type": "LIMIT",
            "time_in_force": "GTX",
            "price": str(test_price),
            "quantity": str(test_quantity),
            "executable_order_created": False,
        },
        "rate_limits": client.rate_limits.payload(),
        "account_order_rate_limits": order_rate_limits,
    }
    store.persist_preflight(payload)
    return payload, rules, fees


def reconcile_authenticated_state(
    client: Any,
    store: Any,
    *,
    symbol: str,
) -> dict[str, Any]:
    state = store.snapshot()
    exchange_orders = client.open_orders(symbol)
    positions = client.position_risk(symbol)
    position = next((row for row in positions if row.get("symbol") == symbol), None)
    if position is None:
        raise HftAuthenticatedPreflightError("exchange position is unavailable")
    exchange_quantity = _decimal(position.get("positionAmt", "0"), "position amount")
    owned = {
        str(row.get("clientOrderId")): row
        for row in exchange_orders
        if str(row.get("clientOrderId", "")).startswith("qoh1-")
    }
    foreign = [
        row for row in exchange_orders
        if not str(row.get("clientOrderId", "")).startswith("qoh1-")
    ]
    if foreign:
        raise HftAuthenticatedPreflightError(
            "non-QuantOS open order exists on the selected symbol"
        )
    local_orders = state.get("orders", {})
    orphan_ids = [client_id for client_id in owned if client_id not in local_orders]
    for client_id in orphan_ids:
        client.cancel_order(symbol, client_id)
    if orphan_ids:
        exchange_orders = client.open_orders(symbol)
        if any(str(row.get("clientOrderId")) in orphan_ids for row in exchange_orders):
            raise HftAuthenticatedPreflightError(
                "orphan QuantOS order cancellation did not reconcile"
            )
        owned = {
            str(row.get("clientOrderId")): row
            for row in exchange_orders
            if str(row.get("clientOrderId", "")).startswith("qoh1-")
        }
    previous = state.get("reconciliation") or {}
    local_quantity = _decimal(previous.get("exchange_position", "0"), "local position")
    if exchange_quantity != 0 and local_quantity != exchange_quantity:
        raise HftAuthenticatedPreflightError(
            "unexpected nonzero exchange position or position disagreement"
        )
    for client_id, row in owned.items():
        local = local_orders.get(client_id)
        if local is None:
            raise HftAuthenticatedPreflightError("unreconciled owned order")
        restored = {
            "symbol": symbol,
            "client_order_id": client_id,
            "exchange_order_id": int(row["orderId"]),
            "side": str(row["side"]),
            "price": str(row["price"]),
            "original_quantity": str(row["origQty"]),
            "executed_quantity": str(row.get("executedQty", "0")),
            "status": str(row["status"]),
            "reduce_only": bool(row.get("reduceOnly", False)),
            "update_time": datetime.now(timezone.utc).isoformat(),
        }
        store.persist_order(restored)
    result = {
        "account_reconciled": True,
        "symbol": symbol,
        "exchange_position": str(exchange_quantity),
        "local_position": str(local_quantity),
        "exchange_open_orders": len(exchange_orders),
        "quantos_open_orders": len(owned),
        "orphan_orders_cancelled": orphan_ids,
        "reconciled_at": datetime.now(timezone.utc).isoformat(),
    }
    store.persist_reconciliation(result)
    return result


def apply_user_data_event(payload: Mapping[str, Any], store: Any) -> dict[str, Any]:
    event_type = payload.get("e")
    if event_type == "ORDER_TRADE_UPDATE":
        order = payload.get("o")
        if not isinstance(order, Mapping):
            raise HftAuthenticatedPreflightError("order update is malformed")
        client_id = str(order.get("c", ""))
        if not client_id.startswith("qoh1-"):
            return {"event": "foreign_order_update_ignored"}
        status = str(order.get("X"))
        if status not in {
            "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED",
            "EXPIRED", "EXPIRED_IN_MATCH",
        }:
            raise HftAuthenticatedPreflightError("unsupported exchange order status")
        update_time = datetime.fromtimestamp(
            int(payload.get("E", 0)) / 1000, timezone.utc
        ).isoformat()
        normalized = {
            "symbol": str(order["s"]),
            "client_order_id": client_id,
            "exchange_order_id": int(order["i"]),
            "side": str(order["S"]),
            "price": str(order.get("p", "0")),
            "original_quantity": str(order["q"]),
            "executed_quantity": str(order["z"]),
            "status": status,
            "reduce_only": bool(order.get("R", False)),
            "update_time": update_time,
        }
        store.persist_order(normalized)
        last_quantity = _decimal(order.get("l", "0"), "last fill quantity")
        if str(order.get("x")) == "TRADE" and last_quantity > 0:
            fill = {
                "client_order_id": client_id,
                "trade_id": int(order["t"]),
                "side": str(order["S"]),
                "price": str(order["L"]),
                "quantity": str(last_quantity),
                "commission": str(order.get("n", "0")),
                "commission_asset": str(order.get("N") or ""),
                "maker": bool(order.get("m", False)),
                "realized_pnl": str(order.get("rp", "0")),
                "timestamp": update_time,
            }
            store.persist_fill(fill)
        return {
            "event": "exchange_order_update",
            "order": normalized,
            "fill": fill if str(order.get("x")) == "TRADE" and last_quantity > 0 else None,
        }
    if event_type == "ACCOUNT_UPDATE":
        account = payload.get("a")
        if not isinstance(account, Mapping):
            raise HftAuthenticatedPreflightError("account update is malformed")
        positions = account.get("P", [])
        btc = next(
            (row for row in positions if isinstance(row, Mapping) and row.get("s") in {"BTCUSDC", "BTCUSDT"}),
            None,
        )
        result = {
            "event": "exchange_account_update",
            "position": str(btc.get("pa", "0")) if btc else None,
            "reason": account.get("m"),
        }
        if btc is not None:
            current = store.snapshot().get("reconciliation") or {}
            current.update({
                "account_reconciled": True,
                "exchange_position": result["position"],
                "local_position": result["position"],
                "unrealized_pnl": str(btc.get("up", "0")),
                "reconciled_at": datetime.now(timezone.utc).isoformat(),
            })
            store.persist_reconciliation(current)
        return result
    if event_type == "ACCOUNT_CONFIG_UPDATE":
        return {"event": "exchange_account_config_update"}
    raise HftAuthenticatedPreflightError("unsupported user-data event")


def authenticated_heartbeat(
    client: Any,
    store: Any,
    *,
    environment: HftExecutionEnvironment,
    user_stream: Any | None,
    mainnet_armed: bool,
) -> dict[str, Any]:
    state = store.snapshot()
    reconciliation = state.get("reconciliation") or {}
    commission = state.get("commission") or {}
    now = __import__("time").monotonic()
    def age(value: float | None) -> Decimal | None:
        return None if value is None else Decimal(str(max((now - value) * 1000, 0)))
    active = [
        order for order in state.get("orders", {}).values()
        if order.get("status") in {"PENDING_SUBMIT", "NEW", "PARTIALLY_FILLED"}
    ]
    return {
        "event": "hft_authenticated_heartbeat",
        "environment": environment.value,
        "auth_health": "HEALTHY" if (state.get("preflight") or {}).get("passed") else "NOT_READY",
        "user_data_health": (
            getattr(user_stream, "health", "NOT_READY")
            if user_stream is not None else "NOT_READY"
        ),
        "account_reconciled": bool(reconciliation.get("account_reconciled")),
        "exchange_position": reconciliation.get("exchange_position"),
        "local_position": reconciliation.get("local_position"),
        "exchange_open_orders": reconciliation.get("exchange_open_orders"),
        "QuantOS_open_orders": len(active),
        "maker_commission": commission.get("maker_rate"),
        "taker_commission": commission.get("taker_rate"),
        "request_weight_state": client.rate_limits.payload(),
        "order_rate_state": client.rate_limits.payload(),
        "last_ORDER_TRADE_UPDATE_age_ms": age(
            getattr(user_stream, "last_order_update_monotonic", None)
        ),
        "last_ACCOUNT_UPDATE_age_ms": age(
            getattr(user_stream, "last_account_update_monotonic", None)
        ),
        "emergency_exit_ready": bool(reconciliation.get("account_reconciled")),
        "mainnet_armed": mainnet_armed,
        "disabled": bool(state.get("disabled", True)),
    }


class HftAuthenticatedStrategyEngine:
    """Same market/Feature/Alpha/Risk logic with exchange-only execution state."""

    def __init__(
        self,
        config: Any,
        rules: FuturesSymbolRules,
        fees: HftFeeSchedule,
        execution: Any,
        store: Any,
    ) -> None:
        self.config = config
        self.rules = rules
        self.book = L2OrderBook(rules.symbol)
        self.features = HftFeatureEngine(config.feature)
        self.alpha = VampOrderFlowAlpha(rules.symbol, config.alpha)
        self.risk = HftRiskEngine(config.risk, fees, rules)
        self.execution = execution
        self.store = store
        self.last_features = None
        self.last_mark_price: Decimal | None = None
        equity = _decimal(
            (store.snapshot().get("preflight") or {}).get("wallet_balance", "0"),
            "wallet balance",
        )
        self.evaluation = HftEvaluation(equity)

    def bootstrap(self, snapshot: Any, buffered: tuple[DepthDelta, ...]) -> int:
        self.execution.cancel_owned_orders()
        return self.book.bootstrap(snapshot, buffered)

    def inventory_quantity(self) -> Decimal:
        reconciliation = self.store.snapshot().get("reconciliation") or {}
        return _decimal(
            reconciliation.get("exchange_position", "0"),
            "reconciled exchange position",
        )

    def on_event(
        self,
        event: Any,
        now: datetime,
        monotonic_now: Decimal,
        feed_latency_ms: Decimal,
        allow_quoting: bool,
    ) -> dict[str, Any]:
        if isinstance(event, AggregateTrade):
            self.features.on_trade(event)
            return {"event": "authenticated_aggregate_trade"}
        if isinstance(event, BookTicker):
            return {"event": "authenticated_book_ticker"}
        if isinstance(event, MarkPriceEvent):
            self.last_mark_price = event.mark_price
            return {"event": "authenticated_mark_price"}
        if not isinstance(event, DepthDelta):
            raise TypeError("unsupported authenticated market event")
        try:
            self.book.apply(event)
            monotonic_ns = int(monotonic_now * Decimal("1000000000"))
            self.book.require_fresh_monotonic(
                monotonic_ns, self.config.maximum_staleness
            )
            received_ns = self.book.last_received_monotonic_ns
            if received_ns is None:
                raise HftBookError("monotonic receive timestamp is missing")
            inventory = self.inventory_quantity()
            features = self.features.compute(
                self.book,
                now,
                inventory,
                feed_latency_ms=feed_latency_ms,
                event_age_ms=max(
                    (monotonic_now - Decimal(received_ns) / Decimal("1000000000"))
                    * Decimal("1000"),
                    Decimal("0"),
                ),
                now_monotonic_ns=monotonic_ns,
            )
            self.last_features = features
            for markout in self.evaluation.on_mid(
                now, features.mid_price, monotonic_now
            ):
                if hasattr(self.store, "persist_markout"):
                    payload = asdict(markout)
                    for key, value in tuple(payload.items()):
                        if isinstance(value, Decimal):
                            payload[key] = str(value)
                    self.store.persist_markout(payload)
            if not allow_quoting:
                self.execution.cancel_owned_orders()
                return {"event": "authenticated_clock_hold"}
            hurdle = cost_hurdle(
                self.risk.fees, features, self.config.risk
            )
            state = self.store.snapshot()
            reconciliation = state.get("reconciliation") or {}
            unrealized_pnl = _decimal(
                reconciliation.get("unrealized_pnl", "0"),
                "unrealized PnL",
            )
            preflight = state.get("preflight") or {}
            equity = _decimal(preflight.get("wallet_balance", "0"), "wallet balance")
            if (
                inventory != 0
                and max(-unrealized_pnl, Decimal("0"))
                >= equity * self.config.risk.maximum_account_loss_fraction
            ):
                return {
                    "event": "CRITICAL",
                    "flatten": self.execution.emergency_flatten(
                        "authenticated_account_loss_breaker"
                    ),
                }
            active = [
                item for item in state.get("orders", {}).values()
                if item.get("status") in {"PENDING_SUBMIT", "NEW", "PARTIALLY_FILLED"}
            ]
            working_side = (
                HftSide(active[0]["side"]) if active else None
            )
            intent = self.alpha.decide(
                features, hurdle.normal_hurdle_bps,
                inventory, working_side,
            )
            if intent.action is HftIntentAction.CANCEL:
                return {
                    "event": "authenticated_quote_cancelled",
                    "orders": self.execution.cancel_owned_orders(),
                    "reason": intent.rationale,
                }
            if intent.action is HftIntentAction.SAFETY_EXIT:
                return {
                    "event": "CRITICAL",
                    "flatten": self.execution.emergency_flatten(intent.rationale),
                }
            if intent.action is HftIntentAction.MAKER_EXIT:
                if active:
                    return {"event": "authenticated_exit_resting"}
                order = self.execution.submit_post_only_exit(
                    intent.side, intent.price, abs(inventory)
                )
                return {"event": "authenticated_maker_exit", "order": order.client_order_id}
            if intent.action is not HftIntentAction.QUOTE:
                return {"event": "authenticated_hold", "reason": intent.rationale}
            if active:
                current = active[0]
                if (
                    current.get("side") == intent.side.value
                    and Decimal(str(current.get("price"))) == intent.price
                ):
                    return {"event": "authenticated_quote_resting"}
                self.execution.cancel_owned_orders()
            net_realized = sum((
                _decimal(fill.get("realized_pnl", "0"), "realized PnL")
                - _decimal(fill.get("commission", "0"), "commission")
                for fill in state.get("fills", [])
            ), Decimal("0"))
            decision = self.risk.evaluate(
                intent,
                features,
                HftRiskState(
                    starting_equity=equity,
                    realized_pnl=net_realized,
                    mark_to_market_loss=max(-unrealized_pnl, Decimal("0")),
                ),
                equity + net_realized + unrealized_pnl,
                inventory,
            )
            if not decision.approved:
                return {"event": "authenticated_risk_rejected", "reason": decision.reason}
            minimum = self.rules.round_quantity_up(max(
                self.rules.minimum_quantity,
                self.rules.minimum_notional / intent.price,
            ))
            order = self.execution.submit_post_only(
                decision, intent.side, intent.price,
                minimum_quantity=minimum,
            )
            return {
                "event": "authenticated_quote_submitted",
                "client_order_id": order.client_order_id,
            }
        except BaseException:
            inventory = self.inventory_quantity()
            if inventory != 0:
                self.execution.emergency_flatten("authenticated_market_state_failure")
            else:
                self.execution.cancel_owned_orders()
            raise

    def on_exchange_fill(self, fill: Mapping[str, Any]) -> None:
        if self.last_features is None or not bool(fill.get("maker", False)):
            return
        timestamp = datetime.fromisoformat(str(fill["timestamp"]))
        order = self.store.snapshot().get("orders", {}).get(
            str(fill["client_order_id"]), {}
        )
        hft_fill = HftFill(
            fill_id=f"exchange-{fill['client_order_id']}-{fill['trade_id']}",
            order_id=str(fill["client_order_id"]),
            timestamp=timestamp,
            side=HftSide(str(fill["side"])),
            price=_decimal(fill["price"], "fill price"),
            quantity=_decimal(fill["quantity"], "fill quantity"),
            maker=True,
            role="EXIT" if bool(order.get("reduce_only", False)) else "ENTRY",
            fee=_decimal(fill["commission"], "fill commission"),
            realized_pnl=_decimal(fill["realized_pnl"], "realized PnL"),
            queue_wait_ms=Decimal("0"),
            queue_ahead_at_placement=Decimal("0"),
            monotonic_timestamp=Decimal(str(time.monotonic())),
        )
        self.evaluation.register_passive_fill(
            hft_fill,
            alpha_bps=self.last_features.alpha_bps,
            obi_z=self.last_features.standardized_obi,
            volatility_bps=self.last_features.realized_volatility_bps,
        )


async def run_authenticated_hft_session(
    *,
    config: Any,
    rules: FuturesSymbolRules,
    fees: HftFeeSchedule,
    authenticated_client: Any,
    public_client: Any,
    public_stream: Any,
    user_stream: Any,
    execution: Any,
    store: Any,
    duration_seconds: int,
    emit: Any,
) -> None:
    if duration_seconds < 0:
        raise ValueError("duration_seconds must be non-negative")
    strategy = HftAuthenticatedStrategyEngine(
        config, rules, fees, execution, store
    )
    clock = HftClockMonitor(
        config.clock_negative_latency_tolerance_ms,
        config.clock_excessive_negative_limit,
        config.clock_recalibration_interval,
    )
    calibration = await _clock_calibration(
        public_client,
        config.clock_calibration_samples,
        config.clock_low_rtt_samples,
        lambda: datetime.now(timezone.utc),
        time.monotonic,
    )
    clock.apply_calibration(calibration)
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=4096)
    application_queue_high_water = 0
    application_overflow_count = 0
    stale_trading_disarmed = False
    stale_trading_disarm_count = 0
    consumer_lag_ms: Decimal | None = None
    reconcile_enqueue_failed = False
    user_ready = asyncio.Event()
    user_ready_count = 0

    prior_lifecycle = user_stream.lifecycle
    def user_lifecycle(payload: dict[str, Any]) -> None:
        nonlocal user_ready_count, reconcile_enqueue_failed
        prior_lifecycle(payload)
        if payload.get("event") == "user_data_ready":
            user_ready_count += 1
            user_ready.set()
            if user_ready_count > 1:
                try:
                    queue.put_nowait(("reconcile", None))
                except asyncio.QueueFull:
                    reconcile_enqueue_failed = True
    user_stream.lifecycle = user_lifecycle

    async def market_producer() -> None:
        nonlocal application_queue_high_water, application_overflow_count
        async for event in public_stream.events(rules.symbol):
            try:
                queue.put_nowait(("market", event))
            except asyncio.QueueFull as error:
                application_overflow_count += 1
                raise HftAuthenticatedPreflightError(
                    "authenticated_application_queue_overflow:market"
                ) from error
            application_queue_high_water = max(
                application_queue_high_water, queue.qsize()
            )

    async def user_producer() -> None:
        nonlocal application_queue_high_water, application_overflow_count
        async for event in user_stream.events():
            try:
                queue.put_nowait(("user", event))
            except asyncio.QueueFull as error:
                application_overflow_count += 1
                raise HftAuthenticatedPreflightError(
                    "authenticated_application_queue_overflow:user"
                ) from error
            application_queue_high_water = max(
                application_queue_high_water, queue.qsize()
            )

    def task_failure(task: asyncio.Task, name: str) -> None:
        if not task.done():
            return
        if task.cancelled():
            raise asyncio.CancelledError
        error = task.exception()
        if error is not None:
            raise error
        raise HftAuthenticatedPreflightError(
            f"authenticated_{name}_stream_ended"
        )

    market_task = asyncio.create_task(market_producer())
    user_task = asyncio.create_task(user_producer())
    try:
        await asyncio.wait_for(public_stream.wait_public_ready(), timeout=10)
        await asyncio.wait_for(public_stream.wait_market_ready(), timeout=10)
        await asyncio.wait_for(user_ready.wait(), timeout=10)
        snapshot = await asyncio.wait_for(
            asyncio.to_thread(public_client.depth_snapshot, rules.symbol),
            timeout=config.snapshot_timeout.total_seconds(),
        )
        buffered = []
        bootstrap_deadline = time.monotonic() + config.bootstrap_timeout.total_seconds()
        while time.monotonic() < bootstrap_deadline:
            route, event = await asyncio.wait_for(queue.get(), timeout=1)
            if route == "user":
                result = apply_user_data_event(event, store)
                if result.get("fill") is not None:
                    strategy.on_exchange_fill(result["fill"])
                emit(result)
                continue
            if isinstance(event, DepthDelta):
                buffered.append(event)
                if event.first_update_id <= snapshot.last_update_id <= event.final_update_id:
                    break
        else:
            raise HftAuthenticatedPreflightError("authenticated depth bootstrap timed out")
        strategy.bootstrap(snapshot, tuple(buffered))
        from quantos.domain.execution.hft_authenticated import HftReadiness
        execution.set_readiness(HftReadiness(
            authenticated_preflight=True,
            account_reconciled=True,
            clock_healthy=True,
            market_data_ready=True,
            user_data_ready=True,
            risk_ready=True,
            state_ready=True,
        ))
        emit({"event": "hft_authenticated_ready", "symbol": rules.symbol})
        started = time.monotonic()
        next_heartbeat = started
        last_depth_received = started
        transport_stale_seconds = max(
            config.websocket_read_timeout.total_seconds() * 3,
            config.maximum_staleness.total_seconds() * 2,
        )
        while duration_seconds == 0 or time.monotonic() - started < duration_seconds:
            remaining = None if duration_seconds == 0 else max(
                duration_seconds - (time.monotonic() - started), 0
            )
            timeout = min(1, remaining) if remaining is not None else 1
            if timeout <= 0:
                break
            try:
                route, event = await asyncio.wait_for(queue.get(), timeout=timeout)
            except TimeoutError:
                route = "idle"
                event = None
            task_failure(market_task, "market")
            task_failure(user_task, "user")
            if reconcile_enqueue_failed:
                raise HftAuthenticatedPreflightError(
                    "authenticated_application_queue_overflow:reconcile"
                )
            if route == "user":
                emit(apply_user_data_event(event, store))
            elif route == "reconcile":
                result = await asyncio.to_thread(
                    reconcile_authenticated_state,
                    authenticated_client,
                    store,
                    symbol=rules.symbol,
                )
                emit({"event": "hft_authenticated_reconciled", **result})
            elif route == "market":
                if isinstance(event, DepthDelta):
                    received_ns = getattr(event, "received_monotonic_ns", None)
                    last_depth_received = (
                        received_ns / 1_000_000_000
                        if received_ns is not None else time.monotonic()
                    )
                received_ns = getattr(event, "received_monotonic_ns", None)
                consumer_lag_ms = Decimal(str(max(
                    (
                        time.monotonic() - received_ns / 1_000_000_000
                        if received_ns is not None else 0
                    ) * 1000,
                    0.0,
                )))
                if (
                    strategy.inventory_quantity() != 0
                    and user_stream.health != "READY"
                ):
                    emit({
                        "event": "CRITICAL",
                        "flatten": execution.emergency_flatten(
                            "user_data_stream_not_ready_while_exposed"
                        ),
                    })
                    raise HftAuthenticatedPreflightError(
                        "user-data stream became unsafe while exposed"
                    )
                latency = clock.observe(event.exchange_time, event.received_at)
                if (
                    strategy.inventory_quantity() != 0
                    and not clock.quoting_allowed
                ):
                    emit({
                        "event": "CRITICAL",
                        "flatten": execution.emergency_flatten(
                            "exchange_clock_not_healthy_while_exposed"
                        ),
                    })
                    raise HftAuthenticatedPreflightError(
                        "exchange clock became unsafe while exposed"
                    )
                result = strategy.on_event(
                    event,
                    datetime.now(timezone.utc),
                    Decimal(str(time.monotonic())),
                    latency.observed_feed_latency_ms,
                    (
                        clock.quoting_allowed
                        and user_stream.health == "READY"
                        and not stale_trading_disarmed
                    ),
                )
                if result.get("event") not in {
                    "authenticated_hold", "authenticated_quote_resting",
                    "authenticated_aggregate_trade", "authenticated_book_ticker",
                    "authenticated_mark_price", "authenticated_risk_rejected",
                }:
                    emit(result)
            now = time.monotonic()
            usable_stale = (
                now - last_depth_received
                > config.maximum_staleness.total_seconds()
            )
            if usable_stale and not stale_trading_disarmed:
                stale_trading_disarmed = True
                stale_trading_disarm_count += 1
                execution.cancel_owned_orders()
                emit({
                    "event": "hft_authenticated_trading_disarmed",
                    "reason": "stale_usable_market_data",
                    "consumer_lag_ms": consumer_lag_ms,
                })
            elif not usable_stale and stale_trading_disarmed:
                stale_trading_disarmed = False
                emit({
                    "event": "hft_authenticated_trading_rearmed",
                    "reason": "fresh_sequenced_depth_recovered",
                })
            if usable_stale and strategy.inventory_quantity() != 0:
                if strategy.inventory_quantity() != 0:
                    emit({
                        "event": "CRITICAL",
                        "flatten": execution.emergency_flatten(
                            "prolonged_market_data_failure"
                        ),
                    })
                raise HftAuthenticatedPreflightError(
                    "authenticated usable depth is stale while exposed"
                )
            stream_telemetry = (
                public_stream.telemetry(int(now * 1_000_000_000))
                if hasattr(public_stream, "telemetry") else {}
            )
            depth_socket_age = stream_telemetry.get(
                "depth_socket_receive_age_ms"
            )
            if (
                depth_socket_age is not None
                and depth_socket_age
                > Decimal(str(transport_stale_seconds * 1000))
            ):
                if strategy.inventory_quantity() != 0:
                    emit({
                        "event": "CRITICAL",
                        "flatten": execution.emergency_flatten(
                            "prolonged_market_data_failure"
                        ),
                    })
                raise HftAuthenticatedPreflightError(
                    "authenticated_transport_ingest_stale:depth"
                )
            if now >= next_heartbeat:
                heartbeat = authenticated_heartbeat(
                    authenticated_client,
                    store,
                    environment=execution.environment,
                    user_stream=user_stream,
                    mainnet_armed=execution.mainnet_armed,
                )
                heartbeat.update({
                    "book_ready": strategy.book.valid,
                    "alpha_bps": (
                        strategy.last_features.alpha_bps
                        if strategy.last_features is not None else None
                    ),
                    "clock_health": clock.health.value,
                    "remaining_duration_seconds": remaining,
                    "consumer_lag_ms": consumer_lag_ms,
                    "application_queue_current": queue.qsize(),
                    "application_queue_high_water": application_queue_high_water,
                    "backpressure_overflow_count": application_overflow_count,
                    "stale_trading_disarm_count": stale_trading_disarm_count,
                    **stream_telemetry,
                })
                emit(heartbeat)
                next_heartbeat = now + 5
    except BaseException:
        store.set_disabled(True, "authenticated_runtime_failure")
        if strategy.inventory_quantity() != 0:
            emit({
                "event": "CRITICAL",
                "flatten": execution.emergency_flatten(
                    "authenticated_runtime_failure"
                ),
            })
        else:
            execution.cancel_owned_orders()
        raise
    finally:
        market_task.cancel()
        user_task.cancel()
        await asyncio.gather(market_task, user_task, return_exceptions=True)
        if strategy.inventory_quantity() != 0:
            emit({
                "event": "CRITICAL",
                "flatten": execution.emergency_flatten(
                    "authenticated_runtime_shutdown"
                ),
            })
        else:
            execution.cancel_owned_orders()
        emit({"event": "hft_authenticated_stopped"})
