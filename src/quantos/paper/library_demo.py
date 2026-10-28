"""Seeded library lifecycle capture and exact runtime/replay comparison."""
from dataclasses import replace
from pathlib import Path

from .adaptive import AdaptiveSizer
from .engine import PaperEngine
from .journal import Journal
from .replay import replay
from .risk import Policy
from .runtime import Runtime, dispatch
from .strategies.fixtures import lifecycle_snapshots
from .types import Candle, D, Trade


def run_library_demo(state: Path):
    state.mkdir(parents=True, exist_ok=False)
    results=[]
    for strategy,snapshot in lifecycle_snapshots(1800000000000):
        case=state/strategy.strategy_id; case.mkdir()
        recorder=Runtime(case)
        journals=[Journal(case/f'journal_{p}.db',p,'synthetic') for p in ('p1','p5','pA')]
        engines=[PaperEngine(j,Policy(risk_fraction=D(f)),adaptive=AdaptiveSizer(j,journals[0]) if j.profile=='pA' else None)
                 for j,f in zip(journals,('.01','.05','.05'))]
        now=snapshot.at_ms
        signal=strategy.evaluate(snapshot)
        assert signal is not None
        def emit(kind,payload,at):
            recorder.record(kind,payload,at)
            dispatch(engines,kind,payload,at,strategies=(strategy,))
        def q(price,at):
            return replace(snapshot.quote,bid=price,ask=price+snapshot.instrument.tick,
                           mark=price,time_ms=at,received_ms=at,mark_time_ms=at,
                           bid_qty=D(100000),ask_qty=D(100000))
        try:
            emit('restart',{},now)
            emit('feed',[('quote',snapshot.quote)],now)
            emit('scan',[snapshot],now)
            entered=[e for e in engines if e.active()]
            assert entered and engines[0] in entered, strategy.strategy_id
            quantity=max(D(e.active()[0]['quantity']) for e in entered)
            price=signal.entry_price
            if signal.entry_type=='LIMIT':
                trade=Trade(signal.symbol,1,now+1000,price-snapshot.instrument.tick if signal.side=='LONG' else price+snapshot.instrument.tick,
                            quantity*300,signal.side=='LONG')
                emit('feed',[('quote',q(price,now+1000)),('trade',trade)],now+1000)
            else:
                emit('feed',[('quote',q(price,now+1000))],now+1000)
                emit('feed',[('quote',q(price,now+2000))],now+2000)
            assert all(e.active() and D(e.active()[0]['filled'])>0 for e in entered), (strategy.strategy_id, [(e.journal.profile, [tuple(r) for r in e.journal.db.execute("SELECT kind,payload FROM events WHERE kind='CANCELED'")]) for e in entered])
            if strategy.strategy_id == 'tsmom_4h':
                risk=max(abs(D(e.active()[0]['average'])-signal.stop_loss) for e in entered)
                emit('feed',[('quote',q(price+4*risk,now+3000))],now+3000)
                assert all(e.journal.db.execute("SELECT count(*) FROM events WHERE kind='STOP_UPDATED'").fetchone()[0] for e in entered)
            elif strategy.strategy_id == 'turtle_55':
                old=snapshot.candles_4h[-1]
                bar=Candle(old.close_ms+1,old.close_ms+14400000,price,price+1,price-1,price,D(100))
                at=bar.close_ms+1
                changed=replace(snapshot,at_ms=at,quote=q(price,at),candles_4h=snapshot.candles_4h+(bar,))
                emit('scan',[changed],at)
                assert all(e.journal.db.execute("SELECT count(*) FROM events WHERE kind='STOP_UPDATED'").fetchone()[0] for e in entered)
            if signal.take_profit is not None:
                end=now+3000; exit_price=signal.take_profit
            else:
                end=now+signal.maximum_hold_seconds*1000+3000; exit_price=price
            emit('feed',[('quote',q(exit_price,end))],end)
            emit('feed',[('quote',q(exit_price,end+1000))],end+1000)
            expected={}
            for e in engines:
                assert not e.active(), strategy.strategy_id
                fills=[tuple(r) for r in e.journal.db.execute('SELECT kind,quantity,price,fee,pnl FROM fills ORDER BY rowid')]
                expected[e.journal.profile]=(str(e.journal.cash()),fills)
                results.append({'strategy':strategy.strategy_id,'profile':e.journal.profile,
                    'state':e.journal.orders()[0]['state'] if e.journal.orders() else 'REJECTED_RISK',
                    'cash':str(e.journal.cash())})
            replay_state=case/'replay'
            replay(sorted((case/'market').glob('*.gz')),replay_state,strategy.strategy_id)
            for profile,(cash,fills) in expected.items():
                other=Journal(replay_state/f'journal_{profile}.db',profile,'replay')
                try:
                    assert str(other.cash())==cash
                    assert [tuple(r) for r in other.db.execute('SELECT kind,quantity,price,fee,pnl FROM fills ORDER BY rowid')]==fills
                    original=next(j for j in journals if j.profile==profile)
                    assert [r[0] for r in other.db.execute("SELECT payload FROM events WHERE kind='STOP_UPDATED'")]==[r[0] for r in original.db.execute("SELECT payload FROM events WHERE kind='STOP_UPDATED'")]
                finally: other.close()
        finally:
            for j in journals: j.close()
    return results
