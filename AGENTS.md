# QuantOS — Codex Operating Instructions

## 1. Authority and Specification Precedence

QuantOS V1 is specification-driven and targets autonomous Binance USDⓈ-M Futures trading.

Before making changes, treat the repository specifications as authoritative:

* `docs/000_READ_FIRST.md` — highest-priority V1 Source of Truth.
* `docs/001_PRODUCT_REQUIREMENTS.md` through `docs/007_VALIDATION_BACKTESTING.md` — coequal V1 specifications.
* `docs/008_IMPLEMENTATION_GUIDE.md` — implementation guidance subordinate to `000–007`.

Do not infer a priority order among documents `001–007` from their filenames or numbering.

If two specifications appear inconsistent:

1. follow `000_READ_FIRST.md` where it resolves the issue;
2. otherwise surface the conflict explicitly;
3. do not silently choose one specification over another.

Do not modify `docs/000_READ_FIRST.md` through `docs/008_IMPLEMENTATION_GUIDE.md` without explicit project-owner authorization.

---

## 2. External Repositories Are Not Specification Authority

Do not treat previous bots, experimental repositories, generated implementations, or external projects as QuantOS requirements.

This includes, but is not limited to:

* `quant-bot-DeepSeek-build`
* `quant-bot-manus1.6`
* previous `quant-bot` repositories
* prior experiments or prototypes

They may be inspected only when explicitly requested.

The current specifications inside this repository govern QuantOS V1.

---

## 3. Required Reading Strategy

Do not reread every specification unnecessarily.

For each task:

1. read `docs/000_READ_FIRST.md`;
2. read the specification files directly relevant to the task;
3. read `docs/008_IMPLEMENTATION_GUIDE.md` when implementation structure or sequencing matters.

For cross-cutting architectural changes, read all affected specifications before modifying code.

Prefer targeted repository inspection over repeatedly scanning the entire repository.

---

## 4. V1 Scope Guardrails

Preserve the authorized V1 constraints:

* local-first Clean Architecture modular monolith;
* exactly six production modules;
* infrastructure must not become additional business modules;
* Binance USDⓈ-M Futures;
* BTCUSDT perpetual initially;
* completed 1-minute candles;
* Parquet + DuckDB canonical data;
* approximately 20 USDT reference capital;
* paper trading as the safe default;
* autonomous LONG / SHORT / HOLD;
* a small multi-strategy library with deterministic selection;
* isolated margin, one-way position mode, and one simultaneous position;
* risk-sized exposure, dynamic leverage, mandatory stop, and fail-closed execution;
* capital preservation, correctness, robustness, simplicity, explainability, performance, and profitability in that order.

Do not add other exchanges, instruments, portfolio trading, microservices, Kubernetes, cloud-first infrastructure, deep learning, reinforcement learning, multi-model production ensembles, sentiment/social pipelines, or on-chain signals unless the specifications change first.

---

## 5. Architectural Boundaries

Preserve the exact production-module boundary defined by the frozen architecture.

QuantOS V1 has exactly six production modules.

Do not turn infrastructure, adapters, storage implementations, utilities, frameworks, or external integrations into additional business modules.

Respect Clean Architecture dependency direction and module ownership defined by the canonical specifications. Regime classification and strategy selection belong to Alpha; position management belongs to Execution subject to Risk.

Do not bypass module boundaries merely for convenience.

---

## 6. Runtime Safety and Execution Authority

Trading and execution behavior must fail closed.

The following rules are mandatory:

* Risk evaluation must occur before Execution.
* A Risk rejection is final for that decision.
* Execution must never override or bypass a Risk rejection.
* Only the Execution module may submit orders to an exchange.
* Other modules may express intent but must not directly submit exchange orders.
* Missing, stale, invalid, inconsistent, ambiguous, or unreconciled execution state must not result in a new order.
* When safe execution state cannot be established, stop the affected action and surface the condition.
* Paper mode must remain the safe default.
* Live operation requires the explicit approval required by the frozen lifecycle.

Never infer that an order probably succeeded, failed, filled, or remains open when authoritative state is unavailable.

---

## 7. Data Integrity and Time Semantics

Data semantics must remain consistent across research, historical validation, paper trading, and live operation.

Mandatory rules include:

* internal timestamps are UTC;
* trading decisions use completed 1-minute candles only;
* incomplete/open candles must not influence decisions;
* canonical historical inputs must be immutable and versioned as required by the frozen data specification;
* do not silently rewrite historical source data;
* historical, paper, and live paths must share canonical data semantics;
* historical, paper, and live operation must reuse the same core business logic where required by the architecture;
* do not create separate behavioral implementations that cause backtest logic and runtime trading logic to diverge;
* timestamp alignment and candle boundaries must be deterministic and explicit.

Treat data corruption, missing required data, inconsistent timestamps, and unreconciled state as correctness failures rather than conditions to guess through.

---

## 8. Mandatory Validation and Promotion Lifecycle

