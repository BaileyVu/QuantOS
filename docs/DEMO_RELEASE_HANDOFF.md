# Demo release handoff template

Owner-only runbook draft for a release cut from PR55 (`788aa97`) plus the two
capture-hook commits. No commands here were run. Replace every `<...>` value
from the owner-reviewed release record before deploying.

## Release identity and snapshot

- Release checkout: `<ABSOLUTE_PATH_TO_CLEAN_RELEASE_CHECKOUT>`
- Release full SHA: `<RELEASE_FULL_SHA>`
- Expected immutable app snapshot: `~/Library/Application Support/QuantOS/app-v4/<RELEASE_FULL_SHA>/source`
- Previous full SHA: `<PREVIOUS_FULL_SHA_FROM_OWNER_RELEASE_RECORD>`
- Previous snapshot: `~/Library/Application Support/QuantOS/app-v4/<PREVIOUS_FULL_SHA>/source`

Verify before an owner deployment:

```sh
REPO="<ABSOLUTE_PATH_TO_CLEAN_RELEASE_CHECKOUT>"
RELEASE_SHA="<RELEASE_FULL_SHA>"
test "$(git -C "$REPO" rev-parse HEAD)" = "$RELEASE_SHA"
test -z "$(git -C "$REPO" status --porcelain=v1 --untracked-files=all --ignore-submodules=none)"
```

`scripts/deploy_three.py::clean_snapshot` derives the full SHA from the clean
checkout and makes a detached worktree under `~/Library/Application Support/QuantOS/app-v4/<SHA>/source`.
`install-demo200` then builds the service `PYTHONPATH` from that snapshot. For
a fresh install, the owner command is:

```sh
cd "$REPO"
../QuantOS/.venv/bin/python scripts/deploy_three.py install-demo200
```

This is a multi-service installer: it starts `com.quantos.v4.demo200`, its
watchdog, `com.quantos.mlalts`, both `com.sentibot.spot` and
`com.sentibot.perp`, and the dashboard. It requires a new demo state (it exits
if `~/Library/Application Support/QuantOS/v4-demo200/run.json` already exists)
and a mode-600 demo key file. It is not a state-preserving update command.

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

## Rollback record and limitation

The previous full SHA and snapshot must come from the owner’s release record or
the exact prior service plist; this handoff did not inspect live state or
LaunchAgents. Verify the selected snapshot before any rollback:

```sh
PREVIOUS_SHA="<PREVIOUS_FULL_SHA_FROM_OWNER_RELEASE_RECORD>"
PREVIOUS_SOURCE="$HOME/Library/Application Support/QuantOS/app-v4/$PREVIOUS_SHA/source"
test "$(git -C "$PREVIOUS_SOURCE" rev-parse HEAD)" = "$PREVIOUS_SHA"
test -z "$(git -C "$PREVIOUS_SOURCE" status --porcelain=v1 --untracked-files=all --ignore-submodules=none)"
```

No state-preserving `demo200` rollback command exists in the inspected
`deploy_three.py`: `install-demo200` refuses the existing `run.json`, while
`start_demo_nonstop.sh` is a fresh-start script that uninstalls services and
archives the current `v4-demo200` state before starting again. The rollback
procedure therefore needs an owner-approved state-preserving plist/restart
command before release; do not use the fresh-start script as an implicit
rollback.

The checked-in `/Users/bailey12/Desktop/PythonProject/start_demo_nonstop.sh`
hard-codes candidate SHA `26a91e5` and checkout
`~/Desktop/PythonProject/QuantOS-nonstop-26a91e5`. That is source evidence of
the script’s chosen candidate only, not evidence that it is the previous
active snapshot. It also fetches a branch, uninstalls multiple services,
archives `v4-demo200`, changes an alt-ML kill file, and starts the legacy
installer; it is incompatible with a narrow release update and must not be
treated as proof of current service state.
