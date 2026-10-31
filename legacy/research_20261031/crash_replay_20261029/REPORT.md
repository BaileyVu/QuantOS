# QuantOS replay of the 2026-10-29 Binance-hack flash crash

Window: **2026-10-29 01:00 UTC → 2026-10-30 05:00 UTC** (Oct 29 08:00 → Oct 30 12:00 UTC+7). The request said
"8:00AM to 12:00AM (UTC+7)". Read literally, 12:00AM is midnight, 17:00 UTC, which is *before* the 20:24 UTC hack.
I ran through Oct 30 12:00 noon (UTC+7) so that the crash and the recovery are both inside the window.

Public Binance data only. No keys, no orders, no account calls. QuantOS was imported read-only and nothing in `QuantOS/` was changed.

## TL;DR

* **No QuantOS process was live during the crash.** The only state on this Mac is the old V3 run. Its market recordings
  stop at 2026-10-27 02:16 UTC, and it resumed on 10-30 01:43 UTC only to finalize. No V4 state directory exists.
  Everything below is a historical replay, not what actually ran.
* **Live QuantOS (6 library strategies, USDC perps, p1/p5/pA):** 3,168 signals. 5 orders filled in p1, 1 in p5 and
  4 in pA. Every fill was `tsmom_4h`. Result marked at the window end: **p1 −$3.99, p5 −$5.83, pA −$1.24** on $200 each.
  It did not "catch" the crash: a BTC short opened at 04:00 was stopped out at 15:10, five hours *before* the hack,
  and a 1000PEPE long opened at 16:00 was stopped by the crash at 20:33.
* **Research candidates (crash_flush / alt_dip / ema_level, USDT, pR):** crash_flush could **not** fire. A Binance
  metrics-archive outage on 10-15 (zero OI rows on all 9 majors) makes the OI z-score fail closed for 30 days.
  alt_dip fired 97 times, but only 2 orders filled (ADA, AXS): **pR +$0.31**.
* **With OI repaired** (sensitivity only), crash_flush would have signalled 13 times on LINK, ADA, AVAX, DOGE and SOL
  between 22:00 and 02:00 UTC. That is the right idea at the right time: unlevered net return to the window end averaged
  about +1.5% per signal. QuantOS still would have taken **zero** of them, because of exchange minimums and position slots.

## 1. Crash timeline (hourly close, UTC; label = hour the bar opened)

| Symbol | 01 | 07 | 13 | 17 | 19 | **20** | 21 | 22 | 23 | 00 | 01 | 02 | 03 | 04 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | 79,972 | 79,820 | 80,275 | 80,853 | 80,810 | **78,820** | 78,997 | 78,976 | 77,963 | 77,208 | 76,796 | 77,385 | 78,192 | 78,233 |
| ETH | 2,313 | 2,309 | 2,326 | 2,341 | 2,336 | **2,257** | 2,272 | 2,273 | 2,252 | 2,222 | 2,195 | 2,209 | 2,237 | 2,243 |
| SOL | 99.80 | 99.71 | 100.62 | 101.06 | 99.78 | **96.10** | 97.50 | 97.02 | 95.62 | 94.83 | 93.94 | 95.37 | 97.19 | 97.23 |
| XRP | 1.2977 | 1.2881 | 1.2903 | 1.2930 | 1.2816 | **1.2376** | 1.2541 | 1.2456 | 1.2371 | 1.2242 | 1.2097 | 1.2204 | 1.2464 | 1.2537 |
| DOGE | .07804 | .07767 | .07915 | .07959 | .07925 | **.07531** | .07609 | .07613 | .07521 | .07431 | .07402 | .07496 | .07604 | .07612 |
| LINK | 11.458 | 11.361 | 11.643 | 11.744 | 11.687 | **10.989** | 11.046 | 10.749 | 10.612 | 10.496 | 10.522 | 10.716 | 10.957 | 11.055 |
| ADA | .1980 | .1973 | .2018 | .2035 | .2022 | **.1904** | .1921 | .1916 | .1902 | .1870 | .1872 | .1890 | .1922 | .1931 |
| CRV | .3262 | .3272 | .3287 | .3276 | .3249 | **.3091** | .3067 | .3079 | .3057 | .3031 | .2928 | .2736 | .2772 | .2699 |

