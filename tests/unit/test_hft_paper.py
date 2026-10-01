from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

import pyarrow.parquet as pq

from quantos.application.hft_trader import select_hft_contract
from quantos.domain.alpha.hft import (
    HftAlphaPolicy,
    HftIntentAction,
    HftQuoteIntent,
    HftSide,
    VampOrderFlowAlpha,
)
from quantos.domain.evaluation.hft import HftEvaluation
from quantos.domain.execution.hft_paper import HftExecutionError, HftPaperAccount
from quantos.domain.features.hft import HftFeatureEngine, HftFeaturePolicy, HftFeatures
from quantos.domain.market_data.futures import FuturesSymbolRules
from quantos.domain.market_data.hft import (
    AggregateTrade,
    DepthDelta,
    DepthSnapshot,
    HftBookError,
    L2OrderBook,
)
from quantos.domain.risk.hft import (
    HftFeeSchedule,
    HftRiskDecision,
    HftRiskEngine,
    HftRiskPolicy,
    HftRiskState,
    cost_hurdle,
)
from quantos.infrastructure.binance.hft import BinanceHftStream, normalize_hft_message
from quantos.infrastructure.configuration.hft import load_hft_config
from quantos.infrastructure.storage.hft_events import (
    HftEventRecorder,
    export_hftbacktest_research,
)
from quantos.infrastructure.storage.hft_paper import (
    HftPaperStateError,
    HftPaperStateStore,
)


D = Decimal
BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)


def at(seconds: int) -> datetime:
    return BASE + timedelta(seconds=seconds)


def rules(symbol: str = "BTCUSDC") -> FuturesSymbolRules:
    return FuturesSymbolRules(
        symbol=symbol,
        status="TRADING",
        quantity_step=D(".001"),
        minimum_quantity=D(".001"),
        maximum_quantity=D("10"),
        price_tick=D(".1"),
        minimum_price=D("1"),
        maximum_price=D("1000000"),
        minimum_notional=D("5"),
    )


def snapshot(symbol: str = "BTCUSDC") -> DepthSnapshot:
    return DepthSnapshot(
        symbol=symbol,
        last_update_id=100,
        bids=((D("100"), D("10")), (D("99"), D("4"))),
        asks=((D("101"), D("5")), (D("102"), D("8"))),
        exchange_time=at(0),
        received_at=at(0),
    )


def delta(
    *,
    first: int = 100,
    final: int = 101,
    previous: int = 99,
    bids=(),
    asks=(),
    second: int = 1,
) -> DepthDelta:
    return DepthDelta(
        symbol="BTCUSDC",
        first_update_id=first,
        final_update_id=final,
        previous_final_update_id=previous,
        bids=tuple(bids),
        asks=tuple(asks),
        exchange_time=at(second),
        received_at=at(second),
    )


def book() -> L2OrderBook:
    result = L2OrderBook("BTCUSDC")
    result.bootstrap(snapshot(), (delta(),))
    return result


def feature(alpha: str = "10", spread: str = "1") -> HftFeatures:
    return HftFeatures(
        timestamp=at(2),
        book_update_id=101,
        best_bid=D("100"),
        best_ask=D("101"),
        best_bid_quantity=D("10"),
        best_ask_quantity=D("5"),
        mid_price=D("100.5"),
        spread_bps=D(spread),
        static_obi=D(".333"),
        standardized_obi=D("1.5"),
        vamp_price=D("100.6"),
        microprice=D("100.66"),
        alpha_bps=D(alpha),
        signed_trade_flow=D("2"),
        realized_volatility_bps=D("1"),
        inventory_quantity=D("0"),
        feed_latency_ms=D("2"),
        event_age_ms=D("1"),
    )


def fees(symbol: str = "BTCUSDC") -> HftFeeSchedule:
    return (
        HftFeeSchedule(D("0"), D(".0004"))
        if symbol == "BTCUSDC"
        else HftFeeSchedule(D(".0002"), D(".0005"))
    )


