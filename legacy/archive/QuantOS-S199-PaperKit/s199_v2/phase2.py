from __future__ import annotations

import hashlib
import json
import math
import os
import random
import statistics
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from .atomic import atomic_json
from .baseline import parse_evaluations
from .fees import FeeMetadata, taker_fee

GAMMA = "https://gamma-api.polymarket.com/events/slug/"
CLOB_FEE = "https://clob.polymarket.com/fee-rate"


def _get_json(url: str, timeout: float) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "QuantOS-S199-V2-audit/2.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def _json_array(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    return []


def label_from_event(slug: str, event: object, *, source_url: str, retrieved_utc: str) -> dict[str, object]:
    base: dict[str, object] = {"slug": slug, "source_url": source_url, "retrieved_utc": retrieved_utc,
        "status": "UNKNOWN", "outcome_up": None, "reason": "unverified"}
    if not isinstance(event, dict):
        return {**base, "reason": "invalid_response"}
    markets = event.get("markets")
    if not isinstance(markets, list) or len(markets) != 1 or not isinstance(markets[0], dict):
        return {**base, "reason": "expected_one_market"}
    market = markets[0]
    outcomes = [str(x).casefold() for x in _json_array(market.get("outcomes"))]
    prices_raw = _json_array(market.get("outcomePrices"))
    try:
        prices = [float(x) for x in prices_raw]
    except (TypeError, ValueError):
        prices = []
    rules = str(market.get("description") or event.get("description") or "")
    source = str(market.get("resolutionSource") or event.get("resolutionSource") or "")
    base.update({"condition_id": market.get("conditionId"), "market_id": market.get("id"),
                 "closed": market.get("closed"), "resolved": market.get("resolved"),
                 "rules": rules, "resolution_source": source,
                 "token_ids": _json_array(market.get("clobTokenIds"))})
    rules_lower = f"{rules} {source}".casefold()
    methodology_ok = any(word in rules_lower for word in ("chainlink", "data streams", "twap"))
    base["methodology_verified"] = methodology_ok
    if market.get("closed") is not True or outcomes != ["up", "down"] or len(prices) != 2:
        return {**base, "reason": "not_closed_or_unexpected_schema"}
    if prices == [1.0, 0.0] and methodology_ok:
        return {**base, "status": "VERIFIED", "outcome_up": 1, "reason": None}
    if prices == [0.0, 1.0] and methodology_ok:
        return {**base, "status": "VERIFIED", "outcome_up": 0, "reason": None}
    return {**base, "reason": "ambiguous_prices_or_methodology"}


