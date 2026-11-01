# Strategy hunt summary, 2026-10-31 / 11-01

Every variant is in `search_ledger.jsonl` (about 4,170 lines; deflated-Sharpe N = lines + 81 earlier CCI trials).
Selection used BUILD 2020-01 to 2023-06 only. HELD-OUT 2023-07 to 2024-12 was used at most once per family.
Tibo's sealed 2025+ data was not used. Fees: maker 0%, taker 0.04% (owner-confirmed USDC), plus slippage.

| Family (doc) | Variants | Verdict | Key number (net) |
|---|---:|---|---|
| crash_flush (crash_episodes_2020 REPORT) | 1 | **survives** | HELD +2.9%/episode, CI [+1.2, +4.6], 21 episodes |
| alt_dip (same) | 1 | **survives** | HELD +2.5%/episode, CI [+0.9, +4.2], 21 episodes |
| bounce_momentum (same) | 1 | dead | HELD −0.6%/episode |
| crash_flush_v2 / alt_dip_v2 (exits, ladder, thresholds, BTC gate) | 3,456 | dead (no improvement) | best BUILD within multiple-testing noise |
| range_mr_maker_v1 | 1 | dead | HELD −23 bp/trade |
| grid_calm_maker_v1 | 1 | dead | HELD −8 bp/trade |
| calm TP variants + 144 sweep | 148 | dead | 0 with CI > 0 on BUILD |
| tsmom_4h_v2 (incl. production config) | 288 | dead | production HELD +18 bp, CI [−43, +90] |
| xs_rank_ret_v1 (weekly L/S momentum) | 24 | dead | +103 bp/wk BUILD, DSR fails |
| xs_funding_v1 (funding rank) | 24 | dead | CI includes 0 |
| xs_risk_rank_v1 (low-beta L/S) | 24 | dead | +93 bp/wk BUILD, DSR fails |
| funding_settle_drift_v1 | 24 | dead | −25 bp/trade |
| new_listing_fade_v1 → v2 (396 listings incl. delisted) | 18 + 36 | dead | v2 +19 bp, CI [−30, +70]; 85% stopped |
| premium extremes, OI surge | – | skipped | duplicates of earlier rounds |

## What survives
Only **crash_flush** and **alt_dip**. Both are small-sample (21 held-out episodes, correlated), use today's
survivor universe, and were designed with 2021–24 in view. They run in paper at 2% and 5% (the 5% profile is
crash/dip/delist only), and the demo at 2%.

## Open forward tests (the real evidence)
- QuantOS paper pL (2%, all rules) + pL5 (5%, crash/dip/delist only) and the demo mirror (2%): end 2026-11-03 02:49 UTC.
- Hyperliquid wallet copy (com.quantos.hlcopy): 12 frozen wallets, 0/30/60 s delay books; judge about 2026-11-07.

## Lessons
Costs, stops and survivorship kill most backtest "edges". Wide searches need the deflation penalty. More forward
data on crash episodes is worth more than further backtest search on the same archive.
