# QuantOS S199 — Mac paper-trading starter kit

**Status:** Locally verified packaging and config checks; **NO live paper session has been started from ChatGPT**. The execution environment has no direct network access to Polymarket Gamma/CLOB/Chainlink feeds and cannot keep a process alive. This kit starts a *local* NautilusTrader SANDBOX session on a Mac with real public market data and entirely virtual USDC. It does not connect a wallet or send a real order.

**Important:** S199 learned from pre-August-2026 BTC 5m markets; current October-2026 5m markets settle using **Chainlink BTC/USD 60s TWAP**. The original author's archived S199 runner can measure how its *old model* behaves now, but its signals are not validated for today's settlement regime. This kit does **not** grant QuantOS production approval.

## Why this is the shortest credible route

Upstream already has a live/sandbox runner, public Polymarket and Binance feeds, model serialization, order simulation, settlement polling, plus optional diagnostic JSON. We reuse that runner at a pinned repository commit rather than rebuilding it from a blog post.

Source code: https://github.com/evan-kolberg/prediction-market-backtesting/tree/c76e77af00ef53472a9da8f66dae7fdd2d3e5928

Upstream docs: https://evan-kolberg.github.io/prediction-market-backtesting/live/ and https://evan-kolberg.github.io/prediction-market-backtesting/setup/

## Mac setup

Prerequisites: macOS with internet, Git, `uv`, Rust/Cargo >=1.93.1, Make, and `python3`. You can install Git and uv through Homebrew; install Rust with the official rustup instructions. The installer uses `uv` to make a Python 3.13 virtualenv. Do not run these scripts with `sudo` or in a folder containing exchange/wallet secrets.

Unzip into a separate research directory, e.g. `~/QuantOS-S199-PaperKit`. Then in Terminal:

```bash
cd ~/QuantOS-S199-PaperKit
./install_mac.sh
./run_s199.sh build
./run_s199.sh paper
```

`install_mac.sh` clones and pins the source SHA, checks S199 profile compatibility, and runs the original `make install` and `make native-develop`. `build` validates the sandbox wiring and uses public APIs but places no simulated orders. `paper` starts the continuous public-market-data sandbox and is explicitly configured with `$200` **virtual** cash.

Run `paper` with the Terminal window open and the Mac awake. Ctrl+C stops the process. Look for `SANDBOX`, `SANDBOX_FILL_TRACKED`, `SANDBOX_PORTFOLIO`, and settlement events in the logs.

## Files produced by each session

```text
runtime/runs/<UTC timestamp + PID>/
    session.json                 # virtual account metadata
    s199_session.log             # stdout/stderr
    s199_settlements.json        # upstream ledger, updated on fills and settlements
    s199_diagnostics.json        # upstream evaluations, orders, fills (usually written on clean stop)
    audit_summary.json           # summary of this session, no cross-session claims
    end.json                     # exit code when run script terminates
```

**A new invocation starts a new independent `$200` sandbox account.** The upstream runner *does not* restore cash, unsettled positions, or daily stops after restarts. **DO NOT concatenate sessions into a single return stream or treat the first test as production-ready.** This is a blocking task for Tibo. A session that contains no fills is not evidence of a profitable or unprofitable strategy.

The daily stop inherited from S199 is **$1.20 settled PnL**. This stop does not necessarily prevent losses from multiple unsettled bets. The source's 8-second market-book staleness and 30-second BTC feature limits are part of the archived baseline and need current latency stress before promotion.

## Exactly which S199 profile is used?

The pinned source includes `live/models/btc_snapshot_model_s199_cost101_daily_stop120_profile.json`; its classifier is derived from the older S150 training run. The runner's *default* model is **S204**, so we explicitly override it. This kit verifies 19 relevant S199 execution parameters against the pinned profile: edge 0.06, 60s snapshot, size 2 shares, min price .10, max ask .75, min probability .65, adverse/exhausted/volatility gates, m15/m30 momentum alignment, daily stop 1.2, etc.

`python3 doctor.py --upstream vendor/prediction-market-backtesting --offline` verifies the config against the source.

## Validation already performed in the ChatGPT environment

- Shell scripts passed `bash -n` syntax checks.
- Python package tests passed (`python3 -m unittest discover -s tests -v`) including fail-closed config, no injected shell strings, and restart-unsafe ledger cautions.
- GitHub pinned source README, S199 profile, exact sandbox runner, fee/settlement and diagnostics code were inspected.
- **Not tested**: upstream package compilation on macOS, current Polymarket CLOB endpoint reachability from your Mac, live WebSocket connectivity, actual paper fills, or the current 60s TWAP inference and settlement path.

## Tibo completion path

See `TIBO_HANDOFF.md`. The first objective is a persistent, reproducible paper-only account with restart-safe SQLite journal, accurately accounted fees, live Chainlink TWAP settlement, correctly defined current-rule features, and full point-in-time market data. Do not enable real-money execution. Protect QuantOS's frozen spot-only main branch.
