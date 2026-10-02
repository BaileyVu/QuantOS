from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs

from quantos.application.hft_authenticated import (
    HftAuthenticatedPreflightError,
    HftAuthenticatedStrategyEngine,
    apply_user_data_event,
    authenticated_preflight,
    reconcile_authenticated_state,
)
from quantos.domain.alpha.hft import HftSide
from quantos.domain.execution.hft_authenticated import (
    HftAuthenticatedExecution,
    HftExecutionEnvironment,
    HftExecutionError,
    HftReadiness,
)
from quantos.domain.market_data.futures import parse_usdm_exchange_info
from quantos.domain.market_data.hft import AggregateTrade
from quantos.domain.risk.hft import HftCostHurdle, HftRiskDecision
from quantos.infrastructure.binance.hft_authenticated import (
    BinanceAuthenticatedError,
    BinanceCredentials,
    BinanceHftUserDataStream,
    BinanceUsdmAuthenticatedClient,
    HttpResponse,
)
from quantos.infrastructure.storage.hft_authenticated import (
    HftAuthenticatedStateError,
    HftAuthenticatedStateStore,
)
from quantos.infrastructure.configuration.hft import load_hft_config


D = Decimal
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def exchange_info(symbol: str = "BTCUSDC") -> dict:
    quote = "USDC" if symbol == "BTCUSDC" else "USDT"
    return {"symbols": [{
        "symbol": symbol,
        "status": "TRADING",
        "contractType": "PERPETUAL",
        "quoteAsset": quote,
        "marginAsset": quote,
        "filters": [
            {"filterType": "LOT_SIZE", "stepSize": ".001", "minQty": ".001", "maxQty": "10"},
            {"filterType": "PRICE_FILTER", "tickSize": ".1", "minPrice": "1", "maxPrice": "1000000"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ],
    }]}


class RateState:
    banned = False
    warning = False
    def payload(self):
        return {"used_weight_1m": 1, "order_count_10s": 0}


class MemoryStore:
    def __init__(self, environment="TESTNET", disabled=False):
        self.state = {
            "environment": environment,
            "disabled": disabled,
            "order_sequence": 0,
            "orders": {},
            "intents": {},
            "fills": [],
            "reconciliation": {
                "account_reconciled": True,
                "exchange_position": "0",
                "local_position": "0",
            },
            "preflight": {"passed": True, "wallet_balance": "100"},
            "commission": None,
        }

    def snapshot(self):
        return json.loads(json.dumps(self.state))

    def persist_intent(self, value):
        self.state["intents"][value["client_order_id"]] = value
        self.state["order_sequence"] = max(
            self.state["order_sequence"], value["sequence"]
        )

    def persist_order(self, value):
        if value["client_order_id"] not in self.state["intents"]:
            raise HftAuthenticatedStateError("missing intent")
        self.state["orders"][value["client_order_id"]] = value

    def persist_fill(self, value):
        key = (value["client_order_id"], value["trade_id"])
        if key not in {(v["client_order_id"], v["trade_id"]) for v in self.state["fills"]}:
            self.state["fills"].append(value)

    def persist_preflight(self, value):
        self.state["preflight"] = value
        self.state["commission"] = value["commission"]

    def persist_reconciliation(self, value):
        self.state["reconciliation"] = value

    def set_disabled(self, value, reason):
        self.state["disabled"] = value
        self.state["disabled_reason"] = reason


class FakeGateway:
    def __init__(self):
        self.created = []
        self.cancelled = []
        self.orders = []
        self.positions = [{"symbol": "BTCUSDC", "positionAmt": "0"}]
        self.order_creation_suppressed = False
        self.create_error = None
        self.recovered = None
        self.cancel_error = None
        self.emergency_error = None

    def new_limit_gtx(self, **values):
        self.created.append(values)
        if self.create_error is not None:
            error, self.create_error = self.create_error, None
            raise error
        return {
            "symbol": values["symbol"], "clientOrderId": values["client_order_id"],
            "orderId": 7, "side": values["side"], "price": str(values["price"]),
            "origQty": str(values["quantity"]), "executedQty": "0", "status": "NEW",
            "reduceOnly": values.get("reduce_only", False), "updateTime": 1,
        }

    def query_order(self, symbol, client_order_id):
        del symbol, client_order_id
        if self.recovered and self.recovered.get("reduceOnly"):
            self.positions = [{"symbol": "BTCUSDC", "positionAmt": "0"}]
        return self.recovered

    def cancel_order(self, symbol, client_order_id):
        if self.cancel_error is not None:
            error, self.cancel_error = self.cancel_error, None
            raise error
        self.cancelled.append(client_order_id)
        row = next(item for item in self.orders if item["clientOrderId"] == client_order_id)
        return {**row, "symbol": symbol, "status": "CANCELED"}

    def open_orders(self, symbol):
        return [row for row in self.orders if row["symbol"] == symbol]

    def emergency_market_exit(self, **values):
        if self.emergency_error is not None:
            error, self.emergency_error = self.emergency_error, None
            raise error
        self.positions = [{"symbol": values["symbol"], "positionAmt": "0"}]
        return {
            "symbol": values["symbol"], "clientOrderId": values["client_order_id"],
            "orderId": 9, "side": values["side"], "origQty": str(values["quantity"]),
            "executedQty": str(values["quantity"]), "status": "FILLED",
            "reduceOnly": True, "updateTime": 2,
        }

    def position_risk(self, symbol=None):
        return list(self.positions)


def readiness() -> HftReadiness:
    return HftReadiness(True, True, True, True, True, True, True)


def approval(quantity=".05") -> HftRiskDecision:
    hurdle = HftCostHurdle(
        D("0"), D("0"), D("0"), D("4"), D(".1"), D(".5"),
        D("0"), D(".5"), D("1"), D("24"),
    )
    return HftRiskDecision(True, "approved", D(quantity), D("5"), D(".05"), hurdle)


class SigningAndTransportTests(unittest.TestCase):
    def test_signing_timestamp_recvwindow_and_api_key(self):
        captured = {}
        def transport(method, url, headers, body, timeout):
            captured.update(method=method, url=url, headers=headers, body=body, timeout=timeout)
            return HttpResponse(200, {}, b'{"makerCommissionRate":"0","takerCommissionRate":".0004"}')
        client = BinanceUsdmAuthenticatedClient(
            HftExecutionEnvironment.MAINNET,
            BinanceCredentials("public-key", "secret-value"),
            recv_window=7000, request_timeout=3, transport=transport,
            timestamp_ms=lambda: 123456,
        )
        client.commission_rate("BTCUSDC")
        query = captured["url"].split("?", 1)[1]
        unsigned, signature = query.rsplit("&signature=", 1)
        expected = hmac.new(b"secret-value", unsigned.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(signature, expected)
        parsed = parse_qs(unsigned)
        self.assertEqual(parsed["timestamp"], ["123456"])
        self.assertEqual(parsed["recvWindow"], ["7000"])
        self.assertEqual(captured["headers"]["X-MBX-APIKEY"], "public-key")
        self.assertEqual(captured["timeout"], 3)

    def test_credentials_are_environment_only_and_health_is_redacted(self):
        credentials = BinanceCredentials.from_environment({
            "QUANTOS_BINANCE_API_KEY": "key-value",
            "QUANTOS_BINANCE_API_SECRET": "secret-value",
        })
        text = json.dumps(credentials.health())
        self.assertNotIn("key-value", text)
        self.assertNotIn("secret-value", text)
        self.assertTrue(credentials.health()["credentials_redacted"])

    def test_gtx_payload_and_reject_parsing(self):
        calls = []
        def transport(method, url, headers, body, timeout):
            del url, headers, timeout
            calls.append((method, body.decode()))
            if len(calls) == 1:
                return HttpResponse(200, {}, b'{"clientOrderId":"x","status":"NEW"}')
            return HttpResponse(400, {}, b'{"code":-5022,"msg":"Post Only order will be rejected"}')
        client = BinanceUsdmAuthenticatedClient(
            HftExecutionEnvironment.TESTNET, BinanceCredentials("k", "s"),
            transport=transport, timestamp_ms=lambda: 1,
        )
        client.new_limit_gtx(
            symbol="BTCUSDC", side="BUY", quantity=D(".001"),
            price=D("100"), client_order_id="x",
        )
        self.assertIn("timeInForce=GTX", calls[0][1])
        self.assertIn("type=LIMIT", calls[0][1])
        with self.assertRaises(BinanceAuthenticatedError) as raised:
            client.new_limit_gtx(
                symbol="BTCUSDC", side="BUY", quantity=D(".001"),
                price=D("100"), client_order_id="y",
            )
        self.assertEqual(raised.exception.code, -5022)

    def test_rate_limit_metadata_suppresses_orders(self):
        client = BinanceUsdmAuthenticatedClient(
            HftExecutionEnvironment.TESTNET, BinanceCredentials("k", "s"),
            transport=lambda *args: HttpResponse(
                429, {"Retry-After": "2", "X-MBX-USED-WEIGHT-1M": "2200"},
                b'{"code":-1003,"msg":"too many requests"}',
            ), timestamp_ms=lambda: 1, sleep=lambda value: None,
        )
        with self.assertRaises(BinanceAuthenticatedError):
            client.account()
        self.assertTrue(client.order_creation_suppressed)
        self.assertEqual(client.rate_limits.retry_after_seconds, 2)

    def test_paper_cannot_construct_authenticated_client(self):
        with self.assertRaises(ValueError):
            BinanceUsdmAuthenticatedClient(
                HftExecutionEnvironment.PAPER, BinanceCredentials("k", "s")
            )


class DurableEnvironmentTests(unittest.TestCase):
    def test_environment_identity_and_explicit_rearm(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.db"
            testnet = HftAuthenticatedStateStore(
                path, HftExecutionEnvironment.TESTNET, "identity"
            )
            state = testnet.initialize()
            self.assertTrue(state["disabled"])
            with self.assertRaises(HftAuthenticatedStateError):
                HftAuthenticatedStateStore(
                    path, HftExecutionEnvironment.MAINNET, "identity"
                ).snapshot()
            with self.assertRaises(HftAuthenticatedStateError):
                testnet.rearm("wrong")
            testnet.rearm("REARM_AUTHENTICATED_HFT")
            self.assertFalse(testnet.snapshot()["disabled"])


class FakePreflightClient:
    def __init__(self):
        self.can_trade = True
        self.futures_enabled = True
        self.api_futures_enabled = True
        self.api_reading_enabled = True
        self.account_status_value = "Normal"
        self.api_trading_locked = False
        self.dual = False
        self.isolated = True
        self.leverage = 5
        self.positions = [{
            "symbol": "BTCUSDC", "positionAmt": "0", "isolated": True,
            "leverage": "5",
        }]
        self.orders = []
        self.calls = []
        self.rate_limits = RateState()
        self.balance_rows = [
            {"asset": "USDT", "balance": "20", "availableBalance": "20"},
            {"asset": "USDC", "balance": "100", "availableBalance": "90"},
        ]
        self.test_order_error = None

    def calibrate_clock(self): return {"clock_offset_ms": 0}
    def exchange_info(self): return exchange_info()
    def account(self):
        result = {"multiAssetsMargin": False}
        if self.can_trade is not None:
            result["canTrade"] = self.can_trade
        return result
    def account_info(self): return {"isFutureEnabled": self.futures_enabled}
    def account_status(self): return {"data": self.account_status_value}
    def api_key_permissions(self): return {
        "enableReading": self.api_reading_enabled,
        "enableFutures": self.api_futures_enabled,
        "enableSpotAndMarginTrading": False,
    }
    def api_trading_status(self): return {
        "data": {"isLocked": self.api_trading_locked}
    }
    def account_config(self): return {"multiAssetsMargin": False}
    def balances(self): return list(self.balance_rows)
    def position_risk(self, symbol=None): return list(self.positions)
    def open_orders(self, symbol=None): return list(self.orders)
    def position_mode(self): return {"dualSidePosition": self.dual}
    def symbol_config(self, symbol):
        return [{"symbol": symbol, "marginType": "ISOLATED" if self.isolated else "CROSSED", "leverage": self.leverage}]
    def order_rate_limits(self): return [{"interval": "MINUTE", "limit": 1200}]
    def commission_rate(self, symbol): return {"symbol": symbol, "makerCommissionRate": "0", "takerCommissionRate": ".0004"}
    def book_ticker(self, symbol): return {"symbol": symbol, "bidPrice": "100", "askPrice": "101"}
    def credential_health(self): return {"credentials_redacted": True}
    def test_order(self, **values):
        self.calls.append(("test_order", values))
        if self.test_order_error is not None:
            raise self.test_order_error
        return {}
    def set_one_way_mode(self): self.calls.append(("one_way",)); self.dual = False; return {}
    def set_isolated_margin(self, symbol): self.calls.append(("isolated", symbol)); self.isolated = True; return {}
    def set_leverage(self, symbol, value): self.calls.append(("leverage", symbol, value)); self.leverage = value; return {}
    def cancel_order(self, symbol, client_id):
        self.calls.append(("cancel", symbol, client_id)); self.orders = []; return {}


class PreflightAndReconciliationTests(unittest.TestCase):
    def test_commission_symbol_account_and_test_order_preflight(self):
        client, store = FakePreflightClient(), MemoryStore()
        payload, rules, fees = authenticated_preflight(
            client, store, environment=HftExecutionEnvironment.MAINNET,
            normalize_account=False, perform_test_order=True,
        )
        self.assertTrue(payload["passed"])
        self.assertEqual(rules.symbol, "BTCUSDC")
        self.assertEqual(fees.maker_rate, D("0"))
        self.assertEqual(fees.taker_rate, D(".0004"))
        self.assertEqual(store.state["commission"]["maker_rate"], "0")
        self.assertEqual(client.calls[0][0], "test_order")
        self.assertTrue(payload["futures_account_enabled"])
        self.assertTrue(payload["api_key_enable_futures"])
        self.assertEqual(payload["account_status"], "Normal")
        self.assertEqual(payload["usdt_futures_wallet_balance"], "20")
        self.assertEqual(payload["usdc_futures_available_balance"], "90")
        self.assertEqual(payload["position_mode"], "ONE_WAY")
        self.assertEqual(payload["account_mode"], "SINGLE_ASSET")
        self.assertEqual(payload["btcusdc_status"], "TRADING")
        self.assertTrue(payload["account_can_trade"])
        self.assertEqual(payload["account_can_trade_source"], "fapi_v3_account.canTrade")
        self.assertTrue(payload["account_can_trade_required"])
        self.assertTrue(payload["order_test_attempted"])
        self.assertTrue(payload["order_test_passed"])
        self.assertTrue(payload["effective_trade_permission"])

    def test_permission_failure_is_not_misclassified_as_zero_balance(self):
        client = FakePreflightClient()
        client.can_trade = False
        client.balance_rows = [
            {"asset": "USDT", "balance": "0", "availableBalance": "0"},
            {"asset": "USDC", "balance": "0", "availableBalance": "0"},
        ]
        with self.assertRaises(HftAuthenticatedPreflightError) as raised:
            authenticated_preflight(
                client, MemoryStore(),
                environment=HftExecutionEnvironment.MAINNET,
                normalize_account=False, perform_test_order=False,
            )
        diagnostic = raised.exception.diagnostic
        self.assertEqual(diagnostic["failure_code"], "ACCOUNT_PERMISSION")
        self.assertEqual(diagnostic["failure_type"], "ACCOUNT_PERMISSION")
        self.assertFalse(diagnostic["account_can_trade"])
        self.assertNotEqual(
            diagnostic["failure_code"], "INSUFFICIENT_FUTURES_COLLATERAL"
        )

    def test_permission_failure_classification_uses_exact_binance_fields(self):
        cases = (
            ("FUTURES_NOT_ENABLED", {"futures_enabled": False}),
            ("API_KEY_PERMISSION", {"api_futures_enabled": False}),
            ("ACCOUNT_RESTRICTED", {"account_status_value": "Restricted"}),
            ("ACCOUNT_RESTRICTED", {"api_trading_locked": True}),
        )
        for expected, changes in cases:
            with self.subTest(expected=expected, changes=changes):
                client = FakePreflightClient()
                for name, value in changes.items():
                    setattr(client, name, value)
                with self.assertRaises(HftAuthenticatedPreflightError) as raised:
                    authenticated_preflight(
                        client, MemoryStore(),
                        environment=HftExecutionEnvironment.MAINNET,
                        normalize_account=False, perform_test_order=False,
                    )
                self.assertEqual(
                    raised.exception.diagnostic["failure_type"], expected
                )

    def test_missing_can_trade_uses_order_test_as_final_permission_probe(self):
        client = FakePreflightClient()
        client.can_trade = None
        payload, _, _ = authenticated_preflight(
            client, MemoryStore(), environment=HftExecutionEnvironment.MAINNET,
            normalize_account=False, perform_test_order=True,
        )
        self.assertTrue(payload["passed"])
        self.assertIsNone(payload["account_can_trade"])
        self.assertIsNone(payload["account_can_trade_source"])
        self.assertFalse(payload["account_can_trade_required"])
        self.assertTrue(payload["order_test_passed"])
        self.assertTrue(payload["effective_trade_permission"])
        self.assertIn(
            "fapi_v1_order_test=PASS",
            payload["effective_trade_permission_evidence"],
        )

    def test_order_test_rejections_remain_fail_closed_with_exact_diagnostic(self):
        cases = (
            (
                BinanceAuthenticatedError("API-key permission denied", code=-2015),
                "API_KEY_PERMISSION",
            ),
            (
                BinanceAuthenticatedError("Signature for this request is not valid", code=-1022),
                "AUTHENTICATION_OR_TRANSPORT",
            ),
        )
        for error, expected in cases:
            with self.subTest(expected=expected):
                client = FakePreflightClient()
                client.can_trade = None
                client.test_order_error = error
                with self.assertRaises(HftAuthenticatedPreflightError) as raised:
                    authenticated_preflight(
                        client, MemoryStore(),
                        environment=HftExecutionEnvironment.MAINNET,
                        normalize_account=False, perform_test_order=True,
                    )
                diagnostic = raised.exception.diagnostic
                self.assertEqual(diagnostic["failure_type"], expected)
                self.assertTrue(diagnostic["order_test_attempted"])
                self.assertFalse(diagnostic["order_test_passed"])
                self.assertEqual(diagnostic["order_test_error_code"], error.code)
                self.assertEqual(diagnostic["order_test_error_message"], str(error))
                self.assertFalse(diagnostic["effective_trade_permission"])

    def test_valid_permissions_with_zero_collateral_is_balance_only(self):
        client = FakePreflightClient()
        client.balance_rows = [
            {"asset": "USDT", "balance": "0", "availableBalance": "0"},
            {"asset": "USDC", "balance": "0", "availableBalance": "0"},
        ]
        with self.assertRaises(HftAuthenticatedPreflightError) as raised:
            authenticated_preflight(
                client, MemoryStore(),
                environment=HftExecutionEnvironment.MAINNET,
                normalize_account=False, perform_test_order=False,
            )
        diagnostic = raised.exception.diagnostic
        self.assertEqual(
            diagnostic["failure_code"], "INSUFFICIENT_FUTURES_COLLATERAL"
        )
        self.assertEqual(diagnostic["failure_type"], "BALANCE_ONLY")
        self.assertTrue(diagnostic["account_can_trade"])
        self.assertEqual(diagnostic["usdc_futures_wallet_balance"], "0")

    def test_one_way_isolated_and_leverage_normalize_only_when_flat(self):
        client, store = FakePreflightClient(), MemoryStore()
        client.dual, client.isolated, client.leverage = True, False, 10
        payload, _, _ = authenticated_preflight(
            client, store, environment=HftExecutionEnvironment.TESTNET,
            normalize_account=True, perform_test_order=False,
        )
        self.assertEqual(
            payload["account_configuration_changes"],
            ["one_way", "isolated_margin", "leverage_5"],
        )
        client = FakePreflightClient()
        client.dual = True
        client.positions[0]["positionAmt"] = ".01"
        with self.assertRaisesRegex(HftAuthenticatedPreflightError, "one-way"):
            authenticated_preflight(
                client, MemoryStore(), environment=HftExecutionEnvironment.TESTNET,
                normalize_account=True, perform_test_order=False,
            )

    def test_orphan_cancel_and_unexpected_position_fail_closed(self):
        client, store = FakePreflightClient(), MemoryStore()
        client.orders = [{
            "symbol": "BTCUSDC", "clientOrderId": "qoh1-t-orphan-b-1",
            "orderId": 1, "side": "BUY", "price": "100", "origQty": ".05",
            "executedQty": "0", "status": "NEW",
        }]
        result = reconcile_authenticated_state(client, store, symbol="BTCUSDC")
        self.assertEqual(result["orphan_orders_cancelled"], ["qoh1-t-orphan-b-1"])
        client.positions[0]["positionAmt"] = ".05"
        with self.assertRaisesRegex(HftAuthenticatedPreflightError, "unexpected"):
            reconcile_authenticated_state(client, store, symbol="BTCUSDC")

    def test_known_order_is_restored_from_exchange_truth(self):
        client, store = FakePreflightClient(), MemoryStore()
        client_id = "qoh1-t-known-b-1"
        store.persist_intent({"client_order_id": client_id, "sequence": 1})
        store.persist_order({
            "client_order_id": client_id, "status": "PENDING_SUBMIT"
        })
        client.orders = [{
            "symbol": "BTCUSDC", "clientOrderId": client_id,
            "orderId": 12, "side": "BUY", "price": "100",
            "origQty": ".05", "executedQty": ".01",
            "status": "PARTIALLY_FILLED", "reduceOnly": False,
        }]
        result = reconcile_authenticated_state(client, store, symbol="BTCUSDC")
        self.assertEqual(result["quantos_open_orders"], 1)
        self.assertEqual(
            store.state["orders"][client_id]["status"], "PARTIALLY_FILLED"
        )


class ExecutionAuthorityTests(unittest.TestCase):
    def controller(self, environment=HftExecutionEnvironment.TESTNET, **kwargs):
        gateway, store = FakeGateway(), MemoryStore(environment.value)
        execution = HftAuthenticatedExecution(
            environment=environment, gateway=gateway, store=store,
            session_id="session", **kwargs,
        )
        execution.set_readiness(readiness())
        return execution, gateway, store

    def test_mainnet_requires_both_ephemeral_gates(self):
        for flag, token, expected in (
            (False, None, False),
            (True, None, False),
            (False, HftAuthenticatedExecution.APPROVAL_TOKEN, False),
            (True, HftAuthenticatedExecution.APPROVAL_TOKEN, True),
        ):
            execution, _, _ = self.controller(
                HftExecutionEnvironment.MAINNET,
                enable_mainnet=flag, approval_token=token,
            )
            self.assertEqual(execution.submission_enabled, expected)

    def test_intent_precedes_submit_and_client_id_is_deterministic(self):
        execution, gateway, store = self.controller()
        order = execution.submit_post_only(approval(), HftSide.BUY, D("100"))
        self.assertIn(order.client_order_id, store.state["intents"])
        self.assertEqual(gateway.created[0]["client_order_id"], order.client_order_id)
        self.assertTrue(order.client_order_id.startswith("qoh1-t-"))
        self.assertLessEqual(len(order.client_order_id), 36)

    def test_timeout_and_duplicate_id_recover_by_client_id(self):
        execution, gateway, store = self.controller()
        error = BinanceAuthenticatedError("timeout", ambiguous=True, retryable=True)
        gateway.create_error = error
        gateway.recovered = {
            "symbol": "BTCUSDC", "clientOrderId": execution.client_order_id(HftSide.BUY, 1),
            "orderId": 5, "side": "BUY", "price": "100", "origQty": ".05",
            "executedQty": "0", "status": "NEW", "updateTime": 1,
        }
        order = execution.submit_post_only(approval(), HftSide.BUY, D("100"))
        self.assertEqual(order.exchange_order_id, 5)
        self.assertEqual(len(gateway.created), 1)
        self.assertIn(order.client_order_id, store.state["orders"])

    def test_authoritative_nonexistence_allows_one_retry(self):
        execution, gateway, _ = self.controller()
        gateway.create_error = BinanceAuthenticatedError("timeout", ambiguous=True)
        gateway.recovered = None
        execution.submit_post_only(approval(), HftSide.BUY, D("100"))
        self.assertEqual(len(gateway.created), 2)

    def test_partial_fill_and_actual_commission_are_exchange_authoritative(self):
        execution, _, store = self.controller()
        client_id = execution.client_order_id(HftSide.BUY, 1)
        store.persist_intent({
            "client_order_id": client_id, "sequence": 1,
        })
        result = apply_user_data_event({
            "e": "ORDER_TRADE_UPDATE", "E": 1000,
            "o": {
                "s": "BTCUSDC", "c": client_id, "i": 7, "S": "BUY",
                "p": "100", "q": ".05", "z": ".02", "X": "PARTIALLY_FILLED",
                "R": False, "x": "TRADE", "l": ".02", "L": "100",
                "t": 9, "n": ".001", "N": "USDC", "m": True, "rp": "0",
            },
        }, store)
        self.assertEqual(result["order"]["status"], "PARTIALLY_FILLED")
        self.assertEqual(store.state["fills"][0]["commission"], ".001")
        self.assertTrue(store.state["fills"][0]["maker"])

    def test_account_update_and_cancel_fill_race(self):
        execution, gateway, store = self.controller()
        update = apply_user_data_event({
            "e": "ACCOUNT_UPDATE", "a": {"m": "ORDER", "P": [
                {"s": "BTCUSDC", "pa": ".05"}
            ]},
        }, store)
        self.assertEqual(update["position"], ".05")
        gateway.orders = [{
            "symbol": "BTCUSDC", "clientOrderId": "qoh1-t-race-b-1",
            "orderId": 1, "side": "BUY", "price": "100", "origQty": ".05",
            "executedQty": ".05", "status": "FILLED", "updateTime": 1,
        }]
        store.persist_intent({"client_order_id": "qoh1-t-race-b-1", "sequence": 1})
        cancelled = execution.cancel_owned_orders()
        self.assertEqual(cancelled, ("qoh1-t-race-b-1",))

    def test_cancel_error_recovers_terminal_fill_by_client_id(self):
        execution, gateway, store = self.controller()
        client_id = "qoh1-t-race-b-2"
        row = {
            "symbol": "BTCUSDC", "clientOrderId": client_id,
            "orderId": 2, "side": "BUY", "price": "100", "origQty": ".05",
            "executedQty": ".05", "status": "FILLED", "updateTime": 1,
        }
        gateway.orders = [row]
        gateway.cancel_error = BinanceAuthenticatedError(
            "unknown order", code=-2011
        )
        gateway.recovered = row
        store.persist_intent({"client_order_id": client_id, "sequence": 2})
        self.assertEqual(execution.cancel_owned_orders(), (client_id,))
        self.assertEqual(store.state["orders"][client_id]["status"], "FILLED")

    def test_emergency_reduce_only_flatten_and_kill_never_reverse(self):
        execution, gateway, store = self.controller()
        gateway.positions = [{"symbol": "BTCUSDC", "positionAmt": ".05"}]
        result = execution.emergency_flatten("risk")
        self.assertEqual(result["status"], "FLAT")
        self.assertTrue(store.state["disabled"])
        self.assertTrue(next(iter(store.state["orders"].values()))["reduce_only"])
        before = len(store.state["intents"])
        self.assertEqual(execution.emergency_flatten("again")["status"], "FLAT")
        self.assertEqual(len(store.state["intents"]), before)
        killed = execution.kill(flatten=False)
        self.assertTrue(killed["disabled"])

    def test_ambiguous_emergency_is_queried_and_never_resubmitted(self):
        execution, gateway, store = self.controller()
        gateway.positions = [{"symbol": "BTCUSDC", "positionAmt": "-.05"}]
        gateway.emergency_error = BinanceAuthenticatedError(
            "timeout", ambiguous=True
        )
        expected_id = execution.client_order_id(HftSide.BUY, 1)
        gateway.recovered = {
            "symbol": "BTCUSDC", "clientOrderId": expected_id,
            "orderId": 20, "side": "BUY", "price": "0", "origQty": ".05",
            "executedQty": ".05", "status": "FILLED", "reduceOnly": True,
            "updateTime": 1,
        }
        self.assertEqual(execution.emergency_flatten("risk")["status"], "FLAT")
        self.assertEqual(len(store.state["intents"]), 1)

    def test_rate_limit_suppression_blocks_new_order(self):
        execution, gateway, _ = self.controller()
        gateway.order_creation_suppressed = True
        with self.assertRaisesRegex(HftExecutionError, "not armed"):
            execution.submit_post_only(approval(), HftSide.BUY, D("100"))

    def test_account_config_update_is_accepted_without_inventing_state(self):
        execution, _, store = self.controller()
        before = store.snapshot()["reconciliation"]
        result = apply_user_data_event(
            {"e": "ACCOUNT_CONFIG_UPDATE", "E": 1}, store
        )
        self.assertEqual(result["event"], "exchange_account_config_update")
        self.assertEqual(store.snapshot()["reconciliation"], before)

    def test_testnet_protocol_quote_is_forbidden_on_mainnet(self):
        execution, _, _ = self.controller(
            HftExecutionEnvironment.MAINNET,
            enable_mainnet=True,
            approval_token=HftAuthenticatedExecution.APPROVAL_TOKEN,
        )
        with self.assertRaisesRegex(HftExecutionError, "forbidden"):
            execution.submit_testnet_protocol_quote(
                HftSide.BUY, D("100"), D(".05")
            )

    def test_live_public_trade_never_infers_an_exchange_fill(self):
        execution, gateway, store = self.controller()
        config = load_hft_config(Path("configs/futures_hft_paper.toml"))
        rules = parse_usdm_exchange_info(exchange_info(), "BTCUSDC")
        engine = HftAuthenticatedStrategyEngine(
            config, rules, config.fees["BTCUSDC"], execution, store
        )
        result = engine.on_event(
            AggregateTrade(
                symbol="BTCUSDC", aggregate_trade_id=1,
                price=D("100"), quantity=D("1"), buyer_is_maker=True,
                exchange_time=NOW, received_at=NOW,
            ),
            NOW,
            D("1"),
            D("0"),
            True,
        )
        self.assertEqual(result["event"], "authenticated_aggregate_trade")
        self.assertEqual(store.state["fills"], [])
        self.assertEqual(gateway.created, [])

    def test_strategy_risk_uses_authenticated_commission(self):
        execution, _, store = self.controller()
        config = load_hft_config(Path("configs/futures_hft_paper.toml"))
        rules = parse_usdm_exchange_info(exchange_info(), "BTCUSDC")
        actual = config.fees["BTCUSDT"]
        engine = HftAuthenticatedStrategyEngine(
            config, rules, actual, execution, store
        )
        self.assertEqual(engine.risk.fees, actual)


class FakeListenClient:
    websocket_base_url = "wss://example.test"
    request_timeout = 1
    def __init__(self): self.keys = 0
    def start_listen_key(self): self.keys += 1; return f"key-{self.keys}"
    def keepalive_listen_key(self, key): pass
    def close_listen_key(self, key): pass


class FakeSocket:
    def __init__(self, messages): self.messages = iter(messages)
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def recv(self):
        try: return next(self.messages)
        except StopIteration: await asyncio.Future()


class UserStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_expiry_reconnects_and_order_event_is_delivered(self):
        sockets = iter([
            FakeSocket([json.dumps({"e": "listenKeyExpired"})]),
            FakeSocket([json.dumps({"e": "ORDER_TRADE_UPDATE", "o": {}})]),
        ])
        lifecycle = []
        stream = BinanceHftUserDataStream(
            FakeListenClient(), read_timeout=1, keepalive_seconds=1000,
            reconnect_delay=.001, lifecycle=lifecycle.append,
            connect_factory=lambda *args, **kwargs: next(sockets),
        )
        events = stream.events()
        payload = await asyncio.wait_for(events.__anext__(), timeout=1)
        self.assertEqual(payload["e"], "ORDER_TRADE_UPDATE")
        self.assertEqual(stream.health, "READY")
        self.assertIn({"event": "user_data_expired"}, lifecycle)
        self.assertIn({"event": "user_data_reconnecting"}, lifecycle)
        await events.aclose()


if __name__ == "__main__":
    unittest.main()
