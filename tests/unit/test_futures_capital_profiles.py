"""Capital-profile invariants for the autonomous Futures paper runtime."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
import unittest

from quantos.application.futures_trader import (
    AutonomousFuturesPaperTrader, run_futures_replay,
)
from quantos.domain.alpha.futures import (
    Direction, MarketRegime, StrategySignal, TradeCandidate,
)
from quantos.domain.market_data import Candle
from quantos.domain.market_data.futures import FuturesSymbolRules
from quantos.domain.risk.futures import (
    FuturesAccountState, assess_candidate_economics, evaluate_futures_risk,
)
from quantos.infrastructure.configuration.futures import load_futures_config


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
PROFILE_PATHS = (
    Path("configs/futures.toml"),
    Path("configs/futures_50usdt.toml"),
    Path("configs/futures_100usdt.toml"),
)


def rules() -> FuturesSymbolRules:
    return FuturesSymbolRules(
        symbol="BTCUSDT",
        status="TRADING",
        minimum_price=D(".1"),
        maximum_price=D("1000000"),
        price_tick=D(".1"),
        minimum_quantity=D(".001"),
        maximum_quantity=D("1000"),
        quantity_step=D(".001"),
        minimum_notional=D("5"),
    )


def btc_candidate() -> TradeCandidate:
    return TradeCandidate(StrategySignal(
        timestamp=START,
        symbol="BTCUSDT",
        direction=Direction.LONG,
        strategy_id="trend_continuation",
        regime=MarketRegime.TREND_UP,
        entry=D("60000"),
        stop=D("59750"),
        target=D("61000"),
        strength=D(".7"),
        evidence=("capital-profile fixture",),
        rationale="fixture",
        timeframe="1m",
        higher_timeframe_context=(),
        signal_id="capital-profile-fixture",
        atr=D("500"),
    ))


def candle(minute: int) -> Candle:
    opened = START + timedelta(minutes=minute)
    return Candle(
        symbol="BTCUSDT",
        interval="1m",
        open_time=opened,
        close_time=opened + timedelta(minutes=1) - timedelta(milliseconds=1),
        open=D("60000"),
        high=D("60010"),
        low=D("59990"),
        close=D("60000"),
        volume=D("1"),
        quote_volume=D("60000"),
        trade_count=1,
    )


class FuturesCapitalProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.configs = tuple(load_futures_config(path) for path in PROFILE_PATHS)

    def test_profiles_change_only_starting_equity(self):
        baseline, fifty, hundred = self.configs
        self.assertEqual(
            (baseline.starting_equity, fifty.starting_equity, hundred.starting_equity),
            (D("20"), D("50"), D("100")),
        )
        self.assertEqual(
            replace(fifty, starting_equity=baseline.starting_equity), baseline
        )
        self.assertEqual(
            replace(hundred, starting_equity=baseline.starting_equity), baseline
        )
        self.assertEqual(
            tuple(config.risk.leverage_ceiling for config in self.configs),
            (5, 5, 5),
        )

    def test_risk_budgets_maximum_risk_and_usable_margin_scale_by_equity(self):
        expected = (
            (D(".2"), D(".4"), D("16")),
            (D(".5"), D("1"), D("40")),
            (D("1"), D("2"), D("80")),
        )
        for config, (normal_risk, maximum_risk, usable_margin) in zip(
            self.configs, expected, strict=True
        ):
            with self.subTest(equity=config.starting_equity):
                policy = config.risk
                self.assertEqual(
                    config.starting_equity * policy.risk_fraction, normal_risk
                )
                self.assertEqual(
                    config.starting_equity * policy.maximum_risk_fraction,
                    maximum_risk,
                )
                self.assertEqual(
                    config.starting_equity * policy.margin_fraction, usable_margin
                )

    def test_minimum_btc_trade_fails_at_twenty_and_passes_higher_profiles(self):
        results = tuple(
            assess_candidate_economics(
                btc_candidate(),
                FuturesAccountState(config.starting_equity, config.starting_equity),
                rules(),
                config.risk,
            )
            for config in self.configs
        )
        self.assertFalse(results[0].approved)
        self.assertEqual(results[0].rejection_category, "minimum_executable_risk")
        self.assertTrue(results[1].approved)
        self.assertTrue(results[2].approved)
        self.assertLessEqual(results[1].leverage_required, 5)
        self.assertLessEqual(results[2].leverage_required, 5)

    def test_current_equity_compounds_risk_after_profit_and_loss(self):
        config = self.configs[1]

        def next_budget_after_realized_close(close: D) -> tuple[D, D]:
            trader = AutonomousFuturesPaperTrader(config, rules())
            opening_account = trader._account_state(D("60000"))
            approval = evaluate_futures_risk(
                btc_candidate(), opening_account, rules(), config.risk
            )
            self.assertTrue(approval.approved)
            trader.execution.open(btc_candidate(), approval)
            trader.execution.close_at(close, START + timedelta(minutes=1))
            current_account = trader._account_state(close)
            next_economics = assess_candidate_economics(
                btc_candidate(), current_account, rules(), config.risk
            )
            return current_account.equity, next_economics.risk_budget

        losing_equity, losing_budget = next_budget_after_realized_close(D("59900"))
        profitable_equity, profitable_budget = next_budget_after_realized_close(
            D("60100")
        )
        self.assertLess(losing_equity, config.starting_equity)
        self.assertGreater(profitable_equity, config.starting_equity)
        self.assertEqual(losing_budget, losing_equity * D(".01"))
        self.assertEqual(profitable_budget, profitable_equity * D(".01"))
        self.assertLess(losing_budget, profitable_budget)

    def test_final_risk_remains_authoritative_after_economics_passes(self):
        config = self.configs[1]
        account = FuturesAccountState(D("50"), D("50"), has_position=True)
        economics = assess_candidate_economics(
            btc_candidate(), account, rules(), config.risk
        )
        decision = evaluate_futures_risk(
            btc_candidate(), account, rules(), config.risk
        )
        self.assertTrue(economics.approved)
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "one-position rule")

    def test_all_profiles_are_accepted_by_replay_and_live_paper_engine(self):
        for config in self.configs:
            with self.subTest(equity=config.starting_equity):
                metrics, replay_trader = run_futures_replay(
                    (candle(0),), config, rules()
                )
                paper_trader = AutonomousFuturesPaperTrader(config, rules())
                self.assertEqual(metrics["starting_equity"], str(config.starting_equity))
                self.assertEqual(
                    replay_trader.execution.starting_equity, config.starting_equity
                )
                self.assertEqual(
                    paper_trader.execution.starting_equity, config.starting_equity
                )

    def test_futures_business_logic_has_no_fixed_twenty_dollar_equity(self):
        business_files = (
            Path("src/quantos/application/futures_trader.py"),
            Path("src/quantos/domain/risk/futures.py"),
            Path("src/quantos/domain/execution/futures_paper.py"),
        )
        forbidden = (
            'Decimal("20")',
            "Decimal('20')",
            "equity == 20",
            "equity = 20",
            "starting_equity == 20",
            "starting_equity = 20",
        )
        for path in business_files:
            source = path.read_text(encoding="utf-8")
            for literal in forbidden:
                with self.subTest(path=path, literal=literal):
                    self.assertNotIn(literal, source)


if __name__ == "__main__":
    unittest.main()
