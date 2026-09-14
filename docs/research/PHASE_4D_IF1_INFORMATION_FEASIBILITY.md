# Phase 4D-IF1 Information Feasibility Record

## 1. Purpose

Decision date: 2026-09-14. Determine whether genuinely additional causal information justifies one bounded next feasibility phase after [R1–R6 closure](PHASE_4C_R1_R6_RESEARCH_DECISION.md). This is a documentation decision, not R7, feature selection or implementation. Preflight found a clean tree on `codex/phase-4d-model-feasibility-mf1`, HEAD `579bf21512d09a0ab4fe088bd18bd7cf4d7284cb`, with no difference from locally recorded `origin/main`.

## 2. Frozen V1 constraints

Authority remains `docs/000_READ_FIRST.md` highest, `001–007` coequal/frozen, and `008` subordinate. The relevant boundaries are:

| Frozen reference and heading | Consequence |
| --- | --- |
| `docs/000_READ_FIRST.md` §2 “V1 Mission”, §4 “Exact V1 Production Modules”, §8 “V1 Exclusions” | Local Binance Spot, BTCUSDT/ETHUSDT, completed 1-minute decisions, six production modules, one production model and strategy. Additional markets, derivatives, on-chain and news/social pipelines are outside V1. |
| `docs/001_PRODUCT_REQUIREMENTS.md` §4 “Market Data Requirements” | “The canonical V1 decision dataset is 1-minute OHLCV plus approved derived data.” This is not blanket authorization for new raw fields. |
| `docs/003_DATA_ARCHITECTURE.md` §3 “Canonical Candle” | The explicit contract includes **quote_volume** (quote-asset volume) and **trade_count** (number of trades), beyond ordinary OHLCV. “Provider-specific fields must not leak into the canonical domain contract without explicit approval.” |
| `docs/003_DATA_ARCHITECTURE.md` §5 “Storage”, §6 “Data Immutability”, §8 “Timestamp and Causality Rules”, §9 “Completed-Candle Rule”, §11 “Live Data”, §13 “Dataset Identity” | Parquet/DuckDB, immutable versions, UTC, availability by decision time, completed candles and consistent historical/live semantics remain mandatory. |
| `docs/003_DATA_ARCHITECTURE.md` §16 “Data Not Required for V1” | Its operative instruction is “Do not build”, including tick-data storage, full historical order-book infrastructure and alternative-data pipelines. The heading does not make them freely optional. |
| `docs/004_FEATURE_ENGINE_SPECIFICATION.md` §1 “Objective”, §3 “Approved Feature Families”, §5 “Causality”, §8 “Feature Versioning”, §11 “Production Promotion” | Volume behavior is an approved family. Derived features need causal, versioned definitions and incremental evidence. The 10–15 target / 20 maximum production-feature budget does not authorize a raw-schema expansion. |
| `docs/006_RISK_EXECUTION_SPECIFICATION.md` §5 “Execution Responsibilities”, §6 “Market vs Limit”, §7 “Fees and Slippage” | Actual execution quality and cost measurement are legitimate responsibilities; reducing assumed costs is not new alpha. |
| `docs/007_VALIDATION_BACKTESTING.md` §6 “Look-Ahead Prevention”, §11 “Test Set” | Future trade/book information and test-set reuse are forbidden. Mentioning future book/trade information here does not override the infrastructure prohibition in 003 §16. |
| `docs/008_IMPLEMENTATION_GUIDE.md` §2 “Implementation Rule” | Implementation guidance cannot expand the frozen scope. |

**Boundary answer:** frozen V1 permits derived features from approved canonical data, which explicitly includes quote volume and trade count. It does **not** generally permit adding new raw provider fields. Such additions require explicit approval under 003 §3 and resolution of any affected frozen constraints. No unresolved frozen conflict prevents using the two existing fields; unapproved taker-flow additions require a human V1 decision.

## 3. Definition of genuinely new information

Information is new here only when it adds a causal observation not recoverable as a mathematical transform of the OHLCV inputs tested in R1–R6. The same OHLCV bar can result from different numbers and sizes of trades; its total base volume and OHLC prices do not determine exact quote turnover. Thus observed `trade_count` and `quote_volume` add information relative to those experiments, although they are already present in the canonical schema.

