"""Adaptive risk profile pA: edge-tracked sizing, drawdown throttle, edge-decay states, breaker referee.

Every closed trade of the evidence profile (p1, which takes every approved signal at 1%) is converted to R
(net PnL / planned risk) and updates a per-strategy edge estimate. pA sizes from that estimate, so a
strategy pA has stopped trading keeps being measured ("shadow" learning) without pA risking money on it.
Statistics are floats; the resulting risk fraction is returned as a Decimal for the unchanged risk engine.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math

from .journal import Journal
from .report import trade_result
from .types import D

ACTIVE, PROBATION, SHADOW = "ACTIVE", "PROBATION", "SHADOW"
STATE_MULT = {ACTIVE: 1.0, PROBATION: 0.5, SHADOW: 0.0}


@dataclass
class Edge:
    """Shrunk expectancy in R plus a one-sided CUSUM alarm against the expected edge."""
    prior_mu: float = 0.0
    prior_sd: float = 1.3
    prior_n: float = 20.0
    state: str = ACTIVE
    n: int = 0
    s1: float = 0.0
    s2: float = 0.0
    cusum: float = 0.0
    alarms: int = 0
    recent: list = field(default_factory=list)

    def mu(self) -> float:
        return (self.prior_n * self.prior_mu + self.s1) / (self.prior_n + self.n)

    def m2(self) -> float:
        return (self.prior_n * (self.prior_sd ** 2 + self.prior_mu ** 2) + self.s2) / (self.prior_n + self.n)

    def update(self, r: float, k: float = 0.30, h: float = 8.0, recover_n: int = 20) -> None:
        self.n += 1
        self.s1 += r
        self.s2 += r * r
        self.recent = (self.recent + [r])[-recover_n:]
        if self.state == SHADOW:
            if len(self.recent) >= recover_n and lcb(self.recent, 0.5) > 0:
                self.state, self.cusum = PROBATION, 0.0
            return
        self.cusum = max(0.0, self.cusum + (max(self.prior_mu, 0.0) - r) - k)
        if self.cusum > h:
            self.alarms += 1
            self.cusum = 0.0
            self.state = PROBATION if self.state == ACTIVE else SHADOW
        elif self.state == PROBATION and len(self.recent) >= recover_n and sum(self.recent) / len(self.recent) > 0:
            self.state = ACTIVE


def lcb(rs, z: float) -> float:
    n = len(rs)
    if n < 3:
        return -1.0
    m = sum(rs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in rs) / (n - 1))
    return m - z * sd / math.sqrt(n)


def dd_multiplier(dd: float, soft: float = 0.05, hard: float = 0.20, floor: float = 0.25) -> float:
    """Full size to `soft` drawdown, linear down to `floor` at `hard`; the existing 25% breaker pauses beyond."""
    if dd <= soft:
        return 1.0
    if dd >= hard:
        return floor
    return 1.0 - (1.0 - floor) * (dd - soft) / (hard - soft)


@dataclass(frozen=True)
class AdaptiveConfig:
    risk_cap: float = 0.05          # bailey's rule: max 5% of equity at risk
    kelly_fraction: float = 0.25    # quarter Kelly (beat 1/3 and 1/2 Kelly in simulation)
    learn_trades: int = 30          # below this many evidence trades: fixed probe risk only
    probe_risk: float = 0.0025
    min_risk: float = 0.001         # below this the trade is not worth fees: skip


class AdaptiveSizer:
    def __init__(self, journal: Journal, evidence: Journal, config: AdaptiveConfig = AdaptiveConfig(),
                 priors: dict[str, dict] | None = None):
        self.journal, self.evidence, self.config = journal, evidence, config
        self.priors = priors or {}

    def edge(self, strategy_id: str) -> Edge:
        raw = self.journal.get(f"edge:{strategy_id}")
        return Edge(**raw) if raw else Edge(**self.priors.get(strategy_id, {}))

    def learn(self, now_ms: int) -> None:
        """Fold newly closed evidence trades into the edge estimates. Call inside a pA transaction."""
        seen = set(self.journal.get("edge_seen", []))
        closed = sorted((o for o in self.evidence.orders() if o["state"] == "CLOSED" and o["id"] not in seen),
                        key=lambda o: o["closed_ms"])
        for o in closed:
            net, quantity, planned = trade_result(self.evidence, o)
            seen.add(o["id"])
            if quantity <= 0 or planned <= 0:
                continue
            sid = o["signal"]["strategy_id"]
            e = self.edge(sid)
            before = e.state
            e.update(float(net / planned))
            self.journal.set(f"edge:{sid}", asdict(e))
            if e.state != before:
                self.journal.event(now_ms, "EDGE_STATE", {"strategy": sid, "from": before, "to": e.state,
                                                          "mu_R": round(e.mu(), 4), "n": e.n})
        if closed:
            self.journal.set("edge_seen", sorted(seen))

    def risk_fraction(self, strategy_id: str, equity, high_water) -> tuple[D, dict]:
        c, e = self.config, self.edge(strategy_id)
        dd = float(1 - equity / high_water) if high_water > 0 else 0.0
        ddm = dd_multiplier(dd)
        if e.n < c.learn_trades:
            base, mode = c.probe_risk, "LEARNING"
        else:
            base, mode = min(c.risk_cap, max(0.0, c.kelly_fraction * e.mu() / e.m2())), "KELLY"
        risk = base * ddm * STATE_MULT[e.state]
        detail = {"strategy": strategy_id, "mode": mode, "state": e.state, "n": e.n, "mu_R": round(e.mu(), 4),
                  "base_risk": round(base, 5), "dd": round(dd, 4), "dd_mult": round(ddm, 3), "risk": round(risk, 5)}
        if e.state == SHADOW:
            return D(0), {**detail, "reason": "EDGE_SHADOW_ONLY"}
        if risk < c.min_risk:
            return D(0), {**detail, "reason": "NO_EDGE" if e.mu() <= 0 else "RISK_TOO_SMALL"}
        return D(str(round(risk, 6))), {**detail, "reason": "OK"}

    def referee(self, paused_since_ms: int, errors: int) -> tuple[str, list[str]]:
        """Decide whether a pA loss-breaker pause may lift by itself.

        AUTO_RESUME only if: no errors since the pause, the evidence profile closed >= 20 trades since the
        pause with a positive pessimistic mean (the losing streak looks like bad luck that has passed),
        and pA has not auto-resumed in the last 7 days. Otherwise NEED_HUMAN or WAIT.
        """
        why = []
        if errors:
            why.append(f"{errors} errors since the pause")
        last = self.journal.get("auto_resumed_ms")
        if last is not None and paused_since_ms - last < 7 * 86400000:
            why.append("already auto-resumed within 7 days")
        if why:
            return "NEED_HUMAN", why
        rs = []
        for o in self.evidence.orders():
            if o["state"] == "CLOSED" and o["closed_ms"] >= paused_since_ms:
                net, quantity, planned = trade_result(self.evidence, o)
                if planned > 0:
                    rs.append(float(net / planned))
        if len(rs) < 20 or lcb(rs[-20:], 0.5) <= 0:
            return "WAIT", [f"{len(rs)} evidence trades since pause, not yet recovered"]
        return "AUTO_RESUME", ["evidence trades recovered after the pause"]
