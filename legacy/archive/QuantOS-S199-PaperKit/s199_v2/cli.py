from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from .atomic import atomic_json
from .baseline import reproduce
from .phase2 import acquire, enrich_signal_fees, score
from .engine import run_engine
from .optimizer import enrich_historical_fees, optimize
from .reporting import build_report, read_status


def main() -> None:
    parser=argparse.ArgumentParser(description="S199-V2 paper safety and official-outcome audit")
    sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("baseline"); p.add_argument("session",type=Path); p.add_argument("--output",type=Path)
    p=sub.add_parser("acquire"); p.add_argument("log",type=Path); p.add_argument("output_dir",type=Path)
    p=sub.add_parser("fees"); p.add_argument("log",type=Path); p.add_argument("labels",type=Path); p.add_argument("raw",type=Path)
    p=sub.add_parser("score"); p.add_argument("log",type=Path); p.add_argument("labels",type=Path); p.add_argument("settlements",type=Path); p.add_argument("output",type=Path)
    p=sub.add_parser("enrich-history-fees"); p.add_argument("log",type=Path); p.add_argument("labels",type=Path)
    p=sub.add_parser("optimize"); p.add_argument("log",type=Path); p.add_argument("labels",type=Path); p.add_argument("output",type=Path); p.add_argument("candidate",type=Path)
    p=sub.add_parser("engine"); p.add_argument("root",type=Path); p.add_argument("runtime",type=Path)
    p=sub.add_parser("smoke"); p.add_argument("root",type=Path); p.add_argument("runtime",type=Path); p.add_argument("--seconds",type=float,default=12)
    p=sub.add_parser("status"); p.add_argument("runtime",type=Path)
    p=sub.add_parser("report"); p.add_argument("runtime",type=Path); p.add_argument("output",type=Path)
    p=sub.add_parser("doctor"); p.add_argument("root",type=Path)
    args=parser.parse_args()
    if args.command=="baseline":
        result=reproduce(args.session)
        if args.output: atomic_json(args.output,result)
    elif args.command=="acquire": result=acquire(args.log,args.output_dir)
    elif args.command=="fees": result=enrich_signal_fees(args.log,args.labels,args.raw)
    elif args.command=="score": result=score(args.log,args.labels,args.settlements,args.output)
    elif args.command=="enrich-history-fees": result=asyncio.run(enrich_historical_fees(args.log,args.labels))
    elif args.command=="optimize": result=optimize(args.log,args.labels,args.output,args.candidate)
    elif args.command=="engine": raise SystemExit(asyncio.run(run_engine(args.root,args.runtime)))
    elif args.command=="smoke": raise SystemExit(asyncio.run(run_engine(args.root,args.runtime,args.seconds)))
    elif args.command=="status": result=read_status(args.runtime)
    elif args.command=="report": result=build_report(args.runtime,args.output)
    else:
        required=[args.root/"strategy_candidate.json",args.root/"FINAL_CHECKPOINT.json",
            args.root/"vendor/prediction-market-backtesting/live/models/btc_snapshot_model_s199_cost101_daily_stop120_profile.json"]
        unsafe=[name for name in ("POLYMARKET_PRIVATE_KEY","PRIVATE_KEY","WALLET_PRIVATE_KEY") if os.getenv(name)]
        result={"files":{str(path):path.is_file() for path in required},"unsafe_credentials_present":unsafe,
            "paper_only":True,"pass":all(path.is_file() for path in required) and not unsafe}
        if not result["pass"]: raise SystemExit(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2,sort_keys=True))


if __name__ == "__main__": main()
