# Actual public shadow capture

- Public mainnet feed worked: True.
- 23781 raw messages, 45630 canonical events.
- Event counts: `{"delta":44958,"disconnect":1,"market":1,"snapshot":448,"status":1,"trade":221}`.
- Run: `6012ae36a15ce45c28aa`; virtual orders: 0; virtual PnL: $0.
- Capture: `/Users/bailey12/Desktop/PythonProject/QuantOS-PM/artifacts/captures/shadow-1792343095793928000`.
- Immutable Parquet SHA-256: `78487fd2d63fb4d9ddc31e3ea24df5ba201ab19fb853c35fdf918fa58f170d3f`.
- Integrity/lifecycle stops: 110; first reasons: `[{"reason":"invalid_event: empty or crossed book","time":1792343102733},{"reason":"invalid_event: empty or crossed book","time":1792343102733},{"reason":"invalid_event: book sequence gap or missing snapshot","time":1792343102734}]`.

Transport success is separate from book/execution validity. This stream failed eligibility and continuity checks, and the strategy remained halted. No live fill or strategy success is claimed. Snapshots and deltas were preserved without silent repairs.

Command actually executed:
```bash
.venv/bin/python -m quantos_pm shadow --config configs/shadow_200.toml --seconds 30
```

- bounded single-market session; rerun for next window
- no independently validated live calibration or settlement-rule approval
- public book stream lacks exchange sequence numbers; quotes fail closed
- public trade aggressor/queue attribution unverified; no awarded live fills
