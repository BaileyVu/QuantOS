"""Causal replay of 2026-10-29 01:00 -> 2026-10-30 05:00 UTC through QuantOS's own code.

QuantOS is imported read-only (sys.path); nothing in QuantOS/ is written. Inputs are public Binance data
fetched by fetch.py. Each scan builds Snapshots from bars closed before the scan time only, and the
engine's own dispatch/risk/fill logic decides approvals, fills and exits.

Groups (each a set of independent $200 journals):
  L  : the six library strategies on USDC perps, profiles p1/p5/pA (Policy, as `quantos start`).
  R  : the three research candidates on USDT perps, profile pR (ResearchPolicy, as `backtest --research`).
  CF : the same candidates under counterfactual p1/p5/pA sizing (USDT, max 1 position, Policy fees).
       QuantOS itself never runs candidates under p1/p5/pA; CF exists only to answer "what if".
"""
from __future__ import annotations

import bisect
import json
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / ("out" if __import__("os").environ.get("OI_MODE", "raw") == "raw" else "out_oirepair")
sys.path.insert(0, str(Path.home() / "Desktop/PythonProject/QuantOS/src"))

from quantos.paper.adaptive import AdaptiveSizer  # noqa: E402
from quantos.paper.engine import PaperEngine  # noqa: E402
from quantos.paper.journal import Journal  # noqa: E402
from quantos.paper.market import parse_exchange_info  # noqa: E402
from quantos.paper.risk import Policy, ResearchPolicy  # noqa: E402
from quantos.paper.runtime import dispatch  # noqa: E402
from quantos.paper.strategies import library  # noqa: E402
from quantos.paper.strategies.alt_dip_reversal import AltDipReversalStrategy  # noqa: E402
from quantos.paper.strategies.candidate_features import (  # noqa: E402
    ALT_SOURCE_UNIVERSE, HOUR_MS, calibrate_quarterly_return_cutoff, resample_hourly, rolling_24h_return)
from quantos.paper.strategies.crash_flush_e2 import CrashFlushE2Strategy, MAJORS  # noqa: E402
from quantos.paper.strategies.ema_level_2h import EmaLevel2hStrategy  # noqa: E402
from quantos.paper.types import D, Candle, Funding, OpenInterestValue, Quote, Snapshot, Trade  # noqa: E402

UTC = timezone.utc


def ms(s):
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=UTC).timestamp() * 1000)


T0, T1 = ms("2026-10-29 01:00"), ms("2026-10-30 05:00")
FEED_STEP = 5000            # feed batch cadence (runtime polls every ~2s; quotes stay valid 5s)
SCAN_LAG = 3000             # scan snapshot is taken 3s after the bar boundary
COUNTS = {"1m": 100, "5m": 100, "1h": 200, "4h": 201, "1d": 150}   # closed bars the runtime holds
WIDTH = {"1m": 60000, "5m": 300000, "1h": 3600000, "4h": 14400000, "1d": 86400000}
MIN_QUOTE_VOLUME = D("20000000")


@dataclass(frozen=True)
class CounterfactualPolicy(Policy):
    """p1/p5 sizing and fees, but allowed to take USDT candidate signals (QuantOS never does this)."""
    risk_ceiling = ".05"
    margin_asset = "USDT"
    candidates_enabled = True
    max_positions = 1


# ---------------------------------------------------------------- data
class Series:
    def __init__(self, rows, width):
        self.bars = [Candle(int(r[0]), int(r[0]) + width - 1, D(r[1]), D(r[2]), D(r[3]), D(r[4]), D(r[5]))
                     for r in rows if int(r[6]) == int(r[0]) + width - 1]
        self.qv = [D(r[7]) for r in rows if int(r[6]) == int(r[0]) + width - 1]
        self.close = [b.close_ms for b in self.bars]
        self.by_open = {b.open_ms: b for b in self.bars}

    def closed(self, at_ms, n):
        i = bisect.bisect_left(self.close, at_ms)          # bars with close_ms < at_ms
        return tuple(self.bars[max(0, i - n):i])


def load(path, interval, symbol):
    f = DATA / path / interval / f"{symbol}.json"
    return Series(json.loads(f.read_text()), WIDTH[interval]) if f.exists() else None


info = json.loads((DATA / "exchangeInfo.json").read_text())
INS = {**parse_exchange_info(info, "USDC"), **parse_exchange_info(info, "USDT")}
BOOK = {r["symbol"]: r for r in json.loads((DATA / "bookTicker_now.json").read_text())}
USDC = [s for s in parse_exchange_info(info, "USDC")]
USDT = list(ALT_SOURCE_UNIVERSE)

