# V1-T1 Risk and paper execution core

This is a deterministic simulation core, not a promoted strategy or continuous paper-trading runtime. It grants no paper-validation or live approval. The six production modules remain unchanged.

## Entry point and ownership

`quantos.application.risk_execution.TradingStep.run(alpha, context)` evaluates the real Risk Engine, constructs an intent only from a matching approval, and submits it to Execution. HOLD and Risk rejection return `TradingResult(risk, None)` without calling Execution. The application never changes balances or positions. Shared immutable snapshots, cost assumptions and pure evidence helpers live in `quantos.domain.runtime_contracts`; Risk has no dependency on Execution implementation or Execution-owned types. Existing Execution contract imports remain compatible aliases. This shared file adds no business module.

`quantos.domain.execution.core.ExecutionEngine` owns account, position, request/result and reconciliation state. It exposes an immutable `snapshot`. `OrderRequest`, `ExecutionReport`, `Position` and `AccountSnapshot` are the existing provider-independent contracts. `ExecutionResult` adds explicit quote-currency fee and slippage evidence. The application orchestration and accounting are provider independent; `PaperFillProvider` supplies the deterministic paper effect and verifies recorded results without submitting orders during replay. No live provider exists.

Composition (explicit simulation start timestamp and an external/ignored ledger path):

```python
from decimal import Decimal
from quantos.application.risk_execution import TradingStep
from quantos.domain.execution import AccountSnapshot
from quantos.domain.execution.core import ExecutionEngine
from quantos.domain.risk.engine import RiskEngine
from quantos.infrastructure.configuration.paper import load_paper_config
from quantos.infrastructure.logging import configure_logging
from quantos.infrastructure.storage.execution_ledger import JsonlExecutionLedger

config = load_paper_config("configs/paper.toml")
logger = configure_logging("INFO")
initial = AccountSnapshot(start_timestamp, {
    "USDT": config.initial_capital, "BTC": Decimal("0"), "ETH": Decimal("0"),
}, ())
execution = ExecutionEngine(initial, config.risk.costs,
                            JsonlExecutionLedger(config.ledger_path), logger=logger)
step = TradingStep(RiskEngine(config.risk), execution, logger)
# Supply an actual AlphaDecision and explicit RiskContext, then call step.run().
```

Restart requires the same initial snapshot (including timestamp) and cost assumptions. Read `execution.snapshot` after recovery when constructing the next context. Replay never uses wall-clock time or calls provider submission.

## Risk inputs and policy

`RiskContext` contains the explicit UTC decision timestamp, an immutable tuple of `MarketState` values, the Execution-owned account snapshot, gross expected edge rate, UTC day-start timestamp/equity, and peak equity. Current equity/exposure are calculated from quote cash and current marks for every held position. Callers must supply authoritative day/peak references; this core does not invent their history.

Each market state contains a canonical completed 1-minute Candle and an explicit validity flag. The close is the paper execution reference. Opens must align to a UTC minute; closes must lie within the final millisecond through the exact next-minute boundary. Future/incomplete, stale, invalid, duplicated or missing required markets are rejected. All held symbols require fresh marks. Unsupported assets, inconsistent base balances/positions, future account/position timestamps and inconsistent equity/day references fail closed.

BUY quantity is `floor(order_notional / reference_price / quantity_step) * quantity_step`. SELL quantity is `floor(owned_quantity * sell_fraction / quantity_step) * quantity_step`. SELL with no position and zero-sized orders are rejected. A fraction of one exits all step-aligned holdings; sub-step dust remains owned and is never silently filled. BUY is never capped to available cash: the full slipped notional plus fee must fit. No borrowing or shorting exists.

