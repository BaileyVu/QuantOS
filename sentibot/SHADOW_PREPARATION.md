# Preparing the Sentibot shadow service

`scripts/prepare_sentibot_shadow.py` only creates a clean detached worktree
snapshot, a new mode-0700 external state directory, and a LaunchAgent plist at
the requested output path. It does not write `~/Library/LaunchAgents`, invoke
`launchctl`, install packages, or start the collector. The plist uses the
separate `com.quantos.sentibot.reach-shadow` label and calls only
`sentibot.sentibot_reach_shadow` from the SHA-pinned source worktree.

Choose new paths for the snapshot root, state directory, and plist file; the
helper refuses existing state or plist outputs and refuses dirty source. Pass
the exact executable path of the reviewed Python environment:

```sh
python3 scripts/prepare_sentibot_shadow.py \
  --snapshots "$HOME/Library/Application Support/QuantOS/sentibot-shadow-snapshots" \
  --state-dir "$HOME/Library/Application Support/QuantOS/sentibot-shadow-state" \
  --plist "$HOME/Library/Application Support/QuantOS/sentibot-shadow-review.plist" \
  --python /absolute/path/to/reviewed/python
```

The snapshot is stored at `<snapshot-root>/<full-40-character-commit>/source`.
The plist sets `--every 900` and `--cycle-timeout 600`; it records output logs
inside the new state directory. Review the commit SHA, source status, paths,
interpreter, and plist before any owner-controlled installation. Rollback before
installation is simply deleting those three newly-created outputs and pruning
the detached worktree with `git worktree remove <snapshot-path>` from the source
repository. This preparation helper intentionally provides no start, stop,
install, or restart action.

The tests build a temporary Git repository and verify the SHA path, plist
contents, new state permissions, and dirty-tree refusal. They never read or
modify the user's LaunchAgents or Sentibot state. A real service run, network
availability, rollback readiness, and the required 72-hour measurement remain
unverified until the owner reviews and deploys a snapshot.
