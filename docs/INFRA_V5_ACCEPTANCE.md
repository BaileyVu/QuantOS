# Infrastructure v5 acceptance status

Implementation drafts PR48–PR56 are open, following contract PR47. No PR has
been merged or deployed by this workstream. Bailey alone merges and deploys.
Passing source checks is not operational acceptance or mainnet authorization.

| Work item | Implemented evidence | Outstanding acceptance |
|---|---|---|
| PR1 stressed-stop sizing | Ported vectors, fees/step rounding, realized-risk close/recovery tests | Owner demo rollout and real fill monitoring |
| PR2 resolver/setup | Repeated lookup recovery, exchange leverage verification, optional fail-closed sentiment flag | Owner demo validation; fail-closed option remains off |
| PR3 protection/settlement | Native TP, independent watchdog, durable intents, order-linked settlement and process-death tests | Real demo kill/network/sleep behavior and reconciliation; a sleeping host cannot enforce local max-hold |
| PR4 clean snapshots | Full-SHA clean worktree and dirty-source rejection tests | Owner review/deployment/rollback on the final intended snapshot |
| PR5 counterfactual shadow | Isolated engine/cost reuse, reporting and dashboard tests | Actual 24-hour demo run; taken shadow trades reconciled with real books |
| PR6 registry/manifests | Manifest integrity, DSR formula checks, historical import proof (4,227 distinct variants) | Canonical persistent registry location must be established before global-N reporting; TimesFM registry is isolated |
| PR7 replay | New no-trade forward soak: 4,006 decision rows exactly matched | Owner replaced original pD requirement with future demo recording: first20fills or3days, exact decisions and per-trade modeled-slippage bounds. No trade-fill parity or turtle/vol-squeeze USDC L2 result |
| PR8 Sentibot | Shadow-only collector, guarded URLs, availability-aware reports, snapshot/plist preparation | Actual 72-hour collection and forward/gate report; one isolated cycle is insufficient and Jina full-text coverage was 0/8 |
| PR9 TimesFM2.5 | Pinned CPU model and fixed 1,029-forecast BUILD volatility comparison | Width failed; owner cancelled crash/dip. One drift-only USDT fixed-cost L2 A/B is being prepared; passing1x/2x earns L3 shadow only. Actual A/B not run |

## Completed operational evidence

Immutable runtime source `4fe48e2` ran the real entrypoint and live-data threads
with a separate state and `--execution dry-run` for 648.261 seconds, exit 0:
607 observations, 2,002 scans, zero error/alert events and no orders/trades.
Its newly captured inputs replayed all 4,006 decision records exactly. This is
not evidence of original pD-period parity, fill reconciliation or profitable
trading. Earlier failed soaks remain documented in the checkpoint.

A final owner-selected deployment SHA still needs the required no-order soak
and rollback preparation for that exact artifact. Do not restart or replace
the existing demo, its state or LaunchAgents to obtain acceptance evidence.

## Data and research limits

The saved hourly archives and fixed 2022 pilot are USDT proxies. They contain
no historical order-book depth or verified USDC listing/availability history.
Do not invent depth or imply historical USDC tradability to obtain an L2 pass.
Original ml1h source is reference-only: its loader touches protected ml5m paths,
its cost model is incomplete and its calibration/label cutoffs need refactoring.
Never execute it as-is or reuse its exploratory HELD results as fresh validation.

The actual TimesFM result and hashes are in `TIMESFM25_BUILD_PILOT_RESULT.md`.
No sizing, strategy or execution behavior was changed based on that result.
Keep sealed 2025+ data out of model selection, preserve all costs and guards,
and record unrun or blocked work explicitly instead of marking this task complete.
