# QOS-07 critical-path fix report

Date: 2026-10-13 (UTC+7). Scope: engineering corrections, public paper only.

## Source and result

Starting sanitized SHA: `b85203b46bfd5af0557b7e05e86b72d713a7cbb2`.
Final tested hotfix implementation SHA: `be0cd7d01146d24c7b4e5c17d241d8e299afa111`.
The following report-only commit contains this document and no executable changes.
Its containing Git commit is the release-tip identifier; the execution's final
publication receipt supplies the exact remote tip SHA after GitHub readback.
This avoids embedding a commit's own unknowable hash in its contents.

- **ENGINEERING_FIX_PASS**: economic admission and all regressions pass.
- **RUNTIME_INTEGRITY_PASS**: fatal-error, durable completion, restart protection,
  final five-minute public session and exact replay pass.
- **ALPHA_INTERFACE_PASS**: exclusive typed provider path and legacy fixtures pass.
- **RETAIL_CAPITAL_PASS**: $200, <=1x, reserves and 3% breakers preserved.
- **PUBLICATION_PASS** engineering/security prerequisites: clean scanner and
  privacy checks; sanitized ancestry only. Publication is authorized only after
  rechecking remote refs and scanning this report's complete commit history.
  Actual remote push/readback result is recorded in the final execution receipt.

Intended published branch:
https://github.com/BaileyVu/QuantOS/tree/fix/qos07-critical-path

## Root causes and corrections

### Economic target validation

The old full-equity admission compared predicted return with costs but checked
only target direction. A large prediction could admit an uneconomic target.
Risk now independently compares target distance from executable ask/bid with
entry/exit taker fees, slippage, latency, funding, safety allowance and remaining
exit half-spread. Entry half-spread is already paid by using ask/bid; it is not
charged again. Exit fees/slippage scale with target/entry notional. Equality
rejects. Prediction minus uncertainty must separately clear the existing hurdle.
Protective-stop distance, adverse-gap reserve and stop-risk sizing are unchanged.
No fee floors or Alpha thresholds changed.

### Failure handling and journal integrity

Broad exception catches and substring decisions previously converted unrelated
failures into reconnects. Only errors classified at public transport/book
boundaries now reconnect; nonretryable HTTP failures escape. Journal, account,
log and programming failures stop. Background producer/worker errors are checked
before completion, including cleanup-time errors. Output I/O errors cannot be
reclassified by the WebSocket adapter as network failures.

Successful completion follows disarm and a validated, durable account checkpoint.
Abnormal paths attempt to preserve the in-memory ledger and append an explicit
aborted marker, never a completed report. A failed journal becomes unusable;
failed terminal writes attempt removal of the uncommitted success record.
Replay rejects aborted/nonterminal completion. The managed launcher verifies
exact replay, durable ledger digest and checkpoint metrics before releasing the
active-session marker. Failures retain the marker and block restart.

### Provider isolation

An explicit provider is the exclusive strategy path, independently of the
legacy unified flag. Both reset/observe interfaces are required. Only
ProtectedRetailSignal or None is accepted; numeric types/finiteness, IDs, side,
book identity, observation/expiry and horizon are checked before Risk. Legacy
Alpha calculations are skipped. Statistics identify the selected provider.
Risk remains final admission authority; Execution and accounting are unchanged.
No provider or synthetic Alpha was added to operational CLI choices.

## Validation

Native arm64 Python 3.12.14. Focused suite: **67 passed**. Complete offline suite:
**1,282 passed**, zero failures (52.217 seconds); baseline was 1,257.
Imports/CLI, compilation, dependency consistency (`pip check`), architecture,
configuration, authority-isolation, ledger/restart and deterministic replay
checks passed. `git diff --check` passed. No new dependencies.

Three representative regressions were also run against the frozen source,
with import location explicitly checked: close target, exclusive provider and
journal disk failure all failed there and pass on the hotfix.

New regression methods in `tests/unit/test_critical_path.py`:

- `TargetEconomicsTests.test_prediction_cannot_override_close_target`
- `TargetEconomicsTests.test_long_and_short_economic_targets`
- `TargetEconomicsTests.test_exact_cost_boundary_rejected`
- `TargetEconomicsTests.test_invalid_protection_and_prediction_rejected`
- `TargetEconomicsTests.test_costs_turn_gross_profit_into_loss`
- `TargetEconomicsTests.test_legacy_hft_leverage_cannot_expand_full_equity`
- `ProviderIsolationTests.test_exclusive_provider_without_unified_and_real_fill`
- `ProviderIsolationTests.test_none_is_no_trade`
- `ProviderIsolationTests.test_malformed_contract_rejected_before_risk`
- `ProviderIsolationTests.test_observe_contract_required`
- `ProviderIsolationTests.test_failed_risk_never_submits`
- `SessionFaultTests.test_timeout_reconnects_under_duration_bound`
- `SessionFaultTests.test_connection_disconnect_reconnects`
- `SessionFaultTests.test_websocket_adapter_disconnect_reconnects`
- `SessionFaultTests.test_producer_programming_failure_is_not_lost_in_cleanup`
- `SessionFaultTests.test_external_worker_programming_failure_is_fatal`
- `SessionFaultTests.test_journal_disk_failure_aborts_without_completion`
- `SessionFaultTests.test_hash_append_failure_aborts_without_completion`
- `SessionFaultTests.test_checkpoint_failure_aborts_without_completion`
- `SessionFaultTests.test_invalid_account_aborts_without_completion`
- `SessionFaultTests.test_controlled_kill_durably_completes`
- `SessionFaultTests.test_failure_after_real_fill_preserves_ledger_without_completion`
- `SessionFaultTests.test_programming_failure_is_fatal`
- `JournalIntegrityTests.test_failed_terminal_fsync_does_not_leave_success`
- `JournalIntegrityTests.test_aborted_or_nonterminal_completion_is_rejected`