def acquire(log_path: Path | str, output_dir: Path | str, *, timeout: float = 15.0) -> dict[str, object]:
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    evaluations = parse_evaluations(log_path)
    final_by_slug: dict[str, dict[str, object]] = {}
    for row in evaluations:
        final_by_slug[str(row["slug"])] = row
    slugs = sorted({str(row["slug"]) for row in evaluations})
    retrieved = datetime.now(UTC).isoformat()
    labels: list[dict[str, object]] = []
    raw_path = out / "raw_responses.jsonl"
    with raw_path.open("w", encoding="utf-8") as raw_handle:
        for slug in slugs:
            url = GAMMA + urllib.parse.quote(slug, safe="")
            try:
                event = _get_json(url, timeout)
                record = {"slug": slug, "url": url, "retrieved_utc": retrieved, "response": event}
                label = label_from_event(slug, event, source_url=url, retrieved_utc=retrieved)
                decision = final_by_slug.get(slug, {})
                if decision.get("kind") == "signal" and label.get("status") == "VERIFIED":
                    token_ids = label.get("token_ids")
                    selected = str(decision.get("selected") or "").casefold()
                    if isinstance(token_ids, list) and len(token_ids) == 2 and selected in {"up", "down"}:
                        token_id = str(token_ids[0 if selected == "up" else 1])
                        fee_url = CLOB_FEE + "?" + urllib.parse.urlencode({"token_id": token_id})
                        fee_response = _get_json(fee_url, timeout)
                        rate_raw = fee_response.get("fee_rate_bps", fee_response.get("base_fee")) if isinstance(fee_response, dict) else None
                        if rate_raw is not None:
                            label.update({"selected_token_id": token_id, "fee_source_url": fee_url,
                                          "fee_rate_bps": str(rate_raw)})
                            raw_handle.write(json.dumps({"slug": slug, "url": fee_url,
                                "retrieved_utc": retrieved, "response": fee_response}, sort_keys=True) + "\n")
            except Exception as exc:
                record = {"slug": slug, "url": url, "retrieved_utc": retrieved, "error": f"{type(exc).__name__}: {exc}"}
                label = {"slug": slug, "source_url": url, "retrieved_utc": retrieved,
                         "status": "UNKNOWN", "outcome_up": None, "reason": "acquisition_error"}
            raw_handle.write(json.dumps(record, sort_keys=True) + "\n")
            labels.append(label)
    with (out / "labels.jsonl").open("w", encoding="utf-8") as handle:
        for row in labels:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    evidence = {"retrieved_utc": retrieved, "requested": len(slugs),
                "verified": sum(x["status"] == "VERIFIED" for x in labels),
                "unknown": sum(x["status"] != "VERIFIED" for x in labels),
                "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest()}
    atomic_json(out / "acquisition_summary.json", evidence)
    return evidence


def enrich_signal_fees(log_path: Path | str, labels_path: Path | str, raw_path: Path | str,
                       *, timeout: float = 15.0) -> dict[str, object]:
    final: dict[str, dict[str, object]] = {}
    for row in parse_evaluations(log_path):
        final[str(row["slug"])] = row
    labels=[json.loads(line) for line in Path(labels_path).read_text().splitlines() if line.strip()]
    retrieved=datetime.now(UTC).isoformat(); enriched=0
    with Path(raw_path).open("a",encoding="utf-8") as raw_handle:
        for label in labels:
            decision=final.get(str(label["slug"]),{})
            if decision.get("kind") != "signal" or label.get("status") != "VERIFIED":
                continue
            if label.get("fee_rate_bps") is not None and label.get("fee_source_url"):
                enriched+=1
                continue
            tokens=label.get("token_ids"); selected=str(decision.get("selected") or "").casefold()
            if not isinstance(tokens,list) or len(tokens)!=2 or selected not in {"up","down"}:
                continue
            token_id=str(tokens[0 if selected=="up" else 1])
            url=CLOB_FEE+"?"+urllib.parse.urlencode({"token_id":token_id})
            response=_get_json(url,timeout)
            rate=response.get("fee_rate_bps",response.get("base_fee")) if isinstance(response,dict) else None
            if rate is None:
                continue
            label.update({"selected_token_id":token_id,"fee_source_url":url,
                          "fee_rate_bps":str(rate),"fee_retrieved_utc":retrieved})
            raw_handle.write(json.dumps({"slug":label["slug"],"url":url,
                "retrieved_utc":retrieved,"response":response},sort_keys=True)+"\n")
            enriched+=1
    target=Path(labels_path); temporary=target.with_name(f".{target.name}.tmp")
    with temporary.open("w",encoding="utf-8") as handle:
        for label in labels:
            handle.write(json.dumps(label,sort_keys=True)+"\n")
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary,target)
    result={"signal_fee_records":enriched,"retrieved_utc":retrieved,
            "raw_sha256":hashlib.sha256(Path(raw_path).read_bytes()).hexdigest()}
    summary_path=Path(labels_path).with_name("acquisition_summary.json")
    if summary_path.exists():
        summary=json.loads(summary_path.read_text()); summary.update(result); atomic_json(summary_path,summary)
    return result


def _mean_loss(rows: list[dict[str, object]], key: str, loss: str) -> float:
    vals=[]
    for row in rows:
        p=min(1-1e-15,max(1e-15,float(row[key]))); y=float(row["outcome_up"])
        vals.append((p-y)**2 if loss == "brier" else -(y*math.log(p)+(1-y)*math.log(1-p)))
    return statistics.fmean(vals)


def _bootstrap(rows: list[dict[str, object]], key: str, loss: str) -> list[float] | None:
    if len(rows) < 2: return None
    rng=random.Random(199); values=[]
    for _ in range(2000):
        sample=[rows[rng.randrange(len(rows))] for _ in rows]
        values.append(_mean_loss(sample,key,loss))
    values.sort(); return [values[49], values[1949]]


def _paired_brier_difference_ci(rows: list[dict[str, object]]) -> list[float] | None:
    if len(rows) < 2: return None
    rng=random.Random(199); values=[]
    for _ in range(2000):
        sample=[rows[rng.randrange(len(rows))] for _ in rows]
        values.append(_mean_loss(sample,"prob_up","brier")-_mean_loss(sample,"market_prob_up","brier"))
    values.sort(); return [values[49],values[1949]]