The first leg ran from 20:24 to 21:00: BTC −2.5%, alts −4 to −6%. A second grind lower bottomed at 00:20–02:10 UTC
(BTC low 76,444 at 02:06), then most coins recovered by 05:00. CRV kept falling.
Deepest 1m drawdowns from the pre-hack high: 1000PEPE −24.4%, ENA −23.9%, CRV −22.2%, TIA −20.9%, PENGU −19.9%,
BONK −19.4%, BOME −19.3%, WLD −19.2%, BTC −5.4%, ETH about −6%.

## 2. What each strategy did

Profiles are independent $200 accounts. "Marked PnL" = realized PnL after fees and funding, plus unrealized PnL at the
05:00 UTC mark (exit costs not deducted). R = PnL / cost-inclusive approved risk.

### Live library (`quantos start` set: USDC perps ≥ 20M 24h volume, 10–13 eligible symbols)

| Strategy | Signals | p1 approved / filled / PnL | p5 approved / filled / PnL | pA approved / filled / PnL | Main rejection reasons |
|---|---:|---|---|---|---|
| ema_atr_ref (LIMIT) | 2,591 | 0 / 0 / $0 | 0 / 0 / $0 | 0 / 0 / $0 | NET_REWARD_RISK (976–1,699), EXISTING_EXPOSURE, 2× PERCENT_PRICE_FILTER |
| donchian_atr_ref (STOP) | 524 | 0 | 0 | 0 | NET_REWARD_RISK, EXISTING_EXPOSURE, STOP_ALREADY_TRIGGERED (4–19) |
| tsmom_4h (MARKET) | 41 | 4 / 3 / **−$3.99** (−2.0R) | 5 / 1 / **−$5.83** (−0.97R) | 3 / 3 / **−$1.24** (−2.7R) | EXISTING_EXPOSURE; p5 lost 4 fills to INSUFFICIENT_EXECUTABLE_DEPTH* |
| turtle_55 (STOP) | 10 | 0 | 0 | 0 | p1/p5 EXISTING_EXPOSURE; pA **STOP_ALREADY_TRIGGERED** ×10 (the 00:00 4h close broke the 55-bar low, but price was already below the STOP) |
| vol_squeeze_breakout (STOP) | 2 | 1 / 0 | 1 / 0 | 0 / 0 | XRP short at 02:00: no fill (FILL_EXCEEDS_APPROVED_RISK / DEPTH*); pA BELOW_EXCHANGE_MINIMUM |
| delist_short_e17 | 0 | – | – | – | No "Binance Will Delist" CMS article in the window (latest ones: 10-21 and 10-27) |
| **Total** | **3,168** | **$196.01 (−$3.99)** | **$194.17 (−$5.83)** | **$198.76 (−$1.24)** | 18 same-symbol CONFLICTs dropped |

Every approved trade:

