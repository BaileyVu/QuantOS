# QuantOS HFT V1 Alpha Engine

Version: 3.0.0-V1
Status: Authorized

The primary production research/paper candidate is VAMP/order-flow directional
maker HFT. Alpha never determines final exposure and never places or fills an
order.

For each eligible book state Alpha calculates VAMP displacement from mid in
basis points. Upward displacement may propose one post-only BUY; downward
displacement may propose one post-only SELL. Standardized OBI and signed trade
flow provide configured same-direction confirmation and diagnostics, not an
optimized weighted ensemble. V1 uses the simplest causal thresholds.

Each quote intent identifies symbol, side, price, quantity request, VAMP,
mid/microprice, alpha bps, OBI values, trade flow, volatility, timestamps,
book-update identity, maximum quote age, and cancellation rationale. The price
must not cross the current spread. Execution applies exchange tick rounding
while preserving post-only behavior.

Alpha emits cancellation when displacement no longer clears the economic
hurdle, reverses, ages out, or market state becomes ineligible. It may request a
maker-first inventory exit when displacement realizes or mean-reverts. Severe
reversal may request a safety exit, but Risk decides whether taker semantics are
allowed.

Production HFT Alpha is deterministic and contains no LLM. The prior candle
strategy library remains available only through the retained comparison
runtime and must not be combined with HFT decisions or inventory.