The existing managed signal-exit test now creates real replayable evidence and
a matching durable account rather than treating a mocked zero exit as success.
Existing crash/restart tests verify that unresolved markers prohibit reuse.

## Capital and compatibility

Starting equity remains exactly $200. Full-equity allocation has no standalone
1% per-trade fraction, uses reconciled equity, cannot exceed 1x, and retains
fee/gap reserves and 3% daily/peak breakers. Existing profit/loss compounding,
partial fills, protective stop/target, no averaging and no borrowing tests pass.
A regression asserts that legacy HFT's 5x parameter cannot expand retail exposure.
No configurations, historical documents or HFT implementation were modified.

Saved historical captures are not present in this sanitized checkout. No private
folder was accessed to locate them. Existing deterministic QOS04/QOS05 fixtures
were generated/replayed independently against both baseline and hotfix. Exact
unchanged hashes:

- qos04: decision `f177676e2e4d33b0ce75a0c332e18d6854d407991d6208cea18185f82151635b`; ledger `5b2fc61acc7714ffc74564851eeb1cb4953302c5037ff3bf2c4a3a675948f30b`.
- qos05: decision `e284556d11d81e44a86ea50960b104d082b3076ead9e79c7d00348ef2734d8b0`; ledger `92e240091b368ff11f8b6c483902f5edd0a5c8a9b354f45f651c4c39a7226060`.

The original public source manifest documents the frozen baseline; it is retained
as historical provenance and is not a hash manifest for the changed hotfix files.

## Final public-feed paper smoke

Fresh account, 300 seconds, normal duration expiry, zero reconnects. Exact replay
and durable account reconciliation passed; managed session marker cleared.
Events: {'trade': 1359, 'depth': 2761, 'bookticker': 26284, 'mark_price': 299}. Total: 30703.
Startup/final equity: $200/$200. Orders/cancels/entry fills/exit fills: 0/0/0/0.
Fees, funding, net PnL and drawdown: all zero. Authenticated requests and real
orders: zero; only the public paper adapters were invoked.
Usable coverage: 299.11843875 seconds.
Maximum queue delay: 32.833041 ms;
maximum observed feed latency: 219.443 ms.
Q05 evaluated 2,761 times, all `range_warmup_300s`; no active candidates/orders.
Legacy Q03 diagnostic confirmations were not selected by the active Q05 route.
Decision digest: `621de2257854baa79bebbc04797243d8f5001f2f6b7972fb4742e9317e7da864`.
Ledger digest: `0acbe1323c7755b79d8fadfed7e15ace7d046d32faa7395746b113c8e8ab7d8b`.

A preliminary public run was intentionally stopped via its kill marker during
review of background-error handling. Its evidence/account were preserved. The
final verification used another fresh account and the final executable code.
No accounts or historical results were reset or overwritten.

## Security and limitations

Gitleaks 8.24.3: full snapshot and complete reachable history clean; synthetic
positive control detected. Private path/identity, artifact, credentials, metadata,
mode, symlink, submodule and LFS checks passed. Author/committer use a nonpersonal
release identity. The only root is the sanitized release; original private
history is not reachable. Original local checkout remains clean and unchanged.
Freeze/main and all other remote refs must remain unchanged across publication.
No raw captures, account records, environments or logs enter Git.

A physical storage failure can prevent even an abort record or final persistence;
missing evidence and the unresolved session marker therefore remain blocking.
This is engineering verification, not Alpha profitability or live readiness.
Existing already-public historical identity exposure is not altered by this fix.

## Files changed

- `src/quantos/application/retail_edge_session.py`
- `src/quantos/application/retail_replay.py`
- `src/quantos/application/retail_trader.py`
- `src/quantos/domain/risk/retail.py`
- `src/quantos/infrastructure/storage/retail_events.py`
- `src/quantos/interfaces/retail_edge.py`
- `src/quantos/interfaces/retail_runtime.py`
- `tests/unit/test_critical_path.py`
- `tests/unit/test_retail_full_equity.py`
- `docs/QOS07_CRITICAL_PATH_FIX_REPORT.md`
