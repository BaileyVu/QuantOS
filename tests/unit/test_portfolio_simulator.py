from dataclasses import replace
import unittest

from quantos.paper.research.data import Bar, FundingRate
from quantos.paper.research.simulator import HOUR, IncompleteHistory, Simulator
from quantos.paper.risk import ResearchPolicy
from quantos.paper.types import D, Instrument, Signal

START = 24 * HOUR


def inputs(symbols=("BTCUSDT",), count=30):
    bars, marks, funding, instruments = {}, {}, {}, {}
    for symbol in symbols:
        bars[symbol] = {}
        for t in range(START-HOUR, START+count*HOUR, HOUR):
            bars[symbol][t] = Bar(symbol, "1h", t, t+HOUR-1, D(100), D(101), D(99), D(100), D(10000))
        marks[symbol] = bars[symbol].copy()
        funding[symbol] = tuple(FundingRate(symbol, t, 8, D(".001"))
                                 for t in range(START-8*HOUR, START+(count+8)*HOUR, 8*HOUR))
        instruments[symbol] = Instrument(symbol, "USDT", "TRADING", D(".01"), D(".001"),
                                        D(".001"), D(10000), D(5))
    return bars, marks, funding, instruments


def signal(symbol="BTCUSDT", time=START, **kwargs):
    return replace(Signal("alt_dip_reversal", "research-1.0.0", f"{symbol}-{time}", symbol,
                          "LONG", time, time+90_000, "MARKET", D(100), D(75), None,
                          12*3600, "fixture"), **kwargs)


def run(data=None, signals=None, count=24, policy=None):
    return Simulator(*(data or inputs(count=count)), policy or ResearchPolicy()).run(
        [signal()] if signals is None else signals, start_ms=START, end_ms=START+count*HOUR)


