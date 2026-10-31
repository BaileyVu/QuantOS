import asyncio
import json
import math
import time
from pathlib import Path

from s199_v2.engine import Market, PaperEngine


COLUMNS=["seconds_left","price_diff","return_since_start","momentum_15s","momentum_30s","momentum_60s","volatility_60s","volume_60s",
    "yes_mid","yes_spread","yes_bid_size","yes_ask_size","yes_book_imbalance","yes_microprice","no_mid","no_spread","no_bid_size","no_ask_size","no_book_imbalance","no_microprice","yes_no_ask_cost"]


def make_root(tmp_path:Path)->Path:
    root=tmp_path/"kit"; model=root/"vendor/prediction-market-backtesting/live/models/btc_snapshot_model_s199_cost101_daily_stop120_profile.json"
    model.parent.mkdir(parents=True); model.write_text(json.dumps({"model":{"columns":COLUMNS,"means":[0]*len(COLUMNS),"scales":[1]*len(COLUMNS),"weights":[0]*len(COLUMNS),"bias":math.log(4)}}))
    (root/"strategy_candidate.json").write_text(json.dumps({"candidate_id":"test-candidate","parameters":{"calibration":{"kind":"raw"},"min_net_edge":.01,
        "max_ask":.75,"max_pair_ask_cost":1.02,"momentum":"none","snapshot_seconds":60,"trade_size":2,"min_ask":.1,"min_probability":.65}}))
    return root


def ready_engine(tmp_path:Path)->tuple[PaperEngine,Market]:
    root=make_root(tmp_path); engine=PaperEngine(root=root,runtime=tmp_path/"runtime")
    now=int(time.time()); market=Market(f"btc-updown-5m-{now-240}",now-240,"c","up","down")
    engine.markets[market.slug]=market
    for token,bid,ask in (("up",.29,.30),("down",.69,.70)):
        book=engine.books.setdefault(token,__import__("s199_v2.model",fromlist=["PublicBook"]).PublicBook.empty(token))
        book.snapshot({"bids":[{"price":bid,"size":10}],"asks":[{"price":ask,"size":10}],"min_order_size":"1","tick_size":"0.01","timestamp":1})
        engine.feed.connect(token); f=book.features(); engine.feed.snapshot(token,sequence=0,ts_ns=time.time_ns(),bid=f["bid"],ask=f["ask"],bid_size=10,ask_size=10,depth=2)
    start=market.start
    for second in range(start-61,now+1): engine.btc.record_trade(ts_ns=second*1_000_000_000,price=100+(second-start)*.01,size=1)
    engine.btc_trade_ns=time.time_ns()
    return engine,market


def test_full_fill_restart_duplicate_and_official_settlement(tmp_path) -> None:
    engine,market=ready_engine(tmp_path)
    async def fee(*_args,**_kwargs): return {"base_fee":1000}
    engine.get_json=fee
    asyncio.run(engine.evaluate_market(market))
    assert engine.ledger.portfolio().unresolved==1
    asyncio.run(engine.evaluate_market(market))
    assert len(engine.ledger.open_fills())==1
    engine.ledger.close()

    restarted=PaperEngine(root=engine.root,runtime=engine.runtime)
    assert restarted.ledger.portfolio().unresolved==1
    async def resolved(*_args,**_kwargs):
        return {"markets":[{"closed":True,"outcomes":"[\"Up\",\"Down\"]","outcomePrices":"[\"1\",\"0\"]",
            "description":"Resolved with Chainlink BTC/USD TWAP","clobTokenIds":"[\"up\",\"down\"]"}]}
    restarted.get_json=resolved
    asyncio.run(restarted.settle_once(ignore_clock=True))
    portfolio=restarted.ledger.portfolio()
    assert portfolio.unresolved==0 and portfolio.realized_pnl>0
    restarted.status("stopped",shutdown="clean")
    assert json.loads((restarted.runtime/"status.json").read_text())["shutdown"]=="clean"
    restarted.ledger.close()