def approved(quantity: str = ".05") -> HftRiskDecision:
    hurdle = cost_hurdle(fees(), feature(), HftRiskPolicy())
    return HftRiskDecision(
        True, "approved", D(quantity), D(quantity) * D("100"),
        D(".05"), hurdle,
    )


def trade(
    quantity: str,
    *,
    price: str = "100",
    buyer_is_maker: bool = True,
    second: int = 3,
) -> AggregateTrade:
    return AggregateTrade(
        symbol="BTCUSDC",
        aggregate_trade_id=second,
        price=D(price),
        quantity=D(quantity),
        buyer_is_maker=buyer_is_maker,
        exchange_time=at(second),
        received_at=at(second),
    )


class OrderBookTests(unittest.TestCase):
    def test_bootstrap_applies_bridge_and_ignores_duplicate(self) -> None:
        target = book()
        self.assertTrue(target.valid)
        self.assertEqual(target.last_update_id, 101)
        self.assertFalse(target.apply(delta(first=100, final=101, previous=99)))

    def test_sequence_gap_invalidates_until_fresh_snapshot(self) -> None:
        target = book()
        with self.assertRaisesRegex(HftBookError, "sequence gap"):
            target.apply(delta(first=103, final=103, previous=102))
        self.assertFalse(target.valid)
        with self.assertRaisesRegex(HftBookError, "freshly bootstrapped"):
            target.apply(delta(first=102, final=102, previous=101))

    def test_crossed_delta_fails_closed(self) -> None:
        target = book()
        with self.assertRaisesRegex(HftBookError, "crossed"):
            target.apply(delta(
                first=102, final=102, previous=101,
                bids=((D("102"), D("1")),),
            ))
        self.assertFalse(target.valid)

    def test_stale_book_trips_kill_state(self) -> None:
        target = book()
        with self.assertRaisesRegex(HftBookError, "stale"):
            target.require_fresh(at(10), timedelta(seconds=1))
        self.assertFalse(target.valid)
        self.assertEqual(target.invalid_reason, "stale_stream")


class FeatureAndAlphaTests(unittest.TestCase):
    def test_vamp_obi_microprice_and_trade_flow_are_causal(self) -> None:
        engine = HftFeatureEngine(HftFeaturePolicy(
            depth_levels=1, obi_window=3,
            trade_flow_seconds=D("5"), volatility_seconds=D("10"),
        ))
        engine.on_trade(trade("2", buyer_is_maker=False, second=1))
        engine.on_trade(trade(".5", buyer_is_maker=True, second=2))
        result = engine.compute(book(), at(2))
        expected = (D("100") * D("5") + D("101") * D("10")) / D("15")
        self.assertEqual(result.vamp_price, expected)
        self.assertEqual(result.microprice, expected)
        self.assertEqual(result.static_obi, D("5") / D("15"))
        self.assertEqual(result.signed_trade_flow, D("1.5"))

    def test_alpha_reversal_cancels_existing_quote(self) -> None:
        alpha = VampOrderFlowAlpha("BTCUSDC", HftAlphaPolicy())
        intent = alpha.decide(feature("-10"), D("6"), working_side=HftSide.BUY)
        self.assertEqual(intent.action, HftIntentAction.CANCEL)
        self.assertEqual(intent.rationale, "alpha_reversal")

    def test_alpha_mean_reversion_requests_maker_first_inventory_exit(self) -> None:
        alpha = VampOrderFlowAlpha("BTCUSDC", HftAlphaPolicy())
        intent = alpha.decide(
            feature("1"), D("6"), inventory_quantity=D(".05")
        )
        self.assertEqual(intent.action, HftIntentAction.MAKER_EXIT)
        self.assertEqual(intent.side, HftSide.SELL)
        self.assertEqual(intent.price, D("101"))


