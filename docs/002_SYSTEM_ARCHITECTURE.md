# QuantOS HFT V1 System Architecture

Version: 3.0.0-V1
Status: Authorized

QuantOS remains a local Clean Architecture modular monolith. Business ownership
is separated into Market Data, Feature, Alpha, Risk, Execution, and Evaluation.
HFT-specific packages are authorized inside these boundaries; adapters,
recorders, state stores, configuration, and CLI code remain infrastructure or
interfaces rather than trading authorities.

Market Data owns sequenced event contracts and valid local L2 state. Feature
owns causal microstructure calculations. Alpha owns VAMP fair value,
confirmation, and quote/cancel intent. Risk owns economic admission, inventory
limits, leverage, account-loss breakers, adverse-fill breaker, and stale-data
kill switch. Execution alone owns simulated post-only order lifecycle, queue
position, fills, inventory, accounting, and safety exits. Evaluation owns
latency, fill, PnL, drawdown, attribution, and markout metrics.

Infrastructure implements Binance public REST/WebSocket ingestion, compressed
event recording, durable SQLite paper state, and research export. Application
code orchestrates one event at a time. Domain code imports no network or
persistence client. Production HFT logic contains no LLM call.

The live-paper sequence is:

1. buffer deltas while obtaining a REST snapshot;
2. discard obsolete deltas and bridge the snapshot update ID exactly;
3. apply only contiguous updates and validate the uncrossed book;
4. compute causal features from the completed book event;
5. let Alpha propose or cancel a directional passive quote;
6. let Risk reject or approve bounded exposure;
7. let Execution simulate GTX placement and conservative queue consumption;
8. persist state/events and update Evaluation metrics.

Any missing prerequisite cancels working paper quotes and blocks new exposure
until a fresh valid book exists. The candle runtime is a separate retained
application path and shares no mutable HFT state.
