# QuantOS V1 Alpha Engine

Version: 2.0.0-V1
Status: Authorized

Alpha classifies regime, evaluates compatible strategies, normalizes evidence, resolves conflicts, and returns LONG, SHORT, or HOLD. It never determines final exposure or submits orders.

Required regimes: TREND_UP, TREND_DOWN, RANGE, BREAKOUT_OR_EXPANSION, UNCERTAIN.

Initial families: trend continuation, trend pullback, breakout/volatility expansion, breakout plus retest when causal evidence exists, and range mean reversion. Patterns are supporting evidence unless explicitly promoted.

Every signal includes timestamp, symbol, direction, strategy ID, compatible regimes, entry, mandatory stop, target/reward, normalized strength, evidence, and rationale. Strength ranks evidence; it is not win probability and never authorizes leverage.

Selection ranks compatible signals, combines agreeing evidence without duplicating risk, returns HOLD for weak/stale/malformed/incompatible evidence, returns HOLD on material directional conflict, and never manufactures a stop or forces a trade. Runtime selection is deterministic and uses no LLM.

Each enabled timeframe evaluates only when a new candle for that timeframe completes. Higher timeframes may add an explicitly configured deterministic score adjustment to lower-timeframe candidates; agreement is supporting context, not a profitability claim or mandatory gate. Signals identify their timeframe and completed candle. Equal underlying strategy/direction/timestamp setups are deduplicated before one account-level candidate reaches Risk.
