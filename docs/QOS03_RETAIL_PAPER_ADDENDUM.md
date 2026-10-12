# QOS-03 Retail Directional Paper Addendum

Owner-approved scope: an isolated, experimental, event-driven BTCUSDC/BTCUSDT
USD-M public-feed paper mode with a fresh 200-unit account. It has no
authenticated client or real-order adapter. Existing HFT and candle modes,
their state, and historical evidence retain their original specifications.

The HFT V1 source of truth and product requirements name VAMP maker quoting as
the primary path, while QOS-03 explicitly removes that mandate for this new
paper experiment. HFT retains VAMP and post-only entry. Retail Alpha instead
combines causal signed trade flow, current book imbalance, and observed price
response. The HFT execution specification reserves taker exits for safety;
that restriction remains HFT-specific. Retail may simulate taker-style entry
and exit only after its own full-cost Risk admission and observed post-latency
L2 liquidity. These are scoped additions, not changes to HFT thresholds.

Retail signal: use a 30-second past-mid response and five-second signed flow.
Both must agree with the current best-level book imbalance. An entry intent
expires after two seconds; an open trade exits on a 60-second hold limit,
adverse move of 20 bps, or signal reversal. Any invalid/stale/ambiguous book
or clock disarms new entries. No future prices enter signal or fill decisions.

Retail Risk includes actual configured taker fees on both legs, current
spread, one bp conservative slippage per leg, at least one bp latency
allowance scaled up with observed feed and acknowledgment latency, and a two
bp safety buffer. Expected movement must strictly exceed this hurdle.
Sizing uses current marked equity, at most 1x notional, at most 1% equity
worst-case trade risk including costs, and a 3% peak-equity drawdown stop.
One directional position is permitted; no averaging down or leverage increase.

Paper execution waits for the configured acknowledgment latency and a later
fresh depth observation. It fills only against displayed opposite-side
liquidity, capped by that liquidity; an unchanged displayed level cannot be
consumed again without visible replenishment. Partial fills are explicit. No touch or
signal alone creates a fill. Fees, attributed signal/order IDs, and per-trade
realized net PnL are durable. A missing or invalid book never fabricates an
exit fill. This experiment makes no profitability claim.
