QuantOS Core — 001_PRODUCT_REQUIREMENTS.md
Version: 1.0.0-V1
Status: Replacement baseline
Last Updated: 2026-08-19
1. Product Goal
QuantOS V1 shall provide one production-quality quantitative trading workflow for Binance Spot, from historical data through validated paper trading and explicitly enabled live trading.
The system is designed for one quantitative developer running locally.
2. Scope
Requirement	V1
Exchange	Binance Spot
Symbols	BTCUSDT, ETHUSDT
Capital	20 USDT initial target
Primary timeframe	1 minute
Deployment	Local workstation
Storage	Parquet + DuckDB
Architecture	Clean Architecture, Modular Monolith
Production strategies	1
Production models	1
Production features	10–15 target, 20 maximum
Default mode	Paper trading
3. Operating Modes
Exactly one mode is active at runtime:
Research
Backtest
Paper Trading
Live Trading
Live mode must require explicit configuration and must never be the default.
4. Market Data Requirements
The system shall support:
historical OHLCV ingestion;
live market data;
incremental updates;
UTC-normalized timestamps;
duplicate detection;
missing-candle detection;
symbol metadata validation;
deterministic candle normalization.
The canonical V1 decision dataset is 1-minute OHLCV plus approved derived data.
Raw provider data shall remain immutable.
5. Feature Requirements
Features shall:
be deterministic;
be causally valid at the decision timestamp;
have a documented economic or statistical purpose;
be reproducible from stored data;
be limited to 20 production features;
target 10–15 features for the first production strategy.
Features that duplicate another feature's information without measurable incremental value should be removed.
6. Alpha Requirements
The Alpha Engine shall produce one unified actionable decision:
LONG / BUY
EXIT / SELL
HOLD
The V1 production strategy is one strategy, not an ensemble of independent live strategies.
The Alpha Engine may use deterministic rules plus one production ML model. The model is an aid to the approved strategy, not an autonomous decision-maker.
Every decision must contain enough metadata to explain:
timestamp;
symbol;
feature/model version;
signal;
confidence or score where defined;
strategy state;
reason for rejection or action.
7. Model Requirements
Only one model may be active in production.
The preferred candidate is LightGBM.
Training must be reproducible from:
dataset version;
feature specification;
target definition;
time windows;
model parameters;
random seed;
software/dependency versions;
training configuration.
Model selection must emphasize out-of-sample stability, not raw training performance.
8. Risk Requirements
Risk must be evaluated before execution.
Mandatory controls:
position-size limit;
maximum position risk;
maximum daily loss;
maximum drawdown protection;
volatility-aware sizing;
insufficient-edge rejection;
invalid-market-state rejection;
execution-quality rejection.
The Risk Engine may reject any signal.
No downstream component may override a risk rejection.
9. Execution Requirements
Execution shall:
estimate fees;
estimate slippage;
evaluate expected execution quality;
select Market or Limit only when allowed by the approved execution policy;
reject trades whose expected edge after costs is non-positive;
track order state;
record exchange responses;
fail safely on network or exchange errors.
Only the Execution Engine may submit trading instructions.
10. Evaluation Requirements
Evaluation shall calculate, at minimum:
expected value;
net profit;
Sharpe ratio;
Sortino ratio;
maximum drawdown;
profit factor;
win rate;
trade count;
average trade;
exposure.
Transaction costs must be included.
11. Validation Requirements
The system shall support:
historical backtesting;
walk-forward validation;
Monte Carlo robustness analysis;
paper trading;
explicit live approval.
Validation must be temporal and must prevent leakage.
12. Configuration Requirements
Configuration must be external to business logic and cover:
symbols;
timeframe;
runtime mode;
storage paths;
risk limits;
execution parameters;
model parameters;
validation periods;
API credentials.
Credentials must never be hardcoded.
13. Security Requirements
Withdrawal permissions are not required.
API secrets remain outside source control.
Paper mode is the default.
Live mode requires explicit enablement.
Failed authentication or invalid credentials must fail closed.
14. Logging Requirements
Log:
startup/shutdown;
data ingestion;
data validation failures;
feature generation;
model version and predictions;
alpha decisions;
risk decisions;
order requests;
exchange responses;
fills;
errors and warnings;
validation runs.
15. Acceptance Criteria
V1 is acceptable only when:
historical data loads correctly;
live data operates continuously;
features are deterministic;
model training is reproducible;
backtests execute without leakage;
walk-forward validation passes defined gates;
Monte Carlo robustness is acceptable;
paper trading operates successfully;
live mode can be enabled safely;
risk controls work;
every trade is explainable and logged.
