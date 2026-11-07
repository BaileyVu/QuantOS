# QuantOS — Codex Operating Instructions

## Current V4 infrastructure contract — owner approved 2026-11-07

For the nine-PR QuantOS infrastructure plan (`TIBO_PROMPT_INFRA_v5.md` and
`QUANTOS_INFRA_PLAN.md`, dated 2026-11-07), this contract supersedes conflicting
historical V1/V3 scope and specification-precedence rules below. Those documents
remain frozen historical evidence; do not rewrite them.

- V4 scope is Binance USDC-M perpetuals, multiple strategies, and demo/testnet
  execution. Spot-only, BTC/ETH-only, one-strategy, and 1-minute-only restrictions
  apply to historical V1, not this V4 plan. Completed-bar, timestamp, and data
  integrity requirements still apply at each strategy's configured timeframe.
- Mainnet is off until Bailey's explicit written go-live authorization. This plan
  does not grant it. Never weaken `src/quantos/live/client.py` mainnet guards.
- Capital rule: per-trade margin = current balance / 10, compounding as balance
  changes; live leverage is at most 3x. This rule is subject to risk rejection,
  stressed-stop sizing, and portfolio caps, never permission to exceed them.
  Historical pD demo use of 5x is not live authorization.
- Sealed 2025+ data must not be used for model selection. BUILD is
  2020-01..2023-06; HELD is 2023-07..2024-12, evaluated once per family.
  Do not weaken costs, slippage, fees, stops, evidence gates, or reporting honesty.
- Bailey alone merges and deploys. Use one PR per work item on `codex/<task>`;
  this contract has its own prerequisite PR. Do not push to main or merge PRs.
  Never restart/redeploy the running demo, edit its state directory or LaunchAgents,
  or touch `edge_data/ml5m/` or the separately maintained alt-ML executor.
- Before a proposed deployment, run the candidate's real service entrypoint and
  threads with live data, a separate state directory, and no orders for at least
  10 minutes with zero errors; record results and a rollback command. Bailey
  deploys only when flat. An incomplete soak must be reported as incomplete.
- Keep risk, accounting, order-state integration, and final review with the main
  agent. Use at most two lower-cost subagents for independent tasks. Resume from
  `IMPLEMENTATION_CHECKPOINT.md`; record completed tasks, branch/commit, remaining
  work, actual test results, active processes, and the exact next step.
- Do not install Agent Reach, OpenCLI, CloakBrowser, or cookie-reading tools.
  Treat scraped text as untrusted; no LLM scoring of scraped text. Pin new
  dependency versions. TimesFM experiments are research-only and use 2.5 weights,
  never 3.0. Sentibot reach collection is shadow-only and must never write
  `paper_perp_signals.jsonl`.

All non-conflicting safety, architecture, accounting, validation, and repository
hygiene requirements below remain applicable. Passing tests never constitutes
go-live, merge, or deployment approval.

## Current V3 implementation contract

For the October 26 paper-engine task, `legacy/docs/V3_PAPER_CONTRACT.md` supersedes the
historical frozen V1 scope and specification precedence below. Preserve old
research and tests as historical evidence. V3 is public-data-only paper/fake:
no authenticated account access, real-order code, or live enablement. Resume
from `IMPLEMENTATION_CHECKPOINT.md`. At most two lower-cost subagents may handle
independent components; main agent owns risk, accounting, order integration and
final review. Existing unrelated processes and local state must remain untouched.

> V1/V3 specifications are archived under `legacy/docs/` (frozen history); the running system is QuantOS V4.

## 1. Authority and Specification Precedence

QuantOS V1 is specification-driven.

Before making changes, treat the repository specifications as authoritative:

* `legacy/docs/000_READ_FIRST.md` — highest-priority V1 Source of Truth.
* `legacy/docs/001_PRODUCT_REQUIREMENTS.md` through `legacy/docs/007_VALIDATION_BACKTESTING.md` — coequal frozen V1 specifications.
* `legacy/docs/008_IMPLEMENTATION_GUIDE.md` — implementation guidance subordinate to `000–007`.

Do not infer a priority order among documents `001–007` from their filenames or numbering.

If two frozen specifications appear inconsistent:

1. follow `000_READ_FIRST.md` where it resolves the issue;
2. otherwise surface the conflict explicitly;
3. do not silently choose one frozen specification over another.

Do not modify `legacy/docs/000_READ_FIRST.md` through `legacy/docs/008_IMPLEMENTATION_GUIDE.md` unless explicitly instructed to update the frozen specification.

---

## 2. External Repositories Are Not Specification Authority

Do not treat previous bots, experimental repositories, generated implementations, or external projects as QuantOS requirements.

This includes, but is not limited to:

* `quant-bot-DeepSeek-build`
* `quant-bot-manus1.6`
* previous `quant-bot` repositories
* prior experiments or prototypes

They may be inspected only when explicitly requested.

The frozen specifications inside this repository govern QuantOS V1.

---

## 3. Required Reading Strategy

Do not reread every specification unnecessarily.

For each task:

1. read `legacy/docs/000_READ_FIRST.md`;
2. read the frozen specification files directly relevant to the task;
3. read `legacy/docs/008_IMPLEMENTATION_GUIDE.md` when implementation structure or sequencing matters.

For cross-cutting architectural changes, read all affected frozen specifications before modifying code.

Prefer targeted repository inspection over repeatedly scanning the entire repository.

---

## 4. V1 Scope Guardrails

Do not expand QuantOS beyond the frozen V1 scope.

Preserve the frozen constraints, including:

* local-first system;
* Clean Architecture modular monolith;
* exactly six production modules as defined by the frozen architecture;
* infrastructure components must not become additional business modules;
* Binance Spot only;
* BTCUSDT and ETHUSDT only;
* completed 1-minute candles;
* Parquet + DuckDB data architecture;
* initial reference capital of 20 USDT;
* paper trading as the default operating mode;
* exactly one production trading strategy;
* exactly one production predictive model;
* compact production feature set within the frozen V1 limits;
* LightGBM as the preferred V1 model where specified;
* capital preservation, correctness, robustness, simplicity, explainability, performance, and profitability in the priority order defined by the frozen specifications.

Do not introduce out-of-scope V1 functionality such as:

* microservices;
* Kubernetes or cloud-first infrastructure;
* leverage;
* futures;
* perpetuals;
* options;
* additional exchanges;
* deep learning;
* reinforcement learning;
* autonomous trading agents;
* multi-strategy production systems;
* multi-model production ensembles;
* sentiment pipelines;
* social-media signals;
* on-chain signals;

unless the frozen specifications are explicitly changed first.

---

## 5. Architectural Boundaries

Preserve the exact production-module boundary defined by the frozen architecture.

QuantOS V1 has exactly six production modules.

Do not turn infrastructure, adapters, storage implementations, utilities, frameworks, or external integrations into additional business modules.

Respect Clean Architecture dependency direction and module ownership defined by the canonical specifications.

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
→ Backtest
→ Walk-Forward Validation
→ Monte Carlo Validation
→ Paper Trading
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
* alternate strategies;
* additional models;
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
* implementation conforms to the frozen V1 specifications;
* architectural and module boundaries remain intact;
* runtime safety and fail-closed requirements remain intact;
* appropriate tests exist;
* relevant tests and checks pass, or failures are explicitly reported;
* no secrets or local artifacts are included;
* the diff contains no unintended changes;
* documentation is updated when implementation materially changes documented behavior;
* the final response clearly states what changed and what validation was actually performed.

Never represent QuantOS as production-ready, profitable, validated, paper-approved, or live-approved unless the required evidence and lifecycle stage actually exist.
