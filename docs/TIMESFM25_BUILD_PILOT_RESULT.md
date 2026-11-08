# TimesFM 2.5 BUILD volatility pilot — rejected for promotion

Actual run completed on 2026-11-08 in 137.686 seconds, exit 0. It evaluated
343 non-overlapping daily origins per asset (1,029 total) on the fixed 2022
BTCUSDT, ETHUSDT and SOLUSDT hourly dataset. No HELD data was used.

**Decision:** the tested volatility-width feature fails the requested gate of
beating persistence and EWMA. Do not add it to sizing or live/demo signals.
This is a negative research result, not a statement about every possible use
of TimesFM. The ml1h augmentation and crash/dip L3 filter were not run.

MAE is measured in 24-hour log-return volatility units; lower is better.
These values are forecast errors, not trading returns or P&L.

| Series | Origins | TimesFM | Persistence | EWMA | ATR |
|---|---:|---:|---:|---:|---:|
| BTCUSDT | 343 | 0.00984612 | 0.01125725 | 0.01037655 | 0.00985379 |
| ETHUSDT | 343 | 0.01233612 | 0.01402159 | 0.01254960 | 0.01203392 |
| SOLUSDT | 343 | 0.02017820 | 0.01973464 | 0.01881840 | 0.01776013 |
| Equal-asset mean | 1,029 | 0.01412015 | 0.01500449 | 0.01391485 | 0.01321595 |

Paired circular seven-day block bootstrap: 2,000 resamples, seed 0. Assets
are resampled on the same day indexes to preserve cross-asset dependence.
Every 95% interval for TimesFM-minus-baseline MAE includes zero. A negative
difference favors TimesFM; its pooled point error is worse than EWMA and ATR.

| Baseline | Observed MAE difference | Paired 95% interval |
|---|---:|---|
| atr | 0.00090420 | [-0.00030851, 0.00220003] |
| ewma | 0.00020529 | [-0.00073473, 0.00117370] |
| persistence | -0.00088435 | [-0.00191859, 0.00015708] |

The model uses 512-hour causal contexts and fixed 24-hour targets. Quantile
width is converted with log(q90/q10)/2.563103131; the target is the square root
of next-24h squared hourly log returns. Gaussian width/range assumptions were
frozen before inference; no result-based rescaling or threshold search occurred.

Limitations: three USDT proxy assets and one BUILD year; no historical USDC
execution/depth evidence, no transaction-cost or profitability conclusion, and
unverified overlap between the foundation-model training corpus and these
historical targets. The result does not pass L2 or L3.

## Reproducibility

- Source commit: `06f5b4f36ae59879a4676cf284eb925f6d89f0ee`.
- Relevant source SHA256: `516d52474aa23371e976b85d424039bbfcc7d74b8e8162aff5e7e2cd333cda5f`.
- Frozen plan SHA256: `397bca1593bbdbdd79010efede94f5017a03c29983acd0a703090f5230f0df6f`.
- Report SHA256: `f3b61ee8abead4881933703f352970bc7765804ed340e2d8ace6c8ae2ee3f6a3`.
- Model: `google/timesfm-2.5-200m-pytorch`, revision `2d1b32066b7f3950c45913d4a3849f3c75cb0312`.
- Weights SHA256: `2f776efe6245e42b24bc4153ffdf61810140210e4bd3b01fb21f7aa779ab6ce8`.
- CPU, two threads; complete dependency versions and dataset hashes are in the frozen plan.
- Local artifacts: `QuantOS-infra-timesfm/.research-timesfm25-build2022/run-v1/`.
- The adjacent `registry.sqlite` records these trials only; it is not the global search registry.
- Completed progress contains all 1,029 origins. Do not rerun the experiment.
