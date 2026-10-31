#!/bin/zsh
# Prepare (and optionally install) the $50 USDC paper run. Paper only: no keys are read, no orders sent.
#   scripts/deploy_live50.sh prepare   copy code + create a fresh state folder (safe, starts nothing)
#   scripts/deploy_live50.sh install   bootstrap the launchd paper service + watchdog (STARTS the run)
# Code is copied outside ~/Desktop because launchd jobs hit macOS privacy (TCC) blocks there.
set -euo pipefail
REPO=${0:A:h:h}
COMMIT=$(git -C "$REPO" rev-parse --short HEAD)
BASE="$HOME/Library/Application Support/QuantOS"
APP="$BASE/app-v4/$COMMIT"
STATE="$BASE/v4-live50"
# Base interpreter of ../QuantOS/.venv (stdlib-only runtime; lives outside ~/Desktop).
PY=$("$REPO/../QuantOS/.venv/bin/python" -c 'import os,sys;print(os.path.realpath(sys.executable))')
SENTIMENT="$HOME/Desktop/PythonProject/Claude-QuantOS/sentibot/paper_perp_signals.jsonl"

case "${1:-}" in
  prepare)
    [[ -e "$STATE/run.json" ]] && { echo "state already has a run: $STATE"; exit 1; }
    mkdir -p "$APP" "$STATE"
    rsync -a --delete --exclude '__pycache__' "$REPO/src/" "$APP/src/"
    chmod 700 "$STATE"
    PYTHONPATH="$APP/src" "$PY" -m quantos.paper.cli doctor
    echo "code: $APP/src"; echo "state: $STATE"; echo "python: $PY"
    ;;
  install)
    [[ -d "$APP/src" ]] || { echo "run prepare first"; exit 1; }
    cd "$APP"
    PYTHONPATH="$APP/src" "$PY" -m quantos.paper.cli install-service --state "$STATE" --hours 72 \
      --min-quote-volume 5000000 --portfolio live50 --sentiment-file "$SENTIMENT" \
      --pythonpath "$APP/src" --watchdog
    ;;
  *) echo "usage: $0 prepare|install"; exit 2 ;;
esac
