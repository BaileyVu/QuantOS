# QuantOS V4 Architecture

```
 Binance public feeds (WS/REST, announcements)
        |  FeedMonitor.note() per feed
        v
 scanner thread (universe, quote-volume floor) --> strategies library
        |                                              |
        v                                              v
                 signals --> arbitration / slots (arbitrate, adaptive sizer)
                                   |
                                   v
                          risk policy (Policy, Account limits, kill switch)
                                   |  APPROVED / REJECTED_RISK
                                   v
        paper engine (PaperEngine per profile p1/p5/pA) --> order layer
                                   |            src/quantos/live (planned):
                                   |            modes paper | dry-run | testnet | mainnet
                                   v
                       journal (SQLite WAL, events/orders/fills)
                                   |
        +--------------------------+---------------------------+
        v                          v                           v
 dashboard/status.json     alerts.jsonl + stderr         heartbeat.json
 (build_status/write)      + macOS notification          (Heartbeat.beat)
                           (Alerter, dedup+recovered)         |
                                                              v
                                       launchd watchdog (every 60 s) -> Alerter
```

## Components
- **Feeds**: market data threads push into a bounded queue; overflow requests shutdown. `FeedMonitor` flags feeds silent beyond 60 s, including expected feeds never seen after a startup grace.
- **Scanner / strategies**: filter instruments by quote volume, emit signals (symbol, side, strategy_id, entry, stop_loss, take_profit).
- **Arbitration / slots**: ranks competing signals, limits concurrent positions, optional adaptive sizing.
- **Risk**: sizing by risk fraction, exposure/margin/drawdown/day-loss limits, reconcile and pause gates, kill switch.
- **Engine / order layer**: the paper engine simulates fills against quotes; the live layer (modes paper, dry-run, testnet, mainnet) is the single path to real orders. The journal is the execution authority.
- **Journal**: durable events (SIGNAL, APPROVED, REJECTED_RISK, FILLED, CLOSED), orders, fills, funding.
- **Observability**: `dashboard/status.json` (atomic, Decimals as strings); `alerts.jsonl` with cooldown dedup and recovery records; `heartbeat.json` written by the runtime loop.
- **Supervision**: launchd `com.quantos.v3.paper` restarts on non-zero exit or crash (throttle 30 s) and delivers SIGTERM for graceful close; runtime keeps `caffeinate -i -s -w <pid>` to prevent sleep. `com.quantos.v3.watchdog` runs `python -m quantos.paper.health watchdog --state DIR` each minute; it alerts and exits 1 if the heartbeat is stale/missing while `run.json` status is `running`.