Position quantity/notional and aggregate exposure limits constrain added BUY exposure. SELL reduces existing exposure and does not create a short. Daily-loss and drawdown stops apply to BUY/increasing exposure, including equality at the configured threshold. Under the explicit final-audit exit policy, a legitimate SELL of owned Spot quantity may reduce risk during these stops and does not require positive entry edge. Data, state, ownership, and execution-safety checks remain mandatory for exits. BUY expected edge must exceed fees, adverse slippage, fee on slipped notional, and the safety margin. `net_edge_rate` records the remaining per-order edge after all four terms. The supplied gross edge must have the same per-order meaning; it is not inferred from model score. These are conservative per-order assumptions, not a claim about a strategy's complete round-trip return.

The default remains fixed-notional/owned-fraction sizing because no approved strategy requires volatility scaling in this task. The optional `volatility_target` enables only one deterministic BUY multiplier: `min(1, target / observed_volatility)` (zero observed volatility uses one). It can only reduce notional. Enabling it requires an explicit nonnegative finite Decimal volatility input; missing or invalid input rejects BUY. SELL does not require an entry-sizing volatility estimate. No strategy, model, or sizing framework is added.

All monetary arithmetic uses Decimal with a fresh precision-34 ROUND_HALF_EVEN context, fixed exponent limits and traps for invalid operations, division by zero, overflow, underflow and float operations. Sizing divisions/products use a separate ROUND_DOWN context, then quantities round down explicitly to their configured step. A rounded quantity exceeding its sizing bound is rejected. No float conversion or ambient Decimal settings affect decisions, fills or replay.

## Configuration

`configs/paper.toml` is a separate strict configuration for this core; existing Phase 1 startup configuration/CLI behavior is unchanged. Numbers representing money, quantities and rates must be quoted decimal strings. Unknown/missing keys, non-finite numbers, invalid ranges, and live mode are rejected.

| Setting | Meaning |
|---|---|
| `initial_capital` | Initial quote cash; example/reference 20 USDT |
| `ledger_path` | JSONL path, relative to invoking working directory or absolute; default ignored `artifacts/paper/execution.jsonl` |
| `fee_rate`, `slippage_rate` | Per-fill quote fee and adverse price movement |
| `order_notional`, `quantity_step` | BUY sizing and both-direction quantity increment |
| `sell_fraction` | Fraction of owned quantity to sell, `(0, 1]` |
| `max_position_quantity`, `max_position_notional` | Maximum resulting BUY position in base units / USDT |
| `max_exposure_fraction` | Maximum resulting gross exposure / equity |
| `daily_loss_fraction`, `drawdown_fraction` | Stop thresholds relative to day-start / peak equity |
| `stale_seconds` | Maximum completed-candle age at decision time |
| `safety_margin_rate` | Additional expected-edge buffer |
| `volatility_target` | Optional positive target for bounded BUY-only volatility scaling; disabled when omitted |

The example risk limits are explicit simulation settings, not optimized or approved production parameters. There are no credentials or authenticated endpoints.

## Fills and accounting

MARKET fills the requested quantity at `reference * (1 + slippage_rate)` for BUY and `reference * (1 - slippage_rate)` for SELL. Fee is `quantity * fill_price * fee_rate`, charged in USDT for both sides. BUY debits notional plus fee and adds base; SELL removes only owned base and credits notional minus fee. Average entry price is the quantity-weighted fill price, excluding separately charged quote fees. Partial sells preserve average entry; a full close removes the position. The execution reference and absolute slippage cost in USDT are recorded.

The same primitive supports LIMIT with an immediate-or-reject policy: fill only when the slipped execution price meets the supplied limit, otherwise persist a final REJECTED report with zero fill/costs. There is no resting order, queue simulation, partial order fill, or automatic retry at a different price. A partial position reduction is an ordinary fully filled SELL for less than the holding. MARKET is the default when the approved cost assumptions permit it; callers may supply a stricter LIMIT price for execution quality. Neither policy assumes an invisible later fill.

This snapshot simulator assumes full execution at the explicitly slipped reference for an executable order; it is not a liquidity/queue or exchange-filter simulator and does not establish realistic backtest or paper-validation performance.

## Identity, evidence and reconciliation

