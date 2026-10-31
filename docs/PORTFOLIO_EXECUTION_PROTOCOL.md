# Portfolio evaluation execution protocol

Status: implementation and data validation in progress. No outcome selection or
2025+ evaluation has run. This document fixes modeling choices before outcomes;
it does not itself constitute the final code/data/selection seal.

## Data and causal decisions

Primary universe: the owner's original 29-symbol survivor cohort. The supplied
fetcher used Spot, whereas this experiment uses USD-M perpetuals. Historical
performance therefore cannot be called an exact reproduction. The second
universe is nine specified majors plus twenty other eligible contracts ranked
by December 2024 quote turnover. That second row uses information unavailable
at the beginning of training and is a robustness comparison, not a causal
2022 universe-selection backtest.

Training is 2022-01-01 through 2024-12-31, with October–December 2021 warmup.
Selection, holdings and sizing use training only. The immutable selection must
precede the single 2025+ evaluation wave. The frozen pick is the recommendation;
the best test-window sweep cell is exploratory. Prior research already viewed
2025–2026 for some candidates, so this window is not fully untouched.

Archives are checksum verified. Missing bars are never synthesized. Monthly
archive gaps may be repaired only with independently checksummed exchange
source rows, retaining the original archives and an explicit repair overlay.
An occupied position with a missing trade/mark hour invalidates its simulation
cell; missing funding is never treated as a zero rate. Missing source features
prevent signals. Data defects must be resolved or reported before selection.

OI labels remain unchanged. Exact UTC hourly samples are selected from the raw
metrics; publication is conservatively modeled at label+1h, not asserted to be
an actual release time. The crash candidate uses the latest available hourly
measurement, at most one hour old, and records the measurement lag. This makes
the archive evaluation an explicit lagged-OI adaptation. It must not claim a
reproduction of a contemporaneous-OI result. Missing exact hours remain gaps.
ALT calibration pools coin-hour 24h returns across the full named cohort in
[quarter start−90 days, quarter start−3 days), then holds the quantile fixed.
Incomplete calibration fails closed and must be counted in the report.

## Hourly execution scenario

The research simulator calls the same `risk.evaluate` authority as the paper
engine. It keeps a single $200 cash account, fees, funding, unrealized mark PnL,
initial risk reservations, symbol cooldowns and aggregate exposure. It is a
separate, declared hourly execution scenario: it cannot reconstruct real fills
or demonstrate live/paper runtime equivalence from candles alone.

Only the three MARKET-entry candidates are implemented in this simulator.
Existing LIMIT/STOP-entry strategies require evidence within their original
90-second TTL and are not assumed filled merely because an hourly extreme
crossed their limit. Existing library strategies must be reported separately
as not evaluable where the required historical venue/events/entry evidence is
absent. No USDT/USDC history splicing is allowed.

- A closed-bar decision may enter at the next hour's open. There is no future
  close fill. Signals expire normally; delay sensitivity must explicitly make a
  new delayed decision, never silently extend a production signal's TTL.
- A zero-spread synthetic quote is used for risk evaluation. Full policy
  slippage (minimum 15 basis points) is charged adversely at entry and exit;
  taker fees are 5 basis points per side. These are explicit assumptions, not
  measured spread or order-book impact. Quantity may consume at most 1% of the
  preceding completed hour's base volume; this is only a liquidity proxy.
- The fill must still fit the original approved risk, portfolio margin/gross
  caps, stop/target bracket, liquidation buffer and cost-vs-risk/net-RR gate.
  The retained twice-stop liquidation buffer normally caps a 25% stop at 1x;
  the requested leverage sweep is a ceiling, not guaranteed used leverage.
- Gap exits use the actual observed opening trade price with adverse slippage.
  Within a bar, a mark stop or approximate liquidation threshold takes priority
  over a target. Its exit uses the adverse trade-bar extreme plus slippage, a
  deliberately pessimistic hourly uncertainty scenario. A target, when no
  stop/liquidation trigger occurred, exits at its level with adverse slippage.
- Actual funding events are charged at their recorded timestamps. Because the
  archive does not include the event's exact mark, the most recent hourly mark
  open is a causal proxy. Settlement events within one second of a boundary apply to positions held
  before exits/new entries, modeling execution after settlement; original event
  timestamps remain unchanged. Cadence validation allows at most one second
  of jitter and the old/new reported interval at a schedule transition. Other
  later funding within the hour applies before that hour's modeled protection
  exits. This ordering is an assumption and is retained in the event ledger.
- Held positions exit at their time deadline, or at the final observation close
  with costs. Alphabetical strategy/symbol/ID ordering resolves simultaneous
  competing signals reproducibly; it is not selected after viewing outcomes.
- The existing 10% daily-loss and 50% drawdown thresholds pause entries and
  close remaining positions. Pauses persist; midnight does not silently resume
  a failed account. Gaps can breach these thresholds. The historical scenario
  checks opening/closing MTM; it does not claim tick-level breaker accuracy.
- Hourly MTM equity enters standard drawdown statistics. Simultaneous adverse
  mark extremes are also retained as a separate stress envelope, not presented
  as an observed synchronized account path.

Current public exchangeInfo filters are snapshotted and explicitly designated
as a **current-rule historical proxy**. Tick size, lot size and minimum notional
can differ from historical rules, materially affecting a $200 account. They
must not be described as point-in-time filters or used to infer a delisted
contract never existed. Missing filters are a named rejection, not a guessed
minimum. Any resulting recommendation is conditional on these limitations.

## Comparison and reporting constraints

The 25% catastrophe stop is the primary sizing boundary for crash/ALT; 15% is a
sensitivity only. Their risk grid is 1/2/4/6/8%; EMA/library risk grid is
0.5/1/2/3%, each with leverage ceilings 1/2/3. Hold/quantile grids remain the
owner-approved ones. Training Kelly estimates are descriptive and any chosen
risk must be no more than half the estimate; undefined/no-loss estimates cannot
justify a higher risk. Portfolio aggregation retains the shared risk authority. For a mixed cell,
the cap base is frozen before simulation to the largest configured strategy
risk (or an explicitly higher existing policy cap), at most 8%. Same-direction
and total reserved initial risk cannot exceed twice/three times that base
respectively, capped at 16%/24% of current equity. Each entry still has its own
strategy risk budget. The cap never changes with incoming signal order; costs
and immediate MTM are included in the fill-time recheck.

All recommendations require completed training selection, sealed provenance,
actual held-out results and reconciliation. No recommendation can be inferred
from source claims, a synthetic lifecycle, or passing accounting tests. The
report must include rejections, data exclusions, funding/cost assumptions,
catastrophe stop count/cost, MTM drawdown, calendar-window returns, concentration,
crash-only/random baselines, delayed-entry/double-slippage sensitivities and
conditional 1,000-draw Monte Carlo results. No new paper process is launched.
