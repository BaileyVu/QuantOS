# QOS-06 final report — DATA_LIMITED

Branch: `codex/qos06-market-reliability`. Actual clean QOS-05 base: `48d61052f29e64a22f1d6964189a76a1961a94cd`, descended from the previously verified implementation `4ba46cc3714278a24163cf023cc6d9fe8833cd4d`. QOS-06 implementation checkpoint: `2c368e8`. The final local HEAD is the commit containing this report (`git rev-parse HEAD`). No push or merge was made.

Changed files: `docs/QOS06_PROGRESS.md`, this report, `src/quantos/application/retail_edge_session.py`, `src/quantos/application/retail_replay.py`, `src/quantos/application/retail_trader.py`, `src/quantos/domain/evaluation/retail.py`, `src/quantos/domain/evaluation/reclaim_diagnostics.py`, `src/quantos/domain/features/reclaim.py`, `src/quantos/interfaces/retail_edge.py`, `tests/unit/test_reclaim.py`, and `tests/unit/test_reclaim_diagnostics.py`. All historical code, captures, accounts, specifications, and the frozen Q05-LR-v1 thresholds remain unchanged.

## Reproducible defect and reliability findings

The QOS-05 reconnect path replaced the specific engine disarm reason with `engine disarmed; fresh reconstruction required`. QOS-06 preserves that reason in the reconnect error and records timestamp, corrected feed age, local queue delay, clock offset and calibration RTT half-width for timing rejections. Bounded diagnostics record valid feature-window durations and prior five-minute range histograms without changing the strategy. QOS-06 also has a separate account/manifest identity, a 12-hour mode limit, and an 8 GiB journal cap.

The four QOS-05 stale disarms were genuine corrected feed latencies of 1652.4, 1530.697, 2559.459, and 1559.902 ms against the existing 1500 ms limit. Corresponding local queue delays were 0.915, 2.055, 30.691, and 3.041 ms. The prior capture had four gaps above 1.5 s, maximum 8.011 s. A scheduled post-recalibration `DEGRADED` state is intentional clock-monitor verification behavior, not a demonstrated calibration bug. No limit was widened, and no feature warmup was bypassed. The reported RTT half-width is a calibration diagnostic, not a guarantee of end-to-end timestamp accuracy.

## Verification

Native Python 3.12.14 arm64; package dependencies satisfy `pip check`. Focused offline suite: **81 passed, 0 failed**. Full offline suite after final code changes: **1,242 passed, 0 failed**. `git diff --check` passed. Saved QOS-04 and QOS-05 captures both replay with `matches_capture: true`; the QOS-05 decision digest is `411dcf4c118477acad5f0fff23278e3fe22f60f108c7be5a7209ebadf1795069` and ledger digest is `0acbe1323c7755b79d8fadfed7e15ace7d046d32faa7395746b113c8e8ab7d8b`. These are compatibility checks, not new market evidence.

## Public observation and exact replay

Existing usable captures are short: QOS-05 covers 598.106256 s; the other recent public captures are approximately 11–15 minutes. None covers the requested 12 hours. A separate QOS-06 BTCUSDC paper account was initialized with exactly **$200** and zero authenticated requests/economic orders. Its identity is `local-paper-identity-omitted`; the account and startup proof are under `artifacts/qos06/` (ignored scratch data).

The unattended observation launch was recorded at **2026-10-12T01:40:35Z**. It terminated before emitting a journal manifest, market event, or runtime status. At inspection, the recorded PID had exited, no matching observation process remained, `runtime.jsonl` was zero bytes, and no `events.jsonl` or exit-code marker existed. The process's cause of termination is unproven. A direct managed launch was rejected by automatic approval review as a second 12-hour observation beyond the single-campaign boundary. No retry or workaround was performed. Thus there is **no QOS-06 observation end time, usable coverage, event count, clock-health series, stale-disarm count, reconnect count, gap series, capture checksum, or complete capture to replay**. Exact QOS-06 replay reconciliation and cost stress are inapplicable; they are not reported as passing. No genuine QOS-06 market-performance result is claimed.

## Frozen Q05-LR-v1 funnel and economics

The only genuine market funnel available remains the saved QOS-05 capture: 27,361 received events; 4,998 valid-book/complete-feature evaluations; 3,939 range-warmup rejections; 1,059 prior-five-minute-range-below-60-bp rejections; **zero** Q05 raw candidates, confirmations, cost admissions, Risk approvals, paper orders, fills, and exits. QOS-05 had four stale-feed disarms, one clock-recalibration disarm, and one normal runtime-shutdown disarm. Its exact replay shows $200 balance, zero fees, zero realized net PnL, and zero drawdown. This short sample establishes only that the frozen trigger did not occur *there*; it does not establish zero expectancy or statistical rejection of an infrequent strategy. No QOS-06 displacement, reclaim, flow/imbalance, cost, or Risk observations exist to extend that conclusion.

**Principal diagnosis: DATA_LIMITED.** A complete, trustworthy observation covering the required horizon was not obtained. The startup failure and rejected managed launch are operational blockers. The shorter QOS-05 sample supports a local opportunity-absence observation only, not a market-wide conclusion.

## Reproduction and owner decision

Read-only status and prior-capture compatibility commands:

```sh
cd QuantOS
git status --short --branch
git rev-parse HEAD
.venv/bin/quantos futures retail-edge-status --state-store artifacts/qos06/observation/account.json
.venv/bin/quantos futures retail-edge-replay --capture artifacts/qos05/paper/events.jsonl
```

The QOS-06 observation command, if a **new** bounded attempt is explicitly authorized, is:

```sh
.venv/bin/quantos futures retail-edge-paper --strategy qos06 --state-store artifacts/qos06/observation/account.json --capture artifacts/qos06/observation/events.jsonl --duration-seconds 43200
```

Owner decision: authorize a separately bounded public observation with a managed foreground process and a fresh scratch capture only if more market evidence is needed. Do not infer tradability, change Alpha thresholds, or proceed to live execution from this record.
