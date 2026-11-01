# Claude-QuantOS — what we keep from the Ivy/Tibo era (clean slate)

Digest of the two 2026-10-19 handoffs (Ivy/ChatGPT, Tibo/Codex). Only what matters for new work.

## Bottom line
- After ~2 months of work there is **no verified after-cost edge and no real trading profit**. Infrastructure exists; Alpha does not.
- The $100/day on $200 goal (50%/day) was never close in any test. Treat it as aspirational; each strategy gets judged on $/day capacity first.

## Rules carried forward
- Secrets live in `PythonProject/QuantOS V1 Data and Secrets/` (and `.env` files). Never read/touch without owner permission.
- Pipeline: research → paper → shadow (read-only, live quotes) → tiny funded canary only with explicit owner OK.
- Fail fast: before modelling anything, check **synchronized executable quotes + account access (borrow, geo) + fillable size + real fees + realistic $/day capacity**. If it fails, change the mechanism; don't re-tune the same history.
- Keep paper/synthetic/real fills clearly separate in every report.

## Dead ends — do not redo (tested, negative after costs)
- Binance candle/OHLCV prediction (R1–R6, AF3 599 KILL, AF4B 11 KILL, LightGBM/XGBoost/ML, momentum, breakout, mean reversion, overshoot).
- Minute taker-flow follow/fade (−11 to −13 bp/trade); 4h relative-price reversion; H1–H4 BTCUSDC flow/premium/funding (~−14 bp).
- Passive market making on Binance majors (spread ~0.01 bp vs ~6 bp exit cost).
- Spot triangular arbitrage (0/1,652 positive); cross-venue funding spreads (38 checked, none qualifying).
- Persistent funding carry F2/F3: profitable in backtest but drawdown 15–56% and data-loss exits; NO_GO. Extreme-negative-funding capture: profits came from price moves, not carry.
- ETH cash-and-carry shadow (Oct 14–18): −0.07% (fees > funding at this size).
- Polymarket S199 BTC 5-min directional model: −$0.31 over 18.5h, 3 fills; model Brier no better than market. Hedged-maker V0 (bid both sides): loses on unmatched legs.
- Polymarket weather/MLB cross-market: no fee-adjusted arb; same-title markets can settle differently.

## Leads still open (unproven)
- **Polymarket maker/liquidity provision** — wallet `0x56f6…e251` (arcsine-barrister) shows split → sell (all trades SELL) → redeem workflow + maker rebates; ~$21k API-reported PnL. Mechanism not reproduced.
- Polymarket monotonic threshold pairs (YES(L)+NO(H) ≥ $1) — scanner exists, non-atomic two-leg fill risk.
- R66 tick-size-change effect — proxy lead only.
- Funding/basis on Hyperliquid vs Binance — not yet properly tested with synchronized quotes (Claude scanner work starts here).

## Useful facts
- Polymarket crypto taker fee ≈ shares × rate × p × (1−p) (rate 0.07 in docs; token metadata showed base_fee 1000). Maker fee 0 + rebates. BTC 5-min markets resolve on Chainlink 60s TWAP. Check geo eligibility (owner is in Vietnam; no Coinbase).
- Binance USDC-M historical fees were 0 maker / 4 bp taker (verify current).
- Mac: system python3 = 3.9.6; QuantOS venv has Python 3.12; QuantOS-PM venv Python 3.13.

## Existing local assets (read-only reference, do not edit)
- `PythonProject/QuantOS` — main repo (QOS-07, 1,257 tests). Critical-path fixes are in `QuantOS-autonomous-alpha` lineage (8eb2078).
- `PythonProject/QuantOS-PM` — Polymarket paper engine, stdlib, 86 tests, **untracked (no commits)**; wallet forensics in `artifacts/research/`.
- `Desktop/QuantOS-S199-PaperKit` — S199 Polymarket paper engine (stopped, flat $200).
- `Desktop/QuantOS_PM_LOGIC01_Direct_Execution_Kit` — threshold-pair scanner.
- **Running automation:** an "alpha" LaunchAgent (QuantOS-autonomous-alpha) fires every 30 min; Codex heartbeat paused. Owner may want to disable it.
