from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import unittest
from quantos.domain.alpha.v1t5 import A,B,T5Prediction,decide
from quantos.domain.alpha import AlphaAction
from quantos.domain.market_data import Candle
from quantos.domain.runtime_contracts import AccountSnapshot
from quantos.application.v1t5_evaluation import evaluate

class T5EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.t=datetime(2024,1,1,tzinfo=timezone.utc)
        self.account=AccountSnapshot(self.t,{'USDT':D(20)},())
    def prediction(self,minutes=1,forecast='.01',**kw):
        return T5Prediction(self.t+timedelta(minutes=minutes),self.t,'fixture','v1t5',
                            forecast=D(forecast) if forecast is not None else None,**kw)
    def candles(self,n=5):
        for i in range(n):
            t=self.t+timedelta(minutes=i)
            price=D('10000')+i*100
            yield Candle('BTCUSDT','1m',t,t+timedelta(seconds=59,milliseconds=999),
                price,price,price,price,D(1),price,1)
    def test_cost_gate_is_strict_and_negative_never_shorts(self):
        for forecast,expected in [('.003',AlphaAction.HOLD),('.0031',AlphaAction.BUY),('-.1',AlphaAction.HOLD)]:
            a,_=decide(self.prediction(forecast=forecast),family=B,timestamp=self.t+timedelta(minutes=2),
                       account=self.account,cost_rate=D('.0015'))
            self.assertEqual(a.action,expected)
    def test_delayed_execution_and_streaming_paper_parity(self):
        p=self.prediction(); exit=self.prediction(minutes=3,forecast='-.01')
        predictions={p.timestamp:p,exit.timestamp:exit}
        history=evaluate(tuple(self.candles()),predictions,family=B,run_id='parity')
        paper=evaluate(self.candles(),predictions,family=B,run_id='parity')
        self.assertEqual(history,paper)
        self.assertEqual(len(history.fills),2)
        self.assertEqual(history.fills[0].report.timestamp,self.t+timedelta(minutes=2))
        self.assertEqual(history.fills[0].report.fill_price,D(10100)*D('1.0005'))
        self.assertGreater(history.metrics.fees,0)
        self.assertGreater(history.metrics.slippage,0)
    def test_risk_rejection_final_and_no_pyramiding(self):
        ps=[self.prediction(minutes=i) for i in (1,2,3)]
        out=evaluate(self.candles(),{p.timestamp:p for p in ps},family=B,run_id='one')
        self.assertEqual(len(out.fills),1)
        out=evaluate(self.candles(),{ps[0].timestamp:ps[0]},family=B,run_id='cash',initial_equity=D(5))
        self.assertEqual(len(out.fills),0)
        self.assertTrue(out.rejection_counts)
    def test_future_training_missing_execution_minute_and_gap_rejected(self):
        with self.assertRaises(ValueError):
            T5Prediction(self.t,self.t+timedelta(days=1),'x','x',forecast=D(1))
        p=self.prediction(minutes=5)
        with self.assertRaises(ValueError):
            evaluate(self.candles(),{p.timestamp:p},family=B,run_id='future')
        c=list(self.candles())
        with self.assertRaises(ValueError):evaluate(c[:2]+c[3:],{},family=B,run_id='gap')
    def test_a_negative_class_cash_and_training_edge_rejection(self):
        p=self.prediction(forecast=None,probabilities=(D('.1'),D('.1'),D('.8')),
                          training_class_means=(D('-.01'),D(0),D('.001')))
        out=evaluate(self.candles(),{p.timestamp:p},family=A,run_id='edge')
        self.assertFalse(out.fills)
        self.assertIn('non-positive edge after costs and safety margin',out.rejection_counts)

if __name__=='__main__':unittest.main()
