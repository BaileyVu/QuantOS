# QOS-06 observation checkpoint

QOS-06 started from clean QOS-05 HEAD `48d61052f29e64a22f1d6964189a76a1961a94cd`, descended from tested implementation `4ba46cc3714278a24163cf023cc6d9fe8833cd4d`. This checkpoint precedes the single bounded public-market observation. The final diagnosis belongs in `QOS06_FINAL_REPORT.md` after capture and exact replay.

The existing QOS-05 capture contains 27,361 target events but only about ten minutes of coverage. Four timing disarms had corrected feed ages above the frozen 1,500 ms limit; their local queue delays were much smaller. Scheduled clock `DEGRADED` is the monitor's expected verification state after recalibration. The reproducible logging defect was that reconnection reported a generic disarm error, obscuring the specific reason. QOS-06 retains that reason and adds bounded timing, calibration, feature-window, and range diagnostics. It does not change Alpha, Risk, cost, or timing thresholds.

Before collection, 81 focused tests and 1,242 full offline tests passed. Saved QOS-04 and QOS-05 captures replayed exactly. Python is native arm64 3.12.14. The observation uses a new $200 state identity and scratch-only artifacts in `artifacts/qos06/observation/`. Public market requests only; no authenticated exchange calls or economic orders.

Observation command (run once after `retail-edge-reset --strategy qos06 --confirm-new-paper-account`):

```sh
.venv/bin/quantos futures retail-edge-paper --strategy qos06 --state-store artifacts/qos06/observation/account.json --capture artifacts/qos06/observation/events.jsonl --duration-seconds 43200
```

The command has an internal 12-hour duration cap and an 8 GiB journal cap. The observation will not be restarted or extended if it fails. The preserved capture must be replayed before any final classification.