The request ID is a canonical SHA-256 digest of the Alpha identity, complete Risk approval identity, order type and limit price. A separate Alpha identity guard prevents reusing one decision to create multiple exposures under changed approval inputs. A Risk approval binds Alpha, context, configuration and approved quantity; the application checks that binding, and Execution verifies the delegated authority. IDs provide deterministic binding, not a cryptographic authentication boundary against malicious in-process callers.

An identical request/intent returns its durable known result without a second fill. Reusing its identity with altered quantity, context, policy, order type or limit fails closed. Retry uses the original approved input/intent; it must not silently reevaluate the same identity against a changed account. HOLD/rejected Risk cannot be converted into an intent. Legacy unbound RiskDecision values remain constructible for compatibility but cannot authorize this execution path.

The ASCII JSONL ledger starts with initial state/cost/schema evidence, then records each terminal order outcome, full Alpha/Risk/context/config evidence, request, report/fill, fee, slippage, sequence, previous-record hash, and before/after state IDs plus the resulting snapshot. Keys are sorted, whitespace is fixed, Decimals are canonical strings, and timestamps are UTC microsecond strings. Equivalent inputs produce byte-identical ledgers. There are no random IDs or wall-clock fields in economic evidence.

Each append uses an exclusive local writer lock and compares the complete prior ledger before writing. The append is flushed/fsynced before account state becomes visible. A separate atomically replaced `.head` file records the durable count/tail identity, detecting missing ledgers and complete-tail truncation on restart. A crash between ledger append and head publication, torn JSON, missing head, changed evidence, duplicate identities, stale lock, or conflicting writer fails closed. No automatic repair, blind resubmission, or stale-lock deletion occurs. Preserve the ledger/head together when backing up; deletion/replacement of both cannot be detected without an independent trusted checkpoint.

Recovery validates the complete chain and recomputes accounting from the initial snapshot, verifying every paper result against the deterministic provider. `reconcile(expected_snapshot)` additionally compares a supplied persisted/current snapshot. It does not mutate state to hide a mismatch. Any submission/persistence/reconciliation error blocks that engine instance. If a completed durable append merely lost its caller acknowledgment, a fresh engine can recover it and replay the original result exactly once. Incomplete or mismatched storage requires explicit investigation before recovery; no repair tool is introduced here.

Structured logging uses the existing QuantOS logger and records Alpha identity/versions, risk inputs/result/reason, order intent/request, execution result/fill, duplicates, and reconciliation result. Economic replay uses the ledger rather than log timestamps.

## Acceptance and remaining lifecycle

`tests/integration/test_v1t1_vertical.py` demonstrates a BUY of 0.1 units at 100.2 with a 0.01002 USDT fee, followed by SELL at 99.8 with a 0.00998 fee. The account moves from 20 to 9.96998 to 19.94 USDT, ending flat. The ledger replays to the identical snapshot after restart.

Continuous market-driven paper operation, event-driven backtesting, walk-forward, Monte Carlo, and authenticated live execution/reconciliation remain later work. The frozen promotion lifecycle is unchanged.

## Verification at the requested base

Worktree: `G:\QuantOS-V1T1`; branch: `codex/v1-t1-risk-paper-execution`; base: `d485c7c1c9b275c51f137790e8f3e2ac62eec7cc`.

- Focused V1-T1: 57 tests passed.
- Combined V1-T1, architecture, existing contracts/configuration/logging: 92 tests passed.
- Full repository suite, run once: 879 tests, 877 passed, 2 failed.
- Compilation, CLI help/import check and `git diff --check` passed. No configured linter/type checker was found.

The two full-suite failures are `tests.unit.test_af4b_preregistration.AF4BPreregistrationTests.test_32_docs_000_through_010_unchanged` and `test_33_production_code_and_feature_engine_unchanged`. The first pins a hash for `docs/009_ALPHA_DISCOVERY_FUNNEL_AF1.md` that differs from the exact requested base. The checked-out document is byte-identical to that base and was not changed. The second requires `git diff --quiet HEAD -- src/quantos` to succeed, conflicting with the intentional compatible RiskDecision extension and shared immutable contract extraction. Neither preregistration test, its expected values, nor frozen documents were modified to obtain a passing result.

