# EDGE_SPEC — how every edge in this project is documented

Version 1.0, 2026-10-24. Applies to every folder in `edge_vault/` and to any test in `edge_data/` that
claims a PASS. Copy `EDGE_TEMPLATE.md` into `edge_vault/<edge_id>/README.md` and fill it in.

**Why this exists.** A backtest is cheap to produce and easy to fool yourself with. Bailey, Borwein,
López de Prado and Zhu show that after trying only a handful of variants you expect to find a backtest
that looks good even when the true edge is zero, and that most reports never say how many variants
were tried. So an edge doc in this project has three jobs:

1. **Audit.** Someone else (or you, in 6 months) can see exactly what was claimed, how hard the data
   was searched, and whether the pass rule was fixed before the result was seen.
2. **Reproduce.** One command, on frozen data, prints the same numbers.
3. **Go live.** The rule is precise enough to code a live bot from it, with costs, size limits and
   kill switches already decided.

If a field doesn't apply, write `n/a — <reason>`. Never leave it blank. A blank field is how
silent assumptions get in.

---

## 0. Status ladder

Every edge has exactly one status. It may move up only by meeting the gate below, and it may drop to
`dead` at any point.

| status | gate to enter | meaning |
|---|---|---|
| `idea` | hypothesis + mechanism written (sections 1-2) | Nothing tested yet. |
| `backtest-pass` | pre-registered rule PASSED on out-of-sample data with the conservative cost model | Works on bar or snapshot prices. Not yet proven fillable. |
| `real-print-pass` | PASSED again using real trades/prints (or 1-min / order-book data) at the actual entry time, e.g. `pm28b_check.py` | Proven to survive realistic fills. |
| `paper` | live signal generator running with simulated fills, logged | Testing the plumbing and live drift with no money at risk. |
| `live` | paper period met its pass rule (section 15), sizing caps set | Real money, capped size. |
| `dead` | a kill rule fired, or a pre-registered test FAILED | Kept for the record. Never delete. Say why it died. |

Add an optional flag `parked` (e.g. `backtest-pass, parked`) when an edge is valid but intentionally
not being run (e.g. `ema_level_cross_v1`, parked until capital ~ $100K). *Reason:* a parked edge is
different from a dead one, and the doc should say which.

A FAIL is never "fixed" by editing the rule and re-running on the same test data. A changed rule is a
new variant (bump `_v2`) and counts toward the trial count (section 11).

---

## 1. Identity

| field | content | why |
|---|---|---|
| `edge_id` | `snake_case_vN`, same as the folder name, e.g. `ema_level_cross_v1`, `pm28b_overround_no` | One stable key used in code, logs and the live bot. |
| Name | Plain-English one-liner | A human can tell edges apart at a glance. |
| Status | From section 0, plus date reached | Tells the reader how much to trust it. |
| Owner / author | Who ran it | Who to ask, who signs off. |
| Origin | Loop / experiment id (e.g. `loop 28b`), video, paper, or idea source | Ties the doc to the search that produced it and to its siblings, which count as trials. |
| Created / frozen date | ISO dates | Shows whether data after the freeze date is truly unseen. |

## 2. Hypothesis and mechanism

| field | content | why |
|---|---|---|
| Hypothesis (one sentence) | "Buying X when Y gives +Z per trade after costs." Falsifiable. | Forces a single testable claim, not a vibe. |
| Why it should exist | Who is on the other side and why they keep losing: forced flows, retail overpaying for long shots, slow repricing after an announcement, market-maker inventory, structural over-round, funding imbalance, etc. | An edge with no counterparty story is most likely noise. A mechanism also tells you what would make it disappear. |
| Why it isn't arbitraged away | Size too small for funds, operational hassle, capital lockup, venue access, timing | Explains why it can persist, and gives a hint about capacity. |
| What would kill the mechanism | e.g. venue changes fee or rule, more MMs arrive, listing process changes | These become monitoring items in section 13. |

## 3. Market and instruments

| field | content | why |
|---|---|---|
| Venue(s) | Binance / Bybit / Hyperliquid / Polymarket / spot announcement feeds, etc. | Fees, fills and data quirks are venue-specific. |
| Instruments / universe | Exact list or the rule that builds it (e.g. "9 USDT perps: BTC…LINK", "binary non-sports markets, first print 45-55c") | The universe is part of the strategy. A universe picked with hindsight is look-ahead. |
| Universe survivorship | Are delisted coins / voided or resolved markets included? | Survivorship bias inflates returns. |
| Side | long / short / both; Yes / No | Asymmetries (e.g. No on longshots) are often the whole edge. |
| Contract details | Tick size, min notional, leverage limits, funding interval, settlement / resolution rule | These set the true costs and the smallest size you can trade. |

## 4. Signal rule (must be codeable without asking questions)

