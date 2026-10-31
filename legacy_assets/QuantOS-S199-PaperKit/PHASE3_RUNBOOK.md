# S199-V2 Phase 3 Runbook

Phase 3 is intentionally disabled while any critical checkpoint is not `PASS`. These commands never request wallet credentials.

```bash
cd "$HOME/Desktop/QuantOS-S199-PaperKit"

# Verify baseline, safety components, and deterministic tests.
./run_s199_v2.sh doctor

# Retrieve public official evidence, then score verified labels only.
./run_s199_v2.sh acquire
./run_s199_v2.sh fees
./run_s199_v2.sh score

# Inspect the gate. Do not launch if decision is NO-GO.
python3 -m json.tool S199_V2_CHECKPOINT.json

# Paper launch (currently fail-closed by design).
./run_s199_v2.sh paper
```

When execution integration is later accepted, use a new named experiment once, then reuse its SQLite file on every restart. Never delete or replace that ledger to reset cash.

Monitoring, clean stop, restart, and export commands for the future approved runner:

```bash
tail -F runtime/v2/<EXPERIMENT>/paper.log
kill -TERM "$(cat runtime/v2/<EXPERIMENT>/paper.pid)"
./run_s199_v2.sh paper --experiment <EXPERIMENT>
sqlite3 runtime/v2/<EXPERIMENT>/ledger.sqlite3 '.backup runtime/v2/<EXPERIMENT>/ledger-export.sqlite3'
cp S199_V2_CHECKPOINT.json runtime/v2/<EXPERIMENT>/
cp s199_v2/artifacts/phase2/{acquisition_summary.json,calibration_report.json,labels.jsonl,raw_responses.jsonl} runtime/v2/<EXPERIMENT>/
```

Do not use `kill -9` except for an unresponsive process; SIGTERM is required for an atomic final checkpoint. Do not start live-money trading.
