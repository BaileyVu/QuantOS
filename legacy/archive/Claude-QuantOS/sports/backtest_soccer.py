"""Backtest of notes/sports_rule_v1.json: Polymarket soccer moneylines vs Betfair Exchange pre-match odds
(football-data.co.uk 2026/27). Public data only. Python 3.9 stdlib."""
import csv, io, json, math, os, random, sys, time, urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from live_scan import same_team  # noqa: E402

ROOT = os.path.dirname(HERE)
D = os.path.join(HERE, "data")
RULE = json.load(open(os.path.join(ROOT, "notes", "sports_rule_v1.json")))
H = {"user-agent": "claude-quantos-research"}
DIV = {"premier-league-2025": "E0", "efl-championship": "E1", "soccer-el1": "E2", "soccer-el2": "E3",
       "soccer-enl": "EC", "la-liga-2025": "SP1", "la-liga-2": "SP2", "serie-a-2025": "I1", "serie-b": "I2",
       "bundesliga-2025": "D1", "bundesliga-2": "D2", "ligue-1-2025": "F1", "ligue-2": "F2", "ere-2025": "N1",
       "tur-2025": "T1", "primeira-liga": "P1"}


def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=40) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 ** i)


def load_fd():
    rows = defaultdict(list)
    for series, div in DIV.items():
        path = os.path.join(D, f"fd_{div}.csv")
        if not os.path.exists(path):
            open(path, "wb").write(get(f"https://www.football-data.co.uk/mmz4281/2627/{div}.csv"))
        txt = open(path, encoding="utf-8-sig", errors="replace").read()
        for r in csv.DictReader(io.StringIO(txt)):
            try:
                r["_date"] = datetime.strptime(r["Date"], "%d/%m/%Y").date()
            except (ValueError, KeyError):
                continue
            rows[series].append(r)
    return rows


def odds3(r, cols):
    try:
        o = [float(r[c]) for c in cols]
    except (KeyError, ValueError):
        return None
    if any(x <= 1 for x in o):
        return None
    inv = [1 / x for x in o]
    return [x / sum(inv) for x in inv]