| field | content | why |
|---|---|---|
| Decision clock | Bar size and time zone, e.g. "2h bars, UTC; decide at bar close", "hourly, signal hour t" | Most look-ahead bugs live here. |
| Entry condition | Exact formula with every parameter value | Reproducibility. "EMA cross" is not a rule, "EMA12 crosses EMA26 on close of 2h bar" is. |
| Entry timing / price | Which bar or hour you trade at, and at what price: next bar open, close + slippage, `t+1h` hour, first print after `t`, price at `t` + 1c | The gap between "signal seen" and "order filled" is where backtests lie. Must match what live can do. |
| Filters | Every filter with its value (level filter, sports regex, price band, liquidity floor, over-round threshold) | Filters are parameters too, and count as trials. |
| Exit | Stop, target, opposite signal, time stop, hold to resolution. Say which price triggers the exit (close vs high/low) | Exit assumptions (e.g. gap fills at the stop price) can be optimistic. |
| Sizing | Fixed $ / % equity / slots / Kelly fraction, leverage, max per instrument, max per event | Returns depend on sizing; clustered events need caps. |
| Concurrency / dedup | One position per coin? First qualifying hour per event only? | Stops one event from being counted as many trades. |
| Session / risk stops | e.g. "3 losses in a row → stop for the rest of the VN calendar day" | Part of the live behaviour, so it must be in the backtest too. |
| Code pointer | `strategy.py:function` or `pmXX_check.py:signals()` | The code is the final word if the text and code disagree. Say which one wins. |

## 5. Data

| field | content | why |
|---|---|---|
| Sources | Endpoint / dataset, e.g. "Binance public klines", "Polymarket gamma `markets_v2.jsonl` + CLOB price history `hist.jsonl` + `hist_fix.jsonl`", "data-api trades" | Know what you are trusting. |
| Resolution | 1m / 1h / snapshot ≤6h old / tick prints | Coarse data hides how fills really go. |
| Full date range | Start to end, with timezone | Needed to judge the regimes covered. |
| In-sample / out-of-sample split | Exact cutoff, e.g. `SPLIT = 2025-09-01 00:00 UTC`, train 2022-24 / test 2025-now. Was the test set touched during the search? | The OOS result is the main evidence. If test data shaped any choice, say so. |
| Frozen copy | Path to the frozen data, plus its size or hash | Re-runs must use the same bytes, or the results can't be compared. |
| Known gaps / fixes | Missing hours, re-fetched files (e.g. `hist_fix`), stale-price cutoffs | Data repairs can quietly change results. Variants like `--nofix` should be reported. |
| Leakage checks | List each check, e.g. "resolution price not used before `tres`", "universe built only from info at `t`" | Look-ahead is the most common fake edge. |

## 6. Cost and fill model

| field | content | why |
|---|---|---|
| Fees | Maker/taker per side, per venue, at your expected fee tier | Many small edges are smaller than the fees. |
| Spread / slippage | Assumption and where it comes from, e.g. "0.05%/side all-in", "+1c on the entry price (Polymarket)" | The `+1c` convention models paying through the spread. State it every time. |
| Fill price ladder | Report results at several fill assumptions, from optimistic to pessimistic: snapshot+1c → 1-min price at t+1/5/15/60m +1c → first / median / worst real print in the hour after entry | The way results decay down this ladder is the most honest picture of whether you can really trade it. Example from pm31: on the signals that had a print, the snapshot price +1c gives t = 6.3, but the median real print gives only t = 3.0 (average entry 0.72 vs 0.52). Only 22% of signals had any print at all, so it FAILED. |
| Fill rate | % of signals with a real print / fillable depth in the entry window | An edge on signals that can't be filled is not an edge. Pre-register a minimum (e.g. ≥50%). |
| Funding / borrow / capital lock | Perp funding paid or received; prediction-market capital tied up until resolution | Changes the return per unit of capital per year. |
| Stop and gap handling | Fill at the stop price or at the next price after the gap? | Filling at the stop price is optimistic. Say so. |

## 7. Pre-registered pass criteria

Written and dated **before** the test run, ideally inside the check script's docstring (as
`pm28b_check.py` / `pm31_check.py` do), so the code itself is the record.

| field | content | why |
|---|---|---|
| Rule text | e.g. "median real print in the hour after entry ≥ +4% with t ≥ 2 AND 1-min price at t+15m (+1c) ≥ +4% with t ≥ 2 AND ≥ 50% of signals have a print; n ≥ 30" | A rule fixed in advance can't be bent to fit the result. |
| Date fixed | ISO date / file timestamp | Proves it came first. |
| Unit of the t-stat | Per independent event / per day / per trade | Trades in the same event or hour are correlated, and treating them as independent inflates t. Project default: average per event, then t over events. |
| Multiple-testing adjustment | Trials count (section 11) and the threshold used: t ≥ 3 if many variants were tried (Harvey-Liu-Zhu), or a deflated Sharpe ratio / PBO estimate | t ≥ 2 is only valid for a single test. |
| Verdict | `PASS` / `FAIL`, copied verbatim from the script output, plus the output file path | The doc must match the machine output. No paraphrasing. |

