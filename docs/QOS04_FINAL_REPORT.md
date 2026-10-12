# QOS04 consolidated report

Status: **ENGINEERING_PASS; ALPHA_PASS = NO; DEPLOYMENT_READY = NO.**
Final code completed the 900-second public-only qualification and exact replay.
QOS-04 is complete; await Ivy/owner acceptance before any authenticated deployment.

## Branch and reviewed implementation

Branch: `codex/qos04-unified-retail-edge`.
Verified clean base: `cfaba786ee5e57dd9796c2be6c6e68477471d36b`.
Frozen implementation HEAD: `276e5708df614219c030829663a1e5d485cc1bca`.
The final delivery commit adds evidence/documentation only; identify that HEAD
with `git rev-parse HEAD`. No push, merge, or authenticated exchange action.

| Checkpoint | Local commit | Evidence |
|---|---|---|
| A | `34a8a1d71a3a476fd716d7de220a08df8d25966c` | 33 focused tests; funnel, identity, expiry and liquidity regressions |
| B | `6c925d9406edb4daaa2737df89372f656fa187f4` | 39 focused tests; public observations, freshness and hash-chain capture |
| C | `65435af66f9d2a0319e37063ae5c05f3dfdf970e` | 44 focused tests; two fixed causal candidates |
| D | `7dfea1d8c9c3b251de43b3af79f5737d8a0b6b2f` | 47 focused tests; costs, funding and loss controls |
| E | `419d2903b74def63012497e436b58e64c76caa47` | 50 focused / 1,214 full; genuine development capture and exact replay |
| F | `a680556bc9bcefb1ba6bbf10a0df90381ced37f3` | 93 focused / 1,224 full; fresh install, restart, kill, protocol and pre-fill safety |
| Clock correction / final code | `276e5708df614219c030829663a1e5d485cc1bca` | 95 focused / 1,226 full; actual submission clock and exact sampled-clock replay |

## Implemented files and specification changes

- Market Data / Features: `domain/market_data/intelligence.py`,
  `domain/features/intelligence.py`; provider-independent identity, UTC and
  monotonic availability, provenance, validity, sequence, freshness and causal
  windows. Duplicate/regressed/future observations cannot add favorable evidence.
- Alpha: `domain/alpha/retail.py`, `retail_edge.py`; stable attribution and
  Q04-LL-v1 relative-return lead–lag / Q04-LD-v1 forced-flow continuation. Fixed
  thresholds and explicit uncertainty; QOS03 retained as diagnostic baseline.
- Risk / Execution / Evaluation: `domain/risk/retail.py`,
  `domain/execution/retail_paper.py`, `domain/evaluation/retail.py`; full costs,
  daily/drawdown latches, unlevered collateral proxy, conservative funding,
  per-price liquidity, terminal accounting and candidate-level metrics.
- Application: `application/retail_trader.py`, `retail_replay.py`,
  `retail_edge_session.py`; one causal processor shared by capture and replay,
  full funnel, source isolation, bounded public session and pre-fill revalidation.
- Infrastructure: `infrastructure/public_data/retail.py`,
  `infrastructure/storage/retail_events.py`, `retail_paper.py`; public-only
  adapters, bounded exclusive hash-chain capture, durable reconciled schema 2,
  exclusive account lock and atomic state.
- CLI: `interfaces/retail_edge.py`, routing additions to `interfaces/futures.py`;
  reset/paper/replay/stress/status/kill commands.
- Tests: `test_retail_paper.py`, new `test_retail_intelligence.py`,
  `test_retail_edge.py`, `test_retail_replay.py`, `test_retail_operations.py`;
  architecture transport allowlist scoped to public information adapters.
- Owner-authorized scope references added to AGENTS and `docs/000_READ_FIRST.md`.
  `QOS04_RETAIL_ADDENDUM.md`, `QOS04_EXPERIMENTS.md`, `QOS04_OPERATIONS.md` and
  `QOS04_PROGRESS.md` record requirements, experiments, commands and evidence.
  Paths above are under `src/quantos/` or `tests/unit/` as appropriate.

Existing HFT behavior, configurations, $100 profiles, datasets and historical
artifacts were not changed. No new dependency or generated state was tracked.

## Validation and accounting

Native arm64 Python **3.12.14**. Fresh isolated install of the repository and
pinned dependencies succeeded; imports, CLI and `pip check` passed. Initial
sandbox DNS failure was resolved using approved public network access.
Final focused suite: **95 passed, 0 failed**, including 33 mocked authenticated
interface/protocol checks. Final full offline suite in fresh venv:
**1,226 passed, 0 failed**, 45.155 seconds. No configured lint/type checker;
architecture, compilation/import and diff-whitespace checks passed.