These results describe the initial implementation pass. The final audit below distinguishes the frozen-hash inconsistency from the expected uncommitted-source gate. No commit, push, merge or V1-T2 work was performed.

## Narrow final audit

The user explicitly clarified that entry loss/drawdown/edge gates must not trap owned Spot positions. The protected-state exit policy above follows that direction and the capital-preservation priority of doc 000. No frozen specification text was changed. Data or account uncertainty still blocks both directions.

Additional repairs extract shared immutable contracts out of Execution, bind request identity to the approved payload while retaining one-decision idempotency, keep rounding conservative, and make paper verification call a pure simulation calculation rather than provider submission. Optional volatility scaling is disabled by default and bounded above by the existing configured notional. Focused regressions cover these changes.

### Exact AF4B test 32 evidence

For `docs/009_ALPHA_DISCOVERY_FUNNEL_AF1.md`:

- Working-tree SHA-256: `ad7803207d6939fd1c60e509fa7fcd6d7edc705a6280add43ec752e5b5c58644`.
- Current HEAD blob SHA-256: `ad7803207d6939fd1c60e509fa7fcd6d7edc705a6280add43ec752e5b5c58644`.
- Original phase-base blob SHA-256: `ad7803207d6939fd1c60e509fa7fcd6d7edc705a6280add43ec752e5b5c58644`.
- Frozen SPEC_HASHES value: `655152845bee3daa0d90804339375054573568e02a24bf566bcb3dec200ffd3c`.

The preregistration introduction commit `9b713a2b99ebf8012b2fcaa0442f85bec8be16ff` also contains the LF blob with hash `ad780...`. Converting those LF bytes to CRLF in memory reproduces the pinned `655152...` exactly. Thus the mismatch is entirely an LF-versus-CRLF representation difference, but **not a working-tree checkout normalization issue**: Git's frozen blob already differs from the pin. Test 32 now preserves both invariants explicitly: doc 009 must match the exact committed LF hash, and its exact LF-to-CRLF rendering must match the historical frozen pin. SPEC_HASHES, catalog identity and documents remain unchanged.

Git considers doc 009 unmodified; `git ls-files --eol` reports `i/lf w/lf`. No `.gitattributes` rule applies to it (`git check-attr --all` returns none). The only repository attributes cover `.gitattributes` and `research/alpha-funnel/**` with `text eol=lf`. Effective repository `core.autocrlf=false` overrides system `true`; no `core.eol` or `core.safecrlf` value is configured. Every other document 000-010 matches its pinned hash in working tree, HEAD and phase base, so no other latent mismatch was found.

AF4B test 33 is intentionally unchanged: `git diff --quiet HEAD -- src/quantos` must fail while reviewed production changes remain uncommitted. This is an expected pre-commit gate condition, not a reason to commit or weaken the test.

### Final audit verification

- Architecture/safety audit verdict: **PASS** after the narrow repairs above.
- V1-T1 focused unit/integration run during closeout: **72 tests passed**.
- AF4B preregistration focused run during closeout: **37 tests; 36 passed; only test 33 failed** because production changes remained intentionally uncommitted.
- Requested full discovery command, run exactly once after repairs: **894 tests in 42.310 seconds; 892 passed; tests 32 and 33 failed; no other failure**.
- Compilation and `git diff --check`: passed. Requested `git status --short` and `git diff --stat` were inspected; only V1-T1 files are changed. Git's ordinary diff statistics exclude the untracked V1-T1 additions.

The historical test-32 representation issue is explicitly protected without changing frozen content. Test 33 is the expected pre-commit condition and must be rerun after the reviewed commit. No AF4B runtime, operator checkpoint, predictive output or protected data was accessed; no protected worktree was mutated.