## 8. Results

Report **out-of-sample first**, at the **conservative** fill assumption, then in-sample for comparison.

| field | content | why |
|---|---|---|
| n trades | Count | Sample size. |
| n independent events | Unique events / days / clusters (`ev=` in the check output) | The effective sample size. 360 trades over 102 events is about 102 bets, not 360. |
| Mean return per trade, net | After fees, slippage and the fill model, as a % of capital at risk | The core number. |
| Median and distribution | Median, 5th/95th percentile, largest single win as a % of total PnL | Shows whether a few outliers carry the result (longshot payoffs). |
| t-stat | With the unit stated (section 7) | Significance. |
| Win rate + average win / average loss | Both | Win rate alone is meaningless for binary payoffs. |
| Max drawdown | Peak to trough at 1x and at the planned leverage, plus the longest stretch under water | Tells you whether you can survive it, and sets the kill rules. |
| Annualised return / Sharpe | If time-based, with trades per year | Lets you compare it with other edges and compute the deflated Sharpe ratio. |
| Per-period breakdown | By year / quarter, and train vs test | One great period carrying everything is a red flag. |
| Per-segment breakdown | By coin, category, price bucket, venue | An edge that lives only in one segment may really be that segment, or noise. |
| Robustness / neighbourhood | Results for nearby parameter values | A plateau is more believable than a single sharp peak (`ema_level_cross_v1` reports 9 neighbours). |
| Benchmark | Buy-and-hold, random entry with the same exits, or always taking the same side | Separates the edge from just having market exposure. |

## 9. Capacity and liquidity

| field | content | why |
|---|---|---|
| Typical depth / volume at entry | e.g. median $ traded in the hour after entry, book depth within 1-2c | Sets the size ceiling. |
| Max $ per trade before impact eats half the edge | Estimate and how it was worked out | Decides whether this is a $200 edge or a $100K edge. |
| Max total deployed / concurrent | $ and number of positions | Stops you from overloading correlated bets. |
| Minimum viable size | Min notional, fixed fees, gas | Small accounts can be killed by minimums (`ema_level_cross_v1` at $200). |

## 10. Correlation with other edges

| field | content | why |
|---|---|---|
| Overlap with existing edges | Shared signals or markets, return correlation | Two edges that are really the same edge are not diversification. |
| Correlation of positions within the edge | e.g. "9 coins move together", "several markets in one negRisk event" | N slots are not N independent bets. |

## 11. Search history / multiple testing

| field | content | why |
|---|---|---|
| Number of variants tried | Total configs evaluated in the search that produced this edge, including dropped filters, timeframes and sibling loops (e.g. "#1 of 34,500") | The most important number for judging overfitting. If you don't report it, the result can't be audited. |
| How the winner was picked | e.g. "best on the worse of train/test with DD < 50%". Was test data used for selection? | Using test data to select means it is no longer out-of-sample. |
| Best-of-search hindsight number | e.g. "best test x of any combo: x359" | Shows how extreme pure chance gets in this search space. |
| Adjusted significance | Deflated Sharpe ratio, haircut Sharpe, PBO via CSCV, or at least "t ≥ 3 met: yes/no" | Corrects for selection. |
| Sibling results | Links to failed / passed siblings (pm31 FAIL, pm33, …) | The full picture, not just the winners. |

## 12. Risks and failure modes

| field | content | why |
|---|---|---|
| Known limitations of the backtest | Fill at close, flat stop, gap fills, stale snapshots, missing prints | An honest list of where the backtest is optimistic. |
| Regime risk | What market conditions it has never seen | Tells you when to stop trusting it. |
| Tail / blow-up risk | Binary resolution loss, liquidation, venue halt, oracle/resolution dispute (UMA), delisting | Rare events are missing from short samples. |
| Operational risk | API downtime, rate limits, clock skew, keys, withdrawal limits | Live failures usually happen here, not in the signal. |
| Rule / venue risk | Fee changes, market-creation changes, ToS / geo restrictions | Can remove the mechanism overnight. |

## 13. Kill criteria (live and paper)

Pre-registered, numeric, and checkable by a script.

| field | content | why |
|---|---|---|
| Hard stop | e.g. drawdown > 25% of allocated capital → flat and pause | Caps the damage. |
| Statistical decay | e.g. trailing N trades mean < 0, or live mean outside the backtest 95% interval after ≥ 30 events | Spots an edge that has died, as opposed to normal variance. |
| Live-vs-backtest drift | Realised slippage > 2× the modelled amount, fill rate < X% | The cost model is wrong, so the result is wrong. |
| Mechanism check | The item from section 2 that, if it changes, kills the edge | Kills it on cause, not only on PnL. |
| Review cadence | e.g. `reproduce.py --refresh` every quarter | Keeps the evidence current. |

