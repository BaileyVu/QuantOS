"""Deterministic synthetic lifecycle evidence, isolated from market observation."""
from dataclasses import asdict, replace
from pathlib import Path

from .engine import PaperEngine, Faults
from .journal import Journal
from .report import atomic_json, write_reports
from .risk import Policy
from .types import D, Instrument, Quote, Signal, Trade


def run_demo(state: Path):
    state.mkdir(parents=True, exist_ok=False)
    start = 1700000000000
    instrument = Instrument("BTCUSDC", "USDC", "TRADING", D(".01"), D(".001"),
                            D(".001"), D(100), D(5), market_step=D(".001"))
    evidence = []
    for profile, fraction in (("p1", ".01"), ("p5", ".05")):
        j = Journal(state / f"journal_{profile}.db", profile, "synthetic")
        try:
            e = PaperEngine(j, Policy(risk_fraction=D(fraction)))
            e.recover(start); e.connection(True, start)
            for index, scenario in enumerate(("TP", "SL", "TIME_EXIT", "PARTIAL", "EXPIRED", "RECOVERY")):
                now = start + index * 100000
                def q(price="100", at=now):
                    p = D(price)
                    return Quote("BTCUSDC", at, at, p, p+D(".01"), D(100), D(100), p)
                s = Signal("seeded_lifecycle", "1", f"demo-{scenario}", "BTCUSDC", "LONG", now,
                           now+90000, "LIMIT", D(100), D(98), D(106),
                           1 if scenario == "TIME_EXIT" else 3600, f"SYNTHETIC {scenario}")
                identity = e.propose(s, instrument, q(), now)
                assert identity is not None, scenario
                o = next(o for o in j.orders() if o["id"] == identity)
                if scenario == "EXPIRED":
                    e.tick(now+90001)
                else:
                    quantity = D(o["quantity"]) * (150 if scenario == "PARTIAL" else 1000)
                    e.faults.crash_after_fill = scenario == "RECOVERY"
                    try:
                        e.trade(Trade("BTCUSDC", index+1, now+1000, D("99.99"), quantity, True), now+1000)
                    except RuntimeError:
                        if scenario != "RECOVERY": raise
                        e = PaperEngine(j, Policy(risk_fraction=D(fraction)))
                        assert e.recover(now+1500)
                        e.connection(True, now+1500)
                    if scenario == "PARTIAL": e.cancel(identity, now+1500)
                    price = "97" if scenario == "SL" else "100" if scenario == "TIME_EXIT" else "106"
                    e.quote(q(price, now+3000), now+3000)
                    e.quote(q(price, now+4000), now+4000)
                final = next(o for o in j.orders() if o["id"] == identity)
                assert final["state"] == ("EXPIRED" if scenario == "EXPIRED" else "CLOSED"), final
                evidence.append({"profile": profile, "scenario": scenario, "state": final["state"]})
            assert e.recover(start+700000)
        finally: j.close()
    run = {"started_ms": start, "finished_ms": start+700000, "evidence": "synthetic",
           "status": "completed", "scenarios": evidence}
    atomic_json(state / "run.json", run)
    return write_reports(state, run, final=True)
