# QuantOS V1-T5 Alpha sprint evidence

Verdict: **NO_GO_ALPHA**.

Mainnet: **LOCKED / NOT_APPROVED**.

## Source identity and validation

- Starting SHA: `f85c3497985c23ff3890b693e069afceab76bd8f`.
- Separate authorized specification amendment: `7a522e2`.
- Evaluated source SHA: `2a56f2773b24358f746366d85753d770853f6c96`.
- Implementation/preregistration commit: `9748177`; subsequent source commit changes whitespace only.
- Pre-economic full suite: 1,046 tests passed. Final full suite: 1,047 tests passed in91.429 seconds.
- Dependency consistency (`pip check`) and Python compilation passed. No configured lint/type checker exists.
- Replay parity covers synthetic historical/batch and streaming inputs through the canonical execution path. A continuous Paper runtime adapter for either T5 candidate was not packaged or qualified because both failed development.
- No push, merge, exchange orders or Mainnet enablement performed. Market data/model/evidence files remain outside Git.

## Preregistration hashes

- Protocol: `539194add6c9d2ccb3265859170f03aee61291a6c8235c790c9687f54c95ddb7`.
- Machine configuration: `6fe589b2857c76ff5260d9870e5f23a9f5a4ae95db3495fa017b753d47eaaee0`.
- Dataset/fold manifest: `bf017d59b505e4e6cc7e1d67c0ca763380dc646c80f40e6beb69630746072a22`.

The complete protocol is [V1_T5_ALPHA_PREREGISTRATION.md](V1_T5_ALPHA_PREREGISTRATION.md).

## Data integrity and coverage

Acquired and checksum-validated 1,545 daily archive dates (2019-01-01 through 2023-03-24 plus 2026-09-28), alongside the existing canonical post-gap source. Coverage is segmented, not claimed continuous across outages.
Exactly one REST recovery attempt returned only the 12:39 and 14:00 overlap candles. Both match the archive exactly; 12:39 is itself a partial-minute candle. The 80 missing minutes were not repaired. No interpolation, synthetic candles or source rewriting occurred.
The immutable manifest identifies 24 continuous validated partition chains and 9 quarantined partitions containing incomplete candles. All feature/label/fold windows stay inside one accepted segment.

| Continuous segment start UTC | End UTC | Minutes | Chain identity |
|---|---|---:|---|
| 2021-12-25T00:00:00+00:00 | 2023-03-23T23:59:00+00:00 | 653760 | `d9dc35013e99d064e5817dc6c769042d7cf0953599d9cbef91e4b487f9b39e10` |
| 2023-03-24T14:00:00.000000+00:00 | 2026-09-28T23:59:00+00:00 | 1849560 | `2ffe9116547133577b8e0581d66e2cf89aaeaa267f59296a15cc1b50de701727` |

Earlier accepted segments do not contain sufficient uninterrupted training/validation/test history for the frozen schedules. No performance evidence was calculated from 2025 or 2026.

## Exact candidate contracts

**A — `sae-tbl-30m-longcash-v1`:** completed UTC30m bars; finite39-column OHLCV-derived candidate universe with training-local FD selection, variance/correlation pruning and scaling. Supervised denoising autoencoder, width32, bottleneck8/16, three-class head. Two paired TBL horizon/barrier/noise configurations (4/1/0 and 8/2/.05), 15 deterministic CPU epochs. Positive probability >=.6 and uniquely largest targets LONG; other classes target CASH. Training-only class gross-return calibration supplies Risk edge after reserving exit cost. Barrier outcomes label training data; no perfect barrier execution is assumed.

**B — `cost-aware-hourly-xgb-v1`:** completed UTC1h bars; same finite candidate features and fold-local transformations. XGBoost predicts next-hour close return; exactly depth2/100 or depth3/200 trees, other parameters frozen. Positive forecast targets LONG, otherwise CASH; changes require absolute forecast >2 times effective change cost. No shorting, pyramiding or test-driven tuning.

