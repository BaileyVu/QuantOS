from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from .atomic import atomic_json
from .ledger import Ledger


def process_alive(pid:int)->bool:
    if pid<=0:return False
    try: os.kill(pid,0); return True
    except (ProcessLookupError,PermissionError): return False


def read_status(runtime:Path)->dict[str,object]:
    status_path=runtime/"status.json"; pid_path=runtime/"paper.pid"
    status=json.loads(status_path.read_text()) if status_path.exists() else {"state":"not_started"}
    pid=int(pid_path.read_text().strip()) if pid_path.exists() and pid_path.read_text().strip().isdigit() else 0
    return {**status,"pid":pid or status.get("pid"),"process_alive":process_alive(pid)}


def build_report(runtime:Path,output:Path)->dict[str,object]:
    ledger=Ledger(runtime/"ledger.sqlite3",experiment="s199-paper",initial_cash="200")
    portfolio=ledger.portfolio(); fills=list(ledger.db.execute("SELECT COUNT(*) FROM fills"))[0][0]
    settlements=list(ledger.db.execute("SELECT COUNT(*) FROM settlements"))[0][0]
    market_evaluations=list(ledger.db.execute("SELECT COUNT(*) FROM market_evaluations"))[0][0]
    market_missed=list(ledger.db.execute("SELECT COUNT(*) FROM market_evaluations WHERE decision='missed'"))[0][0]
    shadow=ledger.shadow_summary(); ledger.close()
    diagnostics=runtime/"diagnostics.jsonl"
    report={"generated_utc":datetime.now(UTC).isoformat(),"status":read_status(runtime),
        "portfolio":{k:str(v) for k,v in portfolio.__dict__.items()},"fills":fills,"settlements":settlements,
        "market_evaluations":{"total":market_evaluations,"missed":market_missed},"shadow":shadow,
        "diagnostic_records":sum(1 for _ in diagnostics.open()) if diagnostics.exists() else 0}
    atomic_json(output,report); return report
