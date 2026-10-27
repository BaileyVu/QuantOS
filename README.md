# QuantOS V3 paper engine

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
.paper-venv/bin/quantos start --mode paper --hours 72
```

`start` runs in the foreground. After acceptance, use the macOS supervisor:

```bash
.paper-venv/bin/quantos install-service --hours 72
.paper-venv/bin/quantos status
.paper-venv/bin/quantos journal --profile p1 --limit 50
.paper-venv/bin/quantos report --day 1
.paper-venv/bin/quantos stop
```

Default state is `~/Library/Application Support/QuantOS/v3-paper`. Every stateful
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

- Independent $200 USDC accounts: p1 (1%) and p5 (5% risk ceiling), sharing signals.
- 1x equity gross exposure, <=3x leverage, one active position, 10% daily loss
  breaker and 25% drawdown pause. p5 often sizes below 5% due to the exposure cap.
- EMA20/50 ATR14 LIMIT pullback and Donchian20 ATR14 continuation STOP, completed
  5-minute bars only; 1-minute data is also collected. No parameter optimization.
- E17 is ineligible, with unverified source metadata. An announcement monitor is
  not yet running. Other imported research is deferred in LATER.md.
- LIMIT fills require strict public trade-through, 1% participation after a
  queue allowance. Touch alone never fills. MARKET/STOP uses a later quote,
  latency, adverse slippage and size impact; large gaps/depth shortages reject.
- Fee estimates: maker 0.02%, taker 0.06%; funding at actual published times.
  These are estimates, not the account's personal commission rate.
- Protection is a local simulated bracket, not an exchange-native order.
  Stops can slip. Missing/stale/gapped data blocks new exposure.
- Daily and final JSON/Markdown/CSV reports live under the state `reports/` folder.
  Signals, risk approvals, fills, no-fill/expiry causes, costs and R are separate.

`quantos backtest --input recorded.jsonl.gz --state NEW_DIRECTORY --strategy all`
replays recorded inputs through the same logic without network. It leaves any
end-of-input position open; it does not invent a final quote or missing events.
Synthetic demo, replay and natural journals have distinct immutable identities.

See [RISK_STUDY.md](RISK_STUDY.md) for why annual tails are NOT_ESTIMABLE and
[IMPLEMENTATION_CHECKPOINT.md](IMPLEMENTATION_CHECKPOINT.md) for actual tests,
runtime evidence, unresolved work, commits and next action.

## Development

```bash
python -m pip install '.[research]'
python -m unittest discover -s tests -t .
PYTHONPATH=legacy_assets:legacy_assets/src python -m unittest discover -s legacy_assets/tests -t legacy_assets
```

The retained historical research suite requires its pinned optional dependencies.
One historical preregistration test requires a clean committed production tree;
run the complete suite after committing reviewed implementation changes.
The frozen V1 implementation and research are preserved in
[legacy_assets/](legacy_assets/README.md), outside the default installation.
The historical CLI documentation remains in [docs/LEGACY_V1_README.md](docs/LEGACY_V1_README.md).
The supported `quantos` and `python -m quantos` entry points run the paper engine.
