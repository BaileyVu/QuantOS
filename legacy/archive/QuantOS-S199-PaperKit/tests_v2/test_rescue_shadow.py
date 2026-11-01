import asyncio
import json
import math
import time
from dataclasses import replace
from decimal import Decimal

from s199_v2.engine import PaperEngine
from s199_v2.fees import FeeMetadata
from s199_v2.ledger import Ledger

from test_engine_integration import ready_engine


def test_rescue_shadow_uses_dynamic_five_share_minimum_without_a_fill(tmp_path) -> None:
    engine,market=ready_engine(tmp_path)
    engine.model=replace(engine.model,bias=math.log(99))
    for token,bid,ask in (("up",.81,.82),("down",.18,.19)):
        book=engine.books[token]
        book.snapshot({"bids":[{"price":bid,"size":10}],"asks":[{"price":ask,"size":10}],
            "min_order_size":"5","tick_size":"0.01","timestamp":2})
        features=book.features()
        engine.feed.snapshot(token,sequence=1,ts_ns=time.time_ns(),bid=features["bid"],ask=features["ask"],
            bid_size=10,ask_size=10,depth=2)
    async def fee(*_args,**_kwargs): return {"fee_rate_bps":1000}
    engine.get_json=fee

    results=asyncio.run(engine.evaluate_shadow_horizon(market,120))

    assert results["current"]["decision"]=="skip"
    assert results["current"]["reason"]=="ask_range"
    assert results["rescue"]["decision"]=="enter"
    assert results["rescue"]["shares"]==Decimal("5")
    assert Decimal(str(results["rescue"]["cash_debit"]))<Decimal("6")
    assert engine.ledger.portfolio().cash==Decimal("200")
    assert engine.ledger.open_fills()==[]
    assert engine.ledger.shadow_policies(slug=market.slug,horizon_seconds=120)=={"current","rescue"}
    engine.ledger.close()


def test_shadow_restart_exactly_once_and_official_settlement(tmp_path) -> None:
    path=tmp_path/"ledger.sqlite3"; fee=FeeMetadata("up",Decimal("0.1"),"https://fees",True)
    ledger=Ledger(path,experiment="s199-paper",initial_cash="200")
    inserted=ledger.record_shadow(slug="btc-updown-5m-100",horizon_seconds=60,policy="rescue",
        decision="enter",reason=None,selected="up",shares="5",price="0.7",fee="0.1",cash_debit="3.6",
        prob_up=.8,net_edge=.08,pair_cost=1.01,ts_ns=1,payload={"fee_source":fee.source_url})
    assert inserted
    assert not ledger.record_shadow(slug="btc-updown-5m-100",horizon_seconds=60,policy="rescue",
        decision="enter",reason=None,selected="up",shares="5",price="0.7",fee="0.1",cash_debit="3.6",
        prob_up=.8,net_edge=.08,pair_cost=1.01,ts_ns=2,payload={})
    ledger.close()

    restarted=Ledger(path,experiment="s199-paper",initial_cash="200")
    assert restarted.unsettled_shadow_slugs()=={"btc-updown-5m-100"}
    shadow_id="btc-updown-5m-100:60:rescue"
    assert restarted.record_shadow_settlement(shadow_id=shadow_id,official_outcome="up",ts_ns=3,evidence_url="https://gamma")
    assert not restarted.record_shadow_settlement(shadow_id=shadow_id,official_outcome="up",ts_ns=4,evidence_url="https://gamma")
    group=restarted.shadow_summary()["groups"]["rescue_60s"]
    assert group=={"evaluations":1,"entries":1,"settled_entries":1,"wins":1,"net_pnl":"1.4"}
    assert restarted.portfolio().cash==Decimal("200")
    restarted.close()


def test_market_finalization_is_single_and_explicit(tmp_path) -> None:
    engine,market=ready_engine(tmp_path)
    engine.finalize_market_windows(market,reason="feed_unhealthy:missing_snapshot")
    engine.finalize_market_windows(market,reason="expired")
    row=engine.ledger.db.execute("SELECT decision,reason FROM market_evaluations WHERE slug=?",(market.slug,)).fetchone()
    assert row==("missed","feed_unhealthy:missing_snapshot")
    assert engine.ledger.db.execute("SELECT COUNT(*) FROM shadow_evaluations WHERE slug=?",(market.slug,)).fetchone()[0]==4
    diagnostics=[json.loads(line) for line in (engine.runtime/"diagnostics.jsonl").read_text().splitlines()]
    assert sum(item["kind"]=="market_evaluation_complete" for item in diagnostics)==1
    assert sum(item["kind"]=="shadow_evaluation" for item in diagnostics)==2
    engine.ledger.close()