def score(log_path: Path | str, labels_path: Path | str, settlements_path: Path | str, output_path: Path | str) -> dict[str, object]:
    evaluations=parse_evaluations(log_path)
    by_slug: dict[str,list[dict[str,object]]]=defaultdict(list)
    for row in evaluations: by_slug[str(row["slug"])].append(row)
    labels={json.loads(line)["slug"]:json.loads(line) for line in Path(labels_path).read_text().splitlines() if line.strip()}
    scored=[]
    for slug, attempts in by_slug.items():
        final=attempts[-1]; label=labels.get(slug,{})
        if label.get("status") != "VERIFIED" or "prob_up" not in final: continue
        yes=float(final["yes_ask"]); no=float(final["no_ask"])
        if yes <= 0 or no <= 0 or yes+no <= 0: continue
        scored.append({**final,"outcome_up":label["outcome_up"],"market_prob_up":yes/(yes+no)})
    report: dict[str,object]={"evaluation_records":len(evaluations),"distinct_markets":len(by_slug),
        "usable_final_decisions":sum("prob_up" in x[-1] for x in by_slug.values()),
        "verified_labels":sum(x.get("status")=="VERIFIED" for x in labels.values()),"scored_samples":len(scored)}
    if scored:
        report["model"]={"brier":_mean_loss(scored,"prob_up","brier"),"brier_ci95":_bootstrap(scored,"prob_up","brier"),
            "log_loss":_mean_loss(scored,"prob_up","log")}
        report["market_ask_normalized"]={"brier":_mean_loss(scored,"market_prob_up","brier"),
            "brier_ci95":_bootstrap(scored,"market_prob_up","brier"),"log_loss":_mean_loss(scored,"market_prob_up","log"),
            "limitation":"uses contemporaneous normalized asks because bids were not logged"}
        report["model_minus_market_brier"]={"difference":_mean_loss(scored,"prob_up","brier")-_mean_loss(scored,"market_prob_up","brier"),
            "paired_bootstrap_ci95":_paired_brier_difference_ci(scored)}
        bins=[]
        for low in [i/10 for i in range(10)]:
            bucket=[r for r in scored if low <= float(r["prob_up"]) < low+0.1 or (low==0.9 and float(r["prob_up"])==1)]
            if bucket: bins.append({"low":low,"high":low+0.1,"n":len(bucket),"mean_prediction":statistics.fmean(float(r["prob_up"]) for r in bucket),"observed_up":statistics.fmean(float(r["outcome_up"]) for r in bucket)})
        report["calibration_bins"]=bins
        periods=[]
        for hour in sorted({str(r.get("observed_utc", ""))[:13] for r in scored}):
            bucket=[r for r in scored if str(r.get("observed_utc", ""))[:13] == hour]
            if hour and len(bucket) >= 10:
                periods.append({"utc_hour":hour+":00:00Z","n":len(bucket),
                    "model_brier":_mean_loss(bucket,"prob_up","brier"),
                    "market_brier":_mean_loss(bucket,"market_prob_up","brier")})
        report["utc_periods_min_n_10"]=periods
    expectancy=[]
    for row in scored:
        if row.get("kind") != "signal": continue
        label=labels.get(str(row["slug"]),{})
        if label.get("fee_rate_bps") is None: continue
        rate=float(label["fee_rate_bps"])/10000.0
        selected=str(row.get("selected") or "").casefold(); ask=float(row[f"{'yes' if selected=='up' else 'no'}_ask"])
        model_win=float(row["prob_up"]) if selected=="up" else 1-float(row["prob_up"])
        fee=float(taker_fee(shares="2",price=str(ask),metadata=FeeMetadata(str(label.get("selected_token_id")),
            Decimal(str(rate)),str(label.get("fee_source_url")),True)))
        realized_win=(int(row["outcome_up"])==1) if selected=="up" else (int(row["outcome_up"])==0)
        expectancy.append({"slug":row["slug"],"selected":selected,"shares":2,"ask":ask,"fee_usdc":fee,
            "model_expected_pnl_usdc":2*model_win-2*ask-fee,
            "official_realized_pnl_usdc":(2 if realized_win else 0)-2*ask-fee,
            "assumption":"two shares execute at logged top ask; no slippage or queueing"})
    report["hypothetical_fee_aware_signals"]=expectancy
    settlements=json.loads(Path(settlements_path).read_text())["positions"]
    reconciliation=[]
    for position in settlements:
        label=labels.get(position["slug"],{})
        official=("Up" if label.get("outcome_up")==1 else "Down" if label.get("outcome_up")==0 else "UNKNOWN")
        selected=str(position.get("outcome") or "").casefold()
        expected_winner=official != "UNKNOWN" and selected == official.casefold()
        reconciliation.append({"slug":position["slug"],"sandbox_selected_outcome":selected,
            "sandbox_winner":bool(position.get("winner")),"official_winning_outcome":official,
            "expected_winner":expected_winner,"match":official != "UNKNOWN" and bool(position.get("winner"))==expected_winner,
            "source_url":label.get("source_url")})
    report["settlement_reconciliation"]=reconciliation
    if len(scored) < 30: classification="INCONCLUSIVE"
    else:
        diff=float(report["model"]["brier"])-float(report["market_ask_normalized"]["brier"])
        classification="PROMISING" if diff < -0.01 else "NO_EDGE" if diff > 0.01 else "INCONCLUSIVE"
    report["classification"]=classification
    atomic_json(output_path,report)
    return report
