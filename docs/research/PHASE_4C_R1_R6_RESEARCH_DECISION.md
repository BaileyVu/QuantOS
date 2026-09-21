# Phase 4C R1–R6 Research Decision Record

## 1. Scope

Decision date: 2026-09-14. Close the tested Binance Spot BTCUSDT/ETHUSDT completed-1-minute OHLCV research program. This record evaluates existing evidence; it does not run another experiment or authorize production promotion.

IF1 preflight: branch `codex/phase-4d-model-feasibility-mf1`; HEAD and locally recorded `origin/main` both `579bf21512d09a0ab4fe088bd18bd7cf4d7284cb`. The working tree was clean and `git diff origin/main...HEAD` was empty. Existing upstream AF1–AF3 work is outside this R1–R6 decision and remains unchanged. No fetch was performed.

## 2. R1–R6 evidence table

| Round | Hypothesis / purpose | Persisted result | Decisive evidence |
| --- | --- | --- | --- |
| R1 | CUSUM + Triple Barrier | REJECTED | `NO VIABLE PHASE 4C-R CANDIDATE`; zero viable candidates. |
| R2 | Cost-adjusted fixed-horizon classifier / persistence | REJECTED | `NO VIABLE QPE CANDIDATE`; zero viable candidates. |
| R3 | Multi-timescale + relative-state regression | REJECTED | `NO VIABLE QRE CANDIDATE`. The post-hoc momentum subgroup was a hypothesis, not independent validation. |
| R4 | Fresh absolute + relative momentum / meta-labeling | REJECTED on fresh data | October 2025–April 2026: zero viable QRME candidates; PRIMARY had 0/7 positive months, 2,208 trades, mean net return −0.2891604%, profit factor 0.5005021. |
| R5 | Information sufficiency audit | No multivariate family passed | 0/12 model-set/horizon families passed stable-information or cost-plausibility gates. 28/90 univariate diagnostics passed their stability rule; strongest structure had negative short-horizon IC. |
| R6 | Fresh deterministic overshoot mean reversion | REJECTED | May–August 2026: all four QAOR candidates had negative mean net returns, 0/4 positive months and negative BTC and ETH results. No required composite increment was established over RETURN15. |

Evidence is each run's `summary.json` and `experiment_spec.json`, under these exact directories. Numeric statements use persisted metrics, rounded only for display; no CSVs or market datasets were opened for IF1.

```text
G:\QuantOS-Data\research\phase-4c-r\runs\54c52e07558eae2fd78dcd916a16d340e8e0beb4e1e4bb3620d6ebef814ad488
G:\QuantOS-Data\research\phase-4c-r2\runs\481b88573459464517f8d967ddb2c54a2380f73143e02fc294fe23f4bfa95e09
G:\QuantOS-Data\research\phase-4c-r3\runs\8d94e173a0afd276d88b5607699b88dffd0b27d54aba121b61f0c7346ec359f9
G:\QuantOS-Data\research\phase-4c-r4\runs\9854feb0f34c5301442000998acb8f3502310f984c61ab350d90cc130f20f811
G:\QuantOS-Data\research\phase-4c-r5\runs\924f3a7e36318e998a938d288f3a05756f5892dd8aa4c24277a9d53e194c1c12
G:\QuantOS-Data\research\phase-4c-r6\runs\eb1a395b11e35454b7d5978908a25527568fb1fa3b081d6630308b92fa66f343
```

## 3. What was rejected

The tested event labels, persistence classifiers, regression selections, momentum/meta-labeling construction and overshoot-reversion construction failed their frozen requirements. R4 rejected the underlying fresh momentum hypothesis, not merely its meta-model. R5 rejected the tested multivariate families' information sufficiency, not the existence of all statistical dependence.

Repeated use of development history is not independent replication. R4 and R6 supplied distinct fresh confirmation periods for their respective hypotheses. These results support closure of this program without asserting that every possible OHLCV strategy has been tested.

## 4. What R5 taught us

The strongest descriptive univariate result was `sma_spread_5_20` against 15-minute gross returns: median monthly Spearman IC −0.0593631462, negative in all 16 validation months. Short return and momentum diagnostics also pointed toward mean reversion. Their correlation and overlapping observations do not create 28 independent alpha discoveries.