def main():
    games = json.load(open(os.path.join(D, "closed_soccer.json")))
    fd = load_fd()
    S = RULE["strategy"]
    matched, ambiguous, unmatched, pairs = 0, 0, 0, []
    for e in games:
        series = e.get("seriesSlug")
        if series not in DIV:
            continue
        mls = [m for m in e["markets"] if m.get("sportsMarketType") == "moneyline"]
        if len(mls) != 3 or not mls[0].get("gameStartTime"):
            continue
        start = datetime.fromisoformat(mls[0]["gameStartTime"].replace("+00", "+00:00").replace(" ", "T"))
        title = e.get("title") or ""
        parts = [p.strip() for p in title.replace(" vs. ", " vs ").split(" vs ")]
        if len(parts) != 2:
            continue
        cands = [r for r in fd[series] if abs((r["_date"] - start.date()).days) <= 1
                 and same_team(parts[0], r["HomeTeam"]) and same_team(parts[1], r["AwayTeam"])]
        if len(cands) != 1:
            ambiguous += len(cands) > 1
            unmatched += len(cands) == 0
            continue
        r = cands[0]
        outs = {}
        for m in mls:
            label = m.get("groupItemTitle") or ""
            toks = json.loads(m["clobTokenIds"]) if isinstance(m["clobTokenIds"], str) else m["clobTokenIds"]
            won = (json.loads(m["outcomePrices"]) if isinstance(m["outcomePrices"], str) else m["outcomePrices"])[0] == "1"
            fs = m.get("feeSchedule") or {}
            rate = 0.0 if m.get("feesEnabled") is False else float(fs.get("rate", 0.05))
            if "draw" in label.lower():
                k = "D"
            elif same_team(label, parts[0]) and not same_team(label, parts[1]):
                k = "H"
            elif same_team(label, parts[1]) and not same_team(label, parts[0]):
                k = "A"
            else:
                k = None
            if k:
                outs[k] = {"token": toks[0], "won": won, "rate": rate}
        if len(outs) != 3:
            continue
        matched += 1
        pairs.append({"game": title, "series": series, "start": start.isoformat(), "fd": r, "outs": outs})
    print(f"soccer games in covered leagues: matched {matched}, ambiguous {ambiguous}, unmatched {unmatched}")

    # price history at T-60m (fidelity 10), cached
    cache_p = os.path.join(D, "soccer_prices.json")
    cache = json.load(open(cache_p)) if os.path.exists(cache_p) else {}
    jobs = []
    for p in pairs:
        t = datetime.fromisoformat(p["start"])
        for o in p["outs"].values():
            if o["token"] not in cache:
                jobs.append((o["token"], int((t - timedelta(hours=8)).timestamp()), int(t.timestamp())))

    def one(j):
        tok, s, e = j
        try:
            h = json.loads(get(f"https://clob.polymarket.com/prices-history?market={tok}&startTs={s}&endTs={e}&fidelity=10"))
            return tok, [[x["t"], x["p"]] for x in h.get("history") or []]
        except Exception:
            return tok, None
    with ThreadPoolExecutor(8) as ex:
        for tok, h in ex.map(one, jobs):
            cache[tok] = h
    json.dump(cache, open(cache_p, "w"))

    def run(cols, label):
        trades = []
        for p in pairs:
            probs = odds3(p["fd"], cols)
            if not probs:
                continue
            dec = int((datetime.fromisoformat(p["start"]) - timedelta(minutes=60)).timestamp())
            for k, fair in zip("HDA", probs):
                o = p["outs"][k]
                h = cache.get(o["token"]) or []
                px = None
                for t, v in sorted(h):
                    if t <= dec:
                        px = v
                if px is None:
                    continue
                entry = px + 0.01
                if not (0.03 <= entry <= 0.90) or fair - entry < 0.03:
                    continue
                sh = 10 / entry
                pnl = (sh if o["won"] else 0) - 10 - sh * o["rate"] * entry * (1 - entry)
                trades.append({"game": p["game"], "series": p["series"], "k": k, "fair": fair, "entry": entry,
                               "won": o["won"], "pnl": pnl})
        n = len(trades)
        pnl = sum(t["pnl"] for t in trades)
        by_g = defaultdict(float)
        for t in trades:
            by_g[t["game"]] += t["pnl"]
        g = list(by_g.values())
        random.seed(20261021)
        boot = sorted(sum(random.choice(g) for _ in g) for _ in range(5000)) if g else [0] * 5000
        print(f"\n{label}: trades {n}  win {sum(t['won'] for t in trades) / max(n, 1):.1%}  PnL ${pnl:+.2f}  "
              f"ROI {pnl / max(10 * n, 1):+.1%}  95% CI [{boot[125]:+.2f}, {boot[4875]:+.2f}]  "
              f"avg edge {sum(t['fair'] - t['entry'] for t in trades) / max(n, 1):.3f}")
        by_s = defaultdict(list)
        for t in trades:
            by_s[t["series"]].append(t)
        for s, ts in sorted(by_s.items(), key=lambda kv: -len(kv[1])):
            sp = sum(t["pnl"] for t in ts)
            print(f"   {s:<22} n={len(ts):3d} pnl=${sp:+8.2f} roi={sp / (10 * len(ts)):+.1%}")
        return trades

    have = sum(1 for p in pairs if odds3(p["fd"], ["BFEH", "BFED", "BFEA"]))
    print(f"games with Betfair Exchange pre-match odds: {have}/{len(pairs)}; with price history: "
          f"{sum(1 for p in pairs if all(cache.get(o['token']) for o in p['outs'].values()))}")
    t1 = run(["BFEH", "BFED", "BFEA"], "STRATEGY (Betfair Exchange pre-match, no look-ahead)")
    run(["AvgH", "AvgD", "AvgA"], "fallback reference: market-average pre-match odds")
    run(["BFECH", "BFECD", "BFECA"], "DIAGNOSTIC ONLY - closing odds (LOOK-AHEAD vs T-60m entry)")
    json.dump(t1, open(os.path.join(D, "soccer_trades_v1.json"), "w"))


if __name__ == "__main__":
    main()
