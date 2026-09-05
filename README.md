# QuantOS

QuantOS V1 is a small, research-driven quantitative trading engine for Binance Spot. Its frozen specification prioritizes reproducibility, capital preservation, and one explainable production strategy.

## Current implementation

Phase 1 — Foundation is implemented. Phase 2A adds provider-independent Market Data dataset identity and deterministic canonical-candle sequence validation. Phase 2B adds Binance Spot historical-kline normalization, safe provider-specific range pagination, orchestration into validated in-memory canonical sequences, and checksum-verified in-memory normalization of individual daily archives. Multi-day acquisition, persistence, trading, evaluation, and live market data are intentionally not implemented yet.

## Specification

[000_READ_FIRST.md](./docs/000_READ_FIRST.md) is the highest-priority V1 Source of Truth. Documents [001](./docs/001_PRODUCT_REQUIREMENTS.md) through [007](./docs/007_VALIDATION_BACKTESTING.md) are coequal frozen specifications; [008](./docs/008_IMPLEMENTATION_GUIDE.md) is subordinate implementation guidance.

## Structure

```text
src/quantos/
  domain/          # Six V1 business ownership areas and canonical contracts
  application/     # Runtime coordination
  infrastructure/  # Configuration and structured logging
  interfaces/      # Local CLI
configs/           # Safe example configuration
tests/             # Unit, integration, and validation tests
```

## Setup and verification

QuantOS requires Python 3.11 or later and has no runtime dependencies for Phase 1.

```bash
python -m pip install -e .
python -m unittest discover -s tests -t . -v
python -m quantos --config configs/default.toml
```

The default configuration starts in paper mode, logs startup and shutdown as JSON, and exits without connecting to Binance or performing any trading action.