A uses6-month train/1-month validation/1-month test. B uses up to12-month train (minimum6 for fragmented history),3-month validation/3-month test. Both configurations are chosen by validation predictive loss only. This is a declared adaptation, not a reproduction of the papers.

## Accounting and cost assumptions

Every position change passes the canonical Alpha → Risk → Execution path. Execution owns fills and account mutation; canonical Evaluation computes metrics. Signal execution waits one completed minute, then uses its close plus adverse friction. Initial capital20 USDT, fixed10 USDT entry,60% exposure cap,5% daily-loss and20% drawdown BUY stops, quantity step.00001 BTC, minimum notional5 USDT. These are conservative research filter assumptions, not historical filter observations.
The nominal per-change cost ladder is10/15/20/25/30bps, consisting of10bps explicit fee and0/5/10/15/20bps adverse slippage. Buys additionally incur fee on slipped notional. No BNB discount. Final open positions are marked, not given invented liquidation fills. Independent folds start flat; aggregate return is total PnL divided by20 times fold count.

## Aggregate OOS results and cost sensitivity

| Candidate | Cost bps | Aggregate return | Net PnL USDT | PF | Sharpe | Sortino | Trade expectancy USDT | Change expectancy USDT | Win rate | Trades / changes | Changes/day | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 10 | 0.000% | 0.0000 | undefined | undefined | undefined | undefined | undefined | undefined | 0 / 0 | 0.0000 | 0.000% |
| A | 15 | 0.000% | 0.0000 | undefined | undefined | undefined | undefined | undefined | undefined | 0 / 0 | 0.0000 | 0.000% |
| A | 20 | 0.000% | 0.0000 | undefined | undefined | undefined | undefined | undefined | undefined | 0 / 0 | 0.0000 | 0.000% |
| A | 25 | 0.000% | 0.0000 | undefined | undefined | undefined | undefined | undefined | undefined | 0 / 0 | 0.0000 | 0.000% |
| A | 30 | 0.000% | 0.0000 | undefined | undefined | undefined | undefined | undefined | undefined | 0 / 0 | 0.0000 | 0.000% |
| B | 10 | 3.613% | 3.6133 | 2.4428 | 0.7353 | 1.0602 | 0.2912 | 0.1807 | 75.000% | 8 / 20 | 0.0437 | 14.546% |
| B | 15 | 0.903% | 0.9027 | 2.4748 | 0.2611 | 0.3698 | 0.3302 | 0.0821 | 75.000% | 4 / 11 | 0.0240 | 14.549% |
| B | 20 | -0.043% | -0.0429 | 3.4755 | 0.0282 | 0.0394 | 0.3841 | -0.0048 | 66.667% | 3 / 9 | 0.0197 | 13.818% |
| B | 25 | 2.842% | 2.8419 | undefined | 0.8974 | 1.3058 | 1.0597 | 0.4736 | 100.000% | 2 / 6 | 0.0131 | 8.738% |
| B | 30 | -0.273% | -0.2726 | undefined | -0.0857 | -0.1142 | 0.9069 | -0.0909 | 100.000% | 1 / 3 | 0.0066 | 8.624% |

## Candidate A: every OOS fold

