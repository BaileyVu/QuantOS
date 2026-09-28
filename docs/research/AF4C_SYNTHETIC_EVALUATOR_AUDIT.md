# AF4C Step 8B — synthetic evaluator audit

Verdict: **PASS — AF4C SYNTHETIC EVALUATOR READY FOR AUDIT**.

This records implementation and deterministic synthetic verification only. It
does not establish market evidence, independent confirmation, paper approval,
live approval, or permission to execute the DEVELOPMENT screen.

## A–D. Repository state and scope

- **A. Starting HEAD:** `8a48faa0365e990de1bc13511016ca6bef746d96`.
- **B. Branch:** `codex/af4c-de1-price-impact-divergence-execution`.
- **C. Added files:**
  - `src/quantos/domain/evaluation/af4c.py`
  - `src/quantos/infrastructure/storage/af4c_shadow.py`
  - `tests/unit/test_af4c_evaluator.py`
  - `tests/unit/test_af4c_shadow.py`
  - `docs/research/AF4C_SYNTHETIC_EVALUATOR_AUDIT.md`
- **D. Frozen evidence:** the four frozen AF4C preregistration files were compared
  byte-for-byte against HEAD and remain identical. Documents 000–008 also have
  no Git diff. No pre-existing tracked file was modified.

## E–K. Architecture, boundary and numerical contract

**E. Architecture:** pure Evaluation-domain calculations and a separate storage
adapter. No new production module, trading integration, loader, execution CLI,
unseal command, or acquisition dependency. O1/O2/O3 remain untouched.

**F. Input boundary:** `align_observation` accepts materialized domain
`AggregateTradeMinuteState` and `Candle` objects. It validates state, completed
minute chronology, t+1m+5s readiness and exact symbol/UTC-minute alignment. A
missing join yields no observation. `MinuteObservation` carries permitted signal
fields; `CandlePoint` carries outcome prices. The evaluator accepts immutable
tuples through `SyntheticInputs`, requires a `synthetic:` identity, rejects
duplicates and inconsistent shared Candle prices, and labels all evidence
`SYNTHETIC`. A future real-data authorization and binding boundary is not included.

**G. Exact signal value fields:**

1. `de1.total_base_quantity`
2. `de1.total_quote_notional`
3. `de1.aggressive_buy_base_quantity`
4. `de1.aggressive_sell_base_quantity`
5. `de1.aggressive_buy_quote_notional`
6. `de1.aggressive_sell_quote_notional`
7. `candle.open`
8. `candle.close`

Symbol and exact UTC minute are alignment keys. Signal functions receive only
the causal lookback and same-minute ETH support where required. No high/low,
volume, trade counts, raw events, raw IDs, or future candles enter those functions.
MFE/MAE is not computed.

**H. Numerical policy:** inherited `af1-decimal-50-half-even-tcdf-v1`, including
isolated precision, rounding and traps. Scientific arithmetic uses Decimal.
Only the inherited Student-t CDF retains its existing float implementation.

**I. Reuse:** `af1_decimal_context`, mean, standard error, Student-t statistic and
p-value, SplitMix64 bootstrap, greedy de-overlap, temporal-stability helper,
`benjamini_hochberg`, `ScoreBands`, `EvidenceScoringConfig`,
`ProvisionalClassificationConfig`, classification helper and
`ResearchClassification`. Private imports are an intentional research-only pin
to inherited AF1/AF3 semantics. Bootstrap uses 200 samples, confidence 0.95 and
root seed 20260914; seed material also binds catalog, evaluation, evaluator and
input identity digest. Metadata explicitly records the inherited two-sided test.

**J. AF1:** `alpha_funnel.py` is byte-identical to HEAD. AF1 t+1 behavior and
artifact implementation were not changed; its regression suite passed.

**K. AF4C chronology:** `tplus2_outcome` requires all H exact consecutive target
candles from t+2 through t+(H+1). Return is close(t+H+1)/open(t+2)-1. H=1 uses
open/close of t+2; H=5 exits at close(t+6). No AF1 outcome function is called.

## L–M. Frozen candidates and comparators

All definitions and the nine ordered evaluation identities come from the
cryptographically pinned catalog. The evaluator verifies canonical bytes and
the exact frozen catalog digest, so semantic mutations fail even after rehashing.

