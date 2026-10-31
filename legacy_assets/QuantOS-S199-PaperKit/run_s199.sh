#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/vendor/prediction-market-backtesting/.venv/bin/python"
RUNTIME="$ROOT/runtime/paper"
MODE="${1:-}"
export PYTHONPATH="$ROOT:$ROOT/vendor/prediction-market-backtesting${PYTHONPATH:+:$PYTHONPATH}"
unset POLYGON_PRIVATE_KEY POLYMARKET_PRIVATE_KEY POLYMARKET_API_KEY POLYMARKET_API_SECRET POLYMARKET_API_PASSPHRASE PRIVATE_KEY WALLET_PRIVATE_KEY
[[ -x "$PY" ]] || { echo "Missing pinned Python runtime: $PY" >&2; exit 2; }
mkdir -p "$RUNTIME"

case "$MODE" in
  doctor)
    "$PY" -m s199_v2.cli doctor "$ROOT"
    "$PY" -m pytest "$ROOT/tests_v2" -q
    "$PY" -m s199_v2.cli smoke "$ROOT" "$ROOT/runtime/smoke" --seconds 30
    ;;
  paper)
    if [[ -f "$RUNTIME/paper.pid" ]] && kill -0 "$(cat "$RUNTIME/paper.pid")" 2>/dev/null; then
      echo "Paper engine is already running (PID $(cat "$RUNTIME/paper.pid"))." >&2; exit 2
    fi
    rm -f "$RUNTIME/paper.pid"
    "$PY" -m s199_v2.daemon "$ROOT" "$RUNTIME" "$RUNTIME/paper.log"
    for _ in {1..15}; do [[ -f "$RUNTIME/paper.pid" ]] && break; sleep 1; done
    [[ -f "$RUNTIME/paper.pid" ]] || { tail -50 "$RUNTIME/paper.log" >&2; exit 3; }
    PID="$(cat "$RUNTIME/paper.pid")"
    kill -0 "$PID" 2>/dev/null || { tail -50 "$RUNTIME/paper.log" >&2; exit 3; }
    echo "Started paper-only S199 engine PID $PID"
    echo "Log: $RUNTIME/paper.log"
    ;;
  status)
    "$PY" -m s199_v2.cli status "$RUNTIME"
    ;;
  stop)
    [[ -f "$RUNTIME/paper.pid" ]] || { echo "No paper PID file."; exit 0; }
    PID="$(cat "$RUNTIME/paper.pid")"
    if kill -0 "$PID" 2>/dev/null; then
      kill -TERM "$PID"
      for _ in {1..30}; do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
      kill -0 "$PID" 2>/dev/null && { echo "Process did not stop cleanly." >&2; exit 3; }
    fi
    rm -f "$RUNTIME/paper.pid"
    "$PY" -m s199_v2.cli status "$RUNTIME"
    ;;
  report)
    "$PY" -m s199_v2.cli report "$RUNTIME" "$RUNTIME/paper_report.json"
    ;;
  *) echo "Usage: ./run_s199.sh {doctor|paper|status|stop|report}" >&2; exit 2 ;;
esac
