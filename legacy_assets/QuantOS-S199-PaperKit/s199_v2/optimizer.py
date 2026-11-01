from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import aiohttp

from .atomic import atomic_json
from .baseline import parse_evaluations
from .fees import FeeMetadata, taker_fee
from .model import calibrate


@dataclass(frozen=True)
class Candidate:
    calibration: dict[str, object]
    min_net_edge: float
    max_ask: float
    max_pair_ask_cost: float
    momentum: str
    snapshot_seconds: int = 60
    trade_size: float = 2.0
    min_ask: float = 0.10
    min_probability: float = 0.65


def _sigmoid(x: float) -> float:
    return 1.0/(1.0+math.exp(-max(-40,min(40,x))))


def fit_platt(rows: list[dict[str, object]]) -> dict[str, object]:
    a,b=1.0,0.0
    for _ in range(30):
        gaa=gab=gbb=ga=gb=0.0
        for row in rows:
            p=min(1-1e-8,max(1e-8,float(row["prob_up"]))); x=math.log(p/(1-p)); y=float(row["outcome_up"])
            q=_sigmoid(a*x+b); w=max(1e-8,q*(1-q))
            ga+=(q-y)*x; gb+=q-y; gaa+=w*x*x; gab+=w*x; gbb+=w
        ga+=0.001*(a-1); gaa+=0.001
        det=gaa*gbb-gab*gab
        if abs(det)<1e-12: break
        da=(gbb*ga-gab*gb)/det; db=(-gab*ga+gaa*gb)/det
        a-=da; b-=db
        if abs(da)+abs(db)<1e-8: break
    return {"kind":"platt","slope":a,"intercept":b,"fit_partition":"train_only"}


def load_rows(log_path: Path|str, labels_path: Path|str) -> list[dict[str,object]]:
    final={}
    for row in parse_evaluations(log_path): final[str(row["slug"])]=row
    labels={x["slug"]:x for x in map(json.loads,Path(labels_path).read_text().splitlines())}
    rows=[]
    for slug,row in final.items():
        label=labels.get(slug,{})
        if label.get("status")!="VERIFIED" or "prob_up" not in row: continue
        fee_map=label.get("fee_rate_bps_by_token")
        if not isinstance(fee_map,dict) or len(fee_map)<2: continue
        rows.append({**row,"outcome_up":int(label["outcome_up"]),"token_ids":label["token_ids"],
            "fee_rate_bps_by_token":fee_map})
    rows.sort(key=lambda x:(str(x.get("observed_utc","")),str(x["slug"])))
    return rows


