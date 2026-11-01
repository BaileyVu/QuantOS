# S199 Paper Runbook

The engine is paper-only. It uses public market data and has no wallet, signing, deposit, or live-order path.

```bash
cd "$HOME/Desktop/QuantOS-S199-PaperKit"

# Tests plus a bounded 30-second public-feed smoke test.
./run_s199.sh doctor

# Start or resume the same restart-safe $200 virtual account in the background.
./run_s199.sh paper

# Check health, feed state, cash, open risk, and unresolved positions.
./run_s199.sh status

# Monitor diagnostics for the next 24 hours.
tail -F runtime/paper/paper.log runtime/paper/diagnostics.jsonl

# In another Terminal, request a clean SIGTERM checkpoint and stop.
cd "$HOME/Desktop/QuantOS-S199-PaperKit"
./run_s199.sh stop

# Export the final machine-readable account report.
./run_s199.sh report
python3 -m json.tool runtime/paper/paper_report.json
```

Restarting uses the same command and restores the same SQLite ledger:

```bash
cd "$HOME/Desktop/QuantOS-S199-PaperKit"
./run_s199.sh paper
```

Do not delete `runtime/paper/ledger.sqlite3` to reset performance. A two-share order below the live venue minimum is intentionally skipped.
