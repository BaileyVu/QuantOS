# sentibot: sentiment + momentum crypto paper bot

Spot, long-or-cash, BTC/ETH/SOL in equal sleeves of a $200 paper account. Never sends real orders.

Signals (all scored -1..+1, weights in `strategy.py`):
- momentum 35%: sign of 1d/3d/7d returns and EMA20 vs EMA100 (hourly)
- pattern 25%: 48h Donchian breakout state, RSI(14) extremes, engulfing candles
- Fear & Greed 10%: contrarian at extremes (<20 buy-leaning, >80 sell-leaning)
- Reddit (+X if `X_BEARER_TOKEN` set) 15%: VADER + crypto lexicon on post titles, last 24h, weighted by votes
- news 15%: same scoring on Yahoo Finance, CoinDesk, Cointelegraph, Decrypt, Google News RSS headlines
- volatility: sets position size (40% annualised vol target, halved in vol spikes)

Rules: enter when composite > 0.25, exit when < 0, decide every 4h, trade only entries/exits,
0.10% fee + 0.05% slippage per side, -5% daily paper stop.

Run:
- `pip install pandas numpy vaderSentiment` (VADER optional; falls back to the built-in lexicon)
- `python3 backtest.py` technical backtest (sentiment has no free history, so it is paper-only)
- `python3 sentiment.py` see the live sentiment snapshot
- `python3 paper.py --loop` paper trade (restart-safe), `python3 paper.py --report` for the summary