async def enrich_historical_fees(log_path: Path|str, labels_path: Path|str, *, concurrency: int=12) -> dict[str,object]:
    final={}
    for row in parse_evaluations(log_path): final[str(row["slug"])]=row
    labels=[json.loads(x) for x in Path(labels_path).read_text().splitlines() if x.strip()]
    sem=asyncio.Semaphore(concurrency); fetched=0
    timeout=aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout,headers={"User-Agent":"QuantOS-S199-paper/1.0"}) as session:
        async def fetch(token: str) -> tuple[str,str]:
            nonlocal fetched
            async with sem:
                async with session.get("https://clob.polymarket.com/fee-rate",params={"token_id":token}) as response:
                    response.raise_for_status(); data=await response.json(); rate=data.get("fee_rate_bps",data.get("base_fee"))
                    if rate is None: raise ValueError(f"missing fee metadata for {token}")
                    fetched+=1; return token,str(rate)
        tasks=[]; owners=[]
        for label in labels:
            if label.get("status")!="VERIFIED" or "prob_up" not in final.get(str(label["slug"]),{}): continue
            tokens=[str(x) for x in label.get("token_ids",[])]
            if len(tokens)!=2: continue
            owners.append((label,tokens)); tasks.extend(fetch(t) for t in tokens)
        values=iter(await asyncio.gather(*tasks))
        for label,tokens in owners:
            pairs=[next(values),next(values)]; label["fee_rate_bps_by_token"]={k:v for k,v in pairs}
    target=Path(labels_path); tmp=target.with_name("."+target.name+".tmp")
    with tmp.open("w") as handle:
        for label in labels: handle.write(json.dumps(label,sort_keys=True)+"\n")
        handle.flush()
    tmp.replace(target)
    return {"fee_requests":fetched,"markets":fetched//2}


def _momentum_ok(row:dict[str,object],selected:str,mode:str)->bool:
    sign=1.0 if selected=="up" else -1.0
    m15=sign*float(row.get("momentum_15s",0)); m30=sign*float(row.get("momentum_30s",0))
    if mode=="none": return True
    if mode=="aligned": return m15>=0 and m30>=0
    if mode=="reject_both_opposed": return not (m15<0 and m30<0)
    raise ValueError(mode)


def evaluate(rows:list[dict[str,object]], candidate:Candidate)->dict[str,object]:
    trades=[]; cumulative=peak=drawdown=0.0
    predictions=[]
    for row in rows:
        p=calibrate(float(row["prob_up"]),candidate.calibration); y=int(row["outcome_up"]); predictions.append((p,y))
        asks={"up":float(row["yes_ask"]),"down":float(row["no_ask"])}
        edges={"up":p-asks["up"],"down":1-p-asks["down"]}; selected=max(edges,key=edges.get)
        ask=asks[selected]; selected_p=p if selected=="up" else 1-p
        tokens=[str(x) for x in row["token_ids"]]; token=tokens[0 if selected=="up" else 1]
        rate=Decimal(str(row["fee_rate_bps_by_token"][token]))/Decimal(10000)
        fee=float(taker_fee(shares="2",price=str(ask),metadata=FeeMetadata(token,rate,"historical-clob-fee-rate",True)))
        net_edge=selected_p-ask-(fee/2)
        if not (candidate.min_ask<=ask<=candidate.max_ask and selected_p>=candidate.min_probability): continue
        if float(row["yes_ask"])+float(row["no_ask"])>candidate.max_pair_ask_cost: continue
        if net_edge<candidate.min_net_edge or not _momentum_ok(row,selected,candidate.momentum): continue
        win=(y==1 and selected=="up") or (y==0 and selected=="down")
        pnl=(2.0 if win else 0.0)-2*ask-fee; trades.append(pnl); cumulative+=pnl; peak=max(peak,cumulative); drawdown=max(drawdown,peak-cumulative)
    brier=statistics.fmean((p-y)**2 for p,y in predictions)
    return {"pnl":sum(trades),"trades":len(trades),"max_drawdown":drawdown,
        "mean_trade_pnl":statistics.fmean(trades) if trades else 0.0,"trade_pnls":trades,"brier":brier}


def _bootstrap_ci(values:list[float], *, seed:int=199)->list[float]|None:
    if len(values)<2:return None
    rng=random.Random(seed); means=[]
    for _ in range(5000): means.append(statistics.fmean(values[rng.randrange(len(values))] for _ in values))
    means.sort(); return [means[124],means[4874]]


def optimize(log_path:Path|str,labels_path:Path|str,output_path:Path|str,candidate_path:Path|str)->dict[str,object]:
    rows=load_rows(log_path,labels_path)
    if len(rows)!=122: raise ValueError(f"expected 122 fee-labeled rows, got {len(rows)}")
    train_end=int(len(rows)*0.60); validation_end=int(len(rows)*0.80)
    train,validation,test=rows[:train_end],rows[train_end:validation_end],rows[validation_end:]
    calibrations=[{"kind":"raw"},{"kind":"shrink","factor":0.75},fit_platt(train)]
    candidates=[Candidate(c,e,a,cost,m) for c in calibrations for e in (.02,.04,.06,.08)
        for a in (.65,.75) for cost in (1.01,1.02) for m in ("none","aligned","reject_both_opposed")]
    ranked=[]
    for candidate in candidates:
        tr=evaluate(train,candidate); va=evaluate(validation,candidate)
        if tr["trades"]<5 or va["trades"]<2: continue
        dispersion=statistics.pstdev(va["trade_pnls"]) if va["trades"]>1 else 0
        penalty=dispersion*math.sqrt(2*math.log(len(candidates))*va["trades"])
        score=float(va["pnl"])-0.5*float(va["max_drawdown"])-penalty
        ranked.append((score,candidate,tr,va,penalty))
    if not ranked: raise ValueError("no candidate met minimum train/validation trade counts")
    ranked.sort(key=lambda x:x[0],reverse=True); score,best,tr,va,penalty=ranked[0]
    te=evaluate(test,best); ci=_bootstrap_ci(te["trade_pnls"])
    raw=evaluate(test,Candidate({"kind":"raw"},.06,.75,1.01,"aligned"))
    market_brier=statistics.fmean((float(r["yes_ask"])/(float(r["yes_ask"])+float(r["no_ask"]))-int(r["outcome_up"]))**2 for r in test)
    credible=bool(te["trades"]>=5 and ci and ci[0]>0 and te["brier"]<market_brier)
    candidate_id="s199-"+hashlib.sha256(json.dumps(asdict(best),sort_keys=True).encode()).hexdigest()[:12]
    frozen={"candidate_id":candidate_id,"source_model":"frozen_s199","parameters":asdict(best),
        "selected_on":"train_and_validation_only","test_evaluated_once":True,"credible_edge":credible}
    atomic_json(candidate_path,frozen)
    report={"rows":len(rows),"partition":{"train":len(train),"validation":len(validation),"test":len(test),"chronological":True},
        "candidate_count":len(candidates),"eligible_candidates":len(ranked),"multiple_testing_control":"validation score subtracts Gaussian max-selection penalty across the predeclared grid",
        "selected":frozen,"train":{k:v for k,v in tr.items() if k!="trade_pnls"},
        "validation":{**{k:v for k,v in va.items() if k!="trade_pnls"},"selection_penalty":penalty,"penalized_score":score},
        "untouched_test":{**{k:v for k,v in te.items() if k!="trade_pnls"},"mean_trade_pnl_ci95":ci},
        "frozen_original_test":{k:v for k,v in raw.items() if k!="trade_pnls"},"market_probability_test_brier":market_brier,
        "conclusion":"EDGE" if credible else "NO_EDGE",
        "limitations":["only 122 observations","all observations are at 60 seconds to expiry, so time rules cannot be compared without leakage",
            "historical logs omit full depth for rejected entries; optimizer assumes two shares at logged best ask","candidate search is exploratory and not profitability proof"]}
    atomic_json(output_path,report); return report
