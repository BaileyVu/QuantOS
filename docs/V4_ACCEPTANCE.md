# V4 acceptance evidence

Part1 merged PR23: legacy assets isolated,75 paper +1001 historical tests passed,
clean editable paper-only installation and doctor passed. No paper engine rewrite.

Part2 validation mapping:
- `test_v4_strategies`: all4 new strategy signals/no-signals, closed/future-bar
  rejection, fixed expiry, Turtle exact55 boundary and E17 verified-format event.
- `test_v4_risk_exits`: trusted manifests,72h only4h, nullable target guard,
  cost cap boundary and favorable fill gap, existing fixed-target RR, stop-side
  and liquidation/breaker checks, long/short monotonic trailing, closed channel,
  partial fill/restart and unjournaled-stop recovery rejection.
- `test_v4_market`: USDC perpetual liquidity universe, batched quote validation,
  missing/duplicate/stale rows, announcement age/source/mapping and retry delay.
- `test_v4_scanner`: cache refresh, batched scanning, per-symbol entry eligibility,
  held-symbol exit maintenance/feed subscription and announcement backoff.
- `test_v4_runtime`: actual pA versus p1 order selection, ties/conflicts, closed-only
  descriptive regimes, old and extended snapshot replay decoding.
- `test_v4_cli`: read-only pA listing including historical reference IDs and pause,
  frozen config validation and volume-floor options.
- `quantos demo`: original fault scenarios plus real strategy-generated proposals
  across all6 library strategies, all6 closed for p1/p5, pA probe decisions.
  Synthetic deep-book fixtures provide fills without changing strategy/risk
  parameters. Three pA minimum-size rejections remain honest rejections.
  Every library capture is replayed with identical fills/cash/STOP_UPDATED events.

Financial review fixed favorable-fill cost-ratio bypass and separated initial risk
from mutable protective stops. Entry eligibility never blocks channel maintenance
for held contracts leaving the new-entry universe. No-target checks do not alter
fixed-target strategy rules. Default V4 state differs from preserved canceled V3.

No V4 natural observation, real orders or profitability claim. E17 metrics are
unverified supplied-source claims; missing results/hash are documented in source
notes. New references are unproven; annual tails remain NOT_ESTIMABLE. Publication
still requires final full-suite/clean-install/CI evidence and owner merge approval.
