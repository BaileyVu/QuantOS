# QOS04 local public-paper operations

Run from the repository root. Requires native Python 3.12 (>=3.11 supported),
network access to public APIs, and no exchange credentials. Reproduce install:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m pip check
.venv/bin/quantos futures retail-edge-paper --help
.venv/bin/python -m unittest discover -s tests -t . -q
```

Fresh account and bounded capture (choose an unused directory; no overwrite):

```sh
.venv/bin/quantos futures retail-edge-reset --state-store artifacts/qos04/new-session/account.json --confirm-new-paper-account
.venv/bin/quantos futures retail-edge-paper --state-store artifacts/qos04/new-session/account.json --capture artifacts/qos04/new-session/events.jsonl --duration-seconds 900 > artifacts/qos04/new-session/runtime.jsonl 2>&1
.venv/bin/quantos futures retail-edge-status --state-store artifacts/qos04/new-session/account.json
.venv/bin/quantos futures retail-edge-replay --capture artifacts/qos04/new-session/events.jsonl
.venv/bin/quantos futures retail-edge-replay --capture artifacts/qos04/new-session/events.jsonl --stress
```

Status/replay/kill are offline. Status reports persisted totals and the last
checkpoint UTC; a checkpoint older than 10 seconds requires operator attention.
The live loop also disarms when depth becomes stale and prints status every five
seconds. Provider failures, missing observations, candidate reasons and costs
are distinct from an empty order ledger. Never treat no forced-order messages
as proof of no liquidation activity. Authentication counts are architectural
invariants backed by dependency/protocol tests, not network packet inspection.

```sh
.venv/bin/quantos futures retail-edge-kill --state-store artifacts/qos04/new-session/account.json
```

Kill latches risk-off, cancels pending orders and stops the run. Missing depth
never produces an invented closing fill: an exposed account stays exposed in
state and requires recovery. A restarted account cancels pending orders, validates
its ledger and waits for a fresh book before attempting a risk-reducing exit.
Removing an operator kill file is a deliberate local operator action; it does
not clear the durable account risk-off latch. There is no automatic reset,
rearm, deposit or risk-limit increase. Never use reset to hide losses.

State schema 2/configuration identity is isolated from historical QOS03/$100
accounts; no implicit migration. Exclusive OS lock prevents concurrent writers;
atomic replacement, checksums, directory sync and ledger reconciliation detect
corruption. Configuration/metadata changes fail closed. A replay capture stores
its rules, configuration, initial account, exact command order and clock inputs.
Truncation or digest mismatch fails; no silent replay repair. Session metrics
restart at zero while fills, realized balance, peak, loss latch and account ledger
persist. Captures spanning restarts remain independently replayable.

Retention: each capture is exclusive and capped at 256 MiB, runtime duration at
1800 seconds. Hitting capture capacity stops instead of dropping evidence. Use
one fresh directory per run; archive completed directories externally as immutable
evidence. No automatic deletion/rotation of archived originals is implemented.
Do not commit captures, state, lock files, logs, venvs or generated reports.

## Deployment gates

ENGINEERING_PASS requires final focused/full offline tests, deterministic replay,
fresh install and a final-code public window. ALPHA_PASS requires independent
positive net expectancy; one short public window or synthetic profits cannot
meet it. DEPLOYMENT_READY additionally requires Ivy/owner acceptance, independent
chronological/walk-forward and robustness evidence, authorized real fee/margin
and liquidation-tier verification, exact settled funding reconciliation,
exchange account/order reconciliation and explicit staged testnet/shadow approval.

No QOS04 command has a signed client, credential loader or economic order adapter.
Existing authenticated interface tests use fakes only. No test result grants
permission to use those interfaces against an exchange. Await Ivy acceptance.