class SimulatorTests(unittest.TestCase):
    def test_cash_fee_funding_time_exit_reconcile(self):
        result = run()
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade["exit_reason"], "TIME_EXIT")
        self.assertEqual(trade["closed_ms"], START+12*HOUR)
        self.assertEqual(trade["entry_price"], D("100.15"))
        self.assertEqual(trade["exit_price"], D("99.85"))
        self.assertEqual(trade["leverage"], 1)  # retained twice-stop liquidation buffer
        # Newly opened at funding boundary: first payment is eight hours later.
        self.assertEqual(len(result.funding_events), 1)
        self.assertEqual(trade["funding"], -trade["quantity"]*D(100)*D(".001"))
        self.assertEqual(result.final_cash, D(200)+trade["net_pnl"])
        self.assertEqual(result.points[-1][1], result.final_cash)
        self.assertEqual(result.approvals["NET_RR_NOT_APPLICABLE"], 1)

    def test_short_pays_correct_fees_receives_positive_funding(self):
        data = inputs()
        data[0]["BTCUSDT"][START] = replace(data[0]["BTCUSDT"][START], open=D("100.1"))
        result = run(data, [signal(side="SHORT", stop_loss=D(125))])
        trade = result.trades[0]
        self.assertEqual(trade["entry_price"], D("99.94985"))
        self.assertEqual(trade["exit_price"], D("100.15"))
        self.assertGreater(trade["funding"], 0)
        self.assertLess(trade["gross_pnl"], 0)
        self.assertEqual(result.final_cash, D(200)+trade["net_pnl"])

    def test_stop_uses_mark_trigger_and_adverse_trade_extreme(self):
        data = inputs()
        t = START+HOUR
        data[1]["BTCUSDT"][t] = replace(data[1]["BTCUSDT"][t], low=D(70))
        data[0]["BTCUSDT"][t] = replace(data[0]["BTCUSDT"][t], low=D(72))
        result = run(data)
        trade = result.trades[0]
        self.assertEqual(trade["exit_reason"], "CATASTROPHE_STOP")
        self.assertEqual(trade["exit_price"], D(72)*(1-D(".0015")))
        self.assertLess(trade["exit_price"], D(75))

    def test_gap_cannot_fill_at_old_stop(self):
        data = inputs()
        t = START+HOUR
        for group in data[:2]:
            group["BTCUSDT"][t] = replace(group["BTCUSDT"][t], open=D(60), low=D(59))
        trade = run(data).trades[0]
        self.assertEqual(trade["exit_price"], D(60)*(1-D(".0015")))
        self.assertEqual(trade["closed_ms"], t)

    def test_missing_occupied_mark_aborts_no_forward_fill(self):
        data = inputs()
        del data[1]["BTCUSDT"][START+HOUR]
        with self.assertRaises(IncompleteHistory): run(data)

    def test_missing_funding_is_not_zero_cost(self):
        data = inputs()
        data[2]["BTCUSDT"] = tuple(r for r in data[2]["BTCUSDT"] if r.event_ms != START+8*HOUR)
        with self.assertRaises(IncompleteHistory): run(data)

    def test_funding_timestamp_jitter_is_preserved_without_phantom_entry_charge(self):
        data = inputs()
        data[2]["BTCUSDT"] = tuple(replace(r, event_ms=r.event_ms+31) for r in data[2]["BTCUSDT"])
        result = run(data)
        self.assertEqual(len(result.funding_events), 1)
        event = result.funding_events[0]
        self.assertEqual(event["time_ms"], START+8*HOUR+31)
        self.assertEqual(event["mark_proxy_ms"], START+8*HOUR)
        self.assertEqual(result.final_cash, D(200)+result.trades[0]["net_pnl"])

    def test_reported_funding_schedule_switch_is_valid_but_missing_event_is_not(self):
        data = inputs()
        stamps = [(START-8*HOUR, 8), (START, 8), (START+4*HOUR, 4),
                  (START+8*HOUR, 4), (START+12*HOUR, 4),
                  (START+16*HOUR, 8), (START+24*HOUR, 8)]
        data[2]["BTCUSDT"] = tuple(FundingRate("BTCUSDT", t+7, h, D(".001")) for t,h in stamps)
        result = run(data)
        self.assertEqual(len(result.funding_events), 3)  # settlement before 12h time exit
        data[2]["BTCUSDT"] = tuple(r for r in data[2]["BTCUSDT"] if r.event_ms != START+8*HOUR+7)
        with self.assertRaises(IncompleteHistory): run(data)

    def test_unexplained_funding_drift_remains_invalid(self):
        data = inputs()
        data[2]["BTCUSDT"] = tuple(replace(r, event_ms=r.event_ms+5000)
            if r.event_ms == START+8*HOUR else r for r in data[2]["BTCUSDT"])
        with self.assertRaises(IncompleteHistory): run(data)

    def test_funding_mark_is_causal_open_not_future_close(self):
        data = inputs()
        t = START+8*HOUR
        data[1]["BTCUSDT"][t] = replace(data[1]["BTCUSDT"][t], high=D(110), close=D(110))
        result = run(data)
        self.assertEqual(result.funding_events[0]["mark_proxy"], D(100))

    def test_market_ttl_and_liquidity_proxy_are_enforced(self):
        result = run(signals=[signal(expires_ms=START)])
        self.assertEqual(result.rejections["EXPIRED_SIGNAL"], 1)
        result = run(signals=[signal(entry_type="LIMIT")])
        self.assertEqual(result.rejections["OHLC_CANNOT_ESTABLISH_ENTRY_TTL"], 1)
        data = inputs()
        data[0]["BTCUSDT"][START-HOUR] = replace(data[0]["BTCUSDT"][START-HOUR], volume=D(".01"))
        self.assertEqual(run(data).rejections["INSUFFICIENT_PROXY_LIQUIDITY"], 1)

    def test_cooldown_persists_after_early_exit(self):
        data = inputs()
        data[1]["BTCUSDT"][START] = replace(data[1]["BTCUSDT"][START], low=D(74))
        result = run(data, [signal(), signal(time=START+HOUR)])
        self.assertEqual(result.rejections["STRATEGY_COOLDOWN"], 1)
        self.assertEqual(len(result.trades), 1)

    def test_portfolio_direction_cap_and_deterministic_ties(self):
        symbols = ("SOLUSDT", "BTCUSDT", "ETHUSDT")
        data = inputs(symbols)
        signals = [signal(s) for s in symbols]
        a, b = run(data, signals), run(data, list(reversed(signals)))
        self.assertEqual(a.trades, b.trades)
        self.assertEqual(a.final_cash, b.final_cash)
        self.assertLessEqual(len(a.trades), 2)
        self.assertGreater(sum(a.rejections.values()), 0)

    def test_fixed_target_rr_is_still_enforced(self):
        s = signal(strategy_id="ema_level_2h", stop_loss=D(98), take_profit=D(103))
        result = run(signals=[s])
        self.assertEqual(result.rejections["NET_REWARD_RISK"], 1)
        self.assertEqual(result.trades, [])

    def test_ambiguous_bar_stop_beats_target(self):
        data = inputs()
        data[0]["BTCUSDT"][START] = replace(data[0]["BTCUSDT"][START], low=D(89), high=D(116))
        data[1]["BTCUSDT"][START] = data[0]["BTCUSDT"][START]
        result = run(data, [signal(strategy_id="ema_level_2h", stop_loss=D(90), take_profit=D(115))])
        self.assertEqual(result.trades[0]["exit_reason"], "SL")

    def test_breaker_persists_across_next_utc_day(self):
        data = inputs(count=50)
        data[1]["BTCUSDT"][START] = replace(data[1]["BTCUSDT"][START], low=D(1), close=D(1))
        data[0]["BTCUSDT"][START] = replace(data[0]["BTCUSDT"][START], low=D(1), close=D(1))
        result = run(data, [signal(), signal(time=START+24*HOUR)], count=48,
                     policy=ResearchPolicy(risk_fraction=D(".08")))
        self.assertIsNotNone(result.paused_at_ms)
        self.assertEqual(result.rejections["UNRECONCILED_OR_PAUSED"], 1)
        self.assertEqual(result.trades[0]["exit_reason"], "LIQUIDATION_STRESS")

    def test_favorable_gap_cannot_evade_no_target_cost_guard(self):
        data = inputs()
        data[0]["BTCUSDT"][START] = replace(data[0]["BTCUSDT"][START], open=D("99.7"))
        data[1]["BTCUSDT"][START] = replace(data[1]["BTCUSDT"][START], open=D("99.7"))
        result = run(data, [signal(stop_loss=D("98.39"))])
        self.assertEqual(result.rejections["COSTS_TOO_HIGH_FOR_STOP"], 1)
        self.assertEqual(result.trades, [])

    def test_full_slippage_cannot_exceed_approved_short_risk(self):
        result = run(signals=[signal(side="SHORT", stop_loss=D(125))])
        self.assertEqual(result.rejections["FILL_EXCEEDS_APPROVED_RISK"], 1)

    def test_mixed_strategy_risk_keeps_frozen_caps_and_individual_budgets(self):
        data = inputs(("BTCUSDT", "ETHUSDT"))
        sim = Simulator(*data, ResearchPolicy(), risk_by_strategy={
            "alt_dip_reversal": D(".08"), "ema_level_2h": D(".01")})
        self.assertEqual(sim.policy.portfolio_risk_fraction, D(".08"))
        result = sim.run([signal(), signal("ETHUSDT", strategy_id="ema_level_2h",
                          stop_loss=D(90), take_profit=D(115))],
                         start_ms=START, end_ms=START+24*HOUR)
        trades = {t["strategy_id"]: t for t in result.trades}
        self.assertEqual(set(trades), {"alt_dip_reversal", "ema_level_2h"})
        for name, risk in (("alt_dip_reversal", D(".08")), ("ema_level_2h", D(".01"))):
            self.assertLessEqual(trades[name]["initial_risk"], trades[name]["entry_equity"]*risk)
        with self.assertRaises(ValueError):
            Simulator(*data, ResearchPolicy(), risk_by_strategy={"ema_level_2h": D(".08")})

    def test_duplicate_signals_and_second_run_rejected(self):
        sim = Simulator(*inputs(), ResearchPolicy())
        with self.assertRaises(ValueError): sim.run([signal(), signal()], start_ms=START, end_ms=START+HOUR)
        with self.assertRaises(ValueError): sim.run([], start_ms=START, end_ms=START+HOUR)


if __name__ == "__main__": unittest.main()