## 14. Reproduction

| field | content | why |
|---|---|---|
| Command | Exact shell line incl. interpreter / venv, e.g. `../../emacross/.venv-whisper/bin/python reproduce.py` | One command → same numbers. |
| Files | Paths to strategy code, search engine, check script, signals CSV, frozen data, saved outputs | Everything needed is in one place. |
| Expected output | Path to the dated output file (e.g. `reproduce_output_2026-10-21.txt`) | Diff new runs against it. |
| Dependencies | Python version, packages, stdlib-only? Public endpoints only, no keys? | So it runs on a clean machine. Keys never go in the vault. |
| Runtime / network | Offline vs re-fetch (`--refresh`), roughly how long | Sets expectations. Don't re-fetch by accident. |

## 15. Paper / live tracking

| field | content | why |
|---|---|---|
| Start date, venue, size | | Context for the track record. |
| Pass rule to go from paper to live | e.g. ≥ 4 weeks AND ≥ 30 events AND mean net ≥ 50% of the backtest mean | Paper trading needs a gate too. |
| Log location | Path to the trade log (signal time, intended price, fill price, fees, PnL) | Lets you compute live-vs-backtest. |
| Live vs backtest table | Per month: n, mean net live, mean the backtest predicts for the same signals, slippage vs the model | The only real test of the edge. Do a same-signal comparison, not period vs period. |
| Shadow backtest | Re-run the backtest over the live period with live data and compare signal by signal | Catches signal bugs (missed or extra trades). |

## 16. Change log

Dated, newest last. Every status change, rule change (= new version), data refresh, kill-rule
trigger and re-run goes here. *Reason:* the history of decisions is part of the audit; a doc
without one can't show the pass rule came before the result.

---

## Rules of thumb used in this project

- **Conservative fill wins.** The headline number is the most pessimistic realistic fill (median real print, or next-bar open plus slippage), never the snapshot or close.
- **Count events, not rows.** t-stats are computed over independent events, and `n` and `ev` are always both reported.
- **t ≥ 2 for a single pre-registered test, t ≥ 3 once a search has been run.** Report the trial count either way.
- **One FAIL ends that version.** Re-testing a changed rule on the same OOS data is a new trial on contaminated data. Say so, or wait for fresh data.
- **Frozen data + dated outputs + verdict line copied verbatim.** If the doc and the script disagree, the script wins and the doc gets fixed.
- **No secrets in the vault.** Reproduction uses public endpoints only.

---

## Sources

- Bailey, Borwein, López de Prado, Zhu — "Pseudo-Mathematics and Financial Charlatanism: The Effects of Backtest Overfitting on Out-of-Sample Performance", Notices of the AMS 61(5), 2014. https://carmamaths.org/jon/backtest.pdf
- Bailey, Borwein, López de Prado, Zhu — "The Probability of Backtest Overfitting" (CSCV / PBO), Journal of Computational Finance. https://papers.ssrn.com/abstract=2326253 ; R package `pbo`: https://cran.r-project.org/package=pbo
- Bailey & López de Prado — "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality", JPM 40(5), 2014. https://papers.ssrn.com/abstract=2460551
- Harvey, Liu, Zhu — "… and the Cross-Section of Expected Returns", RFS 29(1), 2016 (t > 3 hurdle). https://papers.ssrn.com/abstract=2513152 ; https://www.nber.org/papers/w20592
- Harvey & Liu — "Backtesting" (haircut Sharpe ratios), JPM 2015. https://people.duke.edu/~charvey/Research/Published_Papers/P120_Backtesting.PDF
- Arnott, Harvey, Markowitz — "A Backtesting Protocol in the Era of Machine Learning", JFDS 2019. https://papers.ssrn.com/abstract=3275654
- White — "A Reality Check for Data Snooping", Econometrica 68(5), 2000. https://www.econometricsociety.org/publications/econometrica/2000/09/01/reality-check-data-snooping
- López de Prado — "The 10 Reasons Most Machine Learning Funds Fail", JPM 44(6), 2018. https://www.garp.org/white-paper/the-10-reasons-most-machine-learning-funds-fail
- Mitchell et al. — "Model Cards for Model Reporting", FAT* 2019. https://arxiv.org/abs/1810.03993
- Gebru et al. — "Datasheets for Datasets", 2018. https://arxiv.org/abs/1803.09010
- In-project examples: `edge_vault/ema_level_cross_v1/README.md`, `edge_data/pm28b_check.py` + `.txt` (PASS), `edge_data/pm31_check.py` + `.txt` (FAIL on the fill-rate rule).