| Test start | End exclusive | Selected configuration | Features | Baseline PnL | Stress PnL | Baseline trades / changes | Baseline PF | Baseline Sharpe | Baseline DD |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 2022-08-01 | 2022-09-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2022-09-01 | 2022-10-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2022-10-01 | 2022-11-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2022-11-01 | 2022-12-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2022-12-01 | 2023-01-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2023-01-01 | 2023-02-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2023-02-01 | 2023-03-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2023-11-01 | 2023-12-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2023-12-01 | 2024-01-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2024-01-01 | 2024-02-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2024-02-01 | 2024-03-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2024-03-01 | 2024-04-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2024-04-01 | 2024-05-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2024-05-01 | 2024-06-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2024-06-01 | 2024-07-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2024-07-01 | 2024-08-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2024-08-01 | 2024-09-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2024-09-01 | 2024-10-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2024-10-01 | 2024-11-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2024-11-01 | 2024-12-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2024-12-01 | 2025-01-01 | `{"barrier": 2.0, "bottleneck": 16, "horizon": 8, "noise": 0.05}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |

Frozen gate results: baseline_positive=FAIL, stress_positive=FAIL, profit_factor=FAIL, sharpe=FAIL, stress_sharpe=FAIL, expectancy=FAIL, stress_expectancy=FAIL, fold_stability=FAIL, fold_concentration=FAIL, trade_concentration=FAIL, drawdown=PASS.

Feature selection counts across folds (training evidence only): `fd_close_0.5` 2/21, `fd_close_0.75` 19/21, `fd_volume_0.25` 21/21, `location_16` 21/21, `location_4` 21/21, `location_48` 21/21, `log_return_1` 21/21, `log_return_16` 21/21, `log_return_2` 21/21, `log_return_4` 21/21, `log_return_48` 21/21, `log_return_8` 21/21, `log_trades` 21/21, `log_volume_change_1` 21/21, `log_volume_change_16` 21/21, `log_volume_change_4` 21/21, `log_volume_change_48` 21/21, `lower_wick` 21/21, `range` 21/21, `relative_volume_16` 21/21, `relative_volume_4` 21/21, `relative_volume_48` 21/21, `trend_16` 21/21, `trend_4` 21/21, `trend_48` 21/21, `upper_wick` 21/21, `volatility_16` 21/21, `volatility_4` 21/21, `volatility_48` 21/21, `volume_z_16` 21/21, `volume_z_4` 21/21, `volume_z_48` 21/21, `vwap_deviation` 21/21.

Baseline Risk rejections: none.

Mean normalized model importance (top10, absent features count zero): `volatility_16` 0.0457, `log_return_1` 0.0377, `relative_volume_16` 0.0358, `volatility_4` 0.0342, `vwap_deviation` 0.0332, `log_volume_change_1` 0.0332, `log_volume_change_4` 0.0331, `trend_4` 0.0330, `location_16` 0.0327, `range` 0.0325.
Importance uses XGBoost gain for B and mean absolute encoder weights for A. Encoder weights are a descriptive sensitivity proxy, not causal feature importance or an ablation study. No outcome-driven feature changes were made.

At 15bps: fees 0.0000 USDT; slippage 0.0000 USDT; mean exposure 0.000%; daily5% expected shortfall 0.000%; worst completed trade undefined USDT; longest within-fold closed-trade losing streak 0; 0 folds end with a marked open position.

At 30bps: fees 0.0000 USDT; slippage 0.0000 USDT; mean exposure 0.000%; daily5% expected shortfall 0.000%; worst completed trade undefined USDT; longest within-fold closed-trade losing streak 0; 0 folds end with a marked open position.

## Candidate B: every OOS fold

| Test start | End exclusive | Selected configuration | Features | Baseline PnL | Stress PnL | Baseline trades / changes | Baseline PF | Baseline Sharpe | Baseline DD |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 2022-10-01 | 2023-01-01 | `{"max_depth": 2, "n_estimators": 100}` | 32 | -1.2817 | -1.1796 | 0 / 1 | undefined | -0.9596 | 14.549% |
| 2024-01-01 | 2024-04-01 | `{"max_depth": 2, "n_estimators": 100}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |
| 2024-04-01 | 2024-07-01 | `{"max_depth": 2, "n_estimators": 100}` | 32 | 0.0818 | 0.0000 | 0 / 1 | undefined | 0.1825 | 9.880% |
| 2024-07-01 | 2024-10-01 | `{"max_depth": 3, "n_estimators": 200}` | 32 | 2.1027 | 0.9069 | 4 / 9 | 2.4748 | 1.9797 | 12.413% |
| 2024-10-01 | 2025-01-01 | `{"max_depth": 2, "n_estimators": 100}` | 32 | 0.0000 | 0.0000 | 0 / 0 | undefined | undefined | 0.000% |

