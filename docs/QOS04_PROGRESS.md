# QOS-04 progress and recovery

Branch: `codex/qos04-unified-retail-edge`; verified clean base
`cfaba786ee5e57dd9796c2be6c6e68477471d36b`.

A in progress: final QOS03 baseline 30/30 focused tests pass. Old smoke has 4
book reconstructions and 3 stale-book disarms, but no raw data or Alpha funnel.
The precise historical zero-candidate cause is unidentifiable; do not infer
threshold failure or fabricate missing evidence. Add explicit reasons now.
Fix reconnect attribution collision and price-revisit liquidity reuse.

B–F pending. No Alpha expectancy or deployment gate passes yet.
Next executable action: run new A regressions; commit; implement public source
contract and capture. No authenticated exchange action is authorized.

## Specification reconciliation (owner QOS04 mandate)

HFT 000–008 remain unchanged in behavior. QOS04 permits public external data as
information only, two experimental retail candidates, shared causal replay,
and operational paper controls. QOS03 remains a diagnostic baseline, not a
third selected candidate. The new scoped addendum governs only the QOS04
retail path. Existing $100 state and evidence remain immutable. Starting
capital remains exactly $200; exposure stays at most 1x, normal risk at most
1%, and drawdown stop at most 3%. No financial target enters code.

A complete: 33 focused tests pass (15 retail + 18 architecture). Reconnect-safe
signal identity, full stage/reason counters, exclusive expiry boundary and
per-price consumption regressions pass. No historical reconstruction claimed.

A commit: `34a8a1d`. B complete: 39 focused tests pass. Provider-independent
information contract/cache plus exclusive hash-chain journal, freshness,
duplicate/regression tests and public protocol adapters. Network sandbox DNS
blocked first probe; approved public probe received genuine Coinbase ticker
(sequence 137536496773), whose initial 3.05-second source age is correctly
ineligible. No liquidation event arrived within 15 seconds; not claimed as
observed liquidation flow. Architecture transport allowlist narrowly extended
to information-only public adapters under the owner-approved addendum.
Next: implement two fixed experimental candidates and causal selection tests.

B commit: `6c925d9`. C complete: 44 focused tests pass. Q04-LL-v1 and Q04-LD-v1
implemented with fixed inputs, explicit NO_TRADE reasons, uncertainty, regime
filter, causal availability and deterministic selection. QOS03 is diagnostic
only in unified mode. Ledger records no economic promotion. Next: strengthen
cost/risk/funding and durable accounting; no threshold optimization.

C commit: `65435af`. D complete: 47 focused tests pass. Added uncertainty and
funding hurdles, conservative once-per-settlement funding debits, 3% daily loss
and sticky emergency risk-off, visible-liquidity sizing cap and adverse-price/
fee exposure reserve. Existing profitable/loss/compounding fixtures still pass;
new nonfinite, funding, uncertainty and emergency tests pass. Exact exchange
liquidation tiers remain an authenticated-deployment blocker; public paper is
unlevered. Next: shared replay/live driver, capture and operational CLI.

D commit: `7dfea1d`. E implementation complete: 50 focused and 1,214 full offline
tests pass. Synthetic profitable/loss full pipelines replay exactly; stressed
fees/slippage/acknowledgment tested. Genuine 60-second development capture at
`artifacts/qos04/development/` completed; baseline replay exactly matches its
terminal report and digests; stress replay completed without parameter changes.
No qualifying QOS04 trades; account remained $200. QOS03 diagnostic baseline did
produce confirmations in this new window, proving the old zero count is not a
universal implementation failure; this cannot recover the old missing funnel.
Final 900-second exercise remains pending after F and final-code tests.
Next: operational/failure tests, fresh environment, freeze implementation.

E commit: `419d290`. F complete, implementation freeze: 93 focused tests pass,
including 33 existing offline authenticated/protocol checks; full FINAL source
suite passes 1,224 tests in 48.152s in a freshly installed arm64 Python 3.12.14
venv. Fresh pinned install, imports and pip check pass (sandbox DNS required
approved public package download). Development capture still replays exactly
under final code; stress replay completes. No linter/type checker is configured;
compile/import and architecture checks pass. Operational docs cover exclusive
locks, reconciliation, kill/recovery, bounded capture and unresolved deployment.
Additional final guards: duplicate trade suppression, target-symbol checks,
feature validation before fills, funding freshness after reconstruction, capital
validation and pre-fill Alpha/Risk revalidation. No economic gate was weakened.

Next executable action (code is frozen): create a fresh `artifacts/qos04/final/`
$200 account, run one 900-second public-only session, then replay it and write
QOS04_FINAL_REPORT.md. Do not change runtime code during that final exercise.

Qualification correction (after F): `artifacts/qos04/final/` was explicitly
stopped with the tested operator kill after 670 mark observations, still flat
at $200 with zero orders/fills. It is preserved and is NOT the final accepted
exercise. An offline delayed-processing fixture exposed that the prior engine
anchored submission/ack to receipt time (6.1s/6.2s) despite actual processing at
6.6s. This could produce a premature simulated fill under backlog.

Fix: actual submission clock sampled separately, both processing/submission
samples captured and exactly replayed, expired/queued pre-submission data fails
closed; account/configuration identity bumped to QOS04 v2. 95 focused tests pass,
including 500ms backlog no-fill and sampled-clock exact replay regressions.
Next: full final-code suite, commit correction, fresh `final-verified/` account
and one complete 900-second qualification. Never overwrite the aborted evidence.

Clock correction final validation: 95 focused and 1,226 full offline tests pass
in the fresh native environment (45.155s full suite). Development baseline/stress
and aborted no-order capture replay complete; both baseline captures match their
terminal reports exactly. Runtime code is frozen again. Final accepted exercise
will be `artifacts/qos04/final-verified/`, preserving the earlier attempt intact.


Final qualification completed on frozen code `276e5708df614219c030829663a1e5d485cc1bca`:
900 seconds, exit 0 / duration_expired, fresh `final-verified/` $200 account.
49,842 target events, 7,932 feature evaluations per candidate, 2,548 Coinbase
observations (2,547 valid / 1 stale), no liquidation observations, 13 idle
liquidation timeouts, zero target reconnects. One raw LL signal failed confirmation;
zero orders/cancels/fills/costs/PnL/drawdown. Two scheduled degraded clock
recalibrations disarmed safely. Account remains flat at $200.

Final baseline replay exactly matches terminal metrics, decisions and ledger;
predefined cost stress completes with zero trades. Offline status reconciles.
95 focused / 1,226 full tests remain final-code results; only documentation changed
after the freeze. Final report and experiment ledger close both candidates without
promotion. ENGINEERING_PASS = YES; ALPHA_PASS = NO; DEPLOYMENT_READY = NO.
The earlier approval-review usage-limit blocker was resolved on authorized resume.
No generated artifacts/configs/historical data were tracked or changed.

Final delivery is the documentation-only commit containing QOS04_FINAL_REPORT.md;
resolve its SHA with `git rev-parse HEAD`. All checkpoint/clock-fix SHAs are in
that report. Next executable action: Ivy/owner review the report and exact offline
replay. QOS-04 stops here; no authenticated action, push or merge is authorized.
