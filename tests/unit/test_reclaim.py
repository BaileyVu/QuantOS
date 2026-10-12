from dataclasses import replace, asdict
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest

from quantos.domain.alpha.reclaim import LiquidityReclaimAlpha
from quantos.domain.alpha.hft import HftSide
from quantos.domain.evaluation.reclaim import trade_monte_carlo
from tests.unit.test_hft_paper import feature, at, rules, snapshot, delta


def observe(alpha, second, mid, direction=1, flow=True):
    f = replace(feature(), timestamp=at(second), book_update_id=int(second*1000)+1,
        mid_price=D(str(mid)), static_obi=D('.3')*direction,
        signed_trade_flow=D(direction if flow else -direction), spread_bps=D('2'),
        realized_volatility_bps=D('1'))
    return alpha.observe(f, int(second*1_000_000_000))


def warm(alpha):
    for i in range(300):
        observe(alpha, i, '101' if i == 1 else '100')


class ReclaimAlphaTests(unittest.TestCase):
    def test_observed_reclaim_stable_identity_and_no_future_extreme(self):
        alpha=LiquidityReclaimAlpha(); warm(alpha)
        self.assertEqual(observe(alpha,300,'99.8').reason,'displacement_observed')
        for i in range(301,305):
            self.assertIsNone(observe(alpha,i,'99.8').signal)
        signal=observe(alpha,305,'100.03').signal
        self.assertEqual(signal.side,HftSide.BUY)
        self.assertEqual(signal.target_price,D('100.5'))
        self.assertEqual(signal.horizon_seconds,900)
        later=observe(alpha,305.1,'100.04').signal
        self.assertEqual(later.signal_id,signal.signal_id)
        self.assertEqual(later.expires_ns,signal.expires_ns)
        self.assertNotEqual(later.book_update_id,signal.book_update_id)
        observe(alpha,306,'100.04')
        self.assertIsNone(observe(alpha,307,'100.04').signal)
        self.assertEqual(observe(alpha,308,'100.04').reason,'setup_cooldown')

    def test_symmetric_short(self):
        alpha=LiquidityReclaimAlpha(); warm(alpha)
        self.assertEqual(observe(alpha,300,'101.2',-1).reason,'displacement_observed')
        for i in range(301,305): observe(alpha,i,'101.2',-1)
        self.assertEqual(observe(alpha,305,'100.97',-1).signal.side,HftSide.SELL)

    def test_sustained_trend_flow_and_expiry(self):
        alpha=LiquidityReclaimAlpha(); warm(alpha)
        observe(alpha,300,'99.8')
        for i in range(301,420):
            self.assertIsNone(observe(alpha,i,D('99.8')-D(i-300)/1000).signal)
        self.assertEqual(observe(alpha,420,'100.03').reason,'continued_trend_or_setup_expired')
        alpha=LiquidityReclaimAlpha(); warm(alpha); observe(alpha,300,'99.8')
        for i in range(301,305): observe(alpha,i,'99.8')
        self.assertEqual(observe(alpha,305,'100.03',flow=False).reason,'flow_or_imbalance_not_confirmed')

    def test_data_gap_regression_noise_and_small_range(self):
        alpha=LiquidityReclaimAlpha(); warm(alpha)
        self.assertEqual(observe(alpha,300,'100.2').reason,'no_liquidity_displacement')
        self.assertEqual(observe(alpha,302,'99.8').reason,'range_data_gap')
        with self.assertRaises(ValueError): observe(alpha,301,'100')
        alpha=LiquidityReclaimAlpha()
        for i in range(301): result=observe(alpha,i,'100')
        self.assertEqual(result.reason,'prior_range_below_60bps')

    def test_monte_carlo_insufficient_and_seeded(self):
        self.assertEqual(trade_monte_carlo([])['status'],'INSUFFICIENT_EVIDENCE')
        pnls=[D('.3'),D('-.2')]*15
        self.assertEqual(trade_monte_carlo(pnls),trade_monte_carlo(pnls))
        self.assertEqual(trade_monte_carlo(pnls)['seed'],505)


