"""Safety-focused acceptance tests for the autonomous Futures MVP."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import unittest

from quantos.application.futures_trader import run_futures_replay
from quantos.domain.alpha.futures import (
    Direction, MarketRegime, StrategySignal, TradeCandidate,
    classify_regime, evaluate_strategies, select_candidate,
)
from quantos.domain.execution.futures_paper import FuturesPaperExecution, PaperExecutionError
from quantos.domain.market_data import Candle
from quantos.domain.market_data.futures import (
    FuturesRuleError, FuturesSymbolRules, parse_usdm_exchange_info,
)
from quantos.domain.risk.futures import (
    FuturesAccountState, FuturesRiskDecision, FuturesRiskPolicy,
    evaluate_futures_risk,
)
from quantos.infrastructure.configuration.futures import load_futures_config
from quantos.infrastructure.binance.futures import BinanceUsdmPublicClient


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def exchange_info(status="TRADING", minimum_notional="5"):
    return {"symbols": [{
        "symbol": "BTCUSDT", "status": status, "contractType": "PERPETUAL",
        "filters": [
            {"filterType": "PRICE_FILTER", "minPrice": "0.10",
             "maxPrice": "1000000", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "minQty": "0.001",
             "maxQty": "1000", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": minimum_notional},
        ],
    }]}


def rules(status="TRADING", minimum_notional="5"):
    return parse_usdm_exchange_info(exchange_info(status, minimum_notional))


def candle(minute, close="100", high="101", low="99", open_="100"):
    opened = START + timedelta(minutes=minute)
    return Candle("BTCUSDT", "1m", opened, opened + timedelta(minutes=1) - timedelta(milliseconds=1),
                  D(open_), D(high), D(low), D(close), D("10"), D("1000"), 10)


def signal(direction=Direction.LONG, entry="100", stop="99", target="102",
           strength=".7", strategy="test"):
    return StrategySignal(START, "BTCUSDT", direction, strategy,
                          MarketRegime.TREND_UP, D(entry), D(stop), D(target),
                          D(strength), ("fixture",), "fixture")


class ExchangeRuleTests(unittest.TestCase):
    def test_parses_rounds_and_validates_dynamic_filters(self):
        value = rules()
        self.assertEqual(value.quantity_step, D(".001"))
        self.assertEqual(value.price_tick, D(".10"))
        self.assertEqual(value.round_quantity(D(".1239")), D(".123"))
        value.validate_market_order(D(".050"), D("100"))

    def test_fails_closed_for_status_precision_and_notional(self):
        with self.assertRaisesRegex(FuturesRuleError, "not TRADING"):
            rules("BREAK").validate_market_order(D(".050"), D("100"))
        with self.assertRaisesRegex(FuturesRuleError, "representable"):
            rules().validate_market_order(D(".0505"), D("100"))
        with self.assertRaisesRegex(FuturesRuleError, "minimum notional"):
            rules(minimum_notional="10").validate_market_order(D(".050"), D("100"))

    def test_missing_required_filter_rejected(self):
        payload = exchange_info()
        payload["symbols"][0]["filters"].pop()
        with self.assertRaisesRegex(FuturesRuleError, "MIN_NOTIONAL"):
            parse_usdm_exchange_info(payload)


class FuturesPublicAdapterTests(unittest.TestCase):
    def test_public_rules_kline_mark_and_funding_are_normalized(self):
        row = [int(START.timestamp() * 1000), "100", "101", "99", "100.5",
               "2", int((START + timedelta(minutes=1) - timedelta(milliseconds=1)).timestamp() * 1000),
               "200", 4, "0", "0", "0"]
        def get(url):
            if "exchangeInfo" in url:
                payload = exchange_info()
            elif "premiumIndex" in url:
                payload = {"markPrice": "100.25", "lastFundingRate": "0.0001"}
            else:
                payload = [row]
            return json.dumps(payload).encode("ascii")
        client = BinanceUsdmPublicClient("https://example.test", get)
        self.assertEqual(client.symbol_rules().minimum_notional, D("5"))
        self.assertEqual(client.klines(limit=1)[0].close, D("100.5"))
        self.assertEqual(client.mark_price(), D("100.25"))
        self.assertEqual(client.funding_rate(), D("0.0001"))


class AlphaDecisionTests(unittest.TestCase):
    def test_long_short_conflict_holds(self):
        long = signal(Direction.LONG, strength=".70")
        short = signal(Direction.SHORT, stop="101", target="98", strength=".62")
        selected = select_candidate((long, short))
        self.assertEqual(selected.direction, Direction.HOLD)
        self.assertIn("conflict", selected.reason)

    def test_breakout_generates_long_candidate(self):
        candles = tuple(candle(i) for i in range(30)) + (candle(30, "104", "105", "99"),)
        regime = classify_regime(candles)
        self.assertEqual(regime.regime, MarketRegime.BREAKOUT_OR_EXPANSION)
        selected = select_candidate(evaluate_strategies(candles, regime))
        self.assertEqual(selected.direction, Direction.LONG)
        self.assertEqual(selected.candidate.signal.strategy_id, "breakout_expansion")


class FuturesRiskTests(unittest.TestCase):
    def setUp(self):
        self.account = FuturesAccountState(D("20"), D("20"))

    def test_sizes_from_stop_then_derives_leverage(self):
        decision = evaluate_futures_risk(TradeCandidate(signal()), self.account, rules())
        self.assertTrue(decision.approved, decision.reason)
        self.assertEqual(decision.quantity, D(".175"))
        self.assertLessEqual(decision.risk_amount, D(".200"))
        self.assertEqual(decision.leverage, 2)
        self.assertLess(decision.liquidation_price, D("99"))

    def test_one_position_and_loss_breakers_reject(self):
        candidate = TradeCandidate(signal())
        self.assertIn("one-position", evaluate_futures_risk(
            candidate, replace(self.account, has_position=True), rules()).reason)
        self.assertIn("consecutive-loss", evaluate_futures_risk(
            candidate, replace(
                self.account,
                consecutive_losses=3,
                consecutive_loss_breaker_active=True,
            ), rules()).reason)
        self.assertIn("daily-loss", evaluate_futures_risk(
            candidate, FuturesAccountState(D("19"), D("20")), rules()).reason)

    def test_leverage_ceiling_and_minimum_notional_reject(self):
        tiny_stop = TradeCandidate(signal(stop="99.99"))
        result = evaluate_futures_risk(tiny_stop, self.account, rules(),
            FuturesRiskPolicy(leverage_ceiling=5, emergency_leverage_ceiling=10))
        self.assertFalse(result.approved)
        self.assertIn("configured ceiling", result.reason)
        result = evaluate_futures_risk(TradeCandidate(signal()), self.account,
                                       rules(minimum_notional="100"))
        self.assertFalse(result.approved)
        self.assertIn("minimum notional", result.reason)


class PaperExecutionTests(unittest.TestCase):
    def approval(self):
        return FuturesRiskDecision(True, "approved", D(".2"), 2, D("20"),
                                   D("10"), D(".2"), D("50"), "qv1-test")

    def test_stop_wins_when_stop_and_target_touch_same_candle(self):
        engine = FuturesPaperExecution(D("20"))
        engine.open(TradeCandidate(signal()), self.approval())
        closed = engine.process_candle(candle(1, "100", "103", "98"))
        self.assertEqual(closed.exit_reason, "stop")
        self.assertLess(closed.pnl, 0)
        self.assertIsNone(engine.position)

    def test_duplicate_order_id_is_rejected(self):
        engine = FuturesPaperExecution(D("20"))
        candidate = TradeCandidate(signal())
        engine.open(candidate, self.approval())
        engine.close_at(D("100"), START)
        with self.assertRaisesRegex(PaperExecutionError, "duplicate"):
            engine.open(candidate, self.approval())


class ReplayVerticalTests(unittest.TestCase):
    def test_market_to_alpha_to_risk_to_paper_exit_and_metrics(self):
        config = load_futures_config(Path("configs/futures.toml"))
        candles = [candle(i) for i in range(30)]
        candles.append(candle(30, "104", "105", "99"))
        candles.append(candle(31, "110", "111", "103", "104"))
        metrics, trader = run_futures_replay(candles, config, rules())
        self.assertEqual(metrics["number_of_trades"], 1)
        self.assertEqual(metrics["wins"], 1)
        self.assertGreater(D(metrics["ending_equity"]), D("20"))
        self.assertTrue(any(d.get("execution") for d in trader.decisions))
        self.assertIsNone(trader.execution.position)


if __name__ == "__main__":
    unittest.main()

