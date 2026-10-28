# QuantOS V4 paper strategy library

A local, public-data-only Binance USD-M Futures paper engine. No API keys, real
orders, transfers or account changes. Reference strategies demonstrate mechanics;
there is no claim of profitability or live readiness.

## Install and operate

Python 3.11+; the paper runtime has no third-party dependencies.

```bash
python3 -m venv .paper-venv
.paper-venv/bin/python -m pip install .
.paper-venv/bin/quantos doctor
.paper-venv/bin/quantos strategies list
.paper-venv/bin/quantos demo --state /tmp/quantos-new-synthetic-demo
.paper-venv/bin/quantos start --mode paper --hours 72 --state "$HOME/Library/Application Support/QuantOS/v4-new-run"
```

`start` runs in the foreground. After acceptance, use the macOS supervisor:

```bash
.paper-venv/bin/quantos install-service --hours 72 --state "$HOME/Library/Application Support/QuantOS/v4-new-run"
.paper-venv/bin/quantos status
.paper-venv/bin/quantos journal --profile p1 --limit 50
.paper-venv/bin/quantos report --day 1
.paper-venv/bin/quantos stop
```

Default state is `~/Library/Application Support/QuantOS/v4-paper`. Every stateful
command accepts `--state PATH`. Use a fresh directory for a new observation;
restarting an existing one retains its original deadline and account history.
`stop` requests graceful local closure, preserving unresolved exposure when the
feed is offline. Inspect status and resume that state to finish reconciliation.
Never delete or share another run's state. One process holds an exclusive lock.

The launchd job `com.quantos.v3.paper` restarts failures and uses caffeinate.
The plist references the exact virtualenv interpreter, so retain that environment.
A successful completed run does not restart. Closing a laptop lid, power loss,
offline periods and OS sleep can interrupt observation. Keep the Mac on AC with
lid open. Reports distinguish wall time, measured observation and recorded gaps.
Local SQLite/WAL journals, gzip input archives and daily/final reports continue
without Codex. A stopped/crashed local process cannot emulate unseen price paths;
restart recovery preserves known protection and cancels stale entry remainders.

## Behavior and evidence

- Independent $200 USDC profiles: p1 (1%), p5 (5% ceiling), and pA (adaptive).
  pA probes at 0.25% for 30 trades, learns from p1, then uses the existing edge
  estimator. SHADOW strategies do not trade. Only pA ranks competing signals by
  `mu / sqrt(m2)`; p1/p5 keep deterministic ordering and one position per profile.
- Existing per-trade leverage logic: gross ceiling 3x equity, leverage <=3x,
  50% margin budget, 10% daily loss breaker and 25% drawdown pause. Actual sizing
  may be smaller than the requested risk and can fall below exchange minimums.
- The universe uses TRADING USDC perpetuals and 24h quote volume >=20M USDC,
  configurable with `--min-quote-volume`; BTCUSDC/ETHUSDC remain default baseline
  feeds but are not exempt from liquidity/freshness gates. Candle caches and
  bounded workers avoid a feed worker for every discovered market.
- Six preregistered strategies are listed below. Daily trend/volatility regime
  tags are recorded for analysis and never filter entries. No parameter tuning.
- LIMIT fills require strict public trade-through, 1% participation after a
  queue allowance. Touch alone never fills. MARKET/STOP uses a later quote,
  latency, adverse slippage and size impact; large gaps/depth shortages reject.
- Fee estimates: maker 0.02%, taker 0.06%; funding at actual published times.
  These are estimates, not the account's personal commission rate.
- Protection is a local simulated bracket, not an exchange-native order.
  Stops can slip. Missing/stale/gapped data blocks new exposure.
- Daily and final JSON/Markdown/CSV reports live under the state `reports/` folder.
  Signals, risk approvals, fills, no-fill/expiry causes, costs and R are separate.

| Strategy/config ID | Rule and exits | Evidence |
|---|---|---|
| `ema_atr_ref` | Existing 5m EMA20/50, ATR14 LIMIT | PAPER_REFERENCE |
| `donchian_atr_ref` | Existing 5m Donchian20 continuation STOP | PAPER_REFERENCE |
| `tsmom_4h` | 30-day return + EMA50 slope, 2 ATR14 stop; +1R activates 2R trail; 72h max | UNPROVEN_REFERENCE |
| `turtle_55` | Closed 4h breakout, STOP at prior 55-bar boundary; 2 ATR20 stop, opposite 20-bar channel or 72h | UNPROVEN_REFERENCE |
| `vol_squeeze_breakout` | 1h BB20/2 inside EMA20 Keltner/1.5 ATR20, release-range breakout STOP; 2R, 4h max | UNPROVEN_REFERENCE |
| `delist_short_e17` | Verified-format Binance CMS delist <=5m, USDC perp only; short, 25% stop, 4h | FRAGILE_SOURCE_ONLY |

Reference aliases retain historical journal IDs. Manifests and matching TOMLs in
`configs/strategies/` fix parameters before observation. New bar strategies emit
only in the first 90 seconds after a close; already-crossed STOP triggers are
rejected, never backfilled. No-target manifests replace target-based net RR with
round-trip costs <=25% of raw stop risk, including a fill-time check. Fixed-target
strategies retain minimum net RR. All stops tighten only and every change is
journaled and reconciled on restart. Only 4h strategies may hold up to 72h.

E17 requires a valid CMS source URL/release timestamp, a traded prior UTC day and
positive current-minute trades. Missing USDC contracts log `SKIPPED_NO_USDC_PERP`.
Its supplied performance results/hash were absent and its edge remains fragile.
Source account sizing/live permissions do not override these paper profiles.

`quantos strategies list --state NEW_DIRECTORY` reads actual pA state without
writing journals. `quantos demo` retains fault scenarios and adds all-six strategy
lifecycles for p1/p5, pA decisions, and exact replay checks; minimum-size probe
rejections remain explicit. Demonstrations are synthetic, not profitability evidence.

Use a **new state directory** for V4. The canceled V3 run is preserved and must not
be resumed. The default state is now `~/Library/Application Support/QuantOS/v4-paper`.
No V4 natural run has been launched or claimed complete.

`quantos backtest --input recorded.jsonl.gz --state NEW_DIRECTORY --strategy all`
replays recorded inputs through the same logic without network. Use
`--config configs/strategies/tsmom_4h.toml` to select one frozen configuration. It leaves any
end-of-input position open; it does not invent a final quote or missing events.
Synthetic demo, replay and natural journals have distinct immutable identities.

See [RISK_STUDY.md](RISK_STUDY.md) for why annual tails are NOT_ESTIMABLE and
[IMPLEMENTATION_CHECKPOINT.md](IMPLEMENTATION_CHECKPOINT.md) for actual tests,
runtime evidence, unresolved work, commits and next action.

## Development

```bash
python -m pip install '.[research]'
python -m unittest discover -s tests -t .
PYTHONSAFEPATH=1 PYTHONPATH=legacy_assets:legacy_assets/src python -m unittest discover -s legacy_assets/tests -t legacy_assets
```

The retained historical research suite requires its pinned optional dependencies.
One historical preregistration test requires a clean committed production tree;
run the complete suite after committing reviewed implementation changes.
The frozen V1 implementation and research are preserved in
[legacy_assets/](legacy_assets/README.md), outside the default installation.
The historical CLI documentation remains in [docs/LEGACY_V1_README.md](docs/LEGACY_V1_README.md).
The supported `quantos` and `python -m quantos` entry points run the paper engine.