class CostAndRiskTests(unittest.TestCase):
    def test_usdc_and_usdt_fee_hurdles_are_distinct(self) -> None:
        policy = HftRiskPolicy()
        usdc = cost_hurdle(fees("BTCUSDC"), feature(), policy)
        usdt = cost_hurdle(fees("BTCUSDT"), feature(), policy)
        self.assertEqual(usdc.maker_entry_bps, D("0"))
        self.assertEqual(usdc.maker_exit_bps, D("0"))
        self.assertEqual(usdc.normal_fee_bps, D("0"))
        self.assertEqual(usdc.normal_hurdle_bps, D("1"))
        self.assertEqual(usdc.emergency_taker_bps, D("4"))
        self.assertEqual(usdc.emergency_loss_hurdle_bps, D("24"))
        self.assertEqual(usdt.maker_entry_bps, D("2"))
        self.assertEqual(usdt.maker_exit_bps, D("2"))
        self.assertEqual(usdt.normal_fee_bps, D("4"))
        self.assertEqual(usdt.normal_hurdle_bps, D("5"))
        self.assertEqual(usdt.emergency_taker_bps, D("5"))
        self.assertEqual(usdt.emergency_loss_hurdle_bps, D("27"))
        self.assertGreater(usdt.admission_hurdle_bps, usdc.admission_hurdle_bps)

    def test_passive_spread_is_diagnostic_not_a_normal_cost(self) -> None:
        narrow = cost_hurdle(
            fees(), feature(spread=".1"), HftRiskPolicy()
        )
        wide = cost_hurdle(
            fees(), feature(spread="4.9"), HftRiskPolicy()
        )
        self.assertEqual(narrow.normal_hurdle_bps, wide.normal_hurdle_bps)
        self.assertEqual(narrow.normal_hurdle_bps, D("1"))
        self.assertEqual(narrow.spread_bps, D(".1"))
        self.assertEqual(wide.spread_bps, D("4.9"))

    def test_normal_hurdle_controls_alpha_and_risk_admission(self) -> None:
        hurdle = cost_hurdle(fees(), feature(), HftRiskPolicy())
        alpha = VampOrderFlowAlpha("BTCUSDC", HftAlphaPolicy())
        at_hurdle = alpha.decide(feature("1"), hurdle.normal_hurdle_bps)
        above_hurdle = alpha.decide(feature("1.01"), hurdle.normal_hurdle_bps)
        self.assertEqual(at_hurdle.action, HftIntentAction.HOLD)
        self.assertEqual(above_hurdle.action, HftIntentAction.QUOTE)
        engine = HftRiskEngine(HftRiskPolicy(), fees(), rules())
        self.assertEqual(
            engine.evaluate(
                replace(self._intent(), expected_alpha_bps=D("1")),
                feature("1"), HftRiskState(D("100")), D("100"),
            ).reason,
            "economic_hurdle",
        )
        self.assertTrue(engine.evaluate(
            replace(self._intent(), expected_alpha_bps=D("1.01")),
            feature("1.01"), HftRiskState(D("100")), D("100"),
        ).approved)

    def test_emergency_taker_fee_remains_in_worst_case_risk(self) -> None:
        large_minimum = replace(rules(), minimum_notional=D("100"))
        no_taker = HftRiskEngine(
            replace(HftRiskPolicy(), emergency_move_bps=D("0")),
            HftFeeSchedule(D("0"), D("0")),
            large_minimum,
        )
        expensive_taker = HftRiskEngine(
            replace(HftRiskPolicy(), emergency_move_bps=D("1")),
            HftFeeSchedule(D("0"), D(".01")),
            large_minimum,
        )
        state = HftRiskState(D("100"))
        self.assertTrue(no_taker.evaluate(
            self._intent(), feature(), state, D("100")
        ).approved)
        self.assertEqual(
            expensive_taker.evaluate(
                self._intent(), feature(), state, D("100")
            ).reason,
            "one_percent_account_risk",
        )

    def _intent(self) -> HftQuoteIntent:
        return HftQuoteIntent(
            HftIntentAction.QUOTE, "BTCUSDC", HftSide.BUY, D("100"),
            D("10"), at(2), 101, 2000, "test",
        )

    def test_risk_approves_smallest_valid_quantity(self) -> None:
        decision = HftRiskEngine(HftRiskPolicy(), fees(), rules()).evaluate(
            self._intent(), feature(), HftRiskState(D("100")), D("100")
        )
        self.assertTrue(decision.approved)
        self.assertEqual(decision.quantity, D(".05"))

    def test_inventory_daily_adverse_and_one_percent_breakers(self) -> None:
        engine = HftRiskEngine(HftRiskPolicy(), fees(), rules())
        self.assertEqual(
            engine.evaluate(
                self._intent(), feature(), HftRiskState(D("100")),
                D("100"), D(".01"),
            ).reason,
            "inventory_already_open",
        )
        self.assertEqual(
            engine.evaluate(
                self._intent(), feature(),
                HftRiskState(D("100"), realized_pnl=D("-3")), D("97"),
            ).reason,
            "daily_loss_breaker",
        )
        self.assertEqual(
            engine.evaluate(
                self._intent(), feature(),
                HftRiskState(D("100"), consecutive_adverse_fills=5), D("100"),
            ).reason,
            "adverse_fill_breaker",
        )
        high_loss = HftRiskEngine(
            replace(HftRiskPolicy(), emergency_move_bps=D("3000")),
            fees(), rules(),
        )
        self.assertEqual(
            high_loss.evaluate(
                self._intent(), feature(), HftRiskState(D("100")), D("100")
            ).reason,
            "one_percent_account_risk",
        )
        too_small = HftRiskEngine(
            replace(HftRiskPolicy(), maximum_inventory_notional=D("4")),
            fees(), rules(),
        )
        self.assertEqual(
            too_small.evaluate(
                self._intent(), feature(), HftRiskState(D("100")), D("100")
            ).reason,
            "inventory_notional_ceiling",
        )


