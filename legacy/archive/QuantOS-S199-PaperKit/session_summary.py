#!/usr/bin/env python3
"""Summarize ONE Nautilus sandbox session. Never combine restart sessions as true equity."""
from __future__ import annotations
import datetime as dt
import json
import sys
from pathlib import Path


def summarize(session_dir: Path) -> dict:
    meta=json.loads((session_dir/"session.json").read_text())
    path=session_dir/"s199_settlements.json"
    base={"session_id":meta["session_id"],"initial_cash":meta["initial_virtual_usd"],
          "trading_mode":"SANDBOX_ONLY", "restart_safe":False,"settlement_ledger_present":path.exists()}
    if not path.exists():
        return {**base,"status":"no_fills_or_no_settlement_ledger","settled_count":0,"unsettled_count":None,
                "account_equity_usd":None,"data_warning":"No evidence of completed execution; do not invent PnL."}
    j=json.loads(path.read_text())
    positions=j.get("positions",[])
    base["settled_count"]=sum(bool(t.get("settled")) for t in positions)
    base["unsettled_count"]=len(positions)-base["settled_count"]
    base["total_simulated_fills"]=len(positions)
    base["position_slugs"] = sorted(set(t.get("slug","") for t in positions if t.get("slug")))
    base["latest_upstream_synthetic_equity"]=j.get("summary",{}).get("synthetic_equity")
    base["status"]="unsettled_positions" if base["unsettled_count"] else "settled_or_empty"
    base["data_warning"]="Exchange-authenticated fills NOT obtained. Equity is upstream session-only and is NOT restart-safe."
    return base


def main():
    if len(sys.argv)!=2:
        raise SystemExit("Usage: python3 session_summary.py runtime/runs/<session_dir>")
    root=Path(sys.argv[1])
    summary=summarize(root)
    (root/"audit_summary.json").write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n")
    print("Session-only audit: "+json.dumps(summary,sort_keys=True))

if __name__=="__main__":
    main()
