# V3 Phase 7 acceptance coverage

This matrix maps the 20 required Phase 7 scenarios in
`TIBO_QUANTOS_PROMPT_v3_FINAL_REVIEW.md` to the current named unit tests. “Partial”
means the test exercises a local deterministic model of the behavior, but does
not establish the full scenario as phrased. These tests are not evidence of
real exchange execution, profitability, or live readiness.

| # | Required scenario | Exact test method(s) | Coverage |
|---:|---|---|---|
| 1 | Valid signal → risk approval → entry → fill → TP → journal | `tests.unit.test_v3_engine.EngineTests.test_limit_fill_target_reconciles_fees_and_no_reverse`; `tests.unit.test_v3_runtime.RuntimeTests.test_each_reference_strategy_traverses_risk_entry_and_target` | Partial: deterministic local paper engine; synthetic inputs, not a natural run or exchange. |
| 2 | Risk rejection creates no order | `tests.unit.test_v3_engine.EngineTests.test_risk_rejection_creates_no_order` | Covered by the local engine test. |
| 3 | Duplicate signal creates no duplicate order | `tests.unit.test_v3_engine.EngineTests.test_duplicate_and_timeout_after_accept_do_not_duplicate` | Covered for repeated local proposal. |
| 4 | Unfilled entry expires and cancellation is verified | `tests.unit.test_v3_engine.EngineTests.test_unfilled_expiry` | Partial: local expiry state is asserted; no independent exchange cancellation query. |
| 5 | Partial fill cancels remainder and protects filled quantity | `tests.unit.test_v3_engine.EngineTests.test_partial_then_expiry_keeps_protection` | Partial: local order state asserts remainder zero and stop active. |
| 6 | Stop loss closes position and cleans unused target | `tests.unit.test_v3_engine.EngineTests.test_stop_and_cleanup` | Covered: SL close, cash loss, and inactive unused target asserted. |
| 7 | Take profit cleans unused stop without reverse exposure | `tests.unit.test_v3_engine.EngineTests.test_limit_fill_target_reconciles_fees_and_no_reverse` | Covered in the local engine model. |
| 8 | Holding deadline exits position | `tests.unit.test_v3_engine.EngineTests.test_hold_deadline_exit` | Partial: deterministic quote-driven local exit. |
| 9 | POST timeout after exchange acceptance does not duplicate | `tests.unit.test_v3_engine.EngineTests.test_duplicate_and_timeout_after_accept_do_not_duplicate` | Covered in fake/local mode: accepted intent persists, timeout forces a query of the durable local authority (asserted by spy), and duplicate proposal cannot resubmit. |
| 10 | Cancellation/fill race resolves from authoritative state | `tests.unit.test_v3_engine.EngineTests.test_cancel_fill_race_queries_authoritative_state` | Partial: local engine receives the fill evidence and asserts protected state; no live exchange query. |
| 11 | Crash after entry fill recovers protection | `tests.unit.test_v3_engine.EngineTests.test_durable_crash_fill_recovered_with_protection` | Covered for the local journal and recovery path. |
| 12 | WebSocket disconnect blocks uncontrolled exposure | `tests.unit.test_v3_engine.EngineTests.test_disconnect_cancels_remaining_and_blocks_new_exposure` | Partial: local connection-down transition; no real WebSocket transport failure. |
| 13 | Stale prices or invalid filters reject order | `tests.unit.test_v3_engine.EngineTests.test_invalid_quote_and_filter_fail_closed` | Partial: risk validation rejects stale quote and zero tick; does not cover every filter failure. |
| 14 | Loss breaker blocks new entries | `tests.unit.test_v3_engine.EngineTests.test_loss_breaker` | Covered for the local breaker state. |
| 15 | Conflicting manual Futures position fails closed | `tests.unit.test_v3_engine.EngineTests.test_manual_position_blocks_recovery` | Partial: injected manual-position conflict in the fake engine, not an account position lookup. |
| 16 | Stop placement rejection triggers controlled protection response | `tests.unit.test_v3_engine.EngineTests.test_rejected_protection_closes_safely` | Partial: fake stop rejection and subsequent local closing path. |
| 17 | Conditional/OCO rejection never claims protected status | `tests.unit.test_v3_engine.EngineTests.test_rejected_protection_closes_safely` | Partial: fake stop/target leg rejection; no exchange-native OCO/conditional adapter path. |
| 18 | USDC balance with incompatible margin symbols cannot trade | `tests.unit.test_v3_engine.EngineTests.test_usdt_incompatible` | Partial: risk rejects a USDT-margin instrument; no actual account-balance query. |
| 19 | Restart after completed trade does not repeat entry | `tests.unit.test_v3_engine.EngineTests.test_completed_trade_restart_cannot_reenter` | Covered against the local durable journal. |
| 20 | No-key paper start works; authenticated endpoint attempts fail | `tests.unit.test_v3_runtime.RuntimeTests.test_full_no_key_supervisor_start_uses_only_injected_public_feed`; `tests.unit.test_v3_runtime.RuntimeTests.test_no_live_mode_or_authenticated_order_command`; `tests.unit.test_v3_market.PublicMarketTests.test_auth_and_arbitrary_endpoints_unavailable`; `tests.unit.test_v3_runtime.RuntimeTests.test_seeded_demo_both_profiles_and_accounting` | Covered: complete supervisor lifecycle with empty environment and network trap; public adapter separately rejects every non-allowlisted endpoint, and live CLI modes are unavailable. |

## Remaining evidence gaps

The listed tests exercise the local paper engine and injected public-data
transport. They do not exercise Binance order endpoints, an authenticated
account, or a real WebSocket. Scenario 9 now verifies the independent local-authority query after an injected timeout. No real HTTP POST or authenticated exchange query is implemented or authorized. Scenarios 10 and 15–18 also remain fake/local
coverage only. No real-order submission code is part of this V3 paper runtime.

The two reference strategy paths have deterministic seeded traversal coverage,
and a separate offline replay is covered by
`tests.unit.test_v3_runtime.RuntimeTests.test_recorded_inputs_replay_deterministically_without_network`.
These do not substitute for the required real public-feed paper observation.
No natural-feed duration or live observation result is asserted by this matrix.

Updated review evidence: partial-restart remainder cancellation, paused/unreconciled entry gates, funding-loss breaker, funding delivery despite quote failure, and identical versioned replay fills/cash have dedicated regression tests. Full observed durations are recorded in IMPLEMENTATION_CHECKPOINT.md.