def pipeline(path, *, profit=True, risk_off=False, partial=False, stop_after_entry=False, small_gain=False, invalid_before_fill=False, reliability=False, full_equity=False):
    from quantos.application.retail_replay import RetailCausalProcessor
    from quantos.application.retail_trader import RetailPaperEngine
    from quantos.domain.evaluation.retail import RetailEvaluation
    from quantos.domain.execution.retail_paper import RetailPaperAccount
    from quantos.domain.market_data.hft import AggregateTrade, MarkPriceEvent
    from quantos.domain.risk.retail import RetailRiskPolicy
    from quantos.infrastructure.configuration.hft import load_hft_config
    from quantos.infrastructure.storage.retail_events import RetailEventJournal
    from quantos.infrastructure.storage.retail_paper import account_payload, validate_account
    config_text=Path('configs/futures_hft_200_paper.toml').read_text().replace('depth_levels = 5','depth_levels = 1')
    config_path=path.parent/'config.toml';config_path.write_text(config_text)
    from quantos.domain.risk.retail import FullEquityRiskPolicy
    config=load_hft_config(config_path); policy=FullEquityRiskPolicy() if full_equity else RetailRiskPolicy()
    reliability = reliability or full_equity
    exchange=replace(rules(),price_tick=D('.01'))
    account=RetailPaperAccount(config.fees['BTCUSDC'],policy)
    account.risk_off=risk_off
    engine=RetailPaperEngine(config,exchange,account,RetailEvaluation(reclaim=True,reliability=reliability),policy,reclaim=True,reliability=reliability)
    processor=RetailCausalProcessor(engine);journal=RetailEventJournal(path)
    journal.append('manifest',version='qos07-v1' if full_equity else 'qos06-v1' if reliability else 'qos05-v1',config_text=config_text,policy=asdict(policy),rules=asdict(exchange),account=account_payload(account))
    def dispatch(action,**args):
        result=processor.apply(action,args)
        journal.append('command',action=action,arguments=args)
        return result
    snap=replace(snapshot(),bids=((D('100.99'),D(10)),),asks=((D('101.01'),D(5)),),received_monotonic_ns=0)
    dispatch('bootstrap',snapshot=snap,buffered=[replace(delta(),received_monotonic_ns=1)])
    update=101;previous_bid=D('100.99');previous_ask=D('101.01')
    def event(item,t):
        ns=int(t*1e9)
        return dispatch('event',event=replace(item,received_at=at(t),exchange_time=at(t),received_monotonic_ns=ns),processing_ns=ns,feed_latency_ms=D(1),clock_healthy=True)
    def depth(t,mid,quantity=D(5)):
        nonlocal update,previous_bid,previous_ask
        update+=1; mid=D(str(mid));bid,ask=mid-D('.01'),mid+D('.01')
        bids=((previous_bid,D(0)),(bid,D(10))) if bid!=previous_bid else ((bid,D(10)),)
        asks=((previous_ask,D(0)),(ask,quantity)) if ask!=previous_ask else ((ask,quantity),)
        previous_bid,previous_ask=bid,ask
        return event(delta(first=update,final=update,previous=update-1,bids=bids,asks=asks),t)
    def funding(t): event(MarkPriceEvent('BTCUSDC',D(100),D('.0001'),at(1000),at(t),at(t)),t)
    depth(.1,'101')
    for i in range(1,301):
        funding(i-.01); depth(i,'101' if i <= 2 else '100.01')
    depth(301,'99.85')
    for i in range(302,307): funding(i-.01);depth(i,'99.85')
    event(AggregateTrade('BTCUSDC',1,D('100'),D(2),False,at(306),at(306)),306.9)
    depth(307,'100.04')
    if invalid_before_fill:
        dispatch('disarm',reason='invalid_data_fixture',timestamp=at(307.05))
    depth(307.1,'100.04',D('1.5') if partial else D(5))
    if partial:
        depth(307.2,'100.04',D('1.5'))
    if risk_off or invalid_before_fill:
        assert not account.fills
    else:
        assert account.position is not None, processor.report()
        if stop_after_entry:
            report=processor.report();journal.append('complete',report=report);journal.close()
            return report,account
        if small_gain:
            dispatch('disarm',reason='fixture_safety_exit',timestamp=at(307.25))
            dispatch('resume')
        # Target or lost-boundary exit, via later valid books; no manual fills.
        depth(307.3,'100.07' if small_gain else '100.6' if profit else '99.9')
        depth(307.4,'100.07' if small_gain else '100.6' if profit else '99.9')
        depth(307.5,'100.07' if small_gain else '100.6' if profit else '99.9')
        assert account.position is None, processor.report()
    dispatch('disarm',reason='runtime_shutdown',timestamp=at(308))
    validate_account(account)
    report=processor.report();journal.append('complete',report=report);journal.close()
    return report,account


