import asyncio
import json
import math
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import aiohttp

from s199_v2.engine import GAMMA, Market, PaperEngine
from s199_v2.fees import FeeMetadata


COLUMNS=["seconds_left","price_diff","return_since_start","momentum_15s","momentum_30s","momentum_60s","volatility_60s","volume_60s",
    "yes_mid","yes_spread","yes_bid_size","yes_ask_size","yes_book_imbalance","yes_microprice","no_mid","no_spread","no_bid_size","no_ask_size","no_book_imbalance","no_microprice","yes_no_ask_cost"]


def make_engine(tmp_path:Path)->PaperEngine:
    root=tmp_path/"kit"; model=root/"vendor/prediction-market-backtesting/live/models/btc_snapshot_model_s199_cost101_daily_stop120_profile.json"
    model.parent.mkdir(parents=True); model.write_text(json.dumps({"model":{"columns":COLUMNS,"means":[0]*len(COLUMNS),"scales":[1]*len(COLUMNS),"weights":[0]*len(COLUMNS),"bias":math.log(4)}}))
    (root/"strategy_candidate.json").write_text(json.dumps({"candidate_id":"test-candidate","parameters":{"calibration":{"kind":"raw"},"min_net_edge":.01,
        "max_ask":.75,"max_pair_ask_cost":1.02,"momentum":"none","snapshot_seconds":60,"trade_size":2,"min_ask":.1,"min_probability":.65}}))
    return PaperEngine(root=root,runtime=tmp_path/"runtime")


def event(start:int,up:str,down:str,*,closed:bool=False,accepting:bool=True)->dict:
    return {"active":not closed,"closed":closed,"archived":False,"markets":[{"slug":f"btc-updown-5m-{start}","conditionId":f"c-{start}",
        "active":not closed,"closed":closed,"archived":False,"enableOrderBook":True,"acceptingOrders":accepting,
        "clobTokenIds":json.dumps([up,down]),"cryptoMarketConfig":{"asset":"btc","duration":"5m"}}]}


def add_market(engine:PaperEngine,start:int,up:str="up",down:str="down")->Market:
    market=Market(f"btc-updown-5m-{start}",start,f"c-{start}",up,down); engine.markets[market.slug]=market
    engine.token_market[up]=market.slug; engine.token_market[down]=market.slug
    return market


def book()->dict:
    return {"bids":[{"price":"0.49","size":"10"}],"asks":[{"price":"0.51","size":"10"}],
        "min_order_size":"1","tick_size":"0.01","timestamp":1}


def not_found(token:str)->aiohttp.ClientResponseError:
    request=SimpleNamespace(real_url=f"https://clob.polymarket.com/book?token_id={token}")
    return aiohttp.ClientResponseError(request,(),status=404,message="Not Found")


