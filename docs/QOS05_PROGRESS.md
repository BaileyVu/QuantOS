# QOS05 progress

Base verified clean: `8396eadf8a6e5047221e88ba5e58ed77d1f87e08`, documentation
only above QOS04 implementation `276e570`. Branch `codex/qos05-first-tradable-alpha`.
Baseline full suite: 1,226 passed in 47.461 seconds. Hypothesis/campaign precommit:
`dda433b`; rules are frozen in QOS05_PRECOMMIT.md. No outcome-guided tuning.

Implemented Q05-LR-v1 causal range/displacement/reclaim, direct existing Risk and
Execution integration, target/boundary exits, stable signal ID and durable source
context. Existing HFT/Q04 entry modes are preserved; Q05 uses an explicit separate
strategy/account identity and public Binance feeds only. Captures contain every
candidate decision. Replay qualification writes exclusive new outputs, preserves
source hashes/timing, and adds an explicit 100ms processing allowance.

Shared stationary bootstrap sampler reused for attributed closed-trade statistics;
<30 trades means no inferential estimate or Monte Carlo claim. Partial/censored
trades remain explicit. Synthetic profits remain separate from market evidence.
Final focused tests: 133 passed, including existing statistical audit tests.
Final full suite: 1,239 passed. Compilation/import, CLI help, pip check and diff
checks pass. QOS04 saved capture still replays exactly. A sampler extraction
regression caught by the first full run was fixed before code freeze. Historical candidate
outcomes have not been evaluated yet; both source identities are frozen.

Next: finish full suite/diff review, commit implementation, one development pass
and one reserved confirmation pass plus fixed stress, then final-code 600-second
public smoke, replay and final report. No push, merge or authenticated action.


Implementation freeze: `4ba46cc3714278a24163cf023cc6d9fe8833cd4d`.
Final focused 133 passed (3.654s); full 1,239 passed (48.556s).
One development and one reserved confirmation outcome pass completed, each with
the single precommitted cost stress. No rule changed after outcomes.
Development: 5,936 evaluations (4,832 warmup / 1,104 range below 60bps).
Confirmation: 7,932 evaluations (2,622 warmup / 5,310 range below 60bps).
Both: zero signals/orders/fills/closed trades/costs/PnL/drawdown. Stress same.
Evidence = INSUFFICIENT_EVIDENCE; promotion = NO_GO; no further candidate/search.
Final-code 600-second public smoke is running on a fresh isolated $200 state in
artifacts/qos05/paper/. Finish terminal replay and final report; no source edits.


Final 600-second smoke completed at frozen implementation, exit 0 / duration_expired.
27,361 received target events, 4,998 feature decisions: 3,939 warmup and 1,059
range-below-60bps. Four latency disarms/reconstructions, one degraded scheduled
clock recalibration. Zero signals/orders/cancels/fills/fees/funding/PnL/drawdown.
Fresh account stays $200, flat, no pending order. Terminal replay matches exactly;
offline status/ledger reconcile. Both qualification baselines and both fixed stress
captures replay exactly; original source SHA256 checks unchanged. No code changed
after freeze, no historical artifact mutation, no generated state tracked.

QOS05 complete. ENGINEERING_PASS; evidence INSUFFICIENT_EVIDENCE; promotion NO_GO.
Final report: docs/QOS05_FINAL_REPORT.md. Final delivery commit is documentation
only (`git rev-parse HEAD`). Stop; no new campaign, push, merge or authenticated
operation. Recommended future engineering action is clock/continuity diagnostics,
subject to a separate owner instruction.
