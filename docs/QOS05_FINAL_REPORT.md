# QOS05 — first tradable Alpha delivery

**ENGINEERING_PASS. Evidence: INSUFFICIENT_EVIDENCE. Promotion: NO_GO.**
Q05-LR-v1 is implemented and closed without promotion: zero genuine qualifying
signals/trades in the bounded campaign. Profitability is unestablished. Stop
QOS05; no new candidate, tuning, acquisition or authenticated deployment follows.

## Commits and implementation

Branch: `codex/qos05-first-tradable-alpha`.
Clean QOS04 base: `8396eadf8a6e5047221e88ba5e58ed77d1f87e08`.
Rules/campaign frozen before outcomes: `dda433b`.
Tested implementation: **`4ba46cc3714278a24163cf023cc6d9fe8833cd4d`**.
Final delivery adds only this report/progress; its SHA is `git rev-parse HEAD`.
No push, merge, authenticated request, real order, deposit or leverage increase.

Changed modules (under `src/quantos/`):

- New `domain/features/reclaim.py`, `domain/alpha/reclaim.py`: causal rolling range
  and one fixed liquidity-reclaim mechanism.
- `application/retail_trader.py`, `retail_edge_session.py`, `retail_replay.py`,
  new `reclaim_qualification.py`: shared paper/replay route, isolated strategy,
  stable signal identity, candidate records and counterfactual source provenance.
- `domain/execution/retail_paper.py`: persisted target/boundary/source context and
  observed-price/slippage attribution using existing order authority.
- `domain/evaluation/retail.py`, new `reclaim.py`, `monte_carlo.py`: attributed
  closed/censored trades, descriptive statistics and reused seeded block sampler.
- `interfaces/retail_edge.py`: explicit `--strategy qos05`, separate account identity
  and exclusive qualification captures. Historical modes remain available.
- `tests/unit/test_reclaim.py`; `docs/QOS05_PRECOMMIT.md`, `QOS05_PROGRESS.md`,
  this report. No historical configs, frozen 000–008 specs or data were changed.

## Frozen Alpha and economics

[Full precommit and targeted LuxAlgo references](QOS05_PRECOMMIT.md).
Prior 300-second causal range must span >=60bps; an observed displacement beyond
its boundary must exceed max(10bps, twice event volatility). Within 120 seconds,
require an actual 2bp reclaim, >=5 seconds since displacement, >=3 seconds without
an adverse new extreme, favorable five-second flow and OBI >=0.15. Symmetric
long/short futures conditions; spread <=10bps and volatility <=20bps.

Frozen prior-range midpoint is the target. Its remaining distance is an
uncalibrated opportunity hypothesis, discounted by max(5bps, half that distance).
Existing Risk must approve full round-trip taker fees, spread, slippage, latency,
funding and safety costs. Intent expires at 2 seconds; cooldown is 300 seconds.
Exit on target, lost reclaim boundary, 20bp adverse stop, invalid data/Risk,
reversal or 900-second hold limit. Observed later liquidity after acknowledgment
is required to fill; no target/stop touch alone creates a fill.

$200 initial capital; realized-equity sizing, <=1x exposure, <=1% modeled risk,
3% daily loss/drawdown limits unchanged. BTCUSDC paper taker input is 4bps/leg
(BTCUSDT floor 5bps), baseline slippage 1bp/leg and stop uncertainty 2bps. This
is not verification of a private fee tier or exact exchange liquidation price.
No forced fills, averaging down, fee reductions or parameter mining occurred.

## Genuine data, chronology and outcomes

Read-only Binance BTCUSDC depth/trades/book ticker/public mark-funding captures;
original exchange/receipt/processing times and source order are preserved.
No candle-to-L2 reconstruction or synthetic flow entered economic evidence.
Full input hashes and precise timestamps are in the precommit and scratch
`artifacts/qos05/source-manifest.json`; hashes were rechecked unchanged at finish.
Q04 external observations remain traceable in historical input but Q05 does not
use them. Its live session connects only Binance public streams.

| Period (UTC, 2026-10-11) | Target events received | Feature evaluations | Warmup rejections | Range <60bps rejections |
|---|---:|---:|---:|---:|
| Development 12:02:24.519715–12:13:45.953425 (681.433710s) | 27,642 | 5,936 | 4,832 | 1,104 |
| Reserved confirmation 18:12:12.604741–18:27:11.060872 (898.456131s) | 49,842 | 7,932 | 2,622 | 5,310 |
| Technical paper 19:10:36.460619–19:20:34.566875 (598.106256s observed within 600s runtime) | 27,361 | 4,998 | 3,939 | 1,059 |

**Every period:** 0 displacements/confirmed signals, 0 cost admissions/Risk
approvals/orders/cancels/entry fills/exit fills/closed or censored trades.
Gross/net PnL, fees, funding, slippage, turnover and maximum drawdown: **$0 / 0%**.
Initial/final equity: **$200 / $200**. Observed trade frequency: 0/hour in these
specific short windows; not an estimated future trading rate. Expectancy,
profit factor, average win/loss and confidence intervals are **undefined**.

Development and reserved confirmation were each evaluated once under the frozen
rules, plus one precommitted cost stress: 1.5x taker fees, 3bps slippage/leg,
300ms acknowledgment. Stress also produced zero trades; no trade reached cost
admission, so this does not establish economic robustness. Both baseline and
both stress output captures replay exactly. Each includes candidate/rejection
records tied to source sequence and book IDs. No outcome was used to tune rules.

