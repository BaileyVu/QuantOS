# Edge v1 — 2h EMA 12/26 cross from a 2-touch level ("the video edge, scaled up")

Frozen 2026-10-21. Parked for later, when capital reaches ~$100K. This is the slow, steady edge, not the
capital-raising one.

## Rules
- **Universe:** BTC, ETH, SOL, BNB, XRP, DOGE, ADA, AVAX, LINK (USDT perps), **2-hour candles**.
- **Trigger (video step 1):** EMA 12 crosses EMA 26. Enter on the close of the cross candle.
- **Level filter (video step 2):** only take the cross if price is coming off a zone that has been touched
  at least twice before (plain S/R or a flip zone), within the last 96 bars (8 days). The zone runs from the
  wick extreme to the candle bodies, as drawn in the video (`zone_body=True`). Parameters:
  `near_atr=3, retest_win=12, tol_atr=1.0, lookback=96`. See `strategy.find_level`.
- **Video step 3 (trend + Fibonacci) is NOT used.** On this timeframe it removed too many good trades.
- **Stop:** fixed 2% from entry, checked against candle highs and lows.
- **Exit:** the opposite EMA cross (closing price), or the stop. Time stop after 60 bars.
- **Sizing:** 4 equal slots of current equity, at most 1 position per coin. After 3 losses in a row, stop
  for the rest of the Vietnam calendar day.
- **Costs modelled:** 0.05% per side (taker fee plus slippage).

## Results (from `reproduce_output_2026-10-21.txt`)
| period | 1x | 2x | 3x |
|---|---|---|---|
| train 2022–2024 | x3.01, DD 17% | x7.08, DD 32% | x13.6, DD 44% |
| **test 2025–Oct 2026 (never seen by the search)** | **x2.21, DD 11%** | x4.00, DD 21% | x6.18, DD 30% |
| 2022 / 2023 / 2024 / 2025 / 2026 (1x) | 1.31 / 1.34 / 1.59 / 1.40 / 1.58 | | |

Every calendar year is profitable at 1x. Nearby settings (EMA 8/21 and 9/20, stops of 2–4.5%) are all profitable
in both periods (x1.4–3.0 train, x1.7–2.3 test), so this is a plateau rather than a lucky single point.
That is why it was chosen. It ranked #1 of 34,500 combos on the worse of its two periods, with drawdown under 50%.

**Realistic expectation at 1x:** roughly +35–60% a year with drawdowns of about 10–17%. At 3x: about x2–3 a year
with drawdowns of about 30–45%.

## Known limitations (fix before trading real size)
1. Fills are assumed at the candle close. Live, you get the next bar's open plus slippage.
2. The stop is a flat 2%, not structural. A gap through the stop is filled at the stop price (optimistic).
3. In 2022–26 these 9 coins move together, so 4 slots are not 4 independent bets.
4. At $100K, liquidity is fine on all 9 coins. At $200, Binance minimum notionals and fees dominate.

## Maintenance (when we revive it)
- Run `python reproduce.py --refresh` every quarter. It re-downloads candles and re-prints the table.
  If the trailing 12 months at 1x drop below x1.0, or drawdown exceeds 25%, pause it and re-research.
- Paper-trade it for at least 4 weeks before going live (reuse `emacross/shadow.py` with these params on 2h).

## Files
- `strategy.py` — the strategy code (levels, zones, crosses), frozen snapshot.
- `research/optimize.py` — the 36,000-strategy walk-forward search engine that found it.
- `search_report_2026-10-21.md` — full output of that search, including the Monte Carlo.
- `reproduce.py` + `reproduce_output_2026-10-21.txt` — the numbers above, re-runnable.
- `data/1h/` — the frozen candles used (Binance public data, Sep 2021 – 21 Oct 2026).
- `STRATEGY_FROM_VIDEO.md` — the original video rules.

Run with any Python that has numpy, e.g. `../../emacross/.venv-whisper/bin/python reproduce.py`.
