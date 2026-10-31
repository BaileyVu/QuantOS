from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from .fees import FeeMetadata, taker_fee


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY, kind TEXT NOT NULL, ts_ns INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fills(fill_id TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, instrument_id TEXT NOT NULL,
 slug TEXT NOT NULL, outcome TEXT NOT NULL, shares TEXT NOT NULL, price TEXT NOT NULL, fee TEXT NOT NULL,
 cash_debit TEXT NOT NULL, ts_ns INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS settlements(settlement_id TEXT PRIMARY KEY, fill_id TEXT NOT NULL UNIQUE,
 official_outcome TEXT NOT NULL, payout TEXT NOT NULL, ts_ns INTEGER NOT NULL, evidence_url TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS market_evaluations(slug TEXT PRIMARY KEY, decision TEXT NOT NULL, reason TEXT,
 seconds_left INTEGER NOT NULL, ts_ns INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shadow_evaluations(shadow_id TEXT PRIMARY KEY, slug TEXT NOT NULL,
 horizon_seconds INTEGER NOT NULL, policy TEXT NOT NULL, decision TEXT NOT NULL, reason TEXT,
 selected TEXT, shares TEXT, price TEXT, fee TEXT, cash_debit TEXT, prob_up TEXT, net_edge TEXT,
 pair_cost TEXT, ts_ns INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(slug,horizon_seconds,policy));
CREATE TABLE IF NOT EXISTS shadow_settlements(shadow_id TEXT PRIMARY KEY, official_outcome TEXT NOT NULL,
 win INTEGER, hypothetical_pnl TEXT, ts_ns INTEGER NOT NULL, evidence_url TEXT NOT NULL);
"""


@dataclass(frozen=True)
class Portfolio:
    cash: Decimal
    open_cost: Decimal
    open_payout_if_won: Decimal
    realized_pnl: Decimal
    equity_at_cost: Decimal
    unresolved: int


class Ledger:
    def __init__(self, path: Path | str, *, experiment: str, initial_cash: Decimal | str = "200") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)
        with self.transaction():
            existing = dict(self.db.execute("SELECT key,value FROM meta"))
            wanted = {"experiment": experiment, "initial_cash": str(Decimal(str(initial_cash)))}
            if existing and any(existing.get(k) != v for k, v in wanted.items()):
                raise ValueError("ledger identity/initial cash mismatch; use a new experiment name")
            for key, value in wanted.items():
                self.db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)", (key, value))

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            self.db.execute("BEGIN IMMEDIATE")
            yield
        except Exception:
            self.db.rollback()
            raise
        else:
            self.db.commit()

    def close(self) -> None:
        self.db.close()

    def record_fill(self, *, fill_id: str, event_id: str, instrument_id: str, slug: str,
                    outcome: str, shares: Decimal | str, price: Decimal | str,
                    fee_metadata: FeeMetadata, ts_ns: int) -> bool:
        qty, px = Decimal(str(shares)), Decimal(str(price))
        fee = taker_fee(shares=qty, price=px, metadata=fee_metadata)
        debit = qty * px + fee
        with self.transaction():
            if self.db.execute("SELECT 1 FROM fills WHERE fill_id=? OR event_id=?", (fill_id, event_id)).fetchone():
                return False
            if debit > self.portfolio().cash:
                raise ValueError("insufficient virtual cash")
            payload = json.dumps({"fill_id": fill_id, "fee_source": fee_metadata.source_url}, sort_keys=True)
            self.db.execute("INSERT INTO events VALUES(?,?,?,?)", (event_id, "fill", ts_ns, payload))
            self.db.execute("INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?,?)", (
                fill_id, event_id, instrument_id, slug, outcome.lower(), str(qty), str(px), str(fee), str(debit), ts_ns,
            ))
        return True

    def record_settlement(self, *, settlement_id: str, fill_id: str, official_outcome: str,
                          ts_ns: int, evidence_url: str) -> bool:
        with self.transaction():
            if self.db.execute("SELECT 1 FROM settlements WHERE settlement_id=? OR fill_id=?", (settlement_id, fill_id)).fetchone():
                return False
            row = self.db.execute("SELECT outcome,shares FROM fills WHERE fill_id=?", (fill_id,)).fetchone()
            if row is None:
                raise ValueError("unknown fill_id")
            payout = Decimal(row[1]) if row[0].casefold() == official_outcome.casefold() else Decimal("0")
            self.db.execute("INSERT INTO settlements VALUES(?,?,?,?,?,?)", (
                settlement_id, fill_id, official_outcome, str(payout), ts_ns, evidence_url,
            ))
        return True

    def portfolio(self) -> Portfolio:
        initial = Decimal(dict(self.db.execute("SELECT key,value FROM meta"))["initial_cash"])
        debits = sum((Decimal(r[0]) for r in self.db.execute("SELECT cash_debit FROM fills")), Decimal("0"))
        payouts = sum((Decimal(r[0]) for r in self.db.execute("SELECT payout FROM settlements")), Decimal("0"))
        open_rows = list(self.db.execute(
            "SELECT f.cash_debit,f.shares FROM fills f LEFT JOIN settlements s ON s.fill_id=f.fill_id WHERE s.fill_id IS NULL"
        ))
        open_cost = sum((Decimal(r[0]) for r in open_rows), Decimal("0"))
        open_payout = sum((Decimal(r[1]) for r in open_rows), Decimal("0"))
        cash = initial - debits + payouts
        realized_cost = debits - open_cost
        return Portfolio(cash, open_cost, open_payout, payouts - realized_cost, cash + open_cost, len(open_rows))

    def open_fills(self) -> list[dict[str, object]]:
        columns=("fill_id","event_id","instrument_id","slug","outcome","shares","price","fee","cash_debit","ts_ns")
        rows=self.db.execute("SELECT f.* FROM fills f LEFT JOIN settlements s ON s.fill_id=f.fill_id WHERE s.fill_id IS NULL ORDER BY f.ts_ns")
        return [dict(zip(columns,row,strict=True)) for row in rows]

    def daily_risk_pnl(self, *, day_utc: str) -> Decimal:
        """Realized PnL for entry day minus all unresolved cost (worst case)."""
        realized=Decimal("0")
        query="""SELECT f.shares,f.price,f.fee,f.ts_ns,s.payout FROM fills f
                 JOIN settlements s ON s.fill_id=f.fill_id"""
        from datetime import UTC, datetime
        for shares,price,fee,ts_ns,payout in self.db.execute(query):
            day=datetime.fromtimestamp(int(ts_ns)/1_000_000_000,tz=UTC).date().isoformat()
            if day==day_utc:
                realized+=Decimal(payout)-Decimal(shares)*Decimal(price)-Decimal(fee)
        return realized-self.portfolio().open_cost

    def can_enter(self, *, proposed_debit: Decimal | str, max_open_risk: Decimal | str = "6") -> tuple[bool, str | None]:
        amount = Decimal(str(proposed_debit))
        p = self.portfolio()
        if amount > p.cash:
            return False, "insufficient_cash"
        if p.open_cost + amount > Decimal(str(max_open_risk)):
            return False, "aggregate_open_risk"
        return True, None

    def record_market_evaluation(self, *, slug: str, decision: str, reason: str | None,
                                 seconds_left: int, ts_ns: int, payload: dict[str, object]) -> bool:
        with self.transaction():
            if self.db.execute("SELECT 1 FROM market_evaluations WHERE slug=?",(slug,)).fetchone(): return False
            self.db.execute("INSERT INTO market_evaluations VALUES(?,?,?,?,?,?)",(
                slug,decision,reason,seconds_left,ts_ns,json.dumps(payload,sort_keys=True,default=str)))
        return True

    def evaluated_slugs(self) -> set[str]:
        return {str(row[0]) for row in self.db.execute("SELECT slug FROM market_evaluations")}

    def record_shadow(self, *, slug: str, horizon_seconds: int, policy: str, decision: str,
                      reason: str | None, selected: str | None, shares: Decimal | str | None,
                      price: Decimal | str | None, fee: Decimal | str | None,
                      cash_debit: Decimal | str | None, prob_up: float | None,
                      net_edge: float | None, pair_cost: float | None, ts_ns: int,
                      payload: dict[str, object]) -> bool:
        shadow_id=f"{slug}:{horizon_seconds}:{policy}"
        values=lambda value: None if value is None else str(value)
        with self.transaction():
            if self.db.execute("SELECT 1 FROM shadow_evaluations WHERE shadow_id=?",(shadow_id,)).fetchone(): return False
            self.db.execute("INSERT INTO shadow_evaluations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(
                shadow_id,slug,horizon_seconds,policy,decision,reason,selected,values(shares),values(price),
                values(fee),values(cash_debit),values(prob_up),values(net_edge),values(pair_cost),ts_ns,
                json.dumps(payload,sort_keys=True,default=str)))
        return True

    def shadow_policies(self, *, slug: str, horizon_seconds: int) -> set[str]:
        return {str(row[0]) for row in self.db.execute(
            "SELECT policy FROM shadow_evaluations WHERE slug=? AND horizon_seconds=?",(slug,horizon_seconds))}

    def unsettled_shadow_slugs(self) -> set[str]:
        rows=self.db.execute("SELECT DISTINCT e.slug FROM shadow_evaluations e LEFT JOIN shadow_settlements s ON s.shadow_id=e.shadow_id WHERE s.shadow_id IS NULL")
        return {str(row[0]) for row in rows}

    def unsettled_shadows(self, slug: str) -> list[dict[str, object]]:
        columns=("shadow_id","slug","horizon_seconds","policy","decision","reason","selected","shares","price","fee","cash_debit","prob_up","net_edge","pair_cost","ts_ns","payload")
        rows=self.db.execute("SELECT e.* FROM shadow_evaluations e LEFT JOIN shadow_settlements s ON s.shadow_id=e.shadow_id WHERE e.slug=? AND s.shadow_id IS NULL ORDER BY e.horizon_seconds DESC,e.policy",(slug,))
        return [dict(zip(columns,row,strict=True)) for row in rows]

    def record_shadow_settlement(self, *, shadow_id: str, official_outcome: str,
                                 ts_ns: int, evidence_url: str) -> bool:
        with self.transaction():
            if self.db.execute("SELECT 1 FROM shadow_settlements WHERE shadow_id=?",(shadow_id,)).fetchone(): return False
            row=self.db.execute("SELECT decision,selected,shares,price,fee FROM shadow_evaluations WHERE shadow_id=?",(shadow_id,)).fetchone()
            if row is None: raise ValueError("unknown shadow_id")
            win=None; pnl=None
            if row[0]=="enter" and all(value is not None for value in row[1:]):
                win=int(str(row[1]).casefold()==official_outcome.casefold())
                pnl=Decimal(str(row[2]))*Decimal(win)-Decimal(str(row[2]))*Decimal(str(row[3]))-Decimal(str(row[4]))
            self.db.execute("INSERT INTO shadow_settlements VALUES(?,?,?,?,?,?)",(
                shadow_id,official_outcome,win,None if pnl is None else str(pnl),ts_ns,evidence_url))
        return True

    def shadow_summary(self) -> dict[str, object]:
        evaluations=int(self.db.execute("SELECT COUNT(*) FROM shadow_evaluations").fetchone()[0])
        settlements=int(self.db.execute("SELECT COUNT(*) FROM shadow_settlements").fetchone()[0])
        rows=list(self.db.execute("SELECT e.policy,e.horizon_seconds,e.decision,s.win,s.hypothetical_pnl FROM shadow_evaluations e LEFT JOIN shadow_settlements s ON s.shadow_id=e.shadow_id"))
        groups:dict[str,dict[str,object]]={}
        for policy,horizon,decision,win,pnl in rows:
            key=f"{policy}_{horizon}s"; group=groups.setdefault(key,{"evaluations":0,"entries":0,"settled_entries":0,"wins":0,"net_pnl":"0"})
            group["evaluations"]=int(group["evaluations"])+1
            if decision=="enter": group["entries"]=int(group["entries"])+1
            if win is not None:
                group["settled_entries"]=int(group["settled_entries"])+1; group["wins"]=int(group["wins"])+int(win)
            if pnl is not None: group["net_pnl"]=str(Decimal(str(group["net_pnl"]))+Decimal(str(pnl)))
        return {"evaluations":evaluations,"settlements":settlements,"groups":groups}
