"""Counterfactual evidence only: isolated PaperEngines, never an exchange adapter.

Each proposal owns an independent in-memory journal persisted atomically as a
SQLite image in shadow_<profile>.db. This removes portfolio competition without
changing sizing floors, fill simulation, fees, funding or exit implementations.
Invalid/unpriceable proposals are retained explicitly, never assigned returns.
"""
from dataclasses import asdict
from functools import lru_cache
import json
from pathlib import Path
import random
import sqlite3

from .engine import PaperEngine, TERMINAL
from .journal import Journal
from .types import D, dumps

# A shadow engine sees every feed batch. Its journal gets an OBSERVATION row per batch (dispatch), and
# serializing/persisting every engine on every batch made the main loop fall behind the feed once
# ~50 proposals were open (11-11: 13 MB images, 783 MB shadow db). Observations carry no shadow
# evidence (summary reads net/notional only), so drop them; persist feed updates at most once a minute
# unless the trade finished.
FEED_PERSIST_MS = 60_000


def _compact(journal):
    with journal.transaction():
        dropped = journal.db.execute("DELETE FROM events WHERE kind='OBSERVATION'").rowcount
    if dropped and not journal.db.in_transaction:
        journal.db.execute("VACUUM")  # small in-memory db: keeps the persisted image from creeping


class ShadowBook:
    def __init__(self, path, source, now):
        self.source, self.policy = source, source.policy
        self.db = sqlite3.connect(path, timeout=2)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS proposals(
                signal_id TEXT PRIMARY KEY, strategy TEXT NOT NULL, reason TEXT NOT NULL,
                created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL,
                status TEXT NOT NULL, detail TEXT, order_id TEXT,
                net TEXT, notional TEXT, image BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        identity = dumps({"profile": source.journal.profile, "policy": asdict(self.policy),
                          "schema": 1, "cost_model": "PaperEngine"})
        prior = self.db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
        if prior and prior[0] != identity:
            self.db.close()
            raise ValueError("shadow policy changed; use a new state directory")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES('identity',?)", (identity,))
        self.engines, self.images, self.persisted = {}, {}, {}
        for row in self.db.execute("SELECT * FROM proposals WHERE status='ACTIVE'").fetchall():
            e = self._restore(row)
            e.recover(now)
            self.engines[row["signal_id"]] = e
            self._persist(row["signal_id"], e, now)

    def _new(self, balance):
        j = Journal(Path(":memory:"), self.source.journal.profile,
                    self.source.journal.get("identity")["evidence"], initial_balance=balance)
        adaptive = None
        if self.source.adaptive is not None:
            from .adaptive import AdaptiveSizer
            original = self.source.adaptive
            with j.transaction():
                for key, value in original.journal.db.execute("SELECT key,value FROM meta WHERE key LIKE 'edge:%' OR key='edge_seen'"):
                    j.set(key, json.loads(value))
            adaptive = AdaptiveSizer(j, original.evidence, original.config, original.priors)
        return PaperEngine(j, self.policy, adaptive=adaptive)

    def _restore(self, row):
        e = self._new("200")
        e.journal.db.deserialize(row["image"])
        _compact(e.journal)
        return e

    def propose(self, signal, instrument, quote, now, reason, order_id=None):
        if self.db.execute("SELECT 1 FROM proposals WHERE signal_id=?", (signal.signal_id,)).fetchone():
            return
        # Read equity without account(), which updates the real journal's high-water/day.
        balance = self.source.journal.cash()
        for o in self.source.active():
            q = self.source.quotes.get(o["signal"]["symbol"])
            mark = q.mark if q else D(o["last_mark"])
            balance += D(o["filled"]) * (mark - D(o["average"])) * D(o["direction"])
        # A nonpositive account cannot support a meaningful same-size hypothetical.
        e = self._new(max(balance, D("0.000001")))
        e.recover(now)
        e.connection(self.source.connected, now)
        e.quotes[quote.symbol] = quote
        if order_id:
            # Preserve the exact approved size, leverage and latency for taken trades.
            order = self.source.query_order(order_id)
            with e.journal.transaction():
                e.journal.save(order)
                e.journal.db.execute("INSERT INTO signals VALUES(?,?)", (signal.signal_id, now))
        else:
            order_id = e.propose(signal, instrument, quote, now) if balance > 0 else None
        detail = None
        if order_id is None:
            row = e.journal.db.execute("SELECT payload FROM events WHERE kind='REJECTED_RISK' ORDER BY id DESC LIMIT 1").fetchone()
            detail = json.loads(row[0])["reason"] if row else "NONPOSITIVE_EQUITY"
        with self.db:
            self.db.execute("INSERT INTO proposals VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (signal.signal_id, signal.strategy_id, reason, now, now,
                             "ACTIVE" if order_id else "UNPRICEABLE", detail, order_id,
                             None, None, e.journal.db.serialize()))
        if order_id:
            self.engines[signal.signal_id] = e
            self.images[signal.signal_id] = e.journal.db.serialize()
        else: e.journal.close()

    def _persist(self, signal_id, e, now):
        _compact(e.journal)
        orders = e.journal.orders()
        o = orders[0] if orders else None
        status = "ACTIVE" if o and o["state"] not in TERMINAL else ("CLOSED" if o and o["state"] == "CLOSED" else "UNFILLED")
        entered = e.journal.db.execute("SELECT quantity,price FROM fills WHERE kind='ENTRY'").fetchall()
        notional = sum((D(r[0]) * D(r[1]) for r in entered), D(0))
        net = e.journal.cash() - D(e.journal.get("identity")["initial_balance"])
        image = e.journal.db.serialize()
        if status == "ACTIVE" and self.images.get(signal_id) == image: return
        with self.db:
            self.db.execute("UPDATE proposals SET updated_ms=?,status=?,net=?,notional=?,image=? WHERE signal_id=?",
                            (now, status, str(net), str(notional), image, signal_id))
        if status != "ACTIVE":
            self.engines.pop(signal_id, None)
            self.images.pop(signal_id, None)
            self.persisted.pop(signal_id, None)
            e.journal.close()
        else:
            self.images[signal_id] = image
            self.persisted[signal_id] = now

    def advance(self, kind, payload, now):
        from .runtime import dispatch
        # Late funding still belongs to closed positions; apply idempotently.
        if kind == "funding":
            for row in self.db.execute("SELECT * FROM proposals WHERE status='CLOSED'").fetchall():
                e = self._restore(row)
                for value in payload: e.funding(value, now)
                self._persist(row["signal_id"], e, now)
        for signal_id, e in list(self.engines.items()):
            if kind == "scan":
                for snapshot in payload: e.channel_stops(snapshot, now)
            elif kind in {"feed", "funding", "tick", "disconnected"}:
                dispatch([e], kind, payload, now)
            else:
                continue
            if (kind == "feed" and e.active()
                    and now - self.persisted.get(signal_id, 0) < FEED_PERSIST_MS):
                continue
            self._persist(signal_id, e, now)

    def symbols(self):
        return {o["signal"]["symbol"] for e in self.engines.values() for o in e.active()}

    def close(self):
        for e in self.engines.values(): e.journal.close()
        self.engines.clear()
        self.images.clear()
        self.db.close()