class QueueExecutionTests(unittest.TestCase):
    def test_touch_is_not_fill_and_queue_must_be_consumed(self) -> None:
        account = HftPaperAccount(D("100"), fees())
        order = account.place_entry(
            approved(".05"), HftSide.BUY, D("100"), book(), at(2), at(2)
        )
        self.assertEqual(order.queue_ahead, D("10"))
        self.assertIsNone(account.on_trade(trade("10", second=3)))
        self.assertIsNone(account.inventory)
        first = account.on_trade(trade(".02", second=4))
        self.assertIsNotNone(first)
        self.assertEqual(first.quantity, D(".02"))
        second = account.on_trade(trade(".03", second=5))
        self.assertEqual(second.quantity, D(".03"))
        self.assertEqual(account.inventory_quantity, D(".05"))
        self.assertIsNone(account.working_order)

    def test_wrong_aggressor_does_not_consume_queue(self) -> None:
        account = HftPaperAccount(D("100"), fees())
        order = account.place_entry(
            approved(".05"), HftSide.BUY, D("100"), book(), at(2), at(2)
        )
        self.assertIsNone(account.on_trade(
            trade("20", buyer_is_maker=False, second=3)
        ))
        self.assertEqual(order.queue_ahead, D("10"))

    def test_post_only_rejects_cross_and_maker_taker_fees_are_separate(self) -> None:
        account = HftPaperAccount(D("100"), fees())
        with self.assertRaisesRegex(HftExecutionError, "would cross"):
            account.place_entry(
                approved(), HftSide.BUY, D("101"), book(), at(2), at(2)
            )
        account.place_entry(
            approved(".05"), HftSide.BUY, D("100"), book(), at(2), at(2)
        )
        account.on_trade(trade("10.05", second=3))
        self.assertEqual(account.maker_fees, D("0"))
        fill = account.emergency_exit(D("99"), at(4), "test")
        self.assertFalse(fill.maker)
        self.assertEqual(account.taker_fees, D(".05") * D("99") * D(".0004"))
        self.assertEqual(account.taker_exits, 1)


