#!/bin/bash
# Prepare-only by default. Owner must review the emitted plan before --execute.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="${DEMO_HELPER_PYTHON:-/usr/bin/python3}"
exec "$PYTHON" "$SCRIPT_DIR/demo_nonstop.py" --repo "$REPO" "$@"
