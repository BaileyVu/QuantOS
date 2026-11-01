"""Re-run the 10-29 crash window on the claude/mainnet-ready code (QuantOS-mainnet-ready/src).

Reuses replay_crash.py's data loading and synthetic quotes unchanged; only the
engine, policy and strategy set differ. Variants (one $50 pL engine each):
  v2        live_library(): tolerant OI rule, USDC mapping, LivePolicy slots + bump
  v2_strict same, but crash_flush uses the original strict OI rule (isolates fix 2)
  v2_1slot  same as v2 but one shared slot and no bump (isolates fix 3)
OI_MODE stays raw: the archive's zero rows are fed in as-is.
"""
from dataclasses import dataclass, replace
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path.home() / "Desktop/PythonProject/QuantOS-mainnet-ready/src"))
import quantos.paper.runtime  # noqa: E402,F401  (cache the new modules before replay_crash imports them)
from quantos.paper.engine import PaperEngine  # noqa: E402
from quantos.paper.journal import Journal  # noqa: E402
from quantos.paper.risk import LivePolicy, LiveR5Policy, Policy  # noqa: E402
from quantos.paper.runtime import dispatch  # noqa: E402
from quantos.paper.strategies import live_library  # noqa: E402
from quantos.paper.strategies.crash_flush_e2 import CrashFlushE2Strategy, MAJORS  # noqa: E402
from quantos.paper.strategies.usdc_mapped import UsdcMapped, usdt_twin  # noqa: E402
from quantos.paper.strategies.candidate_features import ALT_SOURCE_UNIVERSE  # noqa: E402
from quantos.paper.types import D  # noqa: E402

os.environ["OI_MODE"] = "raw"
import replay_crash as rc  # noqa: E402

OUT = rc.ROOT / "out_v2_r5"
rc.MIN_QUOTE_VOLUME = D("5000000")  # the live50 start command's floor


@dataclass(frozen=True)
class OneSlot(LivePolicy):
    """Old behaviour on the new data path: one shared position, no minimum-notional bump."""
    bump_max_risk: Decimal = D("0.000001")
    max_positions = 1
    slots = ()

    def __post_init__(self):
        Policy.__post_init__(self)


def variants():
    lib = list(live_library())
    strict = [s if s.strategy_id != "crash_flush_e2" else UsdcMapped(CrashFlushE2Strategy(), MAJORS) for s in lib]
    return {"v2_r2": (lib, LivePolicy), "v2_r5": (lib, LiveR5Policy)}


def snapshot(symbol, t, held):
    s = rc.library_snapshot(symbol, t, held)
    if s is None:
        return None
    twin, extra = usdt_twin(symbol), {}
    if twin in MAJORS and twin in rc.OI:
        extra["oi_values"] = tuple([v for v in rc.OI[twin] if v.available_ms <= t][-730:])
    if twin in ALT_SOURCE_UNIVERSE:
        extra.update(quarterly_return_cutoff=rc.CAL.threshold, calibration_end_ms=rc.CAL.calibration_end_ms)
    return replace(s, **extra)


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    runs = {}
    for name, (strategies, policy) in variants().items():
        j = Journal(OUT / name / "journal_pL.db", "pL", "replay", initial_balance="50")
        e = PaperEngine(j, policy())
        e.recover(rc.T0)
        runs[name] = (e, strategies)
    funding_cursor, t, started = rc.T0, rc.T0, time.time()
    while t <= rc.T1:
        if t // 60000 > funding_cursor // 60000:
            obs = [f for s in rc.USDC for f in rc.FUND.get(s, ()) if funding_cursor < f.time_ms <= t - 30000]
            if obs:
                for e, _ in runs.values(): dispatch([e], "funding", obs, t)
            funding_cursor = t - 30000
        if (t - rc.SCAN_LAG) % 300000 == 0 and t > rc.T0:
            for e, strategies in runs.values():
                held = {o["signal"]["symbol"] for o in e.active()}
                snaps = [x for s in rc.USDC if (x := snapshot(s, t, held))]
                dispatch([e], "scan", snaps, t, strategies=strategies)
        if t % rc.FEED_STEP == 0:
            for e, _ in runs.values():
                watched = {"BTCUSDC", "ETHUSDC"} | {o["signal"]["symbol"] for o in e.active()}
                records = [("quote", q) for s in sorted(watched) if (q := rc.quote_at(s, t))]
                dispatch([e], "feed", records, t)
                dispatch([e], "tick", {"finish": False}, t)
        t += 1000
        if (t - rc.T0) % 3600000 == 0:
            print(time.strftime("%m-%d %H:%M", time.gmtime(t / 1000)), f"{time.time() - started:.0f}s", flush=True)
    summary = {}
    for name, (e, _) in runs.items():
        with e.journal.transaction():
            a = e.account(rc.T1)
        db = e.journal.db
        kinds = dict(db.execute("SELECT kind, COUNT(*) FROM events GROUP BY kind").fetchall())
        rejects = {}
        for (p,) in db.execute("SELECT payload FROM events WHERE kind='REJECTED_RISK'"):
            r = json.loads(p)["reason"]; rejects[r] = rejects.get(r, 0) + 1
        sigs = {}
        for (p,) in db.execute("SELECT payload FROM events WHERE kind='SIGNAL'"):
            sid = json.loads(p)["strategy_id"]; sigs[sid] = sigs.get(sid, 0) + 1
        trades = [json.loads(p) for (p,) in db.execute("SELECT payload FROM events WHERE kind IN ('FILLED','CLOSED')")]
        opened = [o for o in e.journal.orders() if o["first_fill_ms"]]
        summary[name] = {"equity_marked": str(a.equity), "signals": sigs, "rejects": rejects,
                         "approved": kinds.get("APPROVED", 0), "filled_orders": len(opened),
                         "positions": [{"strategy": o["signal"]["strategy_id"], "symbol": o["signal"]["symbol"],
                                        "qty": o["quantity"], "avg": o["average"], "state": o["state"],
                                        "binding": o["decision"]["binding_constraint"],
                                        "risk": o["decision"]["achievable_risk"],
                                        "close": o.get("close_reason"), "last_mark": o["last_mark"]}
                                       for o in opened]}
        e.journal.close()
    (OUT / "summary_v2.json").write_text(json.dumps(summary, indent=1, sort_keys=True))
    print(json.dumps(summary, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
