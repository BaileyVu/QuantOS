# QuantOS clean slate: what is worth keeping (2026-10-19)

Distilled from the Ivy (ChatGPT) and Tibo (Codex) handoffs plus a read-only scan of
`~/Desktop/PythonProject`. Everything else in those handoffs is research history we do not need.

## Bottom line
- No verified edge, no real-money trade, no real profit so far. Infrastructure is strong; alpha is zero.
- Every directional "predict the price" idea on Binance candles/flow has failed after costs
  (100+ rounds). Do not reopen these without genuinely new data.
- The useful lesson: **check executable quotes, fees, borrow/access and $/day capacity BEFORE
  building a strategy.** Kill fast, change mechanism, never re-tune the same data.

## Code that exists and works (all paper-only, no live order path)
| Folder | State | Use it for |
|---|---|---|
| `QuantOS/` (branch `codex/qos07-final-runtime`, d54c094, clean) | Big Binance engine, 1,257 tests pass. Lacks critical fixes that live in `QuantOS-autonomous-alpha` lineage (8eb2078). | Reference only. Too heavy to iterate on this week. |
| `QuantOS-S199-PaperKit/` (not git) | Polymarket BTC 5-min Up/Down paper engine: public feeds, fee curve, SQLite ledger, TWAP settlement, 32 tests. `./run_s199.sh doctor/paper/status/stop/report`. | Reusable Polymarket feed + ledger code. Strategy itself = NO EDGE. |
| `QuantOS-PM/` (no commits, 77 untracked files) | Polymarket hedged-maker paper engine, Decimal accounting, queue/partial-fill sim, 86 tests. | Reusable maker-fill simulator. Strategy HEDGED_MAKER_V0 loses (unmatched legs). |
| `QuantOS-autonomous-alpha/` (1 untracked) | Research controller; a macOS LaunchAgent was still firing every 30 min per Tibo. | Nothing this week. Consider stopping the LaunchAgent. |
| `QuantOS_PM_LOGIC01_Direct_Execution_Kit/` | Monotone threshold-arb scanner (YES(L)+NO(H)). 106 screens, no after-cost hit. | Idea reference. |
| `Claude  Sonnet 5.5 Medium/` | `retail_lab`: 4 preregistered 4h perp strategy families + honest holdout, never run on real data. | Optional cheap test if we want one directional check. |
| `QuantOS V1 Data and Secrets/` | Data + **Binance secrets. Not opened. Do not touch without Bailey's OK.** | - |
| `QuantOS-S199-PaperKit/config.env` | Not opened (may hold settings/keys). | - |

## Verified facts worth keeping
- Polymarket crypto taker fee per share = `0.07 * p * (1-p)`; makers pay 0 and can earn rebates/liquidity rewards (rebates only count once actually paid).
- Polymarket BTC 5-min markets resolve on Chainlink 60s BTC/USD TWAP, not Binance last price.
- BTC 5-min markets: ~$27k/day volume, ~1c spread. Snapshots showed 0/122 buy-both (YES+NO) under $1 after fees.
- NYC weather 11-bucket sets: 19/1,662 summed under $1 before fees, 0 after fees.
- Identical Polymarket question text can settle differently (doubleheaders). Match by condition/token id, never title.
- Hyperliquid base fees ~0.045% taker / 0.015% maker; funding hourly.
- Binance negative-funding basis trades (GTC/SAND/ERA) looked +130bp/trade in a 14-day in-sample proxy but need **spot borrow that was never confirmed** (likely the very reason the gap exists). ~$6.6/day at $500 even if real.
- ETH cash-and-carry paper ledger (Oct 14-18): -0.07% net. Funding too low to beat fees at this size.
- Wallet 0x56f6...e251 ("arcsine-barrister") shows split/sell/redeem + maker rebates; edge not reproduced. Copying its fills makes you a taker and loses.

## Dead ends (do not repeat)
Candle ML (LightGBM/XGBoost), momentum, breakout, mean reversion, taker-flow follow/fade, H1-H4 premium/flow/funding reversals,
funding-settlement pressure, cash-open, liquidation fade (data-limited), passive maker on Binance BTC (spread 0.01bp vs 6bp cost),
S199 model (Brier no better than market), HEDGED_MAKER_V0, buying cheap YES on BTC 5-min, BONK crash short (3 trades = all the profit),
triangular arb on Binance spot (0/1,652), leverage as a substitute for edge.

## Leads still open (mechanical, not forecasting)
1. Cross-venue funding differential: long perp on the venue paying funding, short on the other (Binance vs Hyperliquid). No borrow needed.
2. Polymarket full-set / neg-risk multi-outcome sums across ALL active markets (not just BTC 5-min), both directions (buy-all-and-merge, split-and-sell-all).
3. Polymarket maker liquidity rewards + rebates on stable, low-volatility markets, where the reward pays more than adverse selection costs.
4. Polymarket short-horizon crypto markets vs live Binance/Chainlink price in the final seconds (latency edge). Needs measuring, not modelling.

## Rules
- Path is backtest, then paper, then shadow, then live. Nothing live without Bailey's explicit go.
- Never read/use secrets or key files without asking. Check Polymarket geo-eligibility from Vietnam before any account step.
