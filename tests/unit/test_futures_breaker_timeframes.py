"""Breaker lifecycle and multi-timeframe Futures acceptance tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
import unittest
from unittest.mock import patch

from quantos.application.futures_trader import (
    AutonomousFuturesPaperTrader, FuturesTraderConfig, run_futures_replay,
)
from quantos.domain.alpha.futures import (
    Direction, MarketRegime, RegimeState, StrategySignal, TradeCandidate,
    classify_regime, evaluate_strategies, select_candidate,
)
from quantos.domain.execution.futures_paper import FuturesPaperPolicy
from quantos.domain.market_data import Candle
from quantos.domain.market_data.futures import parse_usdm_exchange_info
from quantos.domain.market_data.timeframes import (
    CompletedTimeframeAggregator, SUPPORTED_SIGNAL_TIMEFRAMES,
)
from quantos.domain.risk.futures import (
    ConsecutiveLossBreakerState, FuturesAccountState, FuturesRiskPolicy,
    advance_consecutive_loss_breaker, evaluate_futures_risk,
    record_closed_trade,
)
from quantos.infrastructure.configuration.futures import load_futures_config


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def one_minute(minute, *, open_="100", high="100.5", low="99.5",
               close="100", volume="1", quote_volume="100", trades=1):
    opened = START + timedelta(minutes=minute)
    return Candle(
        "BTCUSDT", "1m", opened,
        opened + timedelta(minutes=1) - timedelta(milliseconds=1),
        D(open_), D(high), D(low), D(close), D(volume), D(quote_volume), trades,
    )


def rules():
    return parse_usdm_exchange_info({"symbols": [{
        "symbol": "BTCUSDT", "status": "TRADING", "contractType": "PERPETUAL",
        "filters": [
            {"filterType": "PRICE_FILTER", "minPrice": ".1",
             "maxPrice": "1000000", "tickSize": ".1"},
            {"filterType": "LOT_SIZE", "minQty": ".001",
             "maxQty": "1000", "stepSize": ".001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ],
    }]})


def strategy_signal(timestamp=START, timeframe="1m", direction=Direction.LONG,
                    strength=".70", strategy_id="fixture"):
    entry = D("100")
    stop = D("99") if direction is Direction.LONG else D("101")
    target = D("102") if direction is Direction.LONG else D("98")
    return StrategySignal(
        timestamp=timestamp,
        symbol="BTCUSDT",
        direction=direction,
        strategy_id=strategy_id,
        regime=MarketRegime.TREND_UP,
        entry=entry,
        stop=stop,
        target=target,
        strength=D(strength),
        evidence=("fixture",),
        rationale="fixture",
        timeframe=timeframe,
        higher_timeframe_context=(),
        signal_id=f"BTCUSDT|{timeframe}|{strategy_id}|{direction.value}|{timestamp.isoformat()}",
    )


def trader_config(*, timeframes=("1m",), cooldown=2, daily=".50"):
    return FuturesTraderConfig(
        symbol="BTCUSDT",
        starting_equity=D("20"),
        lookback=120,
        timeframes=timeframes,
        risk=FuturesRiskPolicy(
            daily_loss_fraction=D(daily),
            consecutive_loss_limit=3,
            consecutive_loss_cooldown_minutes=cooldown,
            consecutive_loss_reset_on_utc_day=True,
        ),
        execution=FuturesPaperPolicy(),
    )


class ConsecutiveLossBreakerTests(unittest.TestCase):
    def setUp(self):
        self.policy = FuturesRiskPolicy(
            consecutive_loss_limit=3,
            consecutive_loss_cooldown_minutes=60,
            consecutive_loss_reset_on_utc_day=False,
        )

    def trip(self, start=START):
        state = ConsecutiveLossBreakerState()
        for offset in range(3):
            state = record_closed_trade(
                state, D("-1"), start + timedelta(minutes=offset), self.policy
            )
        return state

    def test_three_losses_trip_and_cooldown_blocks(self):
        state = self.trip()
        self.assertTrue(state.active)
        self.assertEqual(state.trigger_count, 1)
        self.assertEqual(state.consecutive_losses, 3)
        account = FuturesAccountState(
            equity=D("20"), day_start_equity=D("20"),
            consecutive_losses=state.consecutive_losses,
            consecutive_loss_breaker_active=state.active,
        )
        decision = evaluate_futures_risk(
            TradeCandidate(strategy_signal()), account, rules(), self.policy
        )
        self.assertEqual(decision.reason, "consecutive-loss circuit breaker")
        before_expiry = advance_consecutive_loss_breaker(
            state, START + timedelta(minutes=61), self.policy
        )
        self.assertTrue(before_expiry.active)

    def test_cooldown_expiry_resets_and_prevents_permanent_lockout(self):
        state = self.trip()
        reset = advance_consecutive_loss_breaker(
            state, START + timedelta(minutes=62), self.policy
        )
        self.assertFalse(reset.active)
        self.assertEqual(reset.consecutive_losses, 0)
        self.assertEqual(reset.reset_count, 1)
        self.assertEqual(reset.disabled_duration_seconds, 3600)
        decision = evaluate_futures_risk(
            TradeCandidate(strategy_signal()),
            FuturesAccountState(D("20"), D("20")),
            rules(), self.policy,
        )
        self.assertTrue(decision.approved, decision.reason)

    def test_next_utc_day_reset_when_enabled(self):
        policy = FuturesRiskPolicy(
            consecutive_loss_limit=3,
            consecutive_loss_cooldown_minutes=1440,
            consecutive_loss_reset_on_utc_day=True,
        )
        start = datetime(2026, 1, 1, 23, 50, tzinfo=timezone.utc)
        state = ConsecutiveLossBreakerState()
        for offset in range(3):
            state = record_closed_trade(
                state, D("-1"), start + timedelta(minutes=offset), policy
            )
        reset = advance_consecutive_loss_breaker(
            state, datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc), policy
        )
        self.assertFalse(reset.active)
        self.assertEqual(reset.reset_count, 1)

    def test_win_resets_streak_and_breakeven_preserves_it(self):
        state = record_closed_trade(
            ConsecutiveLossBreakerState(), D("-1"), START, self.policy
        )
        state = record_closed_trade(
            state, D("0"), START + timedelta(minutes=1), self.policy
        )
        self.assertEqual(state.consecutive_losses, 1)
        state = record_closed_trade(
            state, D("1"), START + timedelta(minutes=2), self.policy
        )
        self.assertEqual(state.consecutive_losses, 0)

    def test_daily_loss_breaker_has_precedence(self):
        account = FuturesAccountState(
            equity=D("19"), day_start_equity=D("20"),
            consecutive_losses=3,
            consecutive_loss_breaker_active=True,
        )
        decision = evaluate_futures_risk(
            TradeCandidate(strategy_signal()), account, rules(), self.policy
        )
        self.assertEqual(decision.reason, "daily-loss circuit breaker")

    def test_default_threshold_and_finite_cooldown_are_configured(self):
        config = load_futures_config(Path("configs/futures.toml"))
        self.assertEqual(config.risk.consecutive_loss_limit, 3)
        self.assertGreater(config.risk.consecutive_loss_cooldown_minutes, 0)
        self.assertEqual(config.timeframes, SUPPORTED_SIGNAL_TIMEFRAMES)


class TimeframeAggregationTests(unittest.TestCase):
    def test_all_supported_timeframes_emit_once_with_exact_ohlcv(self):
        for timeframe in SUPPORTED_SIGNAL_TIMEFRAMES:
            with self.subTest(timeframe=timeframe):
                minutes = 60 if timeframe == "1h" else int(timeframe[:-1])
                aggregator = CompletedTimeframeAggregator(timeframe)
                output = None
                for index in range(minutes):
                    output = aggregator.update(one_minute(
                        index,
                        open_=str(100 + index),
                        high=str(101 + index),
                        low=str(99 + index),
                        close=str(D(100 + index) + D(".5")),
                        volume=str(index + 1),
                        quote_volume=str((index + 1) * 100),
                        trades=index + 1,
                    ))
                    if index < minutes - 1:
                        self.assertIsNone(output)
                self.assertIsNotNone(output)
                self.assertEqual(output.interval, timeframe)
                self.assertEqual(output.open_time, START)
                self.assertEqual(output.open, D("100"))
                self.assertEqual(output.high, D(100 + minutes))
                self.assertEqual(output.low, D("99"))
                self.assertEqual(output.close, D(100 + minutes - 1) + D(".5"))
                self.assertEqual(
                    output.volume, sum((D(i + 1) for i in range(minutes)), D(0))
                )

    def test_utc_alignment_and_incomplete_bucket_exclusion(self):
        aggregator = CompletedTimeframeAggregator("5m")
        for minute in (1, 2, 3, 4):
            self.assertIsNone(aggregator.update(one_minute(minute)))
        output = None
        for minute in range(5, 10):
            output = aggregator.update(one_minute(minute))
            if minute < 9:
                self.assertIsNone(output)
        self.assertEqual(output.open_time, START + timedelta(minutes=5))
        self.assertEqual(output.close_time, one_minute(9).close_time)

        gap = CompletedTimeframeAggregator("5m")
        for minute in (0, 1, 3, 4):
            self.assertIsNone(gap.update(one_minute(minute)))
        self.assertGreater(gap.incomplete_bucket_count, 0)
        for minute in range(5, 9):
            self.assertIsNone(gap.update(one_minute(minute)))
        self.assertIsNotNone(gap.update(one_minute(9)))

        opened = START + timedelta(minutes=10)
        incomplete = Candle(
            "BTCUSDT", "1m", opened, opened + timedelta(seconds=30),
            D("100"), D("101"), D("99"), D("100"), D("1"), D("100"), 1,
        )
        with self.assertRaisesRegex(ValueError, "complete"):
            gap.update(incomplete)


class MultiTimeframeDecisionTests(unittest.TestCase):
    def test_duplicate_setup_across_timeframes_selects_one(self):
        timestamp = one_minute(59).close_time
        one = strategy_signal(timestamp, "1m")
        five = strategy_signal(timestamp, "5m")
        selection = select_candidate((one, five))
        self.assertEqual(selection.direction, Direction.LONG)
        self.assertEqual(selection.candidate.signal.timeframe, "5m")
        self.assertEqual(len(selection.signals), 1)

    def test_conflicting_timeframes_hold_and_empty_is_no_trade(self):
        timestamp = one_minute(59).close_time
        long = strategy_signal(timestamp, "5m", Direction.LONG, ".70")
        short = strategy_signal(timestamp, "15m", Direction.SHORT, ".66")
        self.assertEqual(select_candidate((long, short)).direction, Direction.HOLD)
        self.assertEqual(select_candidate(()).direction, Direction.HOLD)

    def test_higher_timeframe_context_adjusts_score_explicitly(self):
        candles = tuple(
            one_minute(i, high="101", low="99", close="100") for i in range(30)
        ) + (one_minute(30, high="105", low="99", close="104"),)
        regime = classify_regime(candles)
        signals = evaluate_strategies(
            candles,
            regime,
            timeframe="5m",
            higher_context=(
                ("15m", MarketRegime.TREND_UP),
                ("1h", MarketRegime.TREND_UP),
            ),
        )
        breakout = next(item for item in signals if item.strategy_id == "breakout_expansion")
        self.assertEqual(breakout.strength, D(".86"))
        self.assertEqual(
            breakout.higher_timeframe_context,
            ("15m:TREND_UP", "1h:TREND_UP"),
        )


class ReplayLifecycleTests(unittest.TestCase):
    @staticmethod
    def always_signal(candles, state, timeframe="1m", higher_context=()):
        last = candles[-1]
        return (strategy_signal(last.close_time, timeframe),)

    def test_replay_resumes_after_finite_breaker_cooldown(self):
        trader = AutonomousFuturesPaperTrader(
            trader_config(cooldown=2), rules()
        )
        with patch(
            "quantos.application.futures_trader.evaluate_strategies",
            side_effect=self.always_signal,
        ):
            for minute in range(37):
                if minute in (30, 32, 34):
                    item = one_minute(minute, low="98")
                else:
                    item = one_minute(minute)
                trader.on_candle(item)
        metrics = trader.finish()
        self.assertEqual(metrics["consecutive_loss_breaker_trigger_count"], 1)
        self.assertEqual(metrics["consecutive_loss_breaker_reset_count"], 1)
        self.assertGreaterEqual(
            metrics["consecutive_loss_breaker_blocked_candidates"], 1
        )
        self.assertGreaterEqual(metrics["selected_trades_per_timeframe"]["1m"], 4)

    def test_multiple_timeframes_trade_in_one_replay_with_attribution(self):
        config = trader_config(timeframes=("1m", "3m"), cooldown=2)

        def scheduled(candles, state, timeframe="1m", higher_context=()):
            last = candles[-1]
            if (
                timeframe == "1m"
                and last.close_time == one_minute(90).close_time
            ) or (
                timeframe == "3m"
                and last.close_time == one_minute(95).close_time
            ):
                return (strategy_signal(last.close_time, timeframe),)
            return ()

        candles = []
        for minute in range(97):
            if minute == 91:
                candles.append(one_minute(minute, low="98"))
            elif minute == 96:
                candles.append(one_minute(minute, high="103"))
            else:
                candles.append(one_minute(minute))
        with patch(
            "quantos.application.futures_trader.evaluate_strategies",
            side_effect=scheduled,
        ):
            metrics, _ = run_futures_replay(candles, config, rules())
        self.assertEqual(metrics["number_of_trades"], 2)
        self.assertEqual(metrics["timeframe_attribution"]["1m"]["trades"], 1)
        self.assertEqual(metrics["timeframe_attribution"]["3m"]["trades"], 1)
        self.assertEqual(metrics["candidate_count_per_timeframe"]["1m"], 1)
        self.assertEqual(metrics["candidate_count_per_timeframe"]["3m"], 1)
        self.assertEqual(metrics["selected_trades_per_timeframe"]["1m"], 1)
        self.assertEqual(metrics["selected_trades_per_timeframe"]["3m"], 1)

    def test_daily_loss_trigger_is_reported_and_blocks_after_loss(self):
        config = trader_config(daily=".001")
        trader = AutonomousFuturesPaperTrader(config, rules())
        with patch(
            "quantos.application.futures_trader.evaluate_strategies",
            side_effect=self.always_signal,
        ):
            for minute in range(32):
                trader.on_candle(
                    one_minute(minute, low="98")
                    if minute == 30 else one_minute(minute)
                )
        metrics = trader.finish()
        self.assertEqual(metrics["daily_loss_breaker_trigger_count"], 1)
        self.assertGreaterEqual(
            metrics["rejections"].get("daily-loss circuit breaker", 0), 1
        )


if __name__ == "__main__":
    unittest.main()