class EvaluationAndPersistenceTests(unittest.TestCase):
    def _maker_fill(self):
        account = HftPaperAccount(D("100"), fees())
        account.place_entry(
            approved(".05"), HftSide.BUY, D("100"), book(), at(2), at(2)
        )
        return account, account.on_trade(trade("10.05", second=3))

    def test_adverse_markouts_and_latency_percentiles(self) -> None:
        account, fill = self._maker_fill()
        evaluation = HftEvaluation(D("100"))
        evaluation.record_latency(D("1"), D("2"), D("3"))
        evaluation.register_passive_fill(
            fill, alpha_bps=D("4"), obi_z=D("2"), volatility_bps=D("1")
        )
        evaluation.on_mid(at(4), D("99"))
        report = evaluation.report(account, D("3600"))
        self.assertLess(report["markouts"]["1"]["average"], 0)
        self.assertEqual(report["latency"]["feed_ms"]["median"], D("1"))
        self.assertIn("markout_by_alpha_bucket", report)
        self.assertIn("maximum_drawdown", report)
        self.assertEqual(
            report["fill_probability_diagnostics"]["touch_equals_fill"], False
        )

    def test_funding_and_minute_sharpe_are_reported(self) -> None:
        account = HftPaperAccount(D("100"), fees())
        account.apply_funding(D("0.01"))
        evaluation = HftEvaluation(D("100"))
        evaluation.record_equity(D("100"), at(0))
        evaluation.record_equity(D("101"), at(60))
        evaluation.record_equity(D("100.5"), at(120))
        report = evaluation.report(account, D("3600"))
        self.assertEqual(report["funding"], D("0.01"))
        self.assertIsNotNone(report["sharpe"])
        self.assertEqual(report["sharpe_aggregation"], "one_minute_last_equity")

    def test_quote_diagnostics_and_alpha_distribution_are_exact(self) -> None:
        account = HftPaperAccount(D("100"), fees())
        evaluation = HftEvaluation(D("100"))
        hurdle = cost_hurdle(fees(), feature(), HftRiskPolicy())
        values = (D("-2"), D("-1"), D("1"), D("4"), D("0"))
        for value in values:
            passed = abs(value) > hurdle.normal_hurdle_bps
            evaluation.record_quote_evaluation(
                feature(str(value)),
                hurdle,
                quote_passed=passed,
                rejection_reason=(
                    None if passed else "alpha_below_normal_hurdle"
                ),
                economic_hurdle_passed=passed,
            )
        metrics = evaluation.report(account, D("1"))["quote_economics"]
        absolute = metrics["alpha_distribution"]["absolute_bps"]
        signed = metrics["alpha_distribution"]["signed_bps"]
        self.assertEqual(absolute["count"], 5)
        self.assertEqual(absolute["min"], D("0"))
        self.assertEqual(absolute["p25"], D("1"))
        self.assertEqual(absolute["median"], D("1"))
        self.assertEqual(absolute["p75"], D("2"))
        self.assertEqual(absolute["p90"], D("3.2"))
        self.assertEqual(absolute["p95"], D("3.60"))
        self.assertEqual(absolute["p99"], D("3.920"))
        self.assertEqual(absolute["max"], D("4"))
        self.assertEqual(signed, {
            "count": 5,
            "min": D("-2"),
            "median": D("0"),
            "max": D("4"),
        })
        latest = metrics["latest_candidate"]
        self.assertEqual(latest["VAMP"], D("100.6"))
        self.assertEqual(latest["abs_alpha_bps"], D("0"))
        self.assertEqual(latest["normal_fee_bps"], D("0"))
        self.assertEqual(latest["normal_hurdle_bps"], D("1"))
        self.assertEqual(latest["emergency_taker_fee_bps"], D("4"))
        self.assertEqual(latest["emergency_loss_hurdle_bps"], D("24"))
        self.assertEqual(latest["spread_bps_diagnostic_only"], D("1"))
        self.assertFalse(latest["quote_passed"])
        self.assertEqual(
            latest["rejection_reason"], "alpha_below_normal_hurdle"
        )
        self.assertEqual(metrics["funnel"]["candidate_evaluations"], 5)
        self.assertEqual(metrics["funnel"]["passed_economic_hurdle"], 2)
        self.assertEqual(
            metrics["funnel"]["rejected_alpha_below_normal_hurdle"], 3
        )

    def test_restart_cancels_quote_and_rejects_changed_fees(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hft.db"
            store = HftPaperStateStore(path)
            account, evaluation, runtime = store.reset(
                "identity", "BTCUSDC", D("100"), fees(), at(0)
            )
            account.place_entry(
                approved(), HftSide.BUY, D("100"), book(), at(2), at(2)
            )
            hurdle = cost_hurdle(fees(), feature(), HftRiskPolicy())
            evaluation.record_quote_evaluation(
                feature(".5"),
                hurdle,
                quote_passed=False,
                rejection_reason="alpha_below_normal_hurdle",
                economic_hurdle_passed=False,
            )
            runtime["last_update_id"] = 101
            runtime["book_valid"] = True
            store.save(account, evaluation, runtime, "identity", at(3))
            restored, restored_evaluation, restored_runtime = store.load(
                "identity", fees()
            )
            self.assertIsNone(restored.working_order)
            self.assertEqual(
                restored_evaluation.alpha_bps_observations, [D(".5")]
            )
            self.assertEqual(
                restored_evaluation.quote_funnel[
                    "rejected_alpha_below_normal_hurdle"
                ],
                1,
            )
            self.assertFalse(restored_runtime["book_valid"])
            self.assertIsNone(restored_runtime["last_update_id"])
            with self.assertRaisesRegex(HftPaperStateError, "fees differ"):
                store.load("identity", fees("BTCUSDT"))

    def test_recorder_and_hftbacktest_export_are_compressed_parquet(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recorder = HftEventRecorder(root, "session", batch_size=1)
            partition = recorder.append(delta(), at(2))
            self.assertIsNotNone(partition)
            self.assertEqual(pq.ParquetFile(partition).metadata.num_rows, 1)
            output = root / "export.parquet"
            export_hftbacktest_research((partition,), output)
            columns = set(pq.read_table(output).column_names)
            self.assertIn("exchange_timestamp", columns)
            self.assertIn("previous_final_update_id", columns)


class AdapterAndConfigurationTests(unittest.TestCase):
    def test_depth_trade_normalization_and_symbol_fallback(self) -> None:
        event = normalize_hft_message({
            "e": "depthUpdate", "E": 1000, "s": "BTCUSDC",
            "U": 100, "u": 101, "pu": 99,
            "b": [["100", "1"]], "a": [["101", "2"]],
        }, at(2))
        self.assertIsInstance(event, DepthDelta)
        self.assertEqual(event.previous_final_update_id, 99)
        config = load_hft_config(Path("configs/futures_hft_paper.toml"))
        info = {"symbols": [{
            "symbol": "BTCUSDT", "status": "TRADING",
            "contractType": "PERPETUAL",
            "filters": [
                {"filterType": "LOT_SIZE", "stepSize": ".001", "minQty": ".001", "maxQty": "10"},
                {"filterType": "PRICE_FILTER", "tickSize": ".1", "minPrice": "1", "maxPrice": "1000000"},
                {"filterType": "MIN_NOTIONAL", "notional": "5"},
            ],
        }]}
        self.assertEqual(select_hft_contract(info, config).symbol, "BTCUSDT")
        self.assertEqual(config.fees["BTCUSDC"].maker_rate, D("0"))
        self.assertEqual(config.fees["BTCUSDT"].maker_rate, D(".0002"))

    def test_current_binance_stream_routes_split_public_and_market_data(self) -> None:
        public, market = BinanceHftStream().urls("BTCUSDC")
        self.assertIn("/public/stream?", public)
        self.assertIn("btcusdc@depth@100ms", public)
        self.assertIn("btcusdc@bookTicker", public)
        self.assertNotIn("aggTrade", public)
        self.assertIn("/market/stream?", market)
        self.assertIn("btcusdc@aggTrade", market)
        self.assertIn("btcusdc@markPrice@1s", market)

    def test_hft_path_has_no_real_order_submission_api(self) -> None:
        account = HftPaperAccount(D("100"), fees())
        self.assertFalse(hasattr(account, "submit_exchange_order"))
        self.assertFalse(hasattr(account, "api_key"))


if __name__ == "__main__":
    unittest.main()