@lru_cache(maxsize=128)
def _ci90(values):
    if len(values) < 2: return None
    rng = random.Random(0)
    means = sorted(sum(rng.choices(values, k=len(values))) / len(values) for _ in range(2000))
    return [means[99], means[1899]]


def summary(path, real_journal=None):
    """Read-only report; CI90 is seeded percentile bootstrap, descriptive only."""
    path = Path(path)
    if not path.exists(): return {"available": False}
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try: rows = db.execute("SELECT signal_id,strategy,reason,status,net,notional,order_id,created_ms,updated_ms FROM proposals").fetchall()
    finally: db.close()
    groups, counts = {}, {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        for key in ((r["strategy"], "ALL"), (r["strategy"], r["reason"])):
            groups.setdefault(key, [])
            if r["status"] == "CLOSED" and D(r["notional"] or "0") > 0:
                groups[key].append(float(D(r["net"]) / D(r["notional"]) * 10000))
    results = []
    for (strategy, reason), values in sorted(groups.items()):
        n = len(values)
        ci = _ci90(tuple(values))
        results.append({"strategy": strategy, "reason": reason, "n": n,
                        "mean_bp": sum(values) / n if n else None, "ci90_bp": ci,
                        "hit_rate": sum(v > 0 for v in values) / n if n else None})
    comparisons = []
    if real_journal and Path(real_journal).exists():
        real = sqlite3.connect(Path(real_journal).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            for r in rows:
                if r["reason"] != "TAKEN" or r["status"] != "CLOSED": continue
                order = real.execute("SELECT payload FROM orders WHERE id=?", (r["order_id"],)).fetchone()
                if not order or json.loads(order[0])["state"] != "CLOSED": continue
                fills = real.execute("SELECT fee,pnl FROM fills WHERE order_id=?", (r["order_id"],)).fetchall()
                funding = real.execute("SELECT amount FROM funding WHERE order_id=?", (r["order_id"],)).fetchall()
                fees = sum((D(f[0]) for f in fills), D(0))
                net = sum((D(f[1]) - D(f[0]) for f in fills), D(0)) + sum((D(f[0]) for f in funding), D(0))
                comparisons.append({"signal_id": r["signal_id"], "shadow_net": r["net"], "book_net": str(net),
                                    "difference": str(D(r["net"]) - net), "book_fees": str(fees)})
        finally: real.close()
    return {"available": True, "counts": counts, "groups": results, "taken_comparisons": comparisons,
            "first_proposal_ms": min((r["created_ms"] for r in rows), default=None),
            "updated_ms": max((r["updated_ms"] for r in rows), default=None),
            "acceptance": "UNVERIFIED: requires real 24h run and reconciled demo-book comparison",
            "ci_method": "2000 seeded iid percentile bootstrap; descriptive, correlated trades are not independent evidence"}
