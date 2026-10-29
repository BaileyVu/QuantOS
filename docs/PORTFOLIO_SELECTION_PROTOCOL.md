# Training selection protocol

Fixed before any account-performance evaluation. Training is 2022–2024 with
2021 Q4 indicator warmup. No 2025+ result is available to this selector.

Stage one has 147 individual primary cells:

- Crash flush: three holds (24/48/72h), five risk fractions (1/2/4/6/8%), three
  leverage ceilings (1/2/3). Catastrophe stop 25%.
- ALT dip: two quantiles (0.5/1%), three holds (12/24/72h), the same five risk
  fractions and three leverage ceilings. Catastrophe stop 25%.
- EMA level: fixed requested 12/26 rule and exits, four risk fractions
  (0.5/1/2/3%), three leverage ceilings (1/2/3).

A selection-eligible cell must have positive net training account growth,
observed hourly MTM drawdown and the conservative adverse-bar stress envelope
both below 50%, and configured risk no greater than
half its empirical training Kelly estimate. Undefined/no-loss Kelly estimates
are insufficient to establish eligibility. These estimates are conditional on
the observed sample; there is no claim of a reliable population Kelly value.

Rank eligible cells by highest training CAGR, then lower drawdown, lower risk,
lower leverage ceiling, then canonical cell identifier. For each strategy,
retain its best eligible setting. If none is eligible, retain its best completed
setting for diagnostic combinations only; do not make it eligible by combining
it with another strategy.

Stage two uses those training-selected individual holds, quantiles and risk
fractions in all subsets of two or three candidates. It sweeps the common
leverage ceiling over 1/2/3, giving at most twelve additional cells. Portfolio
caps are fixed as documented in the execution protocol. Combined eligibility
requires both its own account-level conditions and individually eligible
members. This is a staged search, not an exhaustive search over every cross
product of individual settings. The limitation must remain in the report.

The final training pick is the highest-ranked eligible individual or combined
cell. If none qualifies, cash is the frozen candidate; no trading configuration
is justified by training. The saved selection is not automatically promoted to
paper. All source/data/filter/cache hashes and the final evaluation grid must be
sealed before the single held-out wave.

The full primary grid and derived combination grid are evaluated once on 2025+
for the requested exploratory comparison. The test-window winner cannot replace
the training pick. Fifteen-percent catastrophe stops, entry delayed one bar,
doubled slippage, volume-cohort robustness, crash-only and matched-random
baselines remain separately labelled diagnostics, not selection options.
Matched randomness uses seed 28102026 and compares eligible crash-price bars
within the same symbol/calendar month without using subsequent returns. The
sampling protocol is implemented in `quantos.paper.research.baselines` before either
window's baseline outcomes; no baseline or sensitivity has run yet.

Existing V4 library entries must be listed with their actual evaluation status.
The main comparison cannot invent pre-listing USDC history, delisting events or
90-second book/trade-through evidence. Unsupported full-window entries are
not assigned zero-trade performance or silently replaced with a USDT adaptation.

Completed cells are reused only under an identical experiment claim. Changed
source or input data require a separate diagnostic run with its reason retained,
not an overwrite of prior results. No selection seal exists yet; final review,
baseline implementation and the data/source limitations remain outstanding.

## Crash baselines (frozen before outcomes)

The crash-price-only baseline uses the candidate's exact closed-hourly price
predicate: negative four-close change and a close at least 8% below the trailing
24-bar high. It drops only the open-interest gate. It keeps the 25% catastrophe
stop and 24/48/72-hour holds. Signals retain the trusted `crash_flush_e2`
strategy identity for the existing risk path and carry a distinct baseline
identity in their signal IDs and reason. No exposure or cooldown selection is
performed in baseline generation; execution belongs to the shared engine.

The matched-random control samples without replacement from those same eligible
crash-price bars. It matches the actual candidate event count per symbol and UTC
calendar month using fixed seed 28102026. The sampler accepts only counts and
as-of snapshots, never subsequent prices or returns, and fails closed if a
stratum has too few eligible bars.

The one-hour delay sensitivity retains each original decision as a separate
record and emits a new intent only at the next completed hourly boundary. It
does not extend the original 90-second intent expiry. The hold timer runs from
the new fill. Crash stops are 25% below delayed entry; EMA zone sensitivities
retain the original structural stop and place the 1.5R target from delayed
entry when the geometry remains valid. These helpers generate signals only;
they do not change risk, accounting, exposure, cooldown, or execution rules.

## Held-out execution and unavailable-cohort rule

Before opening held-out data, freeze the 2022–2024 selection, all 159 exploratory
cells, the diagnostic job list, both exact 29-symbol cohorts, current filter
snapshots and runtime source hash. The evaluation is 2025-01-01 through
2026-10-28 UTC inclusive, with Q4 2024 warmup. A completed result is immutable;
resume verifies its checksum and never resimulates it. The held-out sweep winner
is labelled exploratory and cannot replace the training-selected candidate.

The current volume-cohort filter snapshot contains UXLINK in SETTLING status.
Under this study's existing current-filter proxy rule, its full frozen cohort
cannot be executed faithfully. Seal the exact symbol/status/filter-hash evidence
as NOT_EVALUABLE, retain the requested rows without numerical performance, and
continue valid primary-cohort evaluation. Do not drop or replace the contract,
change its status, or pretend unavailable performance is zero. Historical data
acquisition still records that cohort's availability for the report; this rule
is determined without held-out returns.

Known missing trade/mark rows require checksummed daily-source repairs. Leading
and trailing coverage count as missing history too. An overlay must bind to its
original manifest/audit and supply exactly requested timestamps without
conflicting existing rows. Unexplained funding gaps are not zero costs or
invented funding-interval changes. No tuning is permitted after these checks.
