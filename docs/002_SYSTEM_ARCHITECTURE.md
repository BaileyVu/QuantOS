# QuantOS V1 System Architecture

Version: 2.0.0-V1
Status: Authorized

QuantOS is a local Clean Architecture modular monolith with exactly six production modules.

1. Market Data owns canonical completed candles and market-state contracts.
2. Feature Engine owns deterministic causal features.
3. Alpha Engine owns regime, strategy evaluators, signal normalization, conflicts, and LONG/SHORT/HOLD selection.
4. Risk Engine owns approval, risk budget, quantity, leverage bounds, breakers, one-position enforcement, and stop/liquidation safety.
5. Execution Engine owns submission, idempotency, reconciliation, account/position state, protective orders, paper fills, and exits.
6. Evaluation Engine owns replay output, attribution, walk-forward, and Monte Carlo validation.

Domain modules depend only on domain contracts. Application code orchestrates. Infrastructure implements Binance, storage, and configuration ports. Interfaces compose commands. No strategy/Risk code imports Binance clients; nothing outside Execution submits orders; no LLM participates in runtime decisions.

Per completed candle: validate market data; classify/evaluate/select in Alpha; pass reconciled state and exchange rules to Risk; reject or approve bounded quantity/leverage; simulate or submit in Execution; manage/reconcile the protected position; record causal evidence. A missing prerequisite stops the action.
