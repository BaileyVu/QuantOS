# QuantOS

**AI-native quantitative trading system.**

QuantOS is a modular quantitative trading platform built to take a trading idea from **market data → features → alpha → risk → execution**.

The project focuses on simplicity, deterministic engineering, rigorous validation, and controlled execution.

---

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
Execution
     ↓
Exchange
```

Supporting the core pipeline:

```text
Data
Backtesting
Validation
Portfolio
Storage
Monitoring
```

The system is designed as a **modular monolith** so that the entire platform remains understandable and maintainable without unnecessary infrastructure.

---

## Core Principles

* **Simplicity over sophistication**
* **Deterministic and reproducible**
* **Capital preservation first**
* **Research and production use the same core definitions**
* **Risk is independent from alpha**
* **Fail safely when system state is uncertain**
* **Build only what provides real value**

QuantOS is intentionally not designed to be a collection of trading strategies or a generic trading-bot framework.

---

## Documentation

The engineering specifications live in [`docs/`](./docs):

| Document                                                                | Description                                   |
| ----------------------------------------------------------------------- | --------------------------------------------- |
| [`000_READ_FIRST.md`](./docs/000_READ_FIRST.md)                         | Engineering philosophy and project principles |
| [`001_PRODUCT_REQUIREMENTS.md`](./docs/001_PRODUCT_REQUIREMENTS.md)     | Product requirements                          |
| [`002_SYSTEM_ARCHITECTURE.md`](./docs/002_SYSTEM_ARCHITECTURE.md)       | System architecture                           |
| [`003_DATA_ARCHITECTURE.md`](./docs/003_DATA_ARCHITECTURE.md)           | Data architecture                             |
| [`004_FEATURE_ENGINE.md`](./docs/004_FEATURE_ENGINE.md)                 | Feature engine                                |
| [`005_ALPHA_ENGINE.md`](./docs/005_ALPHA_ENGINE.md)                     | Alpha engine                                  |
| [`006_RISK_EXECUTION.md`](./docs/006_RISK_EXECUTION.md)                 | Risk and execution                            |
| [`007_VALIDATION_BACKTESTING.md`](./docs/007_VALIDATION_BACKTESTING.md) | Validation and backtesting                    |
| [`008_IMPLEMENTATION_GUIDE.md`](./docs/008_IMPLEMENTATION_GUIDE.md)     | Implementation guide                          |

**Start here:** [`000_READ_FIRST.md`](./docs/000_READ_FIRST.md)

---

## Project Structure

```text
QuantOS/
├── docs/          # Engineering specifications
├── src/           # QuantOS implementation
├── tests/         # Tests
├── configs/       # Configuration
├── scripts/       # Utility scripts
└── README.md
```

---

## Philosophy

QuantOS is not trying to compete on complexity.

The goal is simple:

> **Build a small, reliable system that can discover, validate, and execute a real trading edge.**

Everything else is secondary.

