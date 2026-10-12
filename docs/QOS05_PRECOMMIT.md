# QOS05 fixed hypothesis and qualification contract

Base: clean QOS04 delivery `8396eadf8a6e5047221e88ba5e58ed77d1f87e08`;
implementation `276e5708df614219c030829663a1e5d485cc1bca` unchanged beneath docs.
Owner QOS05 mandate authorizes this separate paper-only candidate and 5–30 minute
horizon. This addendum supersedes QOS03/QOS04 horizons only for Q05-LR-v1;
HFT and closed Q04 candidates remain unchanged. No authenticated authority.

## Frozen Q05-LR-v1 (before outcome replay)

One symmetric BTCUSDC/BTCUSDT futures liquidity-reclaim hypothesis, no fallback.
Use prior 300 seconds of causal mid observations (at most one latest observation
per monotonic second), excluding the current observation. Require continuous
fresh depth (gap <=1.5 seconds), a full 300-second window and prior range >=60bps.
A displacement exceeds the prior low/high by >=max(10bps, twice current realized
volatility). Freeze that range boundary and its midpoint as the target. Reject
spread >10bps or event volatility >20bps. Never use a future local minimum.

A setup lasts at most 120 seconds. Require >=5 seconds since displacement and
>=3 seconds without a new adverse extreme. Price must actually reclaim >=2bps
inside the frozen boundary, remain on the profitable side of the midpoint, and
have signed five-second trade flow and best-level OBI >=0.15 in reversal direction.
The expected opportunity is only the observed distance to the frozen midpoint;
subtract max(5bps, half that distance) as explicit uncalibrated uncertainty.
Full existing Risk costs must still be cleared strictly. A small VAMP move is
irrelevant. One stable signal ID per displacement, intent lifetime 2 seconds,
300-second cooldown after confirmation or setup expiry. No repeated entries from
a single reclaim. Pending fills must reconfirm the same original signal.

Exit on frozen midpoint target, loss of reclaimed boundary, existing 20bp adverse
stop, opposite signal, data/risk failure or 900-second maximum holding horizon.
Safety exits can occur earlier than five minutes. A new adverse extreme resets
failure-to-continue time, not the setup expiry. One position, no averaging down.
Unchanged $200 initial capital, <=1x exposure, <=1% modeled risk, 3% daily loss
and drawdown stops, fees >=4bps BTCUSDC/5bps BTCUSDT per taker leg, slippage >=1bp
per leg, latency, funding reserve and 2bp stop uncertainty. All fills use later
acknowledged fresh displayed liquidity; target/stop touches alone never fill.

## Single bounded campaign

Development: immutable QOS04 `artifacts/qos04/final/events.jsonl` (operator-killed
qualification, usable terminal capture). Confirmation: immutable later QOS04
`artifacts/qos04/final-verified/events.jsonl`, reserved for one Q05 outcome pass.
Only input metadata/integrity may be inspected before freezing implementation;
no candidate-outcome-driven parameter changes. These short captures are genuine
but inadequate for a broad regime/15-minute holding sample. No candle-to-L2 or
synthetic order-flow substitution; no large acquisition or historical campaign.
Fresh $200 virtual account per period; do not reuse or reset original accounts.
Counterfactual Q05 processing/submission uses the latest source clock sample plus
100ms extra processing allowance, recorded explicitly. Replays retain original
availability order, depth gaps, funding observations and failure controls.

One fixed stress per period: 1.5x taker fees, 3bps slippage/leg, 300ms acknowledgment.
Record every Alpha decision and rejection with source command/book identity.
An open terminal position is censored, never force-closed at an invented price.
Minimum 30 genuine fully closed trades **in each period** before inferential
claims/seeded Monte Carlo; fewer => INSUFFICIENT_EVIDENCE and NO_GO for promotion.
If sufficient: existing stationary bootstrap, seed 505, 1000 simulations, mean
block length 5, empirical 95% ranges; no future distribution guarantee. Independent
confirmation must be net positive and stable under fixed cost stress. No tuning,
substitutions or additional qualification periods after observing outcomes.
Final-code public smoke: 600 seconds, fresh QOS05 account and exclusive capture;
technical integration only, never economic confirmation.

## Targeted reference principles (no code copied or installed)

[Edge Stats architecture](https://github.com/LuxAlgo/edge-stats/blob/main/ARCHITECTURE.md):
sample counts, uncertainty and traceable inputs accompany statistics.
[Trade Journal](https://github.com/LuxAlgo/trade-journal): attributed trade economics;
reuse QuantOS signal/order/fill IDs and durable ledger.
[Prop Firm Sim disclaimer](https://github.com/LuxAlgo/prop-firm-sim/blob/main/DISCLAIMER.md):
seeded simulations state assumptions and are not predictions.
[Market Trackers data contract](https://github.com/LuxAlgo/market-trackers/blob/main/docs/market-trackers-data.md):
provenance and freshness remain explicit. These are reference principles, not
specification authority. No external application, dependency or license-bearing
code was imported; reviewed repositories identify MIT code licensing.

## Source identity freeze (metadata only, before Q05 outcome evaluation)

- development: `artifacts/qos04/final/events.jsonl`; SHA256 `105532ec129921a648ad436822341c653001a9a7b3c261fa177e77d541813320`; UTC `2026-10-11T12:02:24.519715+00:00` through `2026-10-11T12:13:45.953425+00:00`; 27,642 target events over 681.43371 seconds; hash-chain and terminal record verified.
- confirmation: `artifacts/qos04/final-verified/events.jsonl`; SHA256 `69474455875ded14799b643066a23f967f6d866194dbbd5c2a1594e5a9aac8fd`; UTC `2026-10-11T18:12:12.604741+00:00` through `2026-10-11T18:27:11.060872+00:00`; 49,842 target events over 898.456131 seconds; hash-chain and terminal record verified.
