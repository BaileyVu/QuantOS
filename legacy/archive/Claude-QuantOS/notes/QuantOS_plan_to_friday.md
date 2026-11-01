# Plan: paper-trading system by Friday 23 Oct 2026

New lean project in `~/Desktop/PythonProject/Claude-QuantOS/`. No edits to existing folders.
Reuse ideas (fee curve, ledger, feed code) from S199 and QuantOS-PM by copying, not modifying.

## Mon 19 - Tue 20: measure before building (read-only scanners, public data only)
Run all four in parallel for 24h+, logging every opportunity with depth, fees, timestamps:
- **S1 Funding spread**: Binance USD-M vs Hyperliquid funding + mark for all shared symbols.
  Opportunity = annualised funding difference minus 4 taker legs and slippage at $100-$200 notional.
- **S2 Polymarket full-set**: every active binary and neg-risk event; buy-all-asks < $1 and
  split-and-sell-all-bids > $1 after fees, at real book depth.
- **S3 Polymarket maker rewards**: which markets pay liquidity rewards, reward per $ quoted,
  vs observed price volatility (adverse-selection proxy).
- **S4 Polymarket short crypto markets vs Binance price** in the last 30-60s before close.

## Wed 21: verdict per scanner
Keep a strategy only if: after-cost edge > 0 at real depth, happens often enough to matter
at $200, and fill risk is understood. Expect most to die. That is the point.

## Thu 22: paper engine for survivors
One engine, one ledger: realistic fills (walk the book, latency, partial fills, fees),
risk caps (max $20/position at $200, 3% daily stop, no leverage beyond 2x), kill switch, restart-safe.

## Fri 23: paper running 24/7 on the Mac
Daily report: trades, PnL, fills vs quotes, rejects.

## After Friday
Paper at least through the weekend, then shadow mode (live signals, no orders, compare to real fills),
then a tiny live canary only with Bailey's explicit go, with Bailey handling the API keys.

## On the $100 Sunday target
$100 from $200 in one day is +50%. No repeatable edge does that. The only ways to hit it are
high-leverage directional bets, which have about the same odds of losing most of the $200.
A realistic good outcome for a genuine small edge is 0.2-2% per day. Our goal for this week:
prove one edge is real in paper, then scale capital, not leverage.
