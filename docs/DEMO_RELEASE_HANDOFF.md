# Demo release handoff template

Owner-only runbook draft for a release cut from PR55 (`788aa97`) plus the two
capture-hook commits. No commands here were run. Replace every `<...>` value
from the owner-reviewed release record before deploying.

## Release identity and snapshot

- Release checkout: `<ABSOLUTE_PATH_TO_CLEAN_RELEASE_CHECKOUT>`
- Release full SHA: `<RELEASE_FULL_SHA>`
- Expected immutable app snapshot: `~/Library/Application Support/QuantOS/app-v4/<RELEASE_FULL_SHA>/source`
- Verified previous full SHA: `5d8342d4e90613783a93a08a58bbc662906236b4`
- Previous installed `PYTHONPATH`: `/Users/bailey12/Library/Application Support/QuantOS/app-v4/5d8342d/src`

The old `5d8342d` path prefix resolves to the full SHA above in local repository
metadata. The installed plist's Python executable, demo/watchdog settings and
environment are used as templates; the key file itself is not read.

Verify before an owner deployment:

```sh
REPO="<ABSOLUTE_PATH_TO_CLEAN_RELEASE_CHECKOUT>"
RELEASE_SHA="<RELEASE_FULL_SHA>"
test "$(git -C "$REPO" rev-parse HEAD)" = "$RELEASE_SHA"
test -z "$(git -C "$REPO" status --porcelain=v1 --untracked-files=all --ignore-submodules=none)"
```

`scripts/deploy_three.py::clean_snapshot` derives the full SHA from the clean
checkout and makes a detached worktree under `~/Library/Application Support/QuantOS/app-v4/<SHA>/source`.
The narrow `scripts/start_demo_nonstop.sh` wrapper is prepare-only by default.
From the clean release checkout, prepare candidate plists and a redacted plan:

```sh
scripts/start_demo_nonstop.sh --plan-dir "$HOME/Library/Application Support/QuantOS/release-plans/<RELEASE_FULL_SHA>"
```

It verifies a clean exact-SHA snapshot, copies the current demo/watchdog plist
settings, retains their Python executable and environment, changes only
`PYTHONPATH`/hours, and sets `QUANTOS_ML1H_SIGNALS` to the existing signal file.
It checks that `signals.json` exists without reading it. The plan and candidate
plists are written outside LaunchAgents and outside demo state. Review those
plists and the plan before the owner invokes:

```sh
scripts/start_demo_nonstop.sh --plan-dir "$HOME/Library/Application Support/QuantOS/release-plans/<RELEASE_FULL_SHA>" --execute --confirmed-flat
```

`--confirmed-flat` is an owner attestation that Binance Demo has no positions or
open orders; the helper does not read the key file or query the exchange. It
requires the existing `run.json` status to be `completed`, unloads/reloads only
`com.quantos.v4.demo200` and `com.quantos.v4.demo200.watchdog`, archives the
completed `v4-demo200` directory under a unique UTC timestamp, and creates the
new nonstop state. It refuses a non-completed state or a state already owned by
an engine lock. Do not use `deploy_three.py install-demo200` here: that broad
installer also starts alt-ML, both Sentibot producers, and the dashboard.

## Sentibot reach shadow

`scripts/prepare_sentibot_shadow.py` only prepares a detached clean source
snapshot, a new private state directory, and a plist. It explicitly refuses to
write under `~/Library/LaunchAgents`; it does not install or start the service.
The shadow service label is `com.quantos.sentibot.reach-shadow`; its only output
is `reach_shadow.jsonl` in its dedicated `--state-dir`. Keep this state
separate from Sentibot's producer state. The existing Sentibot producer still
writes `paper_perp_signals.jsonl`; the shadow collector must not write or
replace that file.

Owner preparation template (all paths must be new and outside the source tree
and snapshot root):

```sh
REPO="<ABSOLUTE_PATH_TO_CLEAN_RELEASE_CHECKOUT>"
QOS="$HOME/Library/Application Support/QuantOS"
SNAPSHOTS="$QOS/sentibot-shadow-snapshots"
SHADOW_STATE="$QOS/sentibot-reach-shadow"
PLIST_OUT="$QOS/release-artifacts/com.quantos.sentibot.reach-shadow.plist"
PINNED_PYTHON="<ABSOLUTE_EXECUTABLE_PYTHON_PATH>"
cd "$REPO"
../QuantOS/.venv/bin/python scripts/prepare_sentibot_shadow.py \
  --repo "$REPO" --snapshots "$SNAPSHOTS" --state-dir "$SHADOW_STATE" \
  --plist "$PLIST_OUT" --python "$PINNED_PYTHON" --every 900 --cycle-timeout 600
```

Review the emitted plist and snapshot SHA before owner installation. Only then
may the owner install and start it:

```sh
install -m 600 "$PLIST_OUT" "$HOME/Library/LaunchAgents/com.quantos.sentibot.reach-shadow.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.quantos.sentibot.reach-shadow.plist"
```

## Quote archive

Owner-provided command (not run):

```sh
nohup /usr/bin/python3 ~/Desktop/PythonProject/edge_data/depth/archive_quotes.py --loop 3600 --keep-days 30 >> ~/Desktop/PythonProject/edge_data/quotes_archive/archive.log 2>&1 &
```

## Rollback

The helper stores the previous demo/watchdog plists in the reviewed plan. With
the new run still resumable, the owner can restore the exact previous source
path and those plists without archiving the current state:

```sh
scripts/start_demo_nonstop.sh --rollback --plan-dir "$HOME/Library/Application Support/QuantOS/release-plans/<RELEASE_FULL_SHA>" --execute --confirmed-flat
```

The previous runtime cannot resume a nonstop `deadline_ms: null` state: it
expects a finite deadline. Rollback therefore saves the current `run.json` in
the plan directory, changes only `hours` to 72 and `deadline_ms` to 72 hours
from rollback time, then starts the old snapshot. Journal and capture files
remain in the same state directory. The helper requires the owner flat
attestation and an unlocked engine state before this metadata adjustment.

The older workspace-level `/Users/bailey12/Desktop/PythonProject/start_demo_nonstop.sh`
hard-codes candidate SHA `26a91e5` and checkout
`~/Desktop/PythonProject/QuantOS-nonstop-26a91e5`. That is source evidence of
the script’s old candidate only. It fetches a branch, uninstalls multiple
services, archives `v4-demo200`, changes an alt-ML kill file, and starts the
legacy installer; it is not the new narrow wrapper and must not be used for
this release.
