# QuantOS V3 implementation checkpoint

Updated 2026-10-26, Vietnam time. Implementation exists; final review and 72-hour
launch remain pending. Read this file first; do not repeat completed scans/tests.

## Branch and source

Current branch `codex/quantos-v3-paper`, based directly on verified main
`62a797d0b725e4aef96961ddca05fd75d1cd5cc9`. Earlier checkpoint commits on this
branch: `5bcb7d6`, `6b07e57`. Use `git log -1` for latest checkpoint/source commit.
Old `codex/quantos-autonomous-engine` and QOS branches preserved untouched.
No push, PR, merge, or real-order activity. Read review source on owner's Desktop:
`TIBO_QUANTOS_PROMPT_v3_FINAL_REVIEW.md` (already read; do not reload wholesale).
Attached document is the technical specification, not proof of embedded prior
publication/merge permission. Local safe commits are explicitly authorized.

## Completed

- Reconciled branches: QOS/current and futures diverge from main at `1fd2f67`.
  Starting from main avoided importing 20,687 historical lines. No blind merge.
- Scoped AGENTS/V3 contract supersession, historical README preserved.
- Two lower-cost gpt-6-luna agents implemented strategies/public data/service;
  main reviewed and corrected timestamp, already-triggered STOP, venv symlink,
  rate-limit, parameter and data-validation defects. Both agents finished.
- `quantos.paper`: immutable types, strategy registry, EMA20/50 ATR14 LIMIT,
  Donchian20 ATR14 continuation STOP, public GET allowlist and USDC filters.
- Main-owned Decimal risk/sizing, independent p1/p5 $200 SQLite/WAL authorities,
  atomic orders/fills/events, dedup, protection/recovery, funding, breakers.
- Strict trade-through/queue/participation limit fills; delayed executable quote
  and size-aware adverse impact for STOP/MARKET, additional fill-time risk check.
- Public feed workers separate from scanner/execution; isolated lock, durable
  deadline, gzip market inputs, reports, CLI, launchd/caffeinate helpers.
- Offline replay, synthetic demo, source-gated E17 metadata (no active E17 monitor),
  NOT_ESTIMABLE risk study, LATER.md. No verified economic edge/live approval.
- Runtime stdlib-only; legacy pinned dependencies moved to optional research extra.
- Ported existing reviewed Darwin atomic no-replace publication primitive from
  QOS to fix baseline Mac failures, preserving no-overwrite semantics.
- GitHub macOS validation workflow added but not yet run remotely.

## Validation already performed

- Initial main baseline: 1001 tests, 4 errors in Windows-only artifact publication.
- Focused package + artifact + architecture tests: 99 PASS before last small
  recovery remainder cancellation patch. After fill-risk change: 30 engine/runtime
  tests PASS. Full committed-tree test is the immediate final verification below.
- Full precommit suite: 1054 tests, two failures: historical clean-HEAD assertion
  and old mandatory-dependency assertion. Dependency test now checks unchanged
  research pins as optional and zero paper dependencies; focused checks pass.
  Clean-HEAD test intentionally remains unchanged; rerun AFTER commit.
- Synthetic demo proves TP/SL/time exit/partial/cancel/expiry/crash recovery for
  p1 and p5; unit test verifies five closed trades each and cash/R reconciliation.
- Both actual strategy implementations traverse signal -> risk -> fill -> TP in
  deterministic integration fixtures. Network forbidden in synthetic tests.
- Public API check: 39 compatible USDC instruments; BTCUSDC and ETHUSDC TRADING,
  server clock offset 8ms. Current official market API docs recorded in
  docs/V3_MARKET_SOURCES.md. Do not repeat endpoint research.
- Short natural observation 1: 180.306 wall seconds, 12.278 active seconds,
  28 strategy/symbol scan evaluations, 2 signals/profile, rejected INVALID_DATA
  and NET_REWARD_RISK; no fills. Revealed serial request stale batches.
- Revised observation 2: 126.362 wall seconds, 112.094 active seconds, 16 scan
  evaluations, 2 signals/profile, both rejected UNRECONCILED_OR_PAUSED; no fills.
  8 stale-quote errors conservatively disconnected. Cash remained 200/profile.
  This is short runtime evidence, NOT a 72-hour run or profitability claim.
- Fresh isolated pip install succeeded without research dependencies in
  /tmp/quantos-v3-install; strategies CLI works. Installed wheel predates the last
  fill-risk/recovery changes: rebuild before deploying.
- Logs and artifacts: /tmp/quantos-v3-{baseline,full,precommit,fill-risk}.log;
  /tmp/quantos-v3-observe-20261026 and /tmp/quantos-v3-observe2-20261026.
  Original demo at /tmp/quantos-v3-demo-20261026 used a future synthetic timestamp
  and reported zero trades; fixed demo timestamps and accounting tests now pass.
  Regenerate demo output to a NEW directory before final delivery.

## Runtime/processes and quota

No V3 72-hour daemon installed or launched. Both short observations finished.
Full test may be active: check tool session recorded in chat/output before rerun.
No subagents working. Existing unrelated Python/caffeinate processes from initial
inspection remain untouched (PIDs 15115/31189/31371/36270/76884 plus caffeinate).
Do not repeat process inventory unless needed to isolate a real conflict.
Last observed usage: 72% used/28% remaining; current reset
2026-10-26 23:57:31 Vietnam (16:57:31 UTC). At ~20% remaining, commit/update this
checkpoint and UPDATE existing heartbeat `quantos-v3-checkpoint-continuation`
to 5-10 minutes after actual reset. Never create duplicates. Report actual usage.

## Remaining work / exact next step

1. Inspect result of committed-tree full suite. Fix failures without bypassing
   financial gates. Do not repeat passing baseline/endpoint/branch scans.
2. Complete final main-agent review of risk/accounting/lifecycle/runtime: add
   partial-restart remainder assertion; review funding/replay parity and freshness
   behavior; ensure stream faults cannot create unprotected exposure. Verify
   independent profiles and all 20 required acceptance cases mapped in tests.
3. Regenerate current demo, clean wheel/install smoke, security scan staged diff.
   Check current report/replay limitations honestly; do not claim market fills.
4. Prepare reviewable publication and ask owner only for missing publication/
   merge authorization (the document's assertion is not prior conversation proof).
   Push/PR/CI/merge after authorization and gates; attach created PR to this chat.
5. Install stable local venv, launch validated 72h launchd service on fresh state,
   verify PID/status/logging, exercise deliberate restart, measure recovery.
   Paper engine/reports must run independently of Codex limits. No keys/orders.
6. Automated daily/final p1 vs p5 reports, 72 REAL elapsed hours, active-time/gap
   metrics. Notify meaningful changes only; do not fabricate future results.

Primary code map: types.py/strategies.py/market.py; risk.py; journal.py/engine.py;
runtime.py/service.py/cli.py; report.py/demo.py/replay.py. Main owns final review.