This is an expansion of the **evaluated information set**, not a proposed expansion of V1 raw data. Trade count is not a count of distinct participants, and neither field identifies aggressor side. Their predictive increment is unproven.

## 4. Classification matrix

Ratings are architectural feasibility judgments, not measured alpha. HIGH reproducibility means an existing deterministic, versioned canonical path; it does not claim a new audit of every stored row. MEDIUM availability/reproducibility is conditional on obtaining suitable provenance; LOW means adequate historical evidence is not established here. All network and new market-data inspection were prohibited.

| Information family | New raw information? | Likely alpha relevance | Historical reproducibility | Engineering complexity | Data/storage burden | Frozen V1 status | Reason | Recommended now? |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A. Existing OHLCV transforms | NO | LOW | HIGH | LOW | LOW | ALLOWED_BY_CURRENT_V1 | Feature families can be permitted, but further same-input search is closed by this decision. | NO |
| B1. Retained kline trade count + quote volume | YES | MEDIUM | HIGH | LOW | LOW | ALLOWED_BY_CURRENT_V1 | Already in Candle, archive/live normalization, Parquet and DuckDB; absent from R1–R6 predictive inputs. | YES |
| B2. Discarded kline taker-buy base/quote fields | YES | MEDIUM | MEDIUM | MEDIUM | LOW | REQUIRES_EXPLICIT_V1_APPROVAL_OR_SPEC_CHANGE | Additional source fields are not approved Candle fields; semantics and recoverable raw-history coverage need verification. | NO |
| C. Individual/aggregate trade events, aggressor flow, size distribution | YES | MEDIUM | MEDIUM | HIGH | HIGH | OUT_OF_SCOPE_FOR_V1 | Requires complete immutable event history and tick storage, prohibited by 003 §16. Minute count/average-size information obtainable through B1 does not require this pipeline. | NO |
| D. Historical order book: spread, depth, imbalance, microprice, pressure | YES | MEDIUM | LOW | HIGH | HIGH | OUT_OF_SCOPE_FOR_V1 | No adequate reproducible local snapshot/delta history established; full historical book infrastructure is prohibited. | NO |
| E. Additional Spot symbols as raw context | YES | MEDIUM | MEDIUM | MEDIUM | MEDIUM | OUT_OF_SCOPE_FOR_V1 | Exceeds the frozen BTCUSDT/ETHUSDT data universe, including when used only as context. Existing BTC/ETH transforms belong to A. | NO |
| F. Funding, open interest, basis, perpetuals, liquidations | YES | MEDIUM | MEDIUM | MEDIUM | MEDIUM | OUT_OF_SCOPE_FOR_V1 | Derivatives inputs expand the frozen Spot-only information scope; reference-only use is not an automatic exception. | NO |
| G. On-chain | YES | LOW | MEDIUM | HIGH | MEDIUM | OUT_OF_SCOPE_FOR_V1 | Explicit exclusion; causal availability/revisions and useful 1-minute increment are unestablished. | NO |
| H. Sentiment / news / social | YES | LOW | LOW | HIGH | MEDIUM | OUT_OF_SCOPE_FOR_V1 | Explicit exclusion; point-in-time publication, revision and ingestion history would be required. | NO |
| I. Actual execution/cost measurements | YES | LOW | MEDIUM | MEDIUM | LOW | ALLOWED_BY_CURRENT_V1 | Allowed for execution diagnostics, not blanket approval to feed spread into Alpha or build historical book infrastructure. | NO |

For C, reproducibility would require immutable trade identifiers, event ordering, deduplication, gap detection and explicit event/availability timestamps. Signed flow requires verified aggressor semantics; aggregate trades do not necessarily preserve the individual trade-size distribution. A completed minute must exclude later events and late information unavailable at its decision time. These requirements explain the high complexity and storage burden; no historical coverage is assumed verified.

For D, deterministic reconstruction requires a known snapshot, complete sequenced updates and gap recovery. A present snapshot cannot reconstruct a past book. End-of-minute aggregation could meet completed-candle timing only with those availability rules, and would still require the prohibited infrastructure.

For I, actual quotes, fill prices, fees and order timing could support a separate future execution-measurement phase. That is worthwhile operational evidence, not the next alpha recommendation. Recorded fills are reproducible only where such records exist; they do not establish counterfactual fills or maker rebates for R1–R6. Frozen research costs remain unchanged.

