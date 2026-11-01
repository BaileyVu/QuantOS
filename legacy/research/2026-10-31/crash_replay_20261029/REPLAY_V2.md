# Crash replay V2: 10-29 01:00 → 10-30 05:00 UTC on `claude/mainnet-ready`

Driver: `scripts/replay_v2.py` (imports `QuantOS-mainnet-ready/src`; reuses `replay_crash.py`'s data, synthetic
quotes and fill model unchanged). Raw OI archive (the 10-15 zero rows are fed in as-is). One $50 `pL` engine per
variant, USDC perps, 24h quote-volume floor $5M (live50 start command). Code at commit `e19922f`.

| Variant | crash_flush signals | Approved | Filled | Equity at window end (marked) | Main rejections |
|---|---:|---:|---:|---:|---|
| Original run (REPORT.md, p1 $200, library) | 0 (blind) | – | – | 196.01 (−2.0%) | EXISTING_EXPOSURE |
| **v2** (all fixes) | **13** | 5 | 4 | **49.18 (−1.6%)** | SLOT_FULL_TREND 87, EXISTING_EXPOSURE 82, SLOT_FULL_EVENT 13, SLOT_FULL_CRASH 2 |
| v2_strict (old OI rule, everything else new) | 0 | 5 | 3 | 49.95 (−0.1%) | SLOT_FULL_TREND 139, SLOT_FULL_EVENT 35 |
| v2_1slot (new data path, old 1-slot / no bump) | 13 | 3 | 2 | 49.12 (−1.8%) | EXISTING_EXPOSURE 172, BELOW_EXCHANGE_MINIMUM 17 |

Trades in **v2** (entry time UTC; event trades are sized at the exchange minimum, about 2.6–2.7% risk):

| Entry | Strategy | Symbol | Sizing | Result at 10-30 05:00 |
|---|---|---|---|---|
| 10-29 04:00 | tsmom_4h | 1000PEPEUSDC | 2% risk | stopped 20:33, −0.16 |
| 10-29 10:00 | alt_dip_reversal | BCHUSDC | minimum bump, risk $1.35 | open, ≈ +0.07 |
| 10-29 22:00 | **crash_flush_e2** | **LINKUSDC** | minimum bump, risk $1.32 | open (24h hold), ≈ −0.01 |
| 10-30 00:00 | tsmom_4h | ARBUSDC (short) | 2% risk | open, ≈ −0.69 |

## What each fix changed

1. **Runner reliability** cannot be shown in a replay, because the replay assumes continuous observation. It is
   covered by live smoke runs instead (heartbeat, 60s staleness alerts, watchdog, clean completion).
2. **OI repair rule** (bad rows count as missing; latest value and the one 4h earlier must be valid; at least 90% of
   the 720h window): crash_flush goes from **0 → 13 signals** (LINK, ADA, AVAX, DOGE, SOL, 22:00–01:00). This matches
   the original `OI_MODE=repair` count. The strict rule (v2_strict) stays blind.
3. **Sizing + slots**: with one shared slot and no bump (v2_1slot), only two tsmom trades happen and 17 event signals
   die as BELOW_EXCHANGE_MINIMUM. With the bump (≤3% risk) and reserved slots, event trades are taken.
   - First pass (two *shared* event slots, kept in `out_v2_sharedslots/`): an alt_dip trade opened before the crash
     (BCH 10:00) plus FIL at 22:00 filled both slots, and all 13 crash_flush signals hit SLOT_FULL_EVENT. This led to
     the dedicated crash_flush slot (crash 1 / other event 1 / trend 1, commit e19922f), with which LINK is taken at
     the first crash signal.

## Caveats (read before trusting a number)

- **The dedicated slot was chosen after seeing this event.** It is an in-sample design change, not evidence. Its
  only justification is structural: two slow 24h event rules must not be able to lock out the crash rule.
- One 28-hour window, one crash. The P&L differences (≈ ±$0.80 on $50) are noise and mostly unrealized. The event
  positions are still inside their 24h hold at the window end. **There is no evidence of an edge here.** The run only
  shows that the plumbing now lets the event rules trade.
- The remaining crash signals (ADA, AVAX, DOGE, SOL) were blocked because the single crash slot was held by LINK, or
  because the 3-position cap was full. ETH/BTC-sized minimums ($20/$100) stay untradable with a 25% stop on $50.
- Fill model, synthetic quotes and constant top-of-book size are unchanged from REPORT.md §5.
