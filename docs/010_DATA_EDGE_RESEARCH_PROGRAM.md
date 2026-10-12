# QuantOS Data Edge Research Program

Version: 1.0
Status: frozen research direction; implementation and each phase require separate human authorization.

## 1. Authority and scope

This additive program is subordinate to **all frozen authoritative QuantOS
specifications, docs 000–009**. [000_READ_FIRST.md](000_READ_FIRST.md) remains the
highest authority; 001–007 remain coequal, 008 remains subordinate implementation
guidance, and [009](009_ALPHA_DISCOVERY_FUNNEL_AF1.md) retains its research and
sealed-OOS controls. Document numbering does not grant 010 precedence.

This document freezes the next research direction. It does not authorize
production changes, acquisition, implementation, experiment execution, or reserve
access by itself. Approval of a planned information family is distinct from
approval to build or consume it. Each phase boundary requires human review and an
explicit scope and data-access decision.

The six production modules, Clean Architecture dependency direction, local-first
deployment, Binance Spot BTCUSDT/ETHUSDT instruments, completed 1-minute decisions,
Parquet/DuckDB architecture, one production strategy, one production model,
production feature limits, initial reference capital of 20 USDT, paper default,
Risk-before-Execution rule, and full promotion lifecycle remain governed by the
frozen specifications.

### Frozen-scope dependency

[003 §3 and §16](003_DATA_ARCHITECTURE.md) require approval for new canonical
provider fields and explicitly prohibit tick-data storage, full historical
order-book infrastructure and alternative-data pipelines in current V1.
[000 §8](000_READ_FIRST.md) and [002 §14](002_SYSTEM_ARCHITECTURE.md) retain their
instrument and architecture exclusions. The
[IF1 record](research/PHASE_4D_IF1_INFORMATION_FEASIBILITY.md) correctly classified
trade-event pipelines and the later excluded families as outside existing V1.

DE1 is the **first approved new information family in the research roadmap**,
with aggregate trades as its initial focus. This is not a claim that current
frozen V1 already permits the proposed pipeline. Before implementing DE1 event
storage/ingestion, the human-approved phase must resolve the cited prohibition
through explicit amendments to affected authoritative specifications. The same
rule applies to later excluded families. A research-only label is not an
exemption. This task makes no such amendments to docs 000–009.

These prerequisites preserve IF1's historical decision while recording the new
post-MF1 direction. There is no implicit production-scope expansion.

## 2. Scientific motivation and durable evidence

The [AF3 completion reference](../research/alpha-funnel/af3c/run.json) records
616 registered development evaluations: **599 KILL, 17 WATCH, 0 PROMOTE** and
`production_approved = false`. AF3 retained its sealed-OOS protections.
A WATCH result is neither promotion nor strategy approval.

The [MF1 completion reference](../research/phase-4d-mf1/run.json) binds the
immutable external run, script, experiment specification and summary by SHA-256.
MF1 compared exact candidate-v1 CONTROL10 with PARTICIPATION13, adding only
`trade_count_ratio_20`, `quote_volume_ratio_20` and
`avg_quote_trade_size_ratio_20`. It used the AF3 development interval
[2024-01-01T00:00:00Z, 2025-10-01T00:00:00Z), H=5 minutes and 15 monthly folds.

Result: **CASE 1 — NO INCREMENTAL PARTICIPATION INFORMATION FOUND**.
All five incremental gates failed; stable-information and cost-plausibility
gates also failed.

| Incremental diagnostic | Observed | Frozen requirement |
| --- | --- | --- |
| Median monthly IC delta | -0.0107380657352599938 | At least +0.005 |
| Months P13 IC exceeds C10 IC | 4/15 | At least 10/15 |
| Aggregate D10 gross-return delta | -0.00002086636659417711616041024664 | Strictly positive |
| Aggregate D10-minus-D1 spread delta | -0.0000290629197323599207773551430 | Strictly positive |
| Best new-feature nonzero-gain coverage | 11/15 | At least 12/15 |

