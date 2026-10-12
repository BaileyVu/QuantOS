# QOS-07 final handoff

Branch `codex/qos07-final-runtime`; actual QOS-06 base
`b523902f31e7c4a96fc641020e5ae855a0805111`, clean and descended from `2c368e8`.
Implementation checkpoint `4f9c077`; final tested executable HEAD
`e94092f1ba37b240c1ab93cf8052701f89d9ba92`. The subsequent documentation-only
commit contains this handoff; resolve its exact final HEAD with
`git rev-parse codex/qos07-final-runtime` (also supplied in the delivery message).
No push, merge, service installation or scheduled follow-up.

## Changes and demonstrated defects

- Durable pre-network startup, structured errors/exit code, process/session
  identity, kernel locks, bounded logs/journal, disk preflight and signal shutdown.
- Interrupted-account marker blocks restart after a potentially incomplete
  checkpoint. Successful terminal persistence clears it. No silent loss rollback.
- Explicit stop/target state follows partial inventory, survives restart, latches
  triggers and preserves consumed exit liquidity across order retries. Recovery
  exits require fresh depth and acknowledgment; stale data never fabricates fills.
- QOS06 CLI now actually constructs the reliability evaluator it requested.
  Previously that flag reached the engine but not its evaluation instance.
- The exact cause of QOS06's detached-process termination remains unknown.
  The verified foreground launcher removes dependence on a detached agent shell
  and makes future failures observable; no invented root cause is claimed.

Changed production files: `application/retail_trader.py`, `domain/alpha/retail.py`,
`domain/risk/retail.py`, `domain/execution/retail_paper.py`,
`infrastructure/configuration/retail.py`, `infrastructure/storage/retail_paper.py`,
`infrastructure/storage/retail_events.py`, `interfaces/retail_edge.py`, and
`interfaces/retail_runtime.py` under `src/quantos/`. Also changed:
`configs/retail_full_equity_paper.toml`, `tests/unit/test_reclaim.py`,
`tests/unit/test_retail_full_equity.py`, `AGENTS.md`, `docs/000_READ_FIRST.md`,
`docs/QOS07_CAPITAL_ADDENDUM.md`, `docs/QOS07_OPERATOR.md`, and this handoff.

## Capital, exits and Alpha boundary

New accounts start at $200. Explicit `retail-full-equity-v1` targets up to 100%
of reconciled equity as unlevered notional after costs/reserves, capped by venue
limits, visible liquidity and remaining 3% daily/peak drawdown loss headroom.
The standalone per-trade risk fraction is **None**, not the legacy 1% cap.
Profits compound; losses reduce size. No borrowing, deposits or position stacking.

Modeled loss includes actual stop distance, full conservative round-trip costs,
20 bps adverse-gap reserve, 2 bps stop uncertainty and 5 bps adverse-entry reserve.
Stops do not guarantee prices or cap actual losses. Gaps and insufficient
liquidity can exceed modeled loss and the 3% circuit threshold. Execution uses
later executable depth and slippage, partial quantities and once-only accounting.
Shutdown preserves unfilled exposure. Frozen Q05-LR-v1 parameters are unchanged.

A future typed `ProtectedRetailSignal`/NO_TRADE provider attaches through the
application protocol without changing Risk, Execution or accounting. The
synthetic provider proves economic admission → Risk → entry → protective exit
→ realized profit → larger next notional. It is not available to the live feed.

## Verification

Python **3.12.14 arm64**. Final offline suites: **75 focused passed; 1,257 full
passed; zero final failures**. Architecture checks, CLI imports/help, dependency
check and diff checks passed. An intermediate disallowed `typing` import was
moved out of Alpha into the application layer before final verification.

Tests cover reserves/full sizing, stop/headroom admission, compounding/loss
reduction, invalid exits/NO_TRADE/Risk rejection, gap slippage, partial entries
and exits, trigger latching, retry liquidity, restart protection, corrupt or
incompatible state, stale session rejection, account concurrency, bounded logs,
startup failure, SIGTERM/exit evidence and existing clock/feed/warmup guards.
QOS07 synthetic capture replay is exact; saved genuine QOS05 replay remains exact.
Open-position recovery is synthetic evidence, not a claim of public-feed trades.

## One final-code public smoke

Foreground managed run: **2026-10-12 02:24:47.644922–02:29:52.052771 UTC**,
300-second session, normal duration shutdown, exit **0**, no error. Fresh account
identity `local-paper-identity-omitted`.

- 35,952 events: 31,894 book tickers, 2,742 depth updates, 1,017 trades, 299 marks.
- One usable window, **298.538576042 s**; zero reconnects or stale/clock disarms.
  One normal shutdown disarm. Peak feed age 517.553 ms; queue delay 74.83525 ms;
  zero invalid queue timestamps. Initial clock HEALTHY, offset 91.539 ms,
  selected RTT 137.046083 ms; no scheduled recalibration was due in this smoke.
- Q05: 2,742 evaluations, all five-minute range warmup; zero candidates,
  confirmations, cost admissions, Risk approvals, orders, fills or exits.
- Fees/funding/net PnL/drawdown **0**; ending equity **$200**, flat, no pending order.
- No authenticated requests or economic exchange orders; only public adapters.
- Complete capture replay: **matches_capture=true**, including exact account,
  decisions, Risk, ledger, costs and PnL. Post-run account preflight passes;
  active-session marker cleared and terminal status is durable.

Decision digest `857040c4143de2da76fad1a987ee51fb39ba0912cde5d6a00e63313fc06f24be`;
ledger digest `0acbe1323c7755b79d8fadfed7e15ace7d046d32faa7395746b113c8e8ab7d8b`.
Capture SHA-256 `6bf6097bb795340003268b7a65d5c9544a7ad4529acd798616df5a2c11763cf1`.
Evidence is preserved under ignored `artifacts/qos07/`; journal 41,859,859 bytes,
runtime log 150,557 bytes. Zero trades is not an expectancy estimate.

## Operator handoff and limits

[QOS07_OPERATOR.md](QOS07_OPERATOR.md) contains complete preflight, new-account,
foreground launch, status, safe stop, replay and recovery commands. Exact verified
launch (its existing session directory is exclusive and must not be reused):

```sh
.venv/bin/python -m quantos.interfaces.retail_runtime run --state-store artifacts/qos07/smoke/account.json --market-config configs/futures_hft_200_paper.toml --capital-profile configs/retail_full_equity_paper.toml --session artifacts/qos07/smoke/session-001 --duration-seconds 300
```

Read-only next command:

```sh
.venv/bin/python -m quantos.interfaces.retail_runtime status --session artifacts/qos07/smoke/session-001
```

Mac sleep/lid closure, power failure and network loss remain environmental
limits. SIGKILL cannot write an exit record; the account marker fails closed.
Incomplete captures require reconciliation and explicit owner recovery, not an
automatic reset. Twelve-hour endurance and an actual OS reboot with exposure were
not tested. No blocking defect remains in the verified bounded paper path.

| Classification | Result and scope |
|---|---|
| RUNTIME_READY | YES — bounded foreground paper operation; offline crash/recovery tests |
| EXECUTION_READY | YES — conservative paper lifecycle and deterministic synthetic recovery |
| RETAIL_CAPITAL_READY | YES — isolated full-equity profile, reserves and loss headroom |
| ALPHA_READY_FOR_INTEGRATION | YES — typed provider boundary and synthetic end-to-end proof |
| ALPHA_PROFITABILITY_VALIDATED | **NO** — no independent market profitability evidence |

No engineering status grants real-money authority. Await owner-selected Alpha
and review; QOS07 ends here.
