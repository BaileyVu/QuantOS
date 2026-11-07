# Shadow counterfactuals

Every post-arbitration proposal is recorded once in `shadow_<profile>.db` with
its actual execution decision reason. Taken proposals preserve the approved
order's quantity, leverage, costs and latency. Slot, sentiment and portfolio
rejections are independently evaluated in isolated books at current equity.
The same `PaperEngine` processes quotes, trade prints, funding, channel/trailing
stops, targets and time exits. Virtual positions add their symbols to the public
feed watch list. They never reach the exchange mirror.

Intrinsic invalid data, brackets, unsupported orders, disabled strategies or
unaffordable sizing remain `UNPRICEABLE`, with a recorded detail and no invented
return. Unfilled/expired proposals are separate from closed-trade statistics.
Isolation removes portfolio competition and shared loss breakers; it cannot
promise identical results after real-account risk intervention or exchange fills.
Adaptive profiles reuse their evidence reader with a separate adaptive journal.

The single shadow database stores an atomic SQLite journal image per proposal,
preserving engine state without a second fill/exit implementation. Pending
intents follow normal engine restart cancellation. Closed trades accept late
funding idempotently. A shadow storage failure emits `SHADOW_ERROR`, raises a
critical runtime alert and disables that shadow book until restart; it does not
alter real risk or execution. Treat the resulting observation gap as missing
evidence, not a zero-return period.

Run `python scripts/shadow_report.py --state /path/to/isolated/state --profile pD`.
Reports include strategy totals and decision reasons, n, mean net basis points,
hit rate and a seeded 2,000-resample percentile CI90 (null for n<2). These are
descriptive iid intervals; correlated proposals are not independent L2 evidence.
Taken-trade comparisons list simulated net, actual book net, difference and fees.
The dashboard Strategies tab includes the same shadow summary card.

Acceptance remains UNVERIFIED until a real 24-hour demo observation is complete,
missing-data/error intervals are reviewed and taken trades are compared against
reconciled exchange books within the documented cost tolerance. Synthetic parity
tests are not that acceptance. No current demo state, service or LaunchAgent has
been changed. Bailey alone deploys after a separate no-order >=10-minute soak and
when flat. Source rollback is a revert of the PR5 source commit; retain shadow
databases as evidence and select the previous immutable snapshot for owner rollback.
