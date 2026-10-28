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
observed hourly MTM drawdown below 50%, and configured risk no greater than
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
sampling protocol must be finalized in code before either window's baseline
outcomes; no baseline or sensitivity has run yet.

Existing V4 library entries must be listed with their actual evaluation status.
The main comparison cannot invent pre-listing USDC history, delisting events or
90-second book/trade-through evidence. Unsupported full-window entries are
not assigned zero-trade performance or silently replaced with a USDT adaptation.

Completed cells are reused only under an identical experiment claim. Changed
source or input data require a separate diagnostic run with its reason retained,
not an overwrite of prior results. No selection seal exists yet; final review,
baseline implementation and the data/source limitations remain outstanding.