P13 aggregate D10 gross return was approximately 0.0000944, compared with
canonical break-even C = `0.002503128284573645913980997751940`.
The candidate margin 2C = `0.005006256569147291827961995503880` is distinct
from break-even. Quote-volume ratio was effectively redundant with base-volume
ratio: median training Spearman approximately 0.99999665.

MF1 accessed development data only: no screening-validation, sealed OOS, 2026
data, network research access, shorts, strategy simulation or production changes.
Its completed-session validation recorded 576 full-suite and 241 focused tests
passed. Those are historical MF1 totals, not a new claim about this document's
validation.

Together with [R1–R6 closure](research/PHASE_4C_R1_R6_RESEARCH_DECISION.md) and IF1,
the evidence makes further arbitrary transformations of the same tested
candle-level information an unsuitable preferred next path. QuantOS will widen
the **information set incrementally**, rather than widen parameter search or
model complexity. Existing same-information research closure remains in force.

This conclusion is limited to the tested QuantOS universe, methods, periods and
cost assumptions. It does not prove that all OHLCV alpha is impossible, that the
entire market is efficient, that an existing feature can never work, or that new
data guarantees future profitability.

The portable MF1 reference uses a locator relative to an externally configured
artifact root, never a workstation path as identity. Resolve it locally and
verify the recorded hashes before relying on the external record. Preserve the
external run and frozen reference as evidence; a correction requires an explicit
reviewed successor record and provenance, not silent replacement of results.
Generated models, market datasets and large external artifacts remain outside Git.

## 3. Data Edge principles

1. **One information family at a time.** Introduce and evaluate each family
   independently before adding another, so incremental attribution remains
   possible. A failed family is not rescued by an unregistered mixture.
2. **Economic mechanism before feature.** Record a plausible market mechanism
   before registering a feature or hypothesis. Blind indicator generation and
   automated feature explosion are excluded.
3. **Point-in-time correctness.** Every datum must have temporal semantics
   sufficient to prove availability by decision time. Distinguish, where relevant,
   `event_time` (when the event happened), `observed_at` (when it was observed),
   `publicly_available_at` (when it became public) and `ingested_at` (when QuantOS
   received/persisted it). A historical event timestamp alone is not proof of
   live availability. Preserve UTC, provenance, revisions and late-arrival
   semantics without inventing unavailable timestamps.
4. **Research-only before production.** New sources initially belong to an
   explicitly authorized research scope. Research use is not production
   admission or permission to bypass frozen scope.
5. **Live parity.** Before a feature enters a production model, equivalent
   point-in-time information must be obtainable reliably in live operation with
   compatible normalization, completion and availability semantics.
6. **Fail closed.** Missing, stale, malformed, temporally ambiguous or
   provenance-invalid exogenous data must be surfaced. Do not silently guess,
   backfill knowledge into earlier decisions, or impute it.
7. **Closed testing universes.** Preregister each generation before observing
   performance: hypotheses, formulas, parameters, data roles, chronology, costs,
   evaluation rules and multiple-testing universe. Multiple-testing controls
   remain mandatory; post-result additions require a separately reviewed
   generation and cannot reuse prior evidence as fresh confirmation.
8. **Costs remain first class.** Gross predictive evidence is insufficient.
   Evaluate realistic cost hurdles and explicitly documented units, assumptions
   and scenarios. Do not lower assumptions to manufacture an edge.
9. **Information before model complexity.** Failure does not justify larger
   models, deeper hyperparameter search, neural networks or automated strategy
   generation. Complexity must be independently justified within approved scope.

## 4. Data Edge ladder

This is the planned order, with conditional later stages. It does not authorize
collecting every family. New research data roles, intervals and access rights
must be approved for each phase before any data is opened.

### DE0 — Current candle information

Canonical completed 1-minute BTCUSDT/ETHUSDT candle information, including
retained quote volume and trade count, has been tested extensively. No approved
alpha is currently promoted. AF3 and MF1 failures do not reopen arbitrary
same-input feature or parameter searches.

### DE1 — Binance Spot trade flow

First approved new family in this roadmap, subject to the specification
dependency in §1 and a separate implementation authorization.

Historical source candidates are Binance Spot `aggTrades` and individual
trades where scientifically necessary. Initial focus: **aggTrades**.
The initial instrument universe remains BTCUSDT/ETHUSDT.

