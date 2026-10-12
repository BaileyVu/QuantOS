# QOS-07 operator commands

Run in an ordinary macOS Terminal at `QuantOS`.
The foreground Python process is owned by that Terminal, independently of a
coding-agent turn. Keep the Mac awake and connected; sleep/network loss is not
continuous market coverage. No daemon, login item or scheduled task is installed.

## Fresh account and preflight

Choose a never-used account path. Initialization refuses existing files.

```sh
.venv/bin/quantos futures retail-edge-reset --strategy qos07 --capital-profile configs/retail_full_equity_paper.toml --market-config configs/futures_hft_200_paper.toml --state-store artifacts/qos07/operator/account.json --confirm-new-paper-account
.venv/bin/python -m quantos.interfaces.retail_runtime preflight --state-store artifacts/qos07/operator/account.json --market-config configs/futures_hft_200_paper.toml --capital-profile configs/retail_full_equity_paper.toml
```

Offline preflight verifies installed dependencies, configuration, checksummed
account, profile, lock availability, unresolved session/kill markers and 9 GiB
free disk. The managed startup then validates current public exchange metadata,
account identity, server clock, WebSocket access and sequenced snapshot bridge.
All network adapters are public; no credentials are read by this path.

## Foreground operation

```sh
.venv/bin/python -m quantos.interfaces.retail_runtime run --state-store artifacts/qos07/operator/account.json --market-config configs/futures_hft_200_paper.toml --capital-profile configs/retail_full_equity_paper.toml --session artifacts/qos07/operator/session-001 --duration-seconds 300
```

Use a new session directory on every launch. Duration is required and capped at
43,200 seconds; QOS-07 verification uses only 300 seconds. `startup.json` is
fsynced before dependency/network work; `preflight.json`, bounded `runtime.jsonl`
(64 MiB), hash-chained `events.jsonl` (8 GiB) and `exit.json` provide evidence.
`exit.json` records actual process result and structured failure. Exit code zero
means the process completed, not that Alpha qualified or the feed was healthy;
inspect observations, reconnects/disarms and replay. Disk/log/journal failures
stop operation rather than silently dropping evidence.

## Status and safe stop

In a second Terminal:

```sh
.venv/bin/python -m quantos.interfaces.retail_runtime status --session artifacts/qos07/operator/session-001
.venv/bin/quantos futures retail-edge-status --state-store artifacts/qos07/operator/account.json
.venv/bin/quantos futures retail-edge-kill --state-store artifacts/qos07/operator/account.json
```

Ctrl-C or SIGTERM also requests a controlled stop. Working entries are cancelled,
Risk is latched off, state is flushed, and unfilled exposure is retained honestly.
Stopping is not a guaranteed flatten: an unavailable market cannot fill an exit.
Status displays balance, Risk, profile, pending quantity, stop/target and remaining
position. The session lock distinguishes an active process from termination
without an exit record, without trusting a reused PID. Kill checks normally run
within 0.5 seconds while ingesting; bootstrap/network calls retain their bounded
timeouts. SIGKILL/power loss cannot write a terminal record.

## Replay and recovery

```sh
.venv/bin/quantos futures retail-edge-replay --capture artifacts/qos07/operator/session-001/events.jsonl
```

A complete capture must reconcile exact decisions, Risk, account, ledger and
costs. An incomplete capture is rejected; do not append a fabricated terminal
record. A `.session.json` account marker survives failures and blocks reuse:
the last account checkpoint may omit later captured fills. Preserve the account,
marker and entire session; resolve their accounting discrepancy before an owner
explicitly clears the marker. There is no automatic rollback, reset or loss wipe.
If reconciliation cannot be proved, keep the account blocked. A clean normal
completion clears the marker. New entries after a killed run remain prohibited
by `.kill` and persisted `risk_off`; do not silently rearm these protections.

A normal restart with a verified open position cancels stale pending orders,
restores its exact stop and residual quantity, discards old monotonic eligibility,
rebuilds the book, and requests protective recovery closure on valid later depth.
No stale quote is revived. Post-reconnect Alpha features warm up from fresh data.

## Alpha attachment

The application `RetailAlphaProvider` contract has `reset()` and
`observe(HftFeatures, received_monotonic_ns) -> ProtectedRetailSignal | None`.
The typed decision carries identity, side, expected move/uncertainty, book ID,
causal observation/expiry, holding horizon, absolute stop and target. None is
NO_TRADE. Supply it through `RetailPaperEngine(alpha_provider=...)`; registration
and replay selection belong to a future composition-root change, not Risk or
Execution. The current CLI selects only frozen Q05-LR-v1. Test providers are
synthetic fixtures and are never selectable on public feeds. Economic admission,
headroom sizing, protected simulation and evaluation use the same core path.
