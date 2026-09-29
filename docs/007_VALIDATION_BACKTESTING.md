# QuantOS Core — 007_VALIDATION_BACKTESTING.md

Version: 1.0.0-V1
Status: Frozen V1
Last Updated: 2026-08-19

V1-F1 amendment (2026-09-30): apply 000 §0 without weakening this lifecycle.
Preregister/hash exact data, universe, costs, funding, features, model/search,
chronology, seeds, gates, leverage/liquidation/Risk/Monte Carlo surfaces and
holdout access before economic inspection. Qualify 1x OOS baseline and stress
expectancy before leverage. Report price, long/short, funding, fees and slippage
separately, plus fold stability/concentration, CVaR, drawdown and tail survival.
Only qualified Alpha proceeds to leverage 1/2/3/5/8/10/15/20x and deterministic
block/stationary Monte Carlo. Preserve untouched pre-final and final holdout;
each test period is evaluated once. Failed Alpha stops economic development;
leverage cannot rescue it. After final qualification require Futures Testnet,
Futures Paper and separate explicit Mainnet approval. Current filters cannot
prove historical 20 USDT executability. Unevaluated gates are never passes.

V1-F1 economic-methodology clarification: 000 §0.1 permits normalized continuous
notional factor simulation without historical order-filter replay. Freeze
5/7/10 bps fees and 2/10 bps slippage before outcomes; actual funding remains
separate. Historical filter gaps alone cannot deny Alpha research. Exact current
20 USDT executability is a separate post-qualification deployment gate.
NO_GO_ALPHA requires actual failed economic evidence; unevaluated Alpha is not
a failure result. All chronological and holdout protections remain mandatory.

## 1. Objective

Validation determines whether the single QuantOS V1 strategy is robust enough to progress toward live trading.

A strong backtest alone is never sufficient.

## 2. Mandatory Lifecycle

```text
Research
   ↓
Backtest
   ↓
Walk-Forward
   ↓
Monte Carlo
   ↓
Paper Trading
   ↓
Live Approval
```

No stage may be skipped.

## 3. Event-Driven Backtest

V1 backtesting must process historical events sequentially.

For each event:

```text
event(t)
  ↓
update market state
  ↓
update features
  ↓
generate alpha
  ↓
evaluate risk
  ↓
generate order
  ↓
simulate execution
  ↓
update account/position state
  ↓
record result
```

The strategy must never receive unrestricted future data merely because the entire historical dataset exists in memory.

## 4. Simulation Clock

The backtester has one authoritative simulation clock.

It controls:

- market timestamp;
- available data;
- feature state;
- alpha timestamp;
- order timestamp;
- simulated execution timestamp;
- account state.

## 5. Execution Simulation

The simulator must model:

- fees;
- slippage;
- order type behavior;
- fills;
- rejected orders;
- position changes;
- cash/balance changes.

Optimistic fills that could not realistically occur must not be used to improve results.

## 6. Look-Ahead Prevention

Forbidden:

- future candles in current features;
- random temporal train/test splitting;
- fitting preprocessing on future data;
- selecting parameters using the final test period;
- using future portfolio state;
- using future order-book/trade information.

## 7. Metrics

At minimum report:

- expected value;
- net profit;
- Sharpe;
- Sortino;
- maximum drawdown;
- profit factor;
- win rate;
- trade count;
- average trade;
- exposure;
- fees;
- slippage.

## 8. Walk-Forward Validation

Walk-forward validation repeatedly:

1. trains on an earlier period;
2. validates on the next unseen period;
3. advances the window;
4. repeats.

Results must be aggregated across windows.

A strategy that succeeds only in one historical period should fail promotion.

## 9. Monte Carlo Robustness

Monte Carlo should test whether conclusions remain plausible under variation such as:

- trade ordering;
- return perturbation consistent with observed behavior;
- execution-cost variation;
- slippage variation.

The purpose is robustness, not manufacturing a favorable distribution.

## 10. Research Run Records

Every run should record:

- run ID;
- dataset ID;
- code version;
- feature version;
- strategy version;
- model version;
- configuration;
- time windows;
- random seeds;
- metrics;
- artifact locations.

This is the main Qlib-inspired research discipline adopted by V1.

## 11. Test Set

The final test period remains untouched during model/parameter selection.

It is used once as an out-of-sample confirmation of the selected strategy.

## 12. Promotion Gates

A candidate may advance only if:

- expected value remains positive after costs;
- drawdown is acceptable;
- results are stable across walk-forward windows;
- Monte Carlo robustness is acceptable;
- no leakage is detected;
- the run is reproducible.

Exact numeric thresholds belong in external configuration rather than hidden code.

## 13. Paper Trading

Paper trading must run the production decision path continuously with real market data but simulated order effects.

It must record the same core events as live trading.

## 14. Live Approval

Live approval requires a documented validation result and explicit configuration change.

A validation pass does not automatically activate live trading.

## 15. Failure Conditions

A strategy fails validation if:

- leakage is found;
- reproducibility fails;
- performance depends on one narrow window;
- costs erase expected edge;
- drawdown exceeds configured limits;
- paper behavior materially diverges from simulation without an explained cause.