Source limitations: development includes two data disarms/reconstructions and
its original terminal operator kill, all retained. The later confirmation window
is reserved for Q05, but was previously used for Q04 technical validation; it is
not a newly collected independent market sample. Only ~26 minutes of historical
coverage, fragmented warmup and a 15-minute hold horizon provide no defensible
regime/expectancy sample. Monte Carlo and uncertainty inference are **not run on
market trades** because each period has 0, below the precommitted 30-trade floor.
Seed 505 / 1,000 simulations / mean block 5 are tested only on segregated synthetic
fixtures. No synthetic return appears in this economic-results table.

Historical counterfactual processing uses the latest captured clock sample plus
an explicit 100ms allowance, then records actual consumed synthetic clock inputs
for exact replay. This is a disclosed timing assumption, not an observed historical
Q05 order. The preserved source account is never loaded as Q05 operating equity.

## Final-code validation and public smoke

Native **Python 3.12.14 arm64**. Baseline: 1,226 tests pass. Final focused:
**133 pass** (3.654s); full offline: **1,239 pass** (48.556s), zero failures.
Compilation/import, CLI help, pip dependency check and diff checks pass. No lint
or type checker is configured. Final evidence: `artifacts/qos05/focused-verified.txt`
and `full-verified.txt`. An initial sampler-extraction regression was caught by
the full suite and fixed before implementation freeze; original failure logs
remain preserved. QOS04's saved final capture still replays exactly unchanged.

Fixtures cover causal/symmetric reclaim, sustained adverse trend, expiry, clock
regression/gaps, full-cost and Risk rejection, profitable/losing executions,
liquidity shrink/cancellation, post-ack partial fills/no liquidity reuse,
fee/slippage loss, invalid-data disarm, censored state and restart source context.
Existing compounding, funding, authentication-fake and safety suites also pass.

Final public smoke on frozen `4ba46cc`: **600 seconds, exit 0, duration_expired**.
Four stale-feed disarms triggered fresh reconstruction: 1652.400, 1530.697,
2559.459 and 1559.902ms exceeded the unchanged 1500ms limit. Initial
clock calibration was HEALTHY; scheduled calibration DEGRADED. Reconnect warmups
explain much of the rejected coverage; no timing tolerance was relaxed.
Final account is flat, no pending order, risk_off=false, kill_requested=false.
Reset proof: `artifacts/qos05/paper-reset.json`; account identity
`3f6a0e8a02feeaae70e5f88637617b503aa98a046ee86cce9fb7c176e504a156`.
Terminal checkpoint: `2026-10-11T19:20:37.591145Z` (2026-10-12 locally).
Public-only route/authentication counts are architectural/test evidence, not a
packet audit. Q04 candidates were never selected in this Q05 route.

Paper exact replay matches the full terminal report and reconciled durable
account. Decision digest:
`411dcf4c118477acad5f0fff23278e3fe22f60f108c7be5a7209ebadf1795069`.
Ledger digest:
`0acbe1323c7755b79d8fadfed7e15ace7d046d32faa7395746b113c8e8ab7d8b`.
Evidence is scratch-only under `artifacts/qos05/`; none is Git-tracked.

## Reproduce and stop

Run from the repository root. Existing evidence paths are immutable; choose a
new output filename/account directory before rerunning commands that write.

```sh
.venv/bin/python -m unittest tests.unit.test_reclaim tests.unit.test_retail_replay tests.unit.test_retail_operations tests.unit.test_retail_paper tests.unit.test_retail_edge tests.unit.test_retail_intelligence tests.unit.test_hft_authenticated tests.validation.test_architecture tests.unit.test_v1t2_evaluation tests.unit.test_v1t2_audit -q
.venv/bin/python -m unittest discover -s tests -t . -q
.venv/bin/quantos futures retail-edge-replay --capture artifacts/qos05/development/events.jsonl
.venv/bin/quantos futures retail-edge-replay --capture artifacts/qos05/confirmation/events.jsonl
.venv/bin/quantos futures retail-edge-replay --capture artifacts/qos05/paper/events.jsonl
.venv/bin/quantos futures retail-edge-status --state-store artifacts/qos05/paper/account.json
```

Exact campaign construction (use previously unused output paths):

```sh
.venv/bin/quantos futures retail-edge-replay --capture artifacts/qos04/final/events.jsonl --qualify-reclaim --output-capture artifacts/qos05/development/events.jsonl
.venv/bin/quantos futures retail-edge-replay --capture artifacts/qos04/final-verified/events.jsonl --qualify-reclaim --output-capture artifacts/qos05/confirmation/events.jsonl
# Fixed stress adds --stress and uses development-stress/ or confirmation-stress/.
```

Exact executed paper commands (do not reset or overwrite the completed account):

```sh
.venv/bin/quantos futures retail-edge-reset --strategy qos05 --state-store artifacts/qos05/paper/account.json --confirm-new-paper-account
.venv/bin/quantos futures retail-edge-paper --strategy qos05 --state-store artifacts/qos05/paper/account.json --capture artifacts/qos05/paper/events.jsonl --duration-seconds 600
```

**One next engineering action:** improve public-feed clock/latency diagnostics
and continuity before any separately authorized future qualification. No such
additional work begins in QOS05. Candidate closed for insufficient evidence;
engineering correctness does not grant economic viability or deployment approval.