## 5. Best candidate family

**Completed-1-minute transaction participation using the existing `trade_count` and `quote_volume` fields.** This is the sole recommended family.

Local evidence:

- `src/quantos/domain/market_data/contracts.py` defines both canonical fields. `src/quantos/infrastructure/binance/daily_archive.py` and `klines.py` map zero-based source columns 7 and 8 into them. `live_klines.py` retains `q` and `n` and emits only completed candles.
- `src/quantos/infrastructure/storage/parquet.py` persists quote volume as a decimal and trade count as an integer. `duckdb_query.py` selects and reconstructs both. Their use therefore needs no new archive acquisition or raw-schema migration for existing compatible datasets. Coverage/quality of any future selected dataset must still be checked in that authorized phase.
- All six persisted experiment specifications list OHLCV-derived features. `src/quantos/domain/features/engine.py` confirms the shared `volume_ratio_20` uses base volume only. R6's three frozen formulas likewise use price/range information. These findings support “not previously evaluated by R1–R6”; they do not claim novelty across unrelated AF work.
- `tests/unit/test_binance_daily_archive.py` and `test_binance_klines.py` contain the extra numeric slots 9 and 10 in the 12-field layout; `test_binance_live_klines.py` includes `V` and `Q`. Their interpretation as taker-buy base/quote volumes follows the source-format convention represented by these fixtures; the repository does not independently document those labels. The adapters ignore those slots/keys. Actual historical taker-field completeness and retained raw archive coverage were **not verified** in IF1, and canonical candles cannot reconstruct them.

The retained fields can distinguish many small trades from fewer large trades at similar base volume, with average trade size as a conceptual example. That supports MEDIUM potential relevance without claiming profitability or aggressor-flow measurement. HIGH reproducibility and LOW engineering/storage burden follow from the existing schema and replay path, not from a new backtest.

## 6. Required architecture/spec changes, if any

For the recommended B1 family: **none to Candle, DatasetIdentity, adapters, Parquet/DuckDB schema, module boundaries or frozen specifications**. No new research raw-data contract is necessary. A future approved feasibility study would use the retained observations; any eventual feature promotion would require versioned definitions, evidence of incremental value and replacement/selection within the existing production-feature budget. No feature is selected here.

For B2, explicit provider-field approval under 003 §3 must precede a design decision. A canonical extension would require agreed field semantics, schema/identity versioning, validation, immutable ingestion versions and historical/live parity. A research-only contract is also an information expansion requiring human approval; it is not a way around frozen scope. Amend affected frozen specifications if the approved design changes them. Families C–H would require changing the cited explicit exclusions/universe, not merely spending unused feature slots. None of these changes is authorized or recommended now.

## 7. Reserve protection

Preserve the R6-declared untouched boundary **2026-09-01T00:00:00Z**, inclusive. IF1 uses only existing summaries/specifications and repository code/schema evidence, with no market-data reads or network calls. It neither consumes reserve nor certifies other tasks' access histories. Any subsequent study needs human review and a frozen causal protocol, evaluation rules and reserve-access decision before fresh data is opened.

## 8. Recommendation

**V1 INFORMATION EXPANSION FEASIBLE**

Recommend exactly one bounded research-feasibility phase for B1: determine whether already-retained transaction-participation information adds reproducible information beyond the closed R1–R6 OHLCV inputs. This satisfies the first priority of IF1's decision rule without a raw-schema expansion. Taker-buy flow is excluded from that recommendation. Alpha relevance remains a hypothesis; this decision authorizes no experiment, reserve access or production promotion.

## 9. Explicitly rejected next actions

All nearby quantiles, alternative holding periods, model substitutions, indicator cocktails, additional OHLCV regimes/transforms, symbol dropping, cheaper assumed costs, threshold/TP/SL search and post-hoc R6 rescue remain `SAME_INFORMATION_SET_RESEARCH_CLOSED`.

Also rejected in IF1: downloads, raw-data inspection, Candle/DatasetIdentity edits, new adapters or schemas, feature implementation, fitting, backtesting, frozen-spec edits, commits, pushes and merges. Human review of these two records is the next action.