def test_expired_token_404_prunes_market_but_keeps_open_fill_for_settlement(tmp_path)->None:
    engine=make_engine(tmp_path); now=int(time.time()); start=(now//300)*300-300; market=add_market(engine,start)
    engine.ledger.record_fill(fill_id="F-expired",event_id="E-expired",instrument_id="up",slug=market.slug,outcome="up",
        shares="2",price="0.40",fee_metadata=FeeMetadata("up",Decimal("0.07"),"test",True),ts_ns=time.time_ns())
    async def get_json(url:str,**params):
        if url.endswith("/book"): raise not_found(str(params["token_id"]))
        return event(start,"up","down",closed=True)
    engine.get_json=get_json
    assert asyncio.run(engine.snapshot_isolated("up",source="test")) is False
    assert asyncio.run(engine.snapshot_isolated("up",source="retry")) is False
    assert market.slug not in engine.markets and not engine.token_market
    assert engine.metrics["clob_404"]==1 and engine.metrics["market_prunes"]==1
    assert len(engine.ledger.open_fills())==1
    engine.ledger.close()


def test_valid_live_tokens_snapshot_and_remain_tradeable(tmp_path)->None:
    engine=make_engine(tmp_path); now=int(time.time()); start=(now//300)*300
    async def discover_json(url:str,**_params): return event(start,"live-up","live-down")
    engine.get_json=discover_json; asyncio.run(engine.discover_once(now=start+30))
    async def snapshot_json(_url:str,**_params): return book()
    engine.get_json=snapshot_json
    assert asyncio.run(engine.snapshot_isolated("live-up",source="test"))
    assert asyncio.run(engine.snapshot_isolated("live-down",source="test"))
    assert engine.feed.tradeable(("live-up","live-down"),now_ns=time.time_ns())[0]
    assert engine.metrics["clob_404"]==0 and len(engine.markets)==1
    engine.ledger.close()


def test_empty_or_crossed_book_is_token_failure_not_websocket_reconnect(tmp_path)->None:
    engine=make_engine(tmp_path); now=int(time.time()); start=(now//300)*300; add_market(engine,start)
    engine.live_checks["polymarket_ws"]=True
    async def crossed(_url:str,**_params):
        return {"bids":[{"price":"0.60","size":"5"}],"asks":[{"price":"0.50","size":"5"}]}
    engine.get_json=crossed
    assert asyncio.run(engine.snapshot_isolated("up",source="test")) is False
    engine.apply_polymarket_event({"event_type":"book","asset_id":"down","bids":[],"asks":[]})
    diagnostics=engine.diagnostics_count
    engine.apply_polymarket_event({"event_type":"book","asset_id":"down","bids":[],"asks":[]})
    assert set(engine.token_market)=={"up","down"}
    assert engine.live_checks["polymarket_ws"] is True
    assert engine.metrics["websocket_reconnects"]==0
    assert engine.metrics["book_failures"]==2 and engine.diagnostics_count==diagnostics
    assert engine.snapshot_failures=={"up":1,"down":1}
    engine.ledger.close()


def test_connection_failure_uses_bounded_websocket_recovery_path(tmp_path)->None:
    engine=make_engine(tmp_path); now=int(time.time()); start=(now//300)*300; add_market(engine,start)
    for token in ("up","down"):
        engine.feed.connect(token)
    engine.live_checks["polymarket_ws"]=True
    engine.mark_websocket_failure({"up","down"},2,ConnectionError("closed"))
    assert engine.live_checks["polymarket_ws"] is False
    assert engine.metrics["websocket_reconnects"]==1
    assert all(not engine.feed.books[token].connected for token in ("up","down"))
    assert engine.snapshot_failures=={}
    engine.ledger.close()


class FakeWS:
    def __init__(self)->None: self.messages=[]
    async def send_json(self,payload:dict)->None: self.messages.append(payload)


def test_market_rollover_prunes_old_tokens_and_updates_socket_in_place(tmp_path)->None:
    engine=make_engine(tmp_path); base=(int(time.time())//300)*300
    async def gamma(url:str,**_params):
        start=int(url.rsplit("-",1)[1]); return event(start,f"up-{start}",f"down-{start}")
    engine.get_json=gamma; asyncio.run(engine.discover_once(now=base+299))
    old=set(engine.token_market); assert old=={f"up-{base}",f"down-{base}"}
    asyncio.run(engine.discover_once(now=base+300))
    new={f"up-{base+300}",f"down-{base+300}"}; assert set(engine.token_market)==new
    assert old.isdisjoint(engine.books) and len(engine.markets)==1 and engine.metrics["market_prunes"]==1
    async def mixed(url:str,**_params):
        if url.endswith("/book"): return book()
        start=int(url.rsplit("-",1)[1]); return event(start,f"up-{start}",f"down-{start}")
    engine.get_json=mixed; ws=FakeWS(); subscribed=asyncio.run(engine.sync_ws_subscriptions(ws,set(old)))
    assert subscribed==new
    assert [message["operation"] for message in ws.messages]==["unsubscribe","subscribe"]
    assert engine.metrics["websocket_reconnects"]==0
    assert engine.feed.tradeable(tuple(sorted(new)),now_ns=time.time_ns())[0]
    engine.ledger.close()