Synthetic fixtures verify profitable and losing trades, spread/slippage loss,
partial fills, acknowledgment and expiry boundaries, delayed processing/submission
clock sampling, no reused price liquidity,
wrong/missing data disarm, final Risk rejection, source failure before fill,
once-only funding, restart reconciliation, kill and concurrent-writer rejection.
Profitable/loss full-pipeline fixtures reproduce account and decision digests.
Compounding tests increase size only after realized gains and reduce it after
losses; fresh capital remains exactly $200. Synthetic profits are not market
performance evidence.

The 60-second genuine development capture processed 6,278 causal commands,
273 Coinbase ticker observations and two sampled liquidation observations. All
275 information observations passed ingestion timing checks in that window.
No QOS04 trade qualified; orders/fills/costs/PnL/drawdown were zero and balance
remained $200. Both liquidation observations were below the liquidity gate.
Frozen-code replay matches the captured terminal metrics and digests exactly.
Chronological half-window diagnostics and 1.5x taker fees / 3bp slippage per leg /
300ms acknowledgment stress remain zero-trade. No parameter was tuned to either
window; these are development diagnostics, not an independent validation set.

The old QOS03 zero-candidate cause is not recoverable from its incomplete logs.
The new development window produced QOS03 diagnostic confirmations; no historical
missing records were fabricated.

Risk remains bounded at 1x equity exposure, 1% maximum modeled risk per trade,
and 3% drawdown/daily loss stops. Entry costs include configured taker fees on
both legs, spread, conservative slippage, latency, funding and uncertainty.
BTCUSDC uses the preserved 0.0004 taker floor (BTCUSDT 0.0005); this is an
explicit paper input, not verification of a private account's current fee tier.
No leverage increase, fee reduction, averaging down or automatic deposit exists.
The unlevered collateral distance is a conservative proxy; exact exchange margin
and liquidation tiers remain a deployment prerequisite.

Evidence files include `artifacts/qos04/focused-clock-fix.txt`,
`full-clock-fix.txt`, `fresh-install-approved.txt`,
`development/replay-clock-fix.json`, `development/stress-clock-fix.json` and
`development/chronological.json` (all under `artifacts/qos04/`).

## Public sources and final exercise

Public Binance BTCUSDC depth, trades, book ticker and mark/funding form the target
feed. Public Coinbase BTC-USD ticker supplies relative returns, preserving the
USD/USDC market distinction. Public Binance forced-order snapshots are sampled,
not a complete liquidation tape. No OI, paid data or private provider is claimed.
Protocol references are recorded in the scoped addendum.

An earlier qualification in `artifacts/qos04/final/` was stopped using the
operator kill after a synthetic backlog fixture exposed receipt-based submission
clock anchoring. It is preserved as aborted evidence, not the accepted final
exercise. At shutdown it was flat at $200 with zero orders/fills/PnL. Coinbase
observations in that attempt often had source timestamps about 8ms ahead of the
local receipt clock and were rejected. No timing tolerance was loosened.

Commit `276e570` uses actual processing and submission clock samples, starts
acknowledgment at actual submission, captures the samples and replays them exactly.
A queued pre-submission event cannot fill; an expired signal cannot submit. The
complete offline suites passed before the new `final-verified/` exercise began.
No runtime code changed during that exercise.

Accepted final exercise: **900 seconds**, clean process exit 0, terminal reason
`duration_expired`, final persisted checkpoint `2026-10-11T18:27:14.507909Z`
(2026-10-12 01:27:14 Asia/Ho_Chi_Minh). Code remained at `276e570` throughout.
Evidence: `artifacts/qos04/final-verified/{account.json,events.jsonl,runtime.jsonl,
replay.json,stress.json,status.json}` and `final-verified-reset.jsonl` in the
parent directory. Initial manifest balance and starting equity are both exactly
`200`; new account/configuration identity:
`49c1b1cec781160b793f0c566787b13cc4a3ec2fa6a430f897bf7580659686f6`.

| Final metric | Observed value |
|---|---:|
| Target events received | 49,842 |
| Book ticker / depth / trade / mark events | 39,481 / 7,932 / 1,530 / 899 |
| Valid book / complete feature evaluations | 7,932 / 7,932 |
| Q04 raw candidates / confirmations | 1 / 0 |
| Candidates reaching cost admission / Risk approval | 0 / 0 |
| Orders / cancellations / entry fills / exit fills | 0 / 0 / 0 / 0 |
| Closed trades / turnover | 0 / $0 |
| Fees / funding / gross PnL / net PnL | $0 / $0 / $0 / $0 |
| Starting / final / peak equity | $200 / $200 / $200 |
| Maximum drawdown | 0% |
| Open position / pending order | false / false |
| Target-feed reconnects | 0 |
| Coinbase observations / accepted / stale at ingestion | 2,548 / 2,547 / 1 |
| Liquidation observations / idle timeouts | 0 / 13 |
| Causal processor commands | 52,414 |
| Authenticated requests / economic orders | 0 / 0 |

Both Q04 candidates evaluated 7,932 times. LL produced one raw signal, rejected
by flow/imbalance confirmation. Complete candidate rejection accounting:

