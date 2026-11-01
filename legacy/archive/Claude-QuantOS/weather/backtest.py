"""Backtest of notes/weather_rule_v1.json on weather/data/*. Read-only, deterministic."""
import json, math, os, random, statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
RULE = json.load(open(os.path.join(os.path.dirname(HERE), "notes", "weather_rule_v1.json")))
S = RULE["strategy"]
LEADS = RULE["decision_times_hours_before_local_day_end"]

events = json.load(open(os.path.join(D, "events.json")))
fc = json.load(open(os.path.join(D, "forecasts.json")))
arch = json.load(open(os.path.join(D, "archive.json")))
prices = {}
for line in open(os.path.join(D, "prices.jsonl")):
    r = json.loads(line)
    if "history" in r:
        prices[r["token"]] = sorted(r["history"])


def n_for(lead):
    return math.ceil((lead + 6) / 24)


def truth_mid(e):
    b = next(b for b in e["buckets"] if b["won"])
    if b["lo"] is None:
        return b["hi"] - 1
    if b["hi"] is None:
        return b["lo"] + 1
    return (b["lo"] + b["hi"]) / 2


def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bucket_probs(e, mu, sigma):
    ps = []
    for b in e["buckets"]:
        lo = -math.inf if b["lo"] is None else b["lo"] - 0.5
        hi = math.inf if b["hi"] is None else b["hi"] + 0.5
        ps.append(ncdf((hi - mu) / sigma) - ncdf((lo - mu) / sigma))
    s = sum(ps)
    return [p / s for p in ps] if s > 0 else ps


def fcst(e, n):
    return fc.get(e["station"], {}).get(f"N{n}", {}).get(e["date"])