Potential canonical research information includes price, quantity, quote notional
where deterministically derivable or available, aggregate trade ID, first/last
underlying trade IDs, event timestamp, maker/aggressor semantics, symbol and
source identity. These are candidate contract concerns, not implemented fields.
Provider meanings, timestamp precision, ordering, aggregation, gaps and side
semantics must be proven with conformance evidence before use. Do not equate
aggregate-trade counts or sizes with individual-trade distributions.

Potential causal completed-minute concepts include:

- signed taker volume and signed taker notional;
- taker-buy fraction;
- trade/aggregate-trade intensity;
- average aggressive trade size;
- large-trade share and large-trade directional imbalance;
- flow persistence;
- VWAP displacement;
- price/flow divergence;
- realized price-impact proxies.

These are research concepts only. No hypothesis, formula, window, size threshold,
sign mapping or trading rule is frozen here. **AF4B will preregister its small
hypothesis universe separately after data semantics are proven.**

### DE2 — Derivatives context

Consider only if justified after DE1 and separately approved. Potential
research-only information includes perpetual basis, funding, open interest,
mark/index divergence, perpetual trade flow and premium state.

**DERIVATIVES DATA DOES NOT AUTHORIZE DERIVATIVES TRADING.**

Reference-only use still requires resolving applicable frozen information-scope
exclusions. Production instruments remain governed by frozen V1, including
Spot-only trading and no leverage.

### DE3 — Order book / microstructure

Begin collection architecture only after explicit approval and resolution of
frozen exclusions. Potential information includes spread, top-of-book, L2 depth,
imbalance, microprice, liquidity depletion, replenishment, and
cancellation/addition behavior.

Do not assume historical full-depth data exists locally. Do not purchase
external data automatically. Historical reconstruction, snapshot/update
continuity and live parity must be established in its own approved phase.

### DE4 — Options / volatility information

Future research only: implied volatility, skew, term structure and
realized-versus-implied volatility. This grants no options-trading permission,
data purchase or ingestion authorization.

### DE5 — Macro / event context

Future research only: deterministic scheduled context from central-bank
decisions, CPI, employment releases and similar timestamped macro events.
Actual publication and revision availability must be distinguished from the
scheduled event time. No news pipeline is authorized here.

### DE6 — Selective alternative data

Lowest priority unless preceding research identifies a specific missing
mechanism. No generic sentiment/on-chain feature explosion. Any proposed family
requires a separate approved research program and resolution of frozen
exclusions before acquisition or implementation.

## 5. Lessons selected from 151 Trading Strategies

Use the book as a hypothesis-inspiration source, not evidence that a strategy is
profitable. The following mechanism-level lessons are the human-selected
research directions for consideration; this document reproduces no book
content, literal strategy or performance claim:

momentum/trend, mean reversion, relative value, residualization, market activity,
carry, term structure, volatility, liquidity, adverse selection, alpha
combination, and event-conditioned behavior.

QuantOS must independently preregister and validate each derived hypothesis.
A mechanism label is not approval of its instruments, data sources or execution
requirements. Alpha-combination inspiration does not expand V1's one-strategy,
one-production-model boundary.

Explicitly reject:

- blindly implementing all 151 strategies;
- mass parameter sweeps;
- option payoff constructions as current V1 alpha;
- generic ANN expansion based only on technical indicators;
- broad social-sentiment ingestion without a separate approved research program;
- latency-dependent triangular-arbitrage work.

## 6. Lessons selected from LuxAlgo

These are selected research/engineering requirements for future QuantOS phases,
not a dependency on an external implementation or a claim of external
performance. Implement only through the separately authorized phase scope.

### Before or with DE1

**SourceHealthManifest.** New external/research sources should expose
deterministic source-health metadata including, as appropriate:

- source/provider, adapter version and schema fingerprint;
- requested interval and observed/source watermark;
- fetched, accepted and rejected counts;
- duplicates and gaps;
- checksum state, freshness, validation status and provenance.

Freshness must be evaluated against an explicit recorded as-of time so replay
does not silently depend on the wall clock. Unknown health fields stay explicit;
a healthy label cannot substitute for verified temporal availability.

