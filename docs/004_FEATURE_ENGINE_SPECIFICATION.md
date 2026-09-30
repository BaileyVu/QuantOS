# QuantOS V1 Feature Engine Specification

Version: 2.0.0-V1
Status: Authorized

Features are deterministic, causal, explainable, and derived only from candles completed at the decision timestamp.

The MVP set may include EMA levels/slopes, ATR and ATR fraction, realized/range volatility, recent high/low structure, compression/expansion, volume ratio, candle body/range, and support/resistance distance.

The engine returns no-trade when lookback is unavailable; rejects invalid inputs; preserves Decimal in price/risk outputs; never accesses future candles; and produces identical results for identical ordered data/configuration. Strategy-specific evidence is allowed under the same rules. Add features only for a concrete V1 strategy or safety need.