Frozen gate results: baseline_positive=PASS, stress_positive=FAIL, profit_factor=PASS, sharpe=FAIL, stress_sharpe=FAIL, expectancy=PASS, stress_expectancy=FAIL, fold_stability=FAIL, fold_concentration=FAIL, trade_concentration=FAIL, drawdown=PASS.

Feature selection counts across folds (training evidence only): `fd_close_0.5` 1/5, `fd_close_0.75` 4/5, `fd_volume_0.25` 5/5, `location_16` 5/5, `location_4` 5/5, `location_48` 5/5, `log_return_1` 5/5, `log_return_16` 5/5, `log_return_2` 5/5, `log_return_4` 5/5, `log_return_48` 5/5, `log_return_8` 5/5, `log_trades` 5/5, `log_volume_change_1` 5/5, `log_volume_change_16` 5/5, `log_volume_change_4` 5/5, `log_volume_change_48` 5/5, `lower_wick` 5/5, `range` 5/5, `relative_volume_16` 5/5, `relative_volume_4` 5/5, `relative_volume_48` 5/5, `trend_16` 5/5, `trend_4` 5/5, `trend_48` 5/5, `upper_wick` 5/5, `volatility_16` 5/5, `volatility_4` 5/5, `volatility_48` 5/5, `volume_z_16` 5/5, `volume_z_4` 5/5, `volume_z_48` 5/5, `vwap_deviation` 5/5.

Baseline Risk rejections: none.

Mean normalized model importance (top10, absent features count zero): `log_return_48` 0.0474, `log_volume_change_16` 0.0463, `log_return_8` 0.0441, `volatility_48` 0.0420, `log_return_16` 0.0415, `volatility_4` 0.0410, `volatility_16` 0.0406, `upper_wick` 0.0375, `trend_16` 0.0363, `lower_wick` 0.0353.
Importance uses XGBoost gain for B and mean absolute encoder weights for A. Encoder weights are a descriptive sensitivity proxy, not causal feature importance or an ablation study. No outcome-driven feature changes were made.

At 15bps: fees 0.1091 USDT; slippage 0.0545 USDT; mean exposure 25.620%; daily5% expected shortfall -2.172%; worst completed trade -0.8956 USDT; longest within-fold closed-trade losing streak 1; 3 folds end with a marked open position.

At 30bps: fees 0.0302 USDT; slippage 0.0603 USDT; mean exposure 5.802%; daily5% expected shortfall -1.038%; worst completed trade 0.9069 USDT; longest within-fold closed-trade losing streak 0; 1 folds end with a marked open position.

## Promotion disposition

SAE produced30,651 OOS predictions; maximum positive-class probability was0.413351, below the frozen0.6 confidence gate. Thus it never requested an entry, rather than being blocked by Risk. Both candidates failed mandatory development gates. No strategy was selected. This is NO_GO_ALPHA for this preregistered experiment; it does not prove that all models in either broad family are unprofitable.
- Monte Carlo/bootstrap: not run on failed candidates; the mandatory economic gates failed first. No simulated survival claims are made.
- Aggressive10/25/50/75/100% allocation and fractional-Kelly study: not performed because no Alpha qualified for promotion; no exposure recommendation.
- Calendar2025 prefinal: not opened.
- Calendar2026 holdout: not opened.
- Paper packaging: not eligible; no launch procedure or activation issued.
- Mainnet: LOCKED / NOT_APPROVED.
- Worktree: clean after the local report commit; no push or merge.

## Evidence locations

- Immutable seal: `G:\QuantOS-Data\v1t5\preregistration_seal.json`.
- Dataset, partitions, quarantine and exact folds: `G:\QuantOS-Data\v1t5\dataset_manifest.json`.
- All fold/scenario metrics and fill evidence: `G:\QuantOS-Data\v1t5\development`.
- Exact model weights, transformation identities and predictions: `G:\QuantOS-Data\v1t5\models`.
- Final machine-readable verdict: `G:\QuantOS-Data\v1t5\verdict.json`.