K = {}   # (symbol, interval) -> Series
for s in USDC:
    for iv in ("1m", "5m", "1h", "4h", "1d"):
        K[s, iv] = load("klines", iv, s)
    K[s, "mark"] = load("markPriceKlines", "1m", s)
for s in USDT:
    for iv in ("1m", "1h", "1d"):
        K[s, iv] = load("klines", iv, s)
    K[s, "mark"] = load("markPriceKlines", "1m", s)
USDC = [s for s in USDC if K[s, "1m"] and K[s, "mark"] and K[s, "1m"].by_open.get(T0)]
FUND = {}
for s in USDC + USDT:
    f = DATA / "funding" / f"{s}.json"
    FUND[s] = [Funding(s, int(r["fundingTime"]), D(r["fundingRate"]), D(r["markPrice"]))
               for r in json.loads(f.read_text())] if f.exists() else []


def oi_values(symbol):
    """Hourly sumOpenInterestValue keyed by its raw on-the-hour archive label; available = label + 1h
    (the research path's conservative proxy). Archive through 10-28; 10-29+ from the 5m API row at
    label+5min, which equals the archive row labelled `label` exactly (verified on 10-28)."""
    import csv
    out = {}
    for f in sorted((DATA / "metrics" / symbol).glob("*.csv")):
        for r in csv.DictReader(f.open()):
            t = int(datetime.strptime(r["create_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp() * 1000)
            if t % HOUR_MS == 0:
                out[t] = D(r["sum_open_interest_value"])
    api = {int(r["timestamp"]): D(r["sumOpenInterestValue"]) for r in json.loads((DATA / "oi_api5m" / f"{symbol}.json").read_text())}
    for t5, v in api.items():
        if (t5 - 300000) % HOUR_MS == 0 and (t5 - 300000) not in out:
            out[t5 - 300000] = v
    return [OpenInterestValue(t, t + HOUR_MS, out[t]) for t in sorted(out)]


REPAIRED = {}


def repaired_oi(symbol):
    """SENSITIVITY ONLY: replace archive rows whose sumOpenInterestValue is 0 (Binance archive outage
    2026-10-15 ~12:40-15:15 UTC) by sum_open_interest x that hour's 1h open price, or, when the
    contract count is also 0, by linear interpolation between neighbouring valid hours."""
    import csv
    counts = {}
    f = DATA / "metrics" / symbol / "2026-10-15.csv"
    for r in csv.DictReader(f.open()):
        t = int(datetime.strptime(r["create_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp() * 1000)
        counts[t] = D(r["sum_open_interest"])
    rows = list(oi_values(symbol))
    bad = [i for i, v in enumerate(rows) if v.value <= 0]
    for i in bad:
        t = rows[i].time_ms
        bar = K[symbol, "1h"].by_open.get(t)
        if counts.get(t, D(0)) > 0 and bar:
            value = counts[t] * bar.open
        else:
            lo = next(j for j in range(i - 1, -1, -1) if rows[j].value > 0)
            hi = next(j for j in range(i + 1, len(rows)) if rows[j].value > 0)
            value = rows[lo].value + (rows[hi].value - rows[lo].value) * D(i - lo) / D(hi - lo)
        rows[i] = OpenInterestValue(t, t + HOUR_MS, value)
        REPAIRED.setdefault(symbol, []).append(datetime.fromtimestamp(t / 1000, UTC).strftime("%m-%d %H:%M"))
    return rows


import os  # noqa: E402
OI_MODE = os.environ.get("OI_MODE", "raw")
OI = {s: (repaired_oi(s) if OI_MODE == "repair" else oi_values(s)) for s in MAJORS}

# ALT quarterly cutoff for Q4-2026, exactly as the research path calibrates it.
Q4 = ms("2026-10-01 00:00")
returns = {s: rolling_24h_return(K[s, "1h"].closed(Q4, 10**6)) for s in USDT}
CAL = calibrate_quarterly_return_cutoff(returns, Q4, D("0.01"))
if CAL is None:
    raise SystemExit("ALT calibration failed (incomplete hourly data)")


# ---------------------------------------------------------------- synthetic quotes
def path_price(bar, t):
    """1m OHLC -> price at time t: open(0s) -> first extreme(20s) -> second extreme(40s) -> close(59s).
    Up bars visit the low first, down bars the high first."""
    sec = (t - bar.open_ms) / 1000
    e1, e2 = (bar.low, bar.high) if bar.close >= bar.open else (bar.high, bar.low)
    pts = ((0, bar.open), (20, e1), (40, e2), (59, bar.close))
    for (a, pa), (b, pb) in zip(pts, pts[1:]):
        if sec <= b:
            return pa + (pb - pa) * D(str((sec - a) / (b - a)))
    return bar.close


def quote_at(symbol, t):
    minute = t // 60000 * 60000
    bar, mbar = K[symbol, "1m"].by_open.get(minute), K[symbol, "mark"].by_open.get(minute)
    if bar is None or mbar is None:
        return None
    tick = INS[symbol].tick
    price = path_price(bar, t)
    bid = (price / tick).to_integral_value(rounding="ROUND_FLOOR") * tick
    book = BOOK.get(symbol, {"bidQty": "1", "askQty": "1"})
    stamp = t - 50
    return Quote(symbol, stamp, stamp, bid, bid + tick, D(book["bidQty"]), D(book["askQty"]),
                 path_price(mbar, t), stamp)


# ---------------------------------------------------------------- aggTrades for pending LIMIT orders
def agg_trades(symbol, start, end):
    cache = DATA / "aggtrades" / f"{symbol}_{start}_{end}.json"
    if not cache.exists():
        cache.parent.mkdir(exist_ok=True)
        rows, url = [], f"https://fapi.binance.com/fapi/v1/aggTrades?symbol={symbol}&startTime={start}&endTime={end}&limit=1000"
        while True:
            with urlopen(Request(url, headers={"User-Agent": "crash-replay/1"}), timeout=20) as r:
                page = json.loads(r.read())
            rows += page
            if len(page) < 1000:
                break
            url = f"https://fapi.binance.com/fapi/v1/aggTrades?symbol={symbol}&fromId={page[-1]['a'] + 1}&limit=1000"
            if page[-1]["T"] > end:
                break
            time.sleep(0.2)
        cache.write_text(json.dumps([r for r in rows if r["T"] <= end]))
    return [Trade(symbol, r["a"], r["T"], D(r["p"]), D(str(r.get("nq", r["q"]))), r["m"])
            for r in json.loads(cache.read_text())]


# ---------------------------------------------------------------- snapshots
def eligible_usdc(symbol, at_ms):
    h = K[symbol, "1h"]
    i = bisect.bisect_left(h.close, at_ms)
    return sum(h.qv[max(0, i - 24):i], D(0)) >= MIN_QUOTE_VOLUME


def library_snapshot(symbol, at_ms, held):
    q = quote_at(symbol, at_ms)
    if q is None:
        return None
    ok = eligible_usdc(symbol, at_ms) and (q.ask - q.bid) / q.bid <= Policy().max_spread
    if not ok and symbol not in held:
        return None
    c = {iv: K[symbol, iv].closed(at_ms, n) if K[symbol, iv] else () for iv, n in COUNTS.items()}
    return Snapshot(at_ms, INS[symbol], q, c["1m"], c["5m"], c["1h"], c["4h"], c["1d"], entry_eligible=ok)


def candidate_snapshot(symbol, at_ms):
    q = quote_at(symbol, at_ms)
    if q is None:
        return None
    hourly = K[symbol, "1h"].closed(at_ms, 620)
    two = resample_hourly(hourly, 2, as_of_ms=at_ms) or ()
    oi = ()
    if symbol in OI:
        avail = [v for v in OI[symbol] if v.available_ms <= at_ms]
        oi = tuple(avail[-730:])
    return Snapshot(at_ms, INS[symbol], q, (), (), candles_1h=hourly[-30:], candles_2h=tuple(two[-300:]),
                    candles_1d=K[symbol, "1d"].closed(at_ms, 150), oi_values=oi,
                    quarterly_return_cutoff=CAL.threshold, calibration_end_ms=CAL.calibration_end_ms)


# ---------------------------------------------------------------- engines
def build():
    if OUT.exists():
        shutil.rmtree(OUT)
    groups = {}
    jl = {p: Journal(OUT / "L" / f"journal_{p}.db", p, "replay") for p in ("p1", "p5", "pA")}
    groups["L"] = [PaperEngine(jl["p1"], Policy(risk_fraction=D(".01"))),
                   PaperEngine(jl["p5"], Policy(risk_fraction=D(".05"))),
                   PaperEngine(jl["pA"], Policy(risk_fraction=D(".05")), adaptive=AdaptiveSizer(jl["pA"], jl["p1"]))]
    groups["R"] = [PaperEngine(Journal(OUT / "R" / "journal_pR.db", "pR", "replay"), ResearchPolicy())]
    jc = {p: Journal(OUT / f"CF_{p}" / "journal_pR.db", "pR", "replay") for p in ("p1", "p5", "pA")}
    groups["CF"] = [PaperEngine(jc["p1"], CounterfactualPolicy(risk_fraction=D(".01"))),
                    PaperEngine(jc["p5"], CounterfactualPolicy(risk_fraction=D(".05"))),
                    PaperEngine(jc["pA"], CounterfactualPolicy(risk_fraction=D(".05")),
                                adaptive=AdaptiveSizer(jc["pA"], jc["p1"]))]
    return groups


def main():
    groups = build()
    if OI_MODE == "repair":          # library strategies do not read OI; only rerun the candidate groups
        for e in groups.pop("L"):
            e.journal.close()
    lib = list(library())
    cand = [CrashFlushE2Strategy(), AltDipReversalStrategy(D("0.01"), 24), EmaLevel2hStrategy()]
    baseline = {"L": ("BTCUSDC", "ETHUSDC"), "R": ("BTCUSDT", "ETHUSDT"), "CF": ("BTCUSDT", "ETHUSDT")}
    for engines in groups.values():
        for e in engines:
            e.recover(T0)
    trades_sent = {}
    funding_cursor = T0
    stale = []
    t = T0
    started = time.time()
    while t <= T1:
        # funding observations published since the last check (engines only book them on held quantity)
        if t // 60000 > funding_cursor // 60000:
            for gname, engines in groups.items():
                syms = USDC if gname == "L" else USDT
                obs = [f for s in syms for f in FUND[s] if funding_cursor < f.time_ms <= t - 30000]
                if obs:
                    dispatch(engines, "funding", obs, t)
            funding_cursor = t - 30000
        # scan at bar boundary + SCAN_LAG (5m for the library, 1h for candidates)
        if (t - SCAN_LAG) % 300000 == 0 and t > T0 and "L" in groups:
            held = {o["signal"]["symbol"] for e in groups["L"] for o in e.active()}
            snaps = [x for s in USDC if (x := library_snapshot(s, t, held))]
            dispatch(groups["L"], "scan", snaps, t, strategies=lib)
        if (t - SCAN_LAG) % HOUR_MS == 0 and t > T0:
            snaps = [x for s in USDT if (x := candidate_snapshot(s, t))]
            for g in ("R", "CF"):
                dispatch(groups[g], "scan", snaps, t, strategies=cand)
        if t % FEED_STEP == 0:
            for gname, engines in groups.items():
                watched = set(baseline[gname]) | {o["signal"]["symbol"] for e in engines for o in e.active()}
                records = []
                for s in sorted(watched):
                    q = quote_at(s, t)
                    if q is None:
                        stale.append((gname, s, t))
                        continue
                    records.append(("quote", q))
                limits = {(o["signal"]["symbol"], o["created_ms"], o["signal"]["expires_ms"])
                          for e in engines for o in e.active()
                          if o["signal"]["entry_type"] == "LIMIT" and D(o["remaining"]) > 0}
                for sym, a, b in limits:
                    seen = trades_sent.setdefault((gname, sym), set())
                    for tr in agg_trades(sym, a, b):
                        if t - 5000 < tr.time_ms <= t and tr.trade_id not in seen:
                            seen.add(tr.trade_id)
                            records.append(("trade", tr))
                dispatch(engines, "feed", records, t)
                dispatch(engines, "tick", {"finish": False}, t)
        t += 1000
        if (t - T0) % 3600000 == 0:
            print(datetime.fromtimestamp(t / 1000, UTC).strftime("%m-%d %H:%M"), f"{time.time() - started:.0f}s", flush=True)
    # Mark open positions at the window end; nothing is closed synthetically.
    marks = {}
    for gname, engines in groups.items():
        for e in engines:
            for o in e.active():
                q = quote_at(o["signal"]["symbol"], T1)
                marks[f'{gname}:{e.journal.profile}:{o["id"]}'] = str(q.mark) if q else None
    (OUT / "end_marks.json").write_text(json.dumps({"T1": T1, "marks": marks, "stale": stale[:50],
                                                    "stale_count": len(stale),
                                                    "alt_cutoff": str(CAL.threshold),
                                                    "alt_calibration": [CAL.calibration_start_ms, CAL.calibration_end_ms, CAL.sample_count]}))
    for engines in groups.values():
        for e in engines:
            e.journal.close()


if __name__ == "__main__":
    main()