| Candidate | NO_TRADE reason | Count |
|---|---|---:|
| Q04-LL-v1 | local warmup | 43 |
| Q04-LL-v1 | leader warmup | 813 |
| Q04-LL-v1 | no leading displacement | 6,126 |
| Q04-LL-v1 | observation not yet available | 15 |
| Q04-LL-v1 | observation stale at decision | 934 |
| Q04-LL-v1 | flow/imbalance not confirmed | 1 |
| Q04-LD-v1 | local warmup | 43 |
| Q04-LD-v1 | missing liquidation | 7,889 |

Decision-time staleness counts repeated feature decisions; they are distinct
from the single stale observation rejected during ingestion. No ambiguous-clock
Coinbase observations occurred in this final window. QOS03 diagnostic-only
baseline recorded 1,405 raw/662 confirmed observations and submitted no orders.
Each candidate has zero closed trades, zero costs/PnL/turnover/drawdown and
**undefined expectancy**, not a measured zero-return expectancy.

Target depth/trade streams remained connected. Initial clock calibration was
HEALTHY; the two scheduled recalibrations reported DEGRADED and each recorded a
clock-recalibration disarm. Shutdown recorded one additional disarm. Final
status depth age was 3,448ms during socket shutdown, not a trading opportunity.
The forced-order source repeatedly connected but each idle read timed out as
`TimeoutError:`; this proves absence of observed messages in this window, not
absence of exchange liquidations or a proven provider outage. External source
clock quality and sparse liquidation coverage remain economic-data limitations.

Baseline replay **exactly matches the complete captured terminal report**, not
only the balance. The predefined stress (1.5x taker fees, 3bps slippage per leg,
300ms acknowledgment) also completes with the same zero-trade result. No cost
admission was reached, so this is not evidence of economic robustness. Persisted
account checksum/ledger reconciliation and last-runtime metrics also match.

Decision digest:
`7ea39d8eb377e6ce12fd426262b9e58a692466caced021e54f825f5d7b4100b7`.
Ledger digest:
`0acbe1323c7755b79d8fadfed7e15ace7d046d32faa7395746b113c8e8ab7d8b`.
Authentication/order counts describe public-only architecture and the exercised
call paths, supported by offline protocol tests; they are not packet-audit claims.


## Readiness and remaining blockers

- ENGINEERING_PASS: **YES** for the scoped public-paper contracts. Final suites,
  fresh install, bounded public qualification, exact replay and reconciliation pass.
- ALPHA_PASS: **NO**. Neither fixed candidate has independent positive after-cost
  evidence. Synthetic gains validate accounting only; short development/public
  windows cannot establish profitability. Both experiments are closed without
  promotion or threshold changes.
- DEPLOYMENT_READY: **NO**. Requires Ivy/owner acceptance, separately authorized
  staged execution, independent chronological economic evidence, provider-clock
  validation, private fee/margin/liquidation-tier verification and settled funding
  and account/order reconciliation. No such authenticated action was attempted.

There is no known unresolved reproducible code defect in the exercised contracts.
Remaining model limitations include sampled/incomplete liquidation information,
external clock uncertainty, conservative funding/stop approximations and the
absence of independent Alpha evidence. The earlier backlog defect was fixed and
regression-tested before the accepted qualification. No claim is made that a
short public window or the offline suite proves all real-world failure modes.

## Reproduction and next action

Use `docs/QOS04_OPERATIONS.md` for install, fresh state, status, kill and recovery.
Exact final focused/full offline validation commands:

```sh
.venv/bin/python -m unittest tests.unit.test_retail_operations tests.unit.test_retail_replay tests.unit.test_retail_edge tests.unit.test_retail_intelligence tests.unit.test_retail_paper tests.unit.test_hft_authenticated tests.validation.test_architecture -q
artifacts/qos04/install-check/bin/python -m unittest discover -s tests -t . -q
```

Exact final-runtime command from repository root:

```sh
.venv/bin/quantos futures retail-edge-paper --state-store artifacts/qos04/final-verified/account.json --capture artifacts/qos04/final-verified/events.jsonl --duration-seconds 900 > artifacts/qos04/final-verified/runtime.jsonl 2>&1
```

The final capture already exists after this run; repeating it must use a fresh
capture filename and reconciled account, or an explicitly created new account.
Never overwrite evidence or reset losses. Reproduce the result offline:

```sh
.venv/bin/quantos futures retail-edge-replay --capture artifacts/qos04/final-verified/events.jsonl
.venv/bin/quantos futures retail-edge-replay --capture artifacts/qos04/final-verified/events.jsonl --stress
.venv/bin/quantos futures retail-edge-status --state-store artifacts/qos04/final-verified/account.json
```

Ivy/owner: review this report, the frozen implementation and replay evidence.
No authenticated deployment before explicit acceptance and separate authority.