class ReclaimPipelineTests(unittest.TestCase):
    def test_profit_loss_partial_and_exact_replay(self):
        from quantos.interfaces.retail_edge import replay_capture
        for profit,partial in ((True,False),(False,False),(True,True)):
            with self.subTest(profit=profit,partial=partial),tempfile.TemporaryDirectory() as root:
                path=Path(root)/'capture.jsonl';report,account=pipeline(path,profit=profit,partial=partial)
                self.assertEqual(report['realized_net_pnl']>0,profit)
                self.assertTrue(replay_capture(path)['matches_capture'])
                self.assertEqual(account.balance-200,account.realized_net_pnl)
                self.assertEqual(report['qualification']['closed_trades'],1)
                self.assertTrue(report['qualification']['trades'][0]['source_context'])
                self.assertGreater(report['qualification']['slippage_cost'],0)
                if partial: self.assertGreater(account.orders_cancelled,0)

    def test_failed_risk_causes_no_order(self):
        with tempfile.TemporaryDirectory() as root:
            report,account=pipeline(Path(root)/'capture.jsonl',risk_off=True)
            self.assertEqual(account.orders_submitted,0)
            self.assertGreater(report['rejections']['emergency_risk_off'],0)

    def test_spread_slippage_fees_can_erase_small_gross_gain(self):
        with tempfile.TemporaryDirectory() as root:
            report,account=pipeline(Path(root)/'capture.jsonl',small_gain=True)
            self.assertLess(account.realized_net_pnl,0)
            self.assertGreater(account.total_fees,0)

    def test_invalid_data_cancels_before_fill(self):
        with tempfile.TemporaryDirectory() as root:
            report,account=pipeline(Path(root)/'capture.jsonl',invalid_before_fill=True)
            self.assertEqual(len(account.fills),0)
            self.assertEqual(account.orders_cancelled,1)

    def test_censored_position_and_restart_preserve_context(self):
        from quantos.infrastructure.storage.retail_paper import RetailPaperStateStore
        with tempfile.TemporaryDirectory() as root:
            report,account=pipeline(Path(root)/'capture.jsonl',stop_after_entry=True)
            self.assertEqual(report['qualification']['closed_trades'],0)
            self.assertEqual(report['qualification']['censored_trades'],1)
            store=RetailPaperStateStore(Path(root)/'account.json')
            store.reset('fixture',account.fees,account.policy)
            store.save('fixture',account,{})
            restored,_=store.load('fixture',account.fees,account.policy)
            self.assertEqual(restored.position.opened_ns,0)
            self.assertTrue(any(r['event']=='alpha_context' for r in restored.lifecycle))
            self.assertEqual(restored.balance,account.balance)

    def test_counterfactual_qualification_output_exact_replays(self):
        from quantos.interfaces.retail_edge import replay_capture
        from tests.unit.test_retail_replay import synthetic_capture
        with tempfile.TemporaryDirectory() as root:
            source=Path(root)/'source.jsonl';synthetic_capture(source)
            for stress in (False,True):
                output=Path(root)/f'qualified-{stress}.jsonl'
                result=replay_capture(source,stress=stress,qualify_reclaim=True,output_capture=output)
                self.assertEqual(result['metrics']['qualification']['closed_trades'],0)
                self.assertTrue(replay_capture(output)['matches_capture'])
                with self.assertRaises(FileExistsError):
                    replay_capture(source,qualify_reclaim=True,output_capture=output)

    def test_q05_execution_partial_fill_ack_and_no_liquidity_reuse(self):
        from quantos.domain.risk.retail import RetailRiskEngine, RetailRiskPolicy
        from quantos.domain.execution.retail_paper import RetailPaperAccount
        from tests.unit.test_hft_paper import fees, book
        from tests.unit.test_retail_paper import depth
        alpha=LiquidityReclaimAlpha();warm(alpha);observe(alpha,300,'99.8')
        for i in range(301,305):observe(alpha,i,'99.8')
        signal=observe(alpha,305,'100.03').signal
        signal=replace(signal,book_update_id=101)
        f=replace(feature(),best_bid=D('100.02'),best_ask=D('100.04'),mid_price=D('100.03'),spread_bps=D('.02')/D('100.03')*10000)
        policy=RetailRiskPolicy();risk=RetailRiskEngine(policy,fees(),rules())
        decision=risk.evaluate(signal,f,D(200),D(200),False)
        self.assertTrue(decision.approved)
        account=RetailPaperAccount(fees(),policy);target=book()
        target.bootstrap(replace(snapshot(),bids=((D('100.02'),D(10)),),asks=((D('100.04'),D(5)),)),(delta(),))
        order=account.submit_entry(decision,signal,at(305),305_000_000_000,D('100.04'))
        event=depth(target,final=102,ns=305_099_999_999,asks=((D('100.04'),D('.01')),))
        self.assertIsNone(account.on_depth(target,event.received_at,event.received_monotonic_ns))
        event=depth(target,final=103,ns=305_100_000_000)
        fill=account.on_depth(target,event.received_at,event.received_monotonic_ns)
        self.assertEqual(fill.quantity,D('.01'))
        self.assertGreater(order.remaining,0)
        event=depth(target,final=104,ns=305_200_000_000)
        self.assertIsNone(account.on_depth(target,event.received_at,event.received_monotonic_ns))
        self.assertEqual(account.lifecycle[-1]['event'],'partial_fill')

    def test_confirmed_reclaim_still_requires_full_cost_admission(self):
        from quantos.domain.risk.retail import RetailRiskEngine,RetailRiskPolicy
        from tests.unit.test_hft_paper import fees
        alpha=LiquidityReclaimAlpha();warm(alpha);observe(alpha,300,'99.8')
        for i in range(301,305):observe(alpha,i,'99.8')
        signal=observe(alpha,305,'100.03').signal
        f=replace(feature(),book_update_id=signal.book_update_id)
        decision=RetailRiskEngine(RetailRiskPolicy(),fees(),rules()).evaluate(
            replace(signal,expected_move_bps=D(10)),f,D(200),D(200),False)
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason,'non_economic_after_full_cost')
