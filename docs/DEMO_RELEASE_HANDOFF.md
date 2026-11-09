# QuantOS v6 owner release handoff

PR57 is based on PR55 (788aa97), excluding PR56 research. Merge PR47 through PR55
in order, then PR57. PR56 remains parked. Owner alone merges and deploys.

**Pre-launch acceptance passed on the exact snapshot below:** the fresh-state
soak ran650.56seconds, exit0, zero journal errors, zero alerts and zero capture
errors. Both public and authenticated testnet account streams were connected.
Capture has1,344records, including719runtime inputs,11consumed sentiment inputs,
one ml1h input and verified start/stop journal snapshots. Replay matched all
6,414decisions exactly, with complete sentiment/external/exchange context.
No fills occurred: this is decision-layer evidence, not fill acceptance.

The initial650.89-second attempt failed on stale Sentibot input. Its producer
refreshed naturally; no file, freshness rule or service was changed for the
successful retry. The passing window does not prove continuous input freshness
or24-hour health. Retain the earlier4,006-decision proof separately.

Full suite ran once:508passed,4skipped. Later stream/launcher changes passed
16runtime tests,one separate-client test and6launcher tests. Subsequent commits
change only documentation/checkpoint, not the tested runtime or scripts.
Evidence is saved outside Git at
`/Users/bailey12/Desktop/PythonProject/QuantOS-release-evidence/72c8cd71d19ed8f64944f4c7b61874d9a6896491/`.
No service or existing demo state was changed.

## Prepared release

Tested source: `72c8cd71d19ed8f64944f4c7b61874d9a6896491`.
The PR51 clean-snapshot function prepared the detached worktree below.
The narrow launcher prepared the plan and candidate/previous plists outside
LaunchAgents. Preparation is complete; do not repeat it into existing outputs.
Review the plan and plists before running the owner-only command.

Confirm Binance Demo has no positions or open orders. The helper additionally
rejects unresolved local journal/mirror orders and requires the prior run to be
completed. It archives that completed state intact and starts only demo200 and
its watchdog. The installed Python/environment are retained; the new run uses
hours=0 and the existing ml1h input. No alt-ML or producer services are changed.

```sh
QOS="$HOME/Library/Application Support/QuantOS"
RELEASE_SHA="72c8cd71d19ed8f64944f4c7b61874d9a6896491"
RELEASE="$QOS/app-v4/$RELEASE_SHA/source"
PLAN="$QOS/release-plans/demo-nonstop-$RELEASE_SHA"
test "$(git -C "$RELEASE" rev-parse HEAD)" = "$RELEASE_SHA" &&
  test -z "$(git -C "$RELEASE" status --porcelain=v1 --untracked-files=all --ignore-submodules=none)" &&
  "$RELEASE/scripts/start_demo_nonstop.sh" --plan-dir "$PLAN" --execute --confirmed-flat
```

Use this repository wrapper, not the older Desktop-level start_demo_nonstop.sh
or broad install-demo200 installer, which touch other services.

## Sentibot shadow

Preparation succeeded against the same snapshot. Its new private state is
`~/Library/Application Support/QuantOS/sentibot-reach-shadow-72c8cd7`.
The prepared plist uses the pinned service Python3.12, a 900-second cadence and
600-second cycle timeout. It writes only reach_shadow.jsonl in that state,
never paper_perp_signals.jsonl. Review it, then the owner installs/starts it:

```sh
QOS="$HOME/Library/Application Support/QuantOS"
PLAN="$QOS/release-plans/demo-nonstop-72c8cd71d19ed8f64944f4c7b61874d9a6896491"
TARGET="$HOME/Library/LaunchAgents/com.quantos.sentibot.reach-shadow.plist"
test ! -e "$TARGET" &&
  install -m 600 "$PLAN/com.quantos.sentibot.reach-shadow.plist" "$TARGET" &&
  launchctl bootstrap "gui/$(id -u)" "$TARGET"
```

## Quote archiver

The script exists; the log directory did not exist at preparation, so create it
before running the owner's supplied command:

```sh
mkdir -p "$HOME/Desktop/PythonProject/edge_data/quotes_archive"
nohup /usr/bin/python3 ~/Desktop/PythonProject/edge_data/depth/archive_quotes.py --loop 3600 --keep-days 30 >> ~/Desktop/PythonProject/edge_data/quotes_archive/archive.log 2>&1 &
```

## Rollback

Verified previous source: `5d8342d4e90613783a93a08a58bbc662906236b4`, installed
at `~/Library/Application Support/QuantOS/app-v4/5d8342d/src`.
Only while the new run is resumable and the owner has confirmed exchange flatness:

```sh
QOS="$HOME/Library/Application Support/QuantOS"
RELEASE_SHA="72c8cd71d19ed8f64944f4c7b61874d9a6896491"
"$QOS/app-v4/$RELEASE_SHA/source/scripts/start_demo_nonstop.sh" \
  --rollback --plan-dir "$QOS/release-plans/demo-nonstop-$RELEASE_SHA" \
  --execute --confirmed-flat
```

Rollback stops only the demo/watchdog pair, checks the engine lock and local flat
state, backs up run.json into the plan, sets hours and the CLI duration to 72
and deadline_ms to rollback time +72h, then restores the old snapshot/plists.
The old runtime cannot accept a null nonstop deadline. Journal and capture files
stay in place; rollback is a 72-hour timed run.

## After launch

Build the per-fill report while capture accumulates. At the first 20 real fills
or three days, compare exact decisions and each independent modeled fill against
its real Binance fill within modeled slippage; report error per trade. Shadow
reconciliation is due at24h and Sentibot reporting at72h. These gates remain open.
