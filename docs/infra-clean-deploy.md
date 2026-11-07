# Clean deployment snapshots

`scripts/deploy_three.py` now refuses to prepare a deployment when the source
repository has tracked, untracked, or submodule changes. It checks out the
current full commit SHA into a detached Git worktree at
`~/Library/Application Support/QuantOS/app-v4/<full-sha>/source`; launchd jobs
use that worktree's `src/` directory. Repeated preparations reuse that
snapshot only when its commit and cleanliness checks still pass. Existing
snapshots are retained so a previously deployed full SHA remains available for
rollback.

If worktree creation or its post-checks fail, the newly created worktree is
unregistered and its directory removed. This change only prepares source
snapshots; deployment still requires the existing explicit install/restart
commands and the approved operating procedure.