# ---- split by date
dates = sorted({e["date"] for e in events})
median = dates[(len(dates) - 1) // 2]
calib = [e for e in events if e["date"] <= median]
evalset = [e for e in events if e["date"] > median]

# ---- error model from calibration half only
bias, sigma = {}, {}
for n in (1, 2):
    errs = defaultdict(list)
    for e in calib:
        f = fcst(e, n)
        if f is not None:
            errs[e["city"]].append(f - truth_mid(e))
    for c, v in errs.items():
        bias[(c, n)] = sum(v) / len(v)
    resid = [x - bias[(c, n)] for c, v in errs.items() for x in v]
    # sigma per unit system (F and C differ in scale)
    for unit in ("F", "C"):
        cities_u = {e["city"] for e in calib if e["unit"] == unit}
        r_u = [x - bias[(c, n)] for c, v in errs.items() if c in cities_u for x in v]
        sigma[(unit, n)] = max(0.8, statistics.pstdev(r_u)) if len(r_u) > 5 else None


def price_at(tok, ts):
    h = prices.get(tok)
    if not h:
        return None
    best = None
    for t, p in h:
        if t <= ts:
            best = p
        else:
            break
    return best


trades, calib_rows = [], []
for e in evalset:
    off = fc.get(e["station"], {}).get("utc_offset") or 0
    day_end = datetime.fromisoformat(e["date"]).replace(tzinfo=timezone.utc) + timedelta(days=1) - timedelta(seconds=off)
    traded = set()
    for lead in sorted(LEADS, reverse=True):  # earliest decision first
        n = n_for(lead)
        f = fcst(e, n)
        sg = sigma.get((e["unit"], n))
        if f is None or sg is None or (e["city"], n) not in bias:
            continue
        mu = f - bias[(e["city"], n)]
        probs = bucket_probs(e, mu, sg)
        ts = int((day_end - timedelta(hours=lead)).timestamp())
        for b, p in zip(e["buckets"], probs):
            px = price_at(b["yes"], ts)
            calib_rows.append((lead, p, b["won"], px))
            if px is None or b["yes"] in traded:
                continue
            entry = px + 0.01
            if entry < 0.02 or entry > 0.60 or p - entry < 0.10:
                continue
            rate = b["fee_rate"] if b["fee_rate"] is not None else 0.05
            if b.get("fees_enabled") is False:
                rate = 0.0
            shares = S["stake_usd"] / entry
            fee = shares * rate * entry * (1 - entry)
            pnl = (shares if b["won"] else 0) - S["stake_usd"] - fee
            traded.add(b["yes"])
            trades.append({"event": e["id"], "city": e["city"], "date": e["date"], "lead": lead, "bucket": b["label"],
                           "p": p, "entry": entry, "won": b["won"], "pnl": pnl, "volume": b["volume"]})


def summ(rows):
    if not rows:
        return "n=0"
    pnl = sum(r["pnl"] for r in rows)
    return (f"n={len(rows):4d} win={sum(r['won'] for r in rows) / len(rows):5.1%} pnl=${pnl:+8.2f} "
            f"per_trade=${pnl / len(rows):+.3f} roi={pnl / (10 * len(rows)):+6.1%} avg_entry={sum(r['entry'] for r in rows) / len(rows):.3f} "
            f"avg_p={sum(r['p'] for r in rows) / len(rows):.3f}")


print(f"rule {RULE['rule_id']} frozen {RULE['frozen_at_utc']}")
print(f"events {len(events)}  calibration <= {median}: {len(calib)}  evaluation: {len(evalset)}  "
      f"price histories {len(prices)}")
print("sigma:", {f"{u}N{n}": round(v, 2) for (u, n), v in sigma.items() if v})
print("\nALL   ", summ(trades))
for lead in LEADS:
    print(f"lead {lead:2d}h", summ([t for t in trades if t["lead"] == lead]))
for lo, hi in [(0.02, 0.15), (0.15, 0.30), (0.30, 0.45), (0.45, 0.61)]:
    print(f"entry {lo:.2f}-{hi:.2f}", summ([t for t in trades if lo <= t["entry"] < hi]))
vol_ok = [t for t in trades if t["volume"] >= 1000]
print("volume>=$1k", summ(vol_ok), f"({len(vol_ok)}/{len(trades)} trades)")
# bootstrap by event
by_ev = defaultdict(float)
for t in trades:
    by_ev[t["event"]] += t["pnl"]
evs = list(by_ev.values())
random.seed(20261021)
boot = sorted(sum(random.choice(evs) for _ in evs) for _ in range(5000)) if evs else [0]
print(f"bootstrap 95% CI total PnL (by event): [{boot[125]:+.2f}, {boot[4875]:+.2f}]  events traded {len(evs)}")
print("\nby city:")
cities = defaultdict(list)
for t in trades:
    cities[t["city"]].append(t)
for c, rows in sorted(cities.items(), key=lambda kv: -sum(r["pnl"] for r in kv[1])):
    print(f"  {c.replace('-daily-weather', ''):<14}", summ(rows))
print("\ncalibration (evaluation half, all buckets with a price):")
print(" model_p   n    realized  avg_market_price")
for lo in [0, .05, .1, .2, .3, .4, .5, .6, .8]:
    hi = {0: .05, .05: .1, .1: .2, .2: .3, .3: .4, .4: .5, .5: .6, .6: .8, .8: 1.01}[lo]
    rows = [r for r in calib_rows if lo <= r[1] < hi and r[3] is not None]
    if rows:
        print(f" {lo:.2f}-{hi:.2f} {len(rows):5d}  {sum(r[2] for r in rows) / len(rows):6.3f}   {sum(r[3] for r in rows) / len(rows):6.3f}")
mrows = [r for r in calib_rows if r[3] is not None]
print("market price calibration:")
for lo, hi in [(0, .1), (.1, .3), (.3, .5), (.5, .7), (.7, 1.01)]:
    rows = [r for r in mrows if lo <= r[3] < hi]
    if rows:
        print(f" price {lo:.1f}-{hi:.1f} n={len(rows):5d} realized {sum(r[2] for r in rows) / len(rows):.3f}")
# Brier: model vs market
for lead in LEADS:
    rows = [r for r in mrows if r[0] == lead]
    if rows:
        bm = sum((r[1] - r[2]) ** 2 for r in rows) / len(rows)
        bk = sum((r[3] - r[2]) ** 2 for r in rows) / len(rows)
        print(f"Brier lead {lead:2d}h  model {bm:.4f}  market {bk:.4f}  n={len(rows)}")
# label mismatch: ERA5 grid daily max vs official bucket
mis, tot, absd = 0, 0, []
for e in events:
    a = arch.get(e["station"], {}).get(e["date"])
    if a is None:
        continue
    tot += 1
    win = next(b for b in e["buckets"] if b["won"])
    lo = -math.inf if win["lo"] is None else win["lo"] - 0.5
    hi = math.inf if win["hi"] is None else win["hi"] + 0.5
    mis += not (lo <= a < hi)
    absd.append(abs(a - truth_mid(e)))
print(f"\nERA5 grid max outside official bucket: {mis}/{tot} ({mis / max(tot, 1):.0%}); median |ERA5 - bucket mid| {statistics.median(absd):.2f}")
json.dump(trades, open(os.path.join(D, "trades_v1.json"), "w"))