| Hypothesis | Candidate trigger | Descriptive comparator | Binding / H |
| --- | --- | --- | --- |
| H1 | I > 0 and D > 0 | Unconditional eligible long | BTC→BTC, ETH→ETH / 1 |
| H2 | V_buy > V_sell and D > 0 | close > open | BTC→BTC, ETH→ETH / 5 |
| H3 | D_t > 0 and D_t > D_(t-1) | R_t > 0 and R_t > R_(t-1) | BTC→BTC, ETH→ETH / 1 |
| H4 | I_5t > 0 and close_t > V_5t | close_t > open_(t-4) | BTC→BTC, ETH→ETH / 5 |
| H5 | I_BTC > 0 and D_BTC > 0 and D_ETH <= 0 | BTC close > open and ETH close <= open | BTC+ETH→ETH / 5 |

V=Q/B; I=(Q_buy-Q_sell)/Q; D=(close-V)/V; side VWAPs use their respective
quote/base totals; R=close/open-1. H4 uses the exact five-minute sums. Undefined
required denominators yield ineligibility, without imputation. No extra
parameter set, horizon, hypothesis, comparator or symmetric H5 is available.

## N–U. Eligibility, evidence and advancement

- **N. Eligibility:** exact source support, defined required values, complete
  lookback, frozen boundaries and complete t+2/H outcome are all required.
  Candidate and comparator share that base support. Undefined minutes do not
  enter either denominator. F uses retained candidate events per 1440 eligible
  decision minutes.
- **O. De-overlap:** ascending absolute state-minute indices; retain earliest,
  then require spacing >=H from the previous retained event. Candidate and
  comparator are selected independently. This does not assert IID observations.
- **P. Support:** `[2024-01-01T00:00:00Z, 2025-10-01T00:00:00Z)`; common last
  state minute `2025-09-30T23:53:00Z`. First H1/H2/H5 state is 00:00; H3 is
  00:01; H4 is 00:04 on 2024-01-01. There is no boundary-relaxation flag.
- **Q. Eight blocks:** `floor(absolute_minute_index*8/total_interval_minutes)`.
  Gaps and sparse events do not move boundaries. Qualifying blocks require >=10
  events; coverage below 0.75 makes robustness undefined and R=0. Evidence is
  positive qualified blocks divided by all eight expected blocks. The inherited
  maximum-R cap requires at least six positive qualified blocks. Candidate and
  comparator block counts, means and defined mean differences are recorded.
- **R. Costs:** base `0.002503128284573645913980997751940`; stress
  `0.005006256569147291827961995503880`. Exact 2C equality is checked. Each
  cost is subtracted once from gross mean, after the gross-return statistical test.
- **S. BH:** exactly nine ordered candidate tests, alpha 0.05. Missing, extra,
  duplicate, unknown or reordered result identities are rejected. Undefined
  p-values stay registered as conservative p=1 internally; inherited undefined
  p/q presentation remains null. Comparators never enter BH.
- **T. RVS/classification:** frozen M/S/F/R bands and X=4; M+S+F+R+X <=20;
  S uses max(t,0). KILL for no usable expectancy or base expectancy <0. PROMOTE
  requires RVS>=13, events>=100, q<=0.05 and base expectancy>=BASE_COST.
  Otherwise WATCH. These are provisional research classifications.
- **U. Incremental information:** a separate boolean requires primary PROMOTE
  and a defined, strictly positive candidate-minus-comparator aggregate gross
  mean difference. It never rewrites primary classification or pairs trades.

## V–Y. Result identity and shadow publication

**V. Identity:** immutable canonical bytes bind the full frozen catalog, exact
nine definitions, catalog/family/evaluator IDs, preregistration commit,
implementation and numerical versions, statistical/cost configuration, explicit
synthetic identity, separate input-content digests, and all result rows. SHA-256
of the full payload is its result/run identity. No absolute paths, wall-clock
timestamps, process IDs, or object representations enter identity.

