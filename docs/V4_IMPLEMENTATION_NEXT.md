# V4 Part2 integration checklist

This is remaining work, not evidence of implementation. Source notes and owner
answers are in IMPLEMENTATION_CHECKPOINT.md and V4_*SOURCE_NOTES.md.

1. Preserve existing reference rules/IDs via compatible package exports; expose
   required library names ema_atr_ref/donchian_atr_ref as canonical manifest names
   with explicit legacy aliases, without rewriting old journals.
2. Add trusted versioned manifests (family/source/timeframe/entry/parameters/
   evidence/exit_model/hold), per-strategy configs, and closed-only Snapshot data
   for1h/4h/1d. Record optional regime tags, never gate entries on them.
3. New strategies: TSMOM30-day return on4h +EMA50 slope, WilderATR14 stop2x,
   no target; Turtle55/20 on4h, WilderATR20 stop2x, no target;1h squeeze BB20/2
   inside KC20/1.5 with explicit smoothing/range semantics, STOP and2R target;
   E17 verified public CMS event<=5min, short25% stop/time4h. E17 metrics remain
   source claims, not reproduced evidence. No killed pattern families.
4. Main owns risk/exit integration: optional target only for trusted manifest
   exit_model trailing/channel/time; costs<=25% raw stop distance; log exemption
   NET_RR_NOT_APPLICABLE or reject COSTS_TOO_HIGH_FOR_STOP. Keep all existing
   sizing/liquidation/filter/breaker checks.72h only trusted4h strategies.
5. Persist current protective stop separately from immutable initial risk/stop.
   Tighten only, journal every change, recover it after restart. TSMOM activation
   +1R then trail2R from best price; Turtle closed4h opposite20-bar extreme;
   both maximum72h. Never silently change active positions when offline exits
   research recommends another policy. Test partial fills/recovery/no lookahead.
6. Public universe: exchangeInfo+24hr quote volume configurable20M USDC floor,
   TRADING perpetuals and spread check; preserve BTC/ETH defaults within safety
   eligibility. Scale polling with caching/bounded workers, maintain active
   symbol feeds when universe changes. E17 symbol mapping must use USDC only,
   log SKIPPED_NO_USDC_PERP, validate authoritative release time/source and
   prior-day/current-entry-minute trading evidence. No keys/live order path.
7. Use existing AdaptiveSizer; pA learns before ranking, highest mu/sqrt(m2),
   then shortest hold/strategy ID; baseline p1/p5 deterministic order unchanged.
   Single-position constraint remains; rejected competitors must stay auditable.
   Strategies list combines manifests and actual pA state/n/mu/current risk.
8. Shared runtime/replay semantics and schema compatibility for new bars/events,
   per-strategy backtest configs and seeded full lifecycle for every eligible
   strategy. Focused tests during implementation, full suites before Part2 PR.

Part1 PR23 must not be merged without the explicit requested approval. Part2 may
be developed against its branch and published as a separate stacked PR if needed.
No new paper observation starts automatically. Final deliverable includes start
commands for p1/p5/pA, strategy evidence table, test counts, PRs and limitations.