| Profile | Signal (UTC) | Trade | Entry → exit | Exit reason | Net PnL | R |
|---|---|---|---|---|---:|---:|
| p1 | 10-29 02:00 | vol_squeeze XRPUSDC SHORT STOP 1.2946, SL 1.3269 | no fill | FILL_EXCEEDS_APPROVED_RISK | 0 | – |
| p1 | 10-29 04:00 | tsmom BTCUSDC SHORT 0.001 | 79,690 → 81,519 (15:10) | SL, **5h before the hack** | −1.92 | −0.96 |
| p1 | 10-29 16:00 | tsmom 1000PEPEUSDC LONG 4,107 | 0.0054865 → 0.0049574 (20:33) | SL in the crash | −2.20 | −1.11 |
| p1 | 10-30 00:00 | tsmom BNBUSDC SHORT 0.06 | no fill | FILL_EXCEEDS_APPROVED_RISK | 0 | – |
| p1 | 10-30 04:00 | tsmom 1000PEPEUSDC LONG 3,033 | 0.0048979 → mark 0.0049433 | open at window end | +0.13 | +0.07 |
| p5 | 10-29 02:00 | vol_squeeze XRPUSDC SHORT 228.5, 3x | no fill | INSUFFICIENT_EXECUTABLE_DEPTH* | 0 | – |
| p5 | 10-29 04:00 | tsmom BTCUSDC SHORT 0.003, 3x | 79,686 → 81,537 (15:10) | SL | −5.83 | −0.97 |
| p5 | 16:00, 20:00, 00:00, 04:00 | tsmom PEPE long ×3, BNB short | no fill | INSUFFICIENT_EXECUTABLE_DEPTH* | 0 | – |
| pA | 10-29 02:00 | vol_squeeze XRPUSDC SHORT | no fill | FILL_EXCEEDS_APPROVED_RISK | 0 | – |
| pA | 10-29 04:00 | tsmom DOGEUSDC SHORT 176 | 0.077645 → 0.080388 (14:37) | SL | −0.50 | −1.00 |
| pA | 10-29 16:00 | tsmom 1000PEPEUSDC LONG 1,034 | 0.0054827 → 0.0049602 (20:33) | SL in the crash | −0.55 | −1.10 |
| pA | 10-30 00:00 | tsmom BNBUSDC SHORT 0.01 | 696.80 → mark 716.19 | open at window end | −0.20 | −0.63 |

pA sized at its 0.25% probe risk throughout (LEARNING, fewer than 30 evidence trades). No loss breaker tripped,
no feed disconnects and no drawdown pause in any profile. One funding payment was booked per profile.

### Research candidates (USDT; QuantOS runs these only via `backtest --research`, profile pR)

| Strategy | Signals | pR (ResearchPolicy, 1%, 3 slots) | CF p1 | CF p5 | CF pA |
|---|---:|---|---|---|---|
| crash_flush_e2, raw archive OI (faithful) | **0** | – | – | – | – |
| crash_flush_e2, repaired OI (sensitivity) | 13 | 0 approved: 9 BELOW_EXCHANGE_MINIMUM, 4 EXISTING_SYMBOL_EXPOSURE | 0 (EXISTING_EXPOSURE ×13) | 0 (EXISTING_EXPOSURE ×13) | 0 (BELOW_EXCHANGE_MINIMUM) |
| alt_dip_reversal (q=1%, 24h) | 97 | 8 approved / 2 filled: **+$0.31** | 1 / 1: +$0.11 | 1 / 1: −$0.88 | 0 (BELOW_EXCHANGE_MINIMUM ×97) |
| ema_level_2h | 0 | – | – | – | – |

CF = counterfactual p1/p5/pA sizing (Policy fees, 1 position, USDT). QuantOS itself never allows this combination.

alt_dip fills:
* pR and CF p1: ADAUSDT long at 21:00, entry 0.19030, mark 0.19312 (+0.06R).
* pR: AXSUSDT long at 03:00, entry 0.82090, mark 0.84380 (+0.11R).
* CF p5: NEARUSDT long at **08:00 on 10-29**. This came from a pre-crash dip. It then held CF p5's single slot through
  the whole crash: −$0.88 marked, −0.09R.
* pR's six ALGOUSDT approvals (21:00 → 02:00) all failed with INSUFFICIENT_EXECUTABLE_DEPTH*.

All alt_dip and pR positions were still open at 05:00 (24h hold).

**crash_flush with repaired OI: what the 13 signals saw and what happened** (first signal per coin; entry = signal
close; net return to 05:00 after 0.12% round-trip fees; the 25% catastrophe stop was never close):

| Signal | Coin | DD from 24h high | 4h change | OI z | Return to 05:00 | Worst adverse | Best favourable |
|---|---|---:|---:|---:|---:|---:|---:|
| 10-29 22:00 | LINK | −8.0% | −5.9% | −2.70 | −0.04% | −7.2% | +0.4% |
| 10-29 23:00 | ADA | −8.2% | −5.3% | −4.35 | +0.66% | −5.0% | +1.0% |
| 10-29 23:00 | AVAX | −8.0% | −5.5% | −3.79 | +2.13% | −4.3% | +2.5% |
| 10-30 01:00 | DOGE | −8.8% | −1.3% | −2.96 | +2.32% | −1.4% | +2.8% |
| 10-30 01:00 | SOL | −8.1% | −1.3% | −6.89 | +2.41% | −1.8% | +2.9% |