**W. Sealing:** the storage adapter exclusively creates a fresh caller-selected
directory, writes `sealed/payload.json`, verifies its bytes, then writes
`receipt.json`. Tests publish only beneath temporary directories. No result-based
filenames, overwrites, automatic unsealing or operational unseal API are present.
Before publication, validation recomputes statistics, bootstrap, block evidence,
BH, scoring, classifications and incremental decisions from sealed event rows.
It rejects noncanonical, rehashed inconsistent, or altered-provenance evidence.

**X. Public receipt fields:** `schema`, `completed_successfully`, `data_kind`,
`catalog_id`, `fdr_family_id`, `evaluator_version`,
`registered_evaluation_count`, `sealed_payload_sha256`,
`input_identity_sha256`, `integrity_status`, `shadow`, `sealed`.

**Y. Leak proof:** tests assert that exact structural allowlist, exclude every
hypothesis/evaluation ID and prohibited predictive field/label, and capture
publication stdout to prove it is empty. Full payload bytes are inspected only
by synthetic tests.

## Z–AG. Synthetic proof coverage

| Report item | Evidence |
| --- | --- |
| Z. Formula tests | Five triggers, all sign/equality boundaries, undefined base/quote/side values, H3 prior state, H4 exact window, H5 ETH D=0 and exact joins |
| AA. t+2 adversarial tests | Distinct t+1 and t+2 prices; H=1/H=5 exact results; missing intermediate outcomes; exact final support minute |
| AB. Comparator tests | Every comparator can disagree with its candidate; shared eligibility; separate greedy event counts; aggregate/block differences; p/q independent of comparator |
| AC. Statistical parity | Exact inherited mean, standard error, t, p, bootstrap, BH, score thresholds, classification and greedy behavior; complete engine scoring checks |
| AD. BH adversarial tests | Nine slots, deterministic ties, undefined p=1 counting, all undefined, extra/missing/duplicate/unknown/reordered identities |
| AE. Block tests | First/last minute and all seven transitions, full coverage, <10 events, 5/8 and 6/8 qualification, maximum-R cap, sparse eight-block integration |
| AF. Decimal contexts | Identical full canonical results at precision 6 and 80, different rounding and Inexact traps enabled |
| AG. Catalog binding | Exact catalog/family/evaluator IDs, five hypotheses/nine evaluations, malformed/duplicate/noncanonical bytes and mutations including reidentified definitions |

## AH–AN. Validation and stopping boundary

- **AH. DEVELOPMENT market data:** none accessed. Synthetic timestamps exercise
  the frozen interval without opening market files. No real identity binding or
  screen was started.
- **AI. AF4B outcomes:** none inspected. Only repository preregistration and
  required preregistration tests were used. No process or live artifact inspection.
- **AJ. Tests:** Python **3.12.10**, using the repository `.venv` interpreter.
  **160 tests passed**, including **54 new synthetic tests**, with this gate:

  ```text
  python -m unittest tests.unit.test_af4c_evaluator tests.unit.test_af4c_shadow tests.unit.test_alpha_funnel_af4c_preregistration tests.unit.test_af4b_preregistration tests.unit.test_alpha_funnel
  ```

  The AF1 suite includes its affected evaluation/storage regression coverage.
  Imports and frozen catalog parsing passed. No configured lint/type-check
  tools were found in the repository configuration; none were claimed as run.
- **AK. Whitespace:** `git diff --check` passed; new untracked files were also
  checked separately because ordinary Git diff does not include them.
- **AL. Git status:** only the five added files listed in C are untracked;
  no tracked changes, staging, commits, pushes, merges or PR creation.
- **AM. Concerns/tradeoffs:** intentional private AF1 helper imports require
  inherited regression protection. Student-t bit identity retains the pinned
  Python/runtime dependency. Shadow separation is not encryption or OS access
  control; publication assumes a trusted caller-controlled directory. Receipt
  publication is last; interrupted writes may leave incomplete files and do not
  justify completion. Event-summary verification checks internal scientific
  consistency, not source authenticity; future real execution still requires
  separately authorized immutable source binding, completeness, health and DE1G
  validation. No implementation claims those future gates have been satisfied.
- **AN. Verdict:** **PASS — AF4C SYNTHETIC EVALUATOR READY FOR AUDIT**.

No network access occurred. Work stops at synthetic evaluator verification.
