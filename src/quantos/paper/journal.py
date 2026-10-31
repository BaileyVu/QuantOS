"""SQLite/WAL local execution authority with atomic state and audit events."""
from contextlib import contextmanager
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

from .types import D, dumps


class Journal:
    def __init__(self, path: Path, profile: str, evidence: str = "natural", initial_balance=None):
        if profile not in {"p1", "p5", "pA", "pR", "pL", "pL5"} or evidence not in {"natural", "synthetic", "replay"}:
            raise ValueError("invalid journal identity")
        if initial_balance is not None and (not D(str(initial_balance)).is_finite() or D(str(initial_balance)) <= 0):
            raise ValueError("invalid initial balance")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.profile = path, profile
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, time_ms INTEGER NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS signals(id TEXT PRIMARY KEY, time_ms INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS fills(id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
                time_ms INTEGER NOT NULL, kind TEXT NOT NULL, quantity TEXT NOT NULL,
                price TEXT NOT NULL, fee TEXT NOT NULL, pnl TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS funding(id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
                time_ms INTEGER NOT NULL, amount TEXT NOT NULL);
        ''')
        with self.transaction():
            previous = self.get("identity")
            if initial_balance is None:  # reopen with the stored balance; new journals default to 200
                initial_balance = previous["initial_balance"] if isinstance(previous, dict) else "200"
            identity = {"profile": profile, "evidence": evidence, "schema": 1,
                        "initial_balance": str(initial_balance), "margin_asset": "USDT" if profile == "pR" else "USDC"}
            previous = self.get("identity")
            if previous is not None and previous != identity:
                raise ValueError("journal identity mismatch")
            self.set("identity", identity)

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def close(self):
        self.db.close()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return default if row is None else json.loads(row[0])

    def set(self, key, value):
        self.db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, dumps(value)))

    def event(self, now_ms, kind, payload):
        self.db.execute("INSERT INTO events(time_ms,kind,payload) VALUES(?,?,?)",
                        (now_ms, kind, dumps(payload)))

    def orders(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM orders ORDER BY rowid")]

    def save(self, order):
        self.db.execute("INSERT INTO orders VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                        (order["id"], dumps(order)))

    def cash(self) -> Decimal:
        cash = D(self.get("identity")["initial_balance"])
        for row in self.db.execute("SELECT fee,pnl FROM fills"):
            cash += D(row["pnl"]) - D(row["fee"])
        for row in self.db.execute("SELECT amount FROM funding"):
            cash += D(row[0])
        return cash
