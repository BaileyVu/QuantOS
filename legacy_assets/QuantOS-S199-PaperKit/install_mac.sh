#!/usr/bin/env bash
set -euo pipefail
KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UPSTREAM="$KIT_DIR/vendor/prediction-market-backtesting"
PIN="c76e77af00ef53472a9da8f66dae7fdd2d3e5928"
REPO="https://github.com/evan-kolberg/prediction-market-backtesting.git"
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo 'This installer targets macOS. For Linux, follow the upstream setup documentation.' >&2
  exit 2
fi
for prog in git uv rustc cargo make python3; do
  if ! command -v "$prog" >/dev/null 2>&1; then
    echo "Missing $prog. See README.md prerequisites. No installation commands were auto-executed." >&2
    exit 2
  fi
done
mkdir -p "$KIT_DIR/vendor"
if [[ -d "$UPSTREAM/.git" ]]; then
  echo "Using existing pinned upstream checkout at $UPSTREAM"
else
  git clone "$REPO" "$UPSTREAM"
fi
cd "$UPSTREAM"
# No automatic updates to a mutable default branch.
git fetch --no-tags origin "$PIN"
git checkout --detach "$PIN"
FOUND="$(git rev-parse HEAD)"
[[ "$FOUND" == "$PIN" ]] || { echo "Repository SHA mismatch: $FOUND" >&2; exit 3; }
if [[ -f .env ]]; then
  echo 'REFUSING: upstream .env exists. Remove or isolate credentials before proceeding.' >&2
  exit 4
fi
python3 "$KIT_DIR/doctor.py" --upstream "$UPSTREAM" --offline
make install
make native-develop
python3 "$KIT_DIR/doctor.py" --upstream "$UPSTREAM" --offline
printf '\nSandbox-only dependencies installed. Next run: ./run_s199.sh build\n'