**Provider conformance vectors.** Raw provider fixtures must normalize
deterministically to canonical QuantOS contracts. Verify the selected provider
semantics and adversarial malformed, duplicate, missing and ordering cases
before evaluating predictive information.

**ResearchEvidenceEnvelope.** Future results should structurally bind code
identity, dataset identity, hypothesis/catalog identity, sample counts,
effective/de-overlapped counts where applicable, estimate, uncertainty, cost
scenarios, multiple-testing metadata, temporal stability, assumptions, warnings
and artifact identity. De-overlap must not be presented as proof of independent
samples. Preserve the closed-universe and provenance guarantees of 009.

**CI.** QuantOS should gain committed CI before the new data/research surface
becomes large. Its implementation and commit need their own authorization; no CI
configuration is introduced by this document.

### After an alpha candidate legitimately promotes

At the appropriate lifecycle stage, consider structured RiskVerdict, an execution
flight recorder, an immutable FillEvent ledger, deterministic
fill-to-position/P&L reconstruction, and stationary block-bootstrap Monte Carlo.

Do not build these merely to avoid the current alpha bottleneck. A provisional
research PROMOTE is eligibility for human review, not permission for paper or
live execution. These future components must respect existing module ownership,
Risk's final rejection and Execution's exclusive order authority. They cannot
delay or bypass mandatory validation stages.

## 7. Immediate research sequence and human gates

1. **MF1 closeout:** preserve the portable completion reference and review it.
2. **AF4A:** forensic analysis of all 17 AF3 WATCH cases, under §8.
3. **DE1 contracts and historical Binance Spot aggregate-trade ingestion:**
   first resolve the frozen-scope dependency, then obtain explicit phase and
   data-access authorization.
4. **Deterministic completed-minute trade-flow state:** prove causal availability
   and compatible historical/live semantics.
5. **SourceHealthManifest, provider conformance and CI hardening:** complete and
   harden the foundations begun before/with DE1; no unvalidated ingestion is
   licensed by placing hardening here.
6. **AF4B:** freeze a small human-directed preregistered trade-flow hypothesis
   universe after data semantics are proven.
7. **AF4B development screening:** execute only the authorized closed universe
   and approved data roles.
8. **DE2:** advance only if justified by results and explicitly approved.

No phase automatically authorizes the next. Human review remains mandatory at
every boundary, including approval of this document. No step changes the
Research → Backtest → Walk-Forward → Monte Carlo → Paper Trading → Explicit Live
Approval lifecycle.

## 8. AF4A purpose and limits

AF4A must precede AF4B. It asks **why all 17 AF3 WATCH cases failed promotion**,
using existing authorized AF3 evidence. It should classify:

- insufficient post-cost expectancy;
- insufficient effective/de-overlapped events, without claiming de-overlap is IID;
- FDR/q-value failure;
- temporal instability;
- insufficient robustness-block coverage;
- inadequate event frequency;
- symbol dependence;
- horizon dependence;
- combinations of these mechanisms.

Its output informs which missing information mechanisms DE1 should target.
AF4A must not change AF3 thresholds, create new hypotheses, inspect sealed OOS,
optimize parameters or rescue WATCH cases. This document does not execute AF4A
or prejudge any WATCH case's failure mechanism.

## 9. Data protection and stopping boundary

Preserve existing data-role declarations; this roadmap neither relabels consumed
data as fresh nor opens screening-validation, sealed-OOS or reserve data.
MF1's development boundary ended at 2025-10-01T00:00:00Z, with screening-validation
and sealed OOS untouched by MF1. The separate R6-declared untouched reserve
beginning 2026-09-01T00:00:00Z remains protected. Different experiments' roles are
not interchangeable and must not be inferred from calendar date alone.

This closeout/program task reads existing repository evidence and MF1 metadata
only. It creates the MF1 reference and this document; it does not implement
TradeEvent, AggregateTradeEvent, ingestion, new features, LightGBM changes,
Risk/Execution changes, data downloads or AF4A.

Stop after document/reference validation and human review. No commit, push,
merge, new experiment or automatic next-phase action is authorized by this task.
