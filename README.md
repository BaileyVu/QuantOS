# QuantOS

QuantOS is a local, research-driven quantitative trading system for building and evaluating one explainable Binance Spot strategy. Its V1 design prioritizes capital preservation, deterministic behavior, reproducibility, and clear safety boundaries over strategy complexity.

> **Trading status:** QuantOS mainline is research and paper-trading oriented. It is not live-approved, does not claim profitability, and must not be treated as an autonomous live-trading system.

## What QuantOS Is

QuantOS is a Python modular monolith with Clean Architecture boundaries. The frozen V1 scope is deliberately narrow:

- Binance Spot only
- BTCUSDT and ETHUSDT only
- Completed 1-minute candles only
- Approximately 20 USDT reference capital
- One production strategy, one production model, and a compact feature set
- Paper trading by default

The authoritative requirements are in [docs/000_READ_FIRST.md](docs/000_READ_FIRST.md) and the related frozen V1 specifications. This README is a project overview, not a replacement specification.

## Current Status

| Area | Current mainline status |
|---|---|
| V1 scope | Frozen Spot-only specification |
| Market data | Historical and live Binance Spot candle paths, canonical validation, Parquet persistence, and local DuckDB queries |
| Features and modelling | Deterministic candidate features, causal training data, temporal splitting, and LightGBM artifact infrastructure |
| Evaluation | Event-driven backtesting, cost-aware simulation, metrics, walk-forward evaluation, and Monte Carlo support |
| Runtime | Risk-controlled paper execution and a continuous paper-runtime path |
| Live trading | Not approved; explicit promotion and approval gates remain mandatory |

Research records and validation artifacts are retained as evidence; they do not establish future performance or live-trading approval.

## Architecture

```text
Market Data
    ↓
Feature Engine
    ↓
Alpha Engine
    ↓
Risk Engine
    ↓
Execution Engine
    ↓
Binance Spot

Evaluation Engine: backtest, validation, paper-trading evaluation, and reporting
```

QuantOS has exactly six production modules:

1. Market Data
2. Feature Engine
3. Alpha Engine
4. Risk Engine
5. Execution Engine
6. Evaluation Engine

Configuration, adapters, storage, logging, CLI code, and tests support these modules; they are not separate product modules.

## Key Capabilities

- UTC-normalized, completed-candle Binance Spot data handling for BTCUSDT and ETHUSDT.
- Immutable canonical historical datasets stored as Parquet, with typed local DuckDB queries.
- Deterministic feature computation shared by research and runtime paths.
- Causal labels, chronological temporal splits, reproducible LightGBM model artifacts, and model compatibility checks.
- Event-driven backtests with explicit fees, slippage, fills, account state, and performance metrics.
- Walk-forward and Monte Carlo validation support.
- Paper-first risk and execution flow with position, exposure, daily-loss, drawdown, stale-state, and after-cost-edge checks.
- Structured logging, execution evidence, and fail-closed handling for invalid or uncertain state.

## Futures / HFT Research

Futures and HFT work is intentionally preserved outside the frozen Spot-only mainline. It is not merged into `main`, is not a V1 production capability, and is not evidence of live profitability.

Preserved branches include:

- `codex/v1-autonomous-futures-mvp` — Binance USDⓈ-M Futures integration, L2/bookTicker/trade/mark/funding inputs, VAMP, microprice, imbalance, signed aggressive flow, maker-first research, queue-aware simulated fills, liveness controls, authenticated preflight, and Edge V2 paper research.
- `codex/v1-f3-carry-risk-overlay` — Futures carry-risk preregistration and supporting research code.
- `codex/v1-t5-aggressive-alpha` — separate experimental alpha work pending explicit specification review.

These branches remain separate so the frozen V1 Spot scope stays authoritative on `main`.

## Research and Validation

QuantOS follows a staged promotion discipline:

```text
Research → Backtest → Walk-Forward → Monte Carlo → Paper Trading → Explicit Live Approval
```

A successful backtest, model fit, or paper-runtime result never enables live trading automatically. Historical research is evaluated with leakage prevention, temporal separation, realistic costs, reproducible inputs, and documented assumptions.

## Repository Structure

```text
src/quantos/
  domain/           # Six module rules and shared contracts
  application/      # Use-case coordination
  infrastructure/   # Binance adapters, storage, models, config, logging
  interfaces/       # Local CLI and paper runtime entry points

configs/            # Safe example/runtime configuration
docs/               # Frozen V1 specifications and research records
research/           # Reproducible research programs and evidence
tests/              # Unit, integration, and architecture validation
```

## Getting Started

QuantOS requires Python 3.11 or later.

```bash
git clone https://github.com/BaileyVu/QuantOS.git
cd QuantOS
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

On Windows PowerShell, activate with:

```powershell
.\.venv\Scripts\Activate.ps1
```

Useful verification commands:

```bash
python -m unittest tests.validation.test_architecture
python -m unittest discover -s tests -t . -v
python -m quantos --help
```

The CLI exposes paper-runtime and aggregate-trade research commands. The default configuration is paper mode; attempting a runtime without a selected Alpha fails closed. Configuration and command invocation do not confer live-trading approval.

## Development

Read these before changing trading behavior or architecture:

1. [AGENTS.md](AGENTS.md)
2. [docs/000_READ_FIRST.md](docs/000_READ_FIRST.md)
3. The directly relevant frozen specification in [docs](docs)

Preserve module ownership, use completed-candle UTC semantics, and keep backtest, paper, and live behavior aligned. Do not add exchanges, instruments, strategies, or models without an approved specification change.

## Safety and Trading Status

- Begin with research, validation, and paper operation.
- Risk is evaluated before Execution; a Risk rejection is final.
- Only the Execution module may submit an exchange order.
- Missing, stale, invalid, or unreconciled state must fail closed.
- Mainnet operation requires explicit approval and configuration; it is never the default.
- Never commit credentials, market data, model artifacts, logs, or generated reports.

## Data and Secrets

Local datasets and credentials are intentionally outside Git. Typical PC-only locations include:

```text
G:\QuantOS-Data
G:\QuantOS-HFT-Data
G:\QuantOS-Secrets
```

Keep API keys, exchange credentials, `.env` files, Parquet datasets, DuckDB files, and generated artifacts out of commits. Transfer required data and secrets between machines separately and securely.

## License

QuantOS is distributed under the [repository license](LICENSE).
