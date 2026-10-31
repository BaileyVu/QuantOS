import json
import os
from pathlib import Path

from s199_v2.baseline import reproduce
from s199_v2.phase2 import label_from_event, score


SESSION=Path(os.environ.get(
    "S199_SESSION_DIR",
    "/Users/bailey12/Desktop/QuantOS-S199-PaperKit/runtime/runs/20261016T075801Z-52940",
))


def test_reproduces_authoritative_baseline() -> None:
    got=reproduce(SESSION)
    assert got["duration_seconds"]==66839
    assert got["ending_equity"]=="199.685534" and got["net_pnl"]=="-0.314466"
    assert (got["fills"],got["wins"],got["losses"])==(3,2,1)
    assert (got["evaluation_records"],got["distinct_markets"])==(231,222)
    assert got["final_decisions"]=={"ask_above_max":7,"edge_below_threshold":96,"invalid_book_features":100,"momentum_alignment":16,"signal":3}
    assert got["websocket_reconnections"]==120 and got["exit_code"]==134
    assert got["final_diagnostics_present"] is False


def test_label_requires_closed_binary_prices_and_official_methodology() -> None:
    event={"markets":[{"closed":True,"resolved":True,"outcomes":"[\"Up\",\"Down\"]","outcomePrices":"[\"1\",\"0\"]","description":"Resolved from Chainlink Data Streams TWAP","clobTokenIds":"[\"u\",\"d\"]"}]}
    got=label_from_event("s",event,source_url="https://example.test",retrieved_utc="now")
    assert got["status"]=="VERIFIED" and got["outcome_up"]==1
    event["markets"][0]["description"]="Binance last tick"
    assert label_from_event("s",event,source_url="x",retrieved_utc="now")["status"]=="UNKNOWN"


def test_score_excludes_unknown_and_preserves_reconciliation(tmp_path) -> None:
    log=tmp_path/"x.log"; log.write_text("2026-01-01T00:00:00Z SANDBOX_EVAL_SIGNAL slug=s seconds_left=60 prob_up=.8 yes_ask=.7 no_ask=.3\n")
    labels=tmp_path/"labels.jsonl"; labels.write_text(json.dumps({"slug":"s","status":"VERIFIED","outcome_up":1,"source_url":"https://e"})+"\n")
    settlements=tmp_path/"settlements.json"; settlements.write_text(json.dumps({"positions":[{"slug":"s","outcome":"up","winner":True,"settled_outcome":"Up"}] }))
    report=score(log,labels,settlements,tmp_path/"report.json")
    assert report["scored_samples"]==1 and report["classification"]=="INCONCLUSIVE"
    assert report["settlement_reconciliation"][0]["match"] is True
