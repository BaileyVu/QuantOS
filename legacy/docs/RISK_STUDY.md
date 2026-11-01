# Pilot risk study — evidence availability

No sufficiently complete timestamped candidate trade distribution has been
verified for annual day-block bootstrap. The reported E17 sample (37 trades on
10 event days) is unverified here and would be too sparse for reliable annual
tail estimates even if recovered. Reference strategies have no verified edge.

| Risk ceiling | Median/p5 annual equity | Median/p95 max DD | P(DD>25%) | P(DD>50%) | Annual longest loss streak |
|---|---|---|---|---|---|
| 1% | NOT_ESTIMABLE | NOT_ESTIMABLE | NOT_ESTIMABLE | NOT_ESTIMABLE | NOT_ESTIMABLE |
| 2% | NOT_ESTIMABLE | NOT_ESTIMABLE | NOT_ESTIMABLE | NOT_ESTIMABLE | NOT_ESTIMABLE |
| 5% | NOT_ESTIMABLE | NOT_ESTIMABLE | NOT_ESTIMABLE | NOT_ESTIMABLE | NOT_ESTIMABLE |

Conditional arithmetic only, assuming every stop loses exactly the chosen
fraction and ignoring breakers: ten compounded losses imply 9.56%, 18.29%, and
40.13% drawdown at 1%, 2%, and 5%. Thirteen 5% losses imply 48.67%; fourteen imply
51.23%. These are not probabilities. The 25% pause and 10% daily breaker should
stop the pilot sooner; gap/slippage losses can exceed the intended fraction.

The 1x exposure cap binds many p5 trades. At $200 equity and a 2% stop, maximum
pre-cost stop loss is about $4, not $10. Fees, lot increments and reserves further
reduce size. Reports show requested/achievable risk, constraint, notional and
margin. Never rescale p1 PnL by five to infer p5 results.

Retain p1 as default; p5 is an independent paper ceiling comparison. No live risk
choice is authorized. A 10,000-path annual day-block bootstrap and half-mean/
double-slippage stress are NOT_ESTIMABLE until complete, sufficiently broad
timestamped records exist. Offline execution stress must not change pilot limits.