The mean is +1.5%, but n=5 from one event, so this is not evidence of an edge. The 24h hold extends past the window,
so these outcomes are not final.

## 3. Near-misses

**crash_flush** (gate: 24h drawdown ≤ −8%, 4h change < 0, OI z < −2). Worst values in the window, repaired OI:

| Major | Max DD from 24h high | Min OI z | Hours both gates passed |
|---|---:|---:|---|
| BTC | −6.3% | −3.77 | none: price gate missed by 1.7 pts |
| ETH | −7.7% | −3.53 | none: missed by 0.3 pts |
| BNB | −8.4% | −4.10 | none: the drawdown and OI hours did not coincide with a negative 4h change |
| XRP | −8.2% | −2.77 | none: same reason |
| SOL | −8.9% | −6.89 | 01:00, 02:00 |
| DOGE | −9.1% | −2.96 | 01:00, 02:00 |
| ADA | −10.4% | −4.59 | 23:00 → 02:00 |
| AVAX | −9.6% | −4.19 | 23:00 → 01:00 |
| LINK | −12.6% | −2.79 | 22:00, 23:00 |

With the raw archive OI the z-score is `None` for every major (see §5.1).

**alt_dip** (Q4 cutoff = **−7.77%** 24h return; calibrated on 29 coins × 2,088 hours, 07-03 → 09-28). Most coins
crossed it. Lowest 24h return per coin: CRV −16.9%, NEAR −14.4%, UNI −12.9%, FIL −11.3%, AAVE −11.2%, AXS −11.2%,
DOT −11.1%, VET −11.0%, LINK −10.7%, GALA/THETA −10.7%, ALGO −10.6%, MANA −10.4%, ATOM −10.1%, ADA −8.8%,
LTC −8.5%, BNB −8.2%, AVAX −8.1%, BCH −8.1%, SAND −8.0%.
Closest to the cutoff without crossing: **ETC −7.74%** (0.03 pts short), XRP −7.3%, SOL −6.9%, XLM −6.6%, DOGE −6.5%.
Never close: RUNE −5.8%, ETH −5.6%, BTC −4.8%, TRX −0.6%.

## 4. Totals on $200 (marked at 2026-10-30 05:00 UTC)

| Set | p1 | p5 | pA | pR |
|---|---:|---:|---:|---:|
| Live library (what `quantos start` runs) | 196.01 (**−3.99**) | 194.17 (**−5.83**) | 198.76 (**−1.24**) | – |
| Research candidates, faithful | – | – | – | 200.31 (+0.31) |
| Candidates under counterfactual p-sizing | 200.11 (+0.11) | 199.12 (−0.88) | 200.00 (0) | – |
| Same with repaired OI | identical: crash_flush got no slot or was below minimum | | | |

## 5. Data gaps and approximations (read before trusting any number)

1. **OI archive outage.** In `data.binance.vision` metrics, 2026-10-15 ~12:40–15:15 UTC has
   `sum_open_interest_value = 0` on all 9 majors. `oi_value_4h_change_zscore` rejects any non-positive value, so the
   whole 724h series is invalid and crash_flush is blind until about 11-14. The faithful run keeps this. The
   `OI_MODE=repair` run replaces the zero rows with contracts × the 1h open price, or with linear interpolation when the
   contract count is also 0. Also: the 10-29 metrics file is not published yet. For 10-29 onward I used the 5m
   `openInterestHist` row at label+5min, which matched the archive row labelled `label` exactly on all 148 overlapping
   10-28 rows. Availability = label + 1h, as in the research path.
2. **No historical order book.** Quotes are synthesized from 1m klines. Each 1m bar follows the path
   open → first extreme (20s) → second extreme (40s) → close (59s): low first on up bars, high first on down bars.
   Bid/ask are 1 tick apart. Mark follows the same path built from `markPriceKlines`. **Top-of-book size is today's
   `bookTicker` snapshot**, used as a constant. Every `INSUFFICIENT_EXECUTABLE_DEPTH`\* and part of
   `FILL_EXCEEDS_APPROVED_RISK` depends on this proxy. The rule (order > 10% of top-of-book → cancel) is QuantOS's own,
   but the real book during the crash was probably *thinner*, so if anything more fills would have failed.
