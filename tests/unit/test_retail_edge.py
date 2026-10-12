from dataclasses import replace
from decimal import Decimal as D
import unittest

from quantos.domain.alpha.retail_edge import RetailEdgeAlpha
from quantos.domain.features.intelligence import IntelligenceFeatures
from tests.unit.test_hft_paper import feature
from tests.unit.test_retail_intelligence import quote


class RetailEdgeTests(unittest.TestCase):
    def setUp(self):
        self.alpha = RetailEdgeAlpha()
        self.f = replace(feature(), mid_price=D("100"), static_obi=D(".3"), signed_trade_flow=D("2"))
        self.e = IntelligenceFeatures(D("50"), "valid", None, "missing_liquidation", 5_900_000_000)
        self.alpha.observe(self.f, self.e, 1_000_000_000)

    def test_lead_lag_confirmation_and_uncertainty(self):
        decisions = self.alpha.observe(self.f, self.e, 6_000_000_000)
        self.assertEqual(decisions[0].reason, "confirmed")
        self.assertEqual(decisions[0].signal.uncertainty_bps, D("25"))
        self.assertEqual(decisions[1].reason, "missing_liquidation")

    def test_leader_future_and_missing_fail_closed(self):
        decisions = self.alpha.observe(self.f, replace(self.e, leader_available_ns=6_000_000_000), 6_000_000_000)
        self.assertIsNone(decisions[0].signal)
        self.assertEqual(decisions[0].reason, "leader_not_available_before_target")
        decisions = self.alpha.observe(self.f, replace(self.e, leader_reason="stale_observation", leader_return_bps=None), 6_100_000_000)
        self.assertEqual(decisions[0].reason, "stale_observation")

    def test_wrong_flow_and_non_leading_move(self):
        d = self.alpha.observe(replace(self.f, signed_trade_flow=D("-1")), self.e, 6_000_000_000)
        self.assertEqual(d[0].reason, "flow_or_imbalance_not_confirmed")
        d = self.alpha.observe(self.f, replace(self.e, leader_return_bps=D("1")), 6_100_000_000)
        self.assertEqual(d[0].reason, "no_leading_displacement")

    def test_liquidation_requires_size_response_and_confirmation(self):
        event = replace(quote(5_900_000_000), kind="liquidation", price=D("100"), quantity=D("100"), direction=1, integrity="sampled_not_complete")
        e = replace(self.e, liquidation=event, liquidation_reason="valid")
        d = self.alpha.observe(replace(self.f, mid_price=D("100.5")), e, 6_000_000_000)
        self.assertEqual(d[1].reason, "confirmed")
        self.assertEqual(d[1].signal.strategy_id, "Q04-LD-v1")

    def test_warmup_regime_and_clock_regression(self):
        d = self.alpha.observe(self.f, self.e, 2_000_000_000)
        self.assertTrue(all(x.reason == "local_warmup_5s" for x in d))
        d = self.alpha.observe(replace(self.f, spread_bps=D("11")), self.e, 6_000_000_000)
        self.assertTrue(all(x.reason == "dislocated_or_wide_spread_regime" for x in d))
        with self.assertRaises(ValueError):
            self.alpha.observe(self.f, self.e, 5_000_000_000)

class EconomicControlsTests(unittest.TestCase):
    def setUp(self):
        from quantos.domain.risk.retail import RetailRiskEngine, RetailRiskPolicy
        from tests.unit.test_hft_paper import fees, rules
        self.policy = RetailRiskPolicy()
        self.risk = RetailRiskEngine(self.policy, fees(), rules())

    def test_uncertainty_funding_daily_loss_and_emergency_are_final(self):
        from tests.unit.test_retail_paper import signal
        f = feature()
        uncertain = replace(signal(expected="20"), uncertainty_bps=D("10"))
        self.assertFalse(self.risk.evaluate(uncertain, f, D("200"), D("200"), False).approved)
        self.assertFalse(self.risk.evaluate(signal(), f, D("200"), D("200"), False, funding_bps=D("30")).approved)
        daily = self.risk.evaluate(signal(), f, D("200"), D("200"), False, daily_start_equity=D("210"))
        self.assertEqual(daily.reason, "daily_loss_stop")
        emergency = self.risk.evaluate(signal(), f, D("200"), D("200"), False, emergency_risk_off=True)
        self.assertEqual(emergency.reason, "emergency_risk_off")

    def test_collateral_proxy_keeps_stop_inside_unlevered_envelope(self):
        from tests.unit.test_retail_paper import signal
        decision = self.risk.evaluate(signal(), feature(), D("200"), D("200"), False)
        self.assertTrue(decision.approved)
        self.assertGreaterEqual(decision.conservative_collateral_buffer_bps, D("9900"))
        self.assertGreater(decision.conservative_collateral_buffer_bps,
                           decision.hurdle_bps + self.policy.stop_loss_bps + self.policy.stop_uncertainty_bps)

    def test_nonfinite_and_liquidity_fail_closed(self):
        from tests.unit.test_retail_paper import signal
        self.assertEqual(self.risk.evaluate(signal(), feature(), D("NaN"), D("200"), False).reason, "nonfinite_risk_input")
        thin = replace(feature(), best_ask_quantity=D("0.00000001"))
        self.assertFalse(self.risk.evaluate(signal(), thin, D("200"), D("200"), False).approved)

    def test_funding_debits_once_and_roundtrip_reconciles(self):
        from tests.unit.test_retail_paper import RetailPaperTests
        from quantos.domain.market_data.hft import MarkPriceEvent
        from tests.unit.test_hft_paper import at
        fixture = RetailPaperTests()
        fixture.setUp()
        fixture._entry()
        event = MarkPriceEvent("BTCUSDC", D("101"), D(".001"), at(3), at(2), at(2), 2_000_000_000)
        fixture.account.observe_funding(event)
        fixture.account.settle_funding(at(3))
        once = fixture.account.total_funding
        fixture.account.observe_funding(replace(event, exchange_time=at(3)))
        fixture.account.settle_funding(at(3))
        self.assertEqual(fixture.account.total_funding, once)
        self.assertGreater(once, 0)
        fixture._exit()
        self.assertEqual(fixture.account.balance - 200, fixture.account.realized_net_pnl)
