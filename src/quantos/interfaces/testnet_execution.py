"""Explicitly invoked Spot Testnet infrastructure harness; never a production Alpha."""
import argparse
from datetime import datetime,timezone,timedelta
from decimal import Decimal,ROUND_DOWN,ROUND_UP,localcontext
import json
from pathlib import Path

from quantos.application.risk_execution import TradingStep
from quantos.domain.alpha import AlphaAction,AlphaDecision
from quantos.domain.execution.exchange import ExchangeExecutionEngine
from quantos.domain.execution.contracts import OrderType
from quantos.domain.risk.engine import RiskContext,RiskEngine,RiskPolicy,MarketState
from quantos.domain.runtime_contracts import AccountSnapshot,TransactionCosts,arithmetic,canonical,identity
from quantos.infrastructure.binance.spot_rest import SpotRest,decimal,SpotError
from quantos.infrastructure.binance.klines import BinanceSpotHistoricalKlineAdapter
from quantos.infrastructure.storage.execution_ledger import JsonlExecutionLedger
from quantos.infrastructure.configuration.testnet_credentials import load_testnet_credentials
from quantos.interfaces.tsmom_screen import publish

D=Decimal


def run_harness(*,port,state_dir,action,approved=False,clock=lambda:datetime.now(timezone.utc)):
    if not port.testnet:raise ValueError('Testnet harness cannot use Mainnet')
    if action not in ('preflight','recover','cancel','buy','sell'):raise ValueError('invalid harness action')
    if action in ('buy','sell') and approved is not True:raise ValueError('explicit synthetic-intent approval required')
    state_dir=Path(state_dir);ledger=JsonlExecutionLedger(state_dir/'execution.jsonl')
    records=ledger.read();costs=TransactionCosts(D('.001'),D('.002'))
    if records:
        raw=json.loads(records[0])['event']['initial']
        if raw['positions']:raise ValueError('invalid initial allocation')
        initial=AccountSnapshot(datetime.fromisoformat(raw['timestamp']),{k:D(v) for k,v in raw['balances'].items()},())
    else:initial=AccountSnapshot(clock(),{'USDT':D(20),'BTC':D(0),'ETH':D(0)},())
    port.sync_time();info=port.exchange_info('BTCUSDT')
    engine=ExchangeExecutionEngine(initial,costs,ledger,port,recover_pending=action=='cancel')
    result=None
    if action=='cancel':engine.cancel_pending()
    if action in ('buy','sell'):
        # This harness runs one operator-approved round trip per durable allocation.
        # Starting another folder cannot silently reuse a running folder's allocation.
        if action=='buy' and len(records)>1:raise ValueError('one synthetic cycle per allocation; recover existing evidence')
        raw=port._call('GET','/api/v3/klines',{'symbol':'BTCUSDT','interval':'1m','limit':3})
        candles=BinanceSpotHistoricalKlineAdapter._decode_klines(json.dumps(raw,default=str).encode(),symbol='BTCUSDT',interval='1m')
        now=clock();completed=tuple(c for c in candles if c.close_time<=now)
        if not completed:raise ValueError('no completed canonical Testnet candle')
        candle=completed[-1]
        if now-candle.close_time>timedelta(seconds=60):raise ValueError('stale completed candle')
        filters={f['filterType']:f for f in info['symbols'][0]['filters']}
        step=decimal(filters['LOT_SIZE']['stepSize']);tick=decimal(filters['PRICE_FILTER']['tickSize'])
        if not step or not tick:raise ValueError('quantity/price grid unavailable')
        policy=RiskPolicy(D('9.90'),step,D(1),decimal(filters['LOT_SIZE']['maxQty']),D(10),D('.51'),D('.02'),D('.10'),60,D(0),costs)
        a=AlphaAction.BUY if action=='buy' else AlphaAction.SELL
        alpha=AlphaDecision(now,'BTCUSDT','testnet-infrastructure-only-v1','synthetic-no-model','testnet-completed-candle-v1',a,
            'operator-approved synthetic infrastructure test; no economic edge claim','test-only',D(0))
        with localcontext(arithmetic()):
            equity=engine.snapshot.balances['USDT']+engine.snapshot.balances.get('BTC',D(0))*candle.close
            # Fixed synthetic Risk input is permitted only inside this Testnet-only
            # harness, never exposed as production expected-return evidence.
            ctx=RiskContext(now,(MarketState(candle,True),),engine.snapshot,D('.02'),
                now.replace(hour=0,minute=0,second=0,microsecond=0),D(20),max(D(20),equity))
            price=candle.close*(1+costs.slippage_rate if a is AlphaAction.BUY else 1-costs.slippage_rate)
            limit=(price/tick).to_integral_value(rounding=ROUND_DOWN if a is AlphaAction.BUY else ROUND_UP)*tick
        result=TradingStep(RiskEngine(policy),engine).run(alpha,ctx,order_type=OrderType.LIMIT,limit_price=limit)
    evidence={'schema':'testnet-infrastructure-v1','action':action,'timestamp':clock(),'scope':port.scope,
        'exchange_information':info,'result':result,'snapshot':engine.reconcile(),'ledger_identity':identity(ledger.read()),
        'alpha_validation':False,'mainnet_enabled':False}
    publish(state_dir/(identity(evidence)+'.json'),evidence)
    return evidence


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir',type=Path,required=True)
    p.add_argument('--action',choices=('preflight','recover','cancel','buy','sell'),default='preflight')
    p.add_argument('--approve-synthetic-intent',action='store_true')
    args=p.parse_args()
    try:
        evidence=run_harness(port=SpotRest(load_testnet_credentials()),state_dir=args.state_dir,action=args.action,approved=args.approve_synthetic_intent)
        print('Testnet reconciled; evidence',identity(evidence),'Mainnet disabled.')
        return 0
    except Exception:
        # Durable evidence remains. Never print potentially secret-bearing transport exceptions.
        print('EXECUTION_BLOCKED: reconcile the durable state; no blind retry or new allocation. Mainnet disabled.')
        return 2

if __name__=='__main__':raise SystemExit(main())