3. **Cadence.** The feed runs every 5s (runtime: about 2s). Library scans run 3s after each 5m boundary; candidate scans
   3s after each hour (runtime: every 30s, same first-scan semantics). There was no network latency, no sleep and no
   rate limiting, so the real feed-staleness and disconnect gates (`STALE_FEED_BATCH`, `FEED_TIMEOUT`,
   `TRADE_BACKLOG`) **could not be exercised**. Live, Binance's API was congested during the hack, and those gates
   would probably have blocked entries.
4. **Universe.** Today's `exchangeInfo` filters and contract list are used. 24h volume = sum of the last 24 closed 1h
   quote volumes (runtime: rolling `ticker/24hr`). That gave 10–13 eligible USDC perps.
5. LIMIT fills use real `aggTrades` (with `nq` where present) fetched for each pending order window. None were needed:
   no LIMIT order was ever approved.
6. Funding comes from the actual `fundingRate` history. The window end is a mark, not a close: open positions carry no
   exit fee or slippage.
7. CF profiles are a what-if (Policy fees and sizing, candidate hold ceilings, USDT, 1 slot). pR uses only the 3
   candidates. The `--research` path would also run the 6 library rules on USDT; I did not.

## 6. Lessons for QuantOS

1. **Nothing was watching.** The only process on this Mac recorded about 3.3 active hours out of 75 wall hours, and it
   was not running during the hack. Any edge is worth $0 if the runner is not up. Before tuning strategies, get V4
   running continuously (supervised, AC power, `caffeinate`), with an alert when the feed age exceeds 60s.
2. **One bad archive row silently disabled the crash strategy for a month.** Fail-closed is correct, but it must be
   *loud*. Add a daily health check that each strategy can actually compute its features ("crash_flush: OI z
   unavailable on 9/9 majors since 10-15") and a documented repair or source-fallback rule. Here the fix would have
   produced 13 well-timed signals.
3. **Sizing rules block the strategies that fit crashes.** A 25% catastrophe stop at 1% risk on $200 means about $8
   notional, below Binance minimums. That accounts for 9 of 13 crash_flush and 87 of 97 alt_dip pR rejections. pA's
   0.25% probe risk rejects all of them. With $200, either accept a higher risk per trade for wide-stop event
   strategies, use a tighter stop (the 15% sensitivity stop exists), or add capital. Otherwise these rules can only be
   observed, not traded.
4. **One position per profile means the first trade wins the day.** In p1/p5, a 72h `tsmom_4h` position blocked
   1,000+ signals (EXISTING_EXPOSURE). In CF p5, a *pre-crash* NEAR dip trade from 08:00 held the only slot and blocked
   all 13 crash_flush signals. Event strategies need a reserved slot, or priority over slow trend trades.
5. **Breakout STOPs miss gap moves by design.** turtle_55 correctly flagged the 00:00 4h breakdown (10 signals), and
   donchian on 4–19 more, but `STOP_ALREADY_TRIGGERED` (never backfilled) skipped all of them. That is a sound
   anti-chasing rule, but in a crash it guarantees no participation. Meanwhile tsmom took the full stop on both sides:
   a short stopped out before the crash, then a long stopped out in it. This was a −2R to −3R day for the only strategy
   that traded.

## Files

* `scripts/fetch.py` downloads the public data. `scripts/replay_crash.py` is the causal driver (`OI_MODE=repair` for the
  sensitivity run). `scripts/near_miss.py` and `scripts/analyze.py` build the diagnostics and tables.
* `out/` and `out_oirepair/` hold the QuantOS SQLite journals per group/profile, with every SIGNAL / APPROVED /
  REJECTED_RISK / fill / CLOSED event.
* `signals_all.csv` has every signal with its decision, fill and PnL (6,000+ rows). `signals_approved.csv` has approved
  ones only. `summary.json` has per-profile totals. `out/near_miss.json` has the gate inputs per hour.