Monthly combined BTC/ETH deciles were diagnostic rankings using the full month's prediction distribution, with symmetric fractional ties and observation-weighted aggregate returns. They were not executable causal selections. Stable ranking structure did not establish sufficient after-cost magnitude or a tradable rule.

## 5. What R6 confirmed

R6 tested that mean-reversion interpretation causally on fresh May–August 2026 data with `q` in {0.05, 0.10} and holds in {30, 60} minutes. All four QAOR candidates failed economically and in monthly and symbol robustness.

The least-negative QAOR candidate (`q=0.05`, 60 minutes) completed 990 trades: mean net return −0.2363529%, profit factor 0.2745314; BTC mean −0.2380304%, ETH mean −0.2346956%. Its matched RETURN15 baseline had mean −0.2159735% and profit factor 0.3947360.

QAOR profit factor was lower than its matched baseline in all four comparisons. At `q=0.10`, QAOR mean net return was slightly less negative than RETURN15 at both horizons; therefore “no metric improved” would be inaccurate. These small differences did not satisfy the frozen incremental-value and viability requirements.

The tested 0.10% fee and 0.025% slippage per side imply an exact gross break-even hurdle of approximately 0.250313%. R6 did not convert the R5 structure into viable after-cost performance under that accounting model.

## 6. Closed research directions

Status for every item below: `SAME_INFORMATION_SET_RESEARCH_CLOSED`.

- Nearby quantiles, including 0.025 or 0.075; more entry thresholds; 45/90/180-minute holds; TP/SL searches.
- XGBoost, Random Forest, neural networks or another model applied to the same inputs.
- More moving averages, RSI/MACD/Bollinger searches, volatility windows, indicator cocktails, regimes or BTC/ETH OHLCV cross-asset transforms.
- Dropping BTC or ETH, lowering assumed costs, or any post-hoc R6 rescue.

This task's research closure is stricter than whether a formula belongs to an otherwise permitted feature family. It accords with `docs/000_READ_FIRST.md` §3.3 “No backtest optimization” and §5 “Research Discipline”; it does not amend frozen specifications.

## 7. Current untouched reserve

The R6-declared reserve begins at **2026-09-01T00:00:00Z**, inclusive. IF1 has not accessed market data at or after that boundary, or any market dataset at all. No network access or reserve consumption was needed for this decision. The boundary is preserved as recorded; IF1 does not claim an audit of other tasks' access histories.

## 8. Scientific conclusion

Under the tested QuantOS V1 BTCUSDT/ETHUSDT Binance Spot completed-1-minute OHLCV information set, repeated distinct causal strategy constructions failed to demonstrate economically viable and stable after-cost alpha. R5 detected stable short-horizon statistical structure, but R6 showed that its tested deterministic implementation was insufficient to overcome the frozen transaction-cost assumptions.

This conclusion is limited to the tested information set, strategy families, symbols, horizons, periods and cost model. It does not establish that markets are unpredictable, that OHLCV can never work, that no profitable strategy exists, or that QuantOS cannot find alpha.

## 9. Next-decision boundary

Exactly one next direction is recommended in [the IF1 feasibility record](PHASE_4D_IF1_INFORMATION_FEASIBILITY.md): bounded feasibility of transaction-participation information from already-canonical `trade_count` and `quote_volume`. The persisted R1–R6 feature definitions did not use these as predictive inputs; existing `volume_ratio_20` uses base volume only.

`docs/003_DATA_ARCHITECTURE.md` §3 “Canonical Candle” explicitly includes both fields and requires explicit approval for additional provider-specific domain fields. `docs/004_FEATURE_ENGINE_SPECIFICATION.md` §3 “Approved Feature Families” includes volume behavior. This permits studying the retained information without granting permission for taker-flow fields or reopening the closed OHLCV search.

Human review precedes further work. Any later protocol must be frozen before reserve access. No feature, model, strategy or lifecycle promotion is approved by this record; `docs/007_VALIDATION_BACKTESTING.md` §2 “Mandatory Lifecycle” and §11 “Test Set” continue to apply.