Never bypass the QuantOS promotion lifecycle.

The required progression is:

Research
→ Historical Replay / Backtest
→ Walk-Forward Validation
→ Monte Carlo Validation
→ Live-Market Paper Trading
→ Binance Testnet
→ Mainnet Shadow
→ Explicit Live Approval

No implementation may silently skip a required stage.

Passing an earlier stage does not imply approval for a later stage.

Live trading must never become enabled merely because automated validation passes.

---

## 9. Implementation Principles

When multiple implementations satisfy the frozen specifications, prefer the one that is:

1. correct;
2. safe;
3. deterministic;
4. testable;
5. simple;
6. explainable;
7. maintainable;
8. performant enough for V1.

Avoid speculative abstraction.

Do not build infrastructure merely because it may be useful in a future version.

Do not introduce dependencies without a concrete V1 need.

Do not rewrite working components unnecessarily.

Prefer small, reviewable changes over large unrelated refactors.

---

## 10. Financial and Backtesting Correctness

Treat trading, accounting, validation, risk, and execution logic as high-risk code.

Be especially careful about:

* look-ahead bias;
* target leakage;
* timestamp alignment;
* incomplete candles;
* train/validation/test contamination;
* transaction fees;
* slippage;
* order sizing;
* balance accounting;
* position state;
* fill assumptions;
* reproducibility;
* deterministic backtests;
* walk-forward isolation;
* Monte Carlo assumptions;
* execution reconciliation.

Never improve reported performance by weakening realistic assumptions.

If correctness and profitability conflict, preserve correctness.

---

## 11. Testing and Validation

Every behavior-changing implementation must include or update appropriate tests.

Before considering a task complete:

1. inspect the final diff;
2. run the relevant tests;
3. run configured linting and type checks where available;
4. verify imports and entry points where relevant;
5. verify no frozen V1 requirement was violated;
6. verify no secrets or generated local artifacts were added.

Do not claim a check passed unless it was actually run.

If a required check cannot be run, state that explicitly.

Do not hide, suppress, or redefine failing tests merely to obtain a passing result.

---

## 12. Repository Hygiene

Never commit:

* API keys;
* exchange credentials;
* passwords;
* private tokens;
* `.env` files;
* raw market datasets;
* large Parquet datasets;
* DuckDB databases;
* generated model artifacts;
* logs;
* generated backtest output;
* machine-specific files;
* `.DS_Store`.

Respect `.gitignore`.

Use the project's defined configuration mechanisms for secrets.

---

## 13. Git Rules

Do not push directly to `main` unless explicitly instructed.

Normal work should occur on a dedicated branch such as:

`codex/<task-name>`

Before committing:

* review `git status`;
* review the intended diff;
* ensure unrelated files are excluded;
* run appropriate validation.

Use focused, descriptive commit messages.

Do not rewrite Git history, force-push, delete branches, merge pull requests, or perform destructive Git operations unless explicitly instructed.

Do not commit or push merely because code was modified. Commit or push only when the task explicitly authorizes it.

---

## 14. Change Scope

Implement only the requested task and what is strictly necessary to make it correct.

Do not opportunistically add:

* unrelated features;
* speculative optimizations;
* additional frameworks;
* unapproved strategy families;
* unnecessary additional models;
* additional exchanges;
* unnecessary abstractions;
* cosmetic repository-wide refactors.

If unrelated issues are discovered, report them separately rather than silently expanding scope.

---

## 15. Decision Discipline

Do not invent missing product requirements.

When a decision is specified, implement it.

When an implementation detail is unspecified but can be safely derived without changing product behavior, choose the simplest compliant option and document the choice briefly.

When a material ambiguity could alter:

* trading behavior;
* financial correctness;
* architecture;
* module boundaries;
* risk;
* execution;
* data semantics;
* validation integrity;

do not guess.

Surface the ambiguity and stop the affected portion of the implementation.

---

## 16. Context and Token Efficiency

Use repository context efficiently.

* Read files relevant to the current task.
* Do not repeatedly summarize frozen specifications unless requested.
* Do not regenerate architecture already defined in canonical documents.
* Avoid long progress narratives.
* Prefer concise implementation summaries.
* Search for existing implementations before creating duplicates.
* Reuse established project conventions once they exist.
* For large tasks, work in coherent phases rather than unrelated areas simultaneously.

---

## 17. Definition of Done

A task is complete only when:

* the requested functionality is implemented;
* implementation conforms to the current V1 specifications;
* architectural and module boundaries remain intact;
* runtime safety and fail-closed requirements remain intact;
* appropriate tests exist;
* relevant tests and checks pass, or failures are explicitly reported;
* no secrets or local artifacts are included;
* the diff contains no unintended changes;
* documentation is updated when implementation materially changes documented behavior;
* the final response clearly states what changed and what validation was actually performed.

Never represent QuantOS as production-ready, profitable, validated, paper-approved, or live-approved unless the required evidence and lifecycle stage actually exist.
