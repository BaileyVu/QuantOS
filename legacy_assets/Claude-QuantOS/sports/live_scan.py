"""Live scan: Polymarket sports moneylines vs devigged Pinnacle (The Odds API v4). No orders. Python 3.9 stdlib.

The API key is read ONLY from Claude-QuantOS/.odds_api_key (one line, chmod 600) and is never printed or logged.
Quota: every odds call logs x-requests-remaining; the scan stops if it drops below --min-remaining (default 100).
Usage: python3 sports/live_scan.py [--hours 48] [--top 15]
"""
import argparse, difflib, json, os, re, sys, unicodedata, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ODDS = "https://api.the-odds-api.com/v4"
GAMMA = "https://gamma-api.polymarket.com/events/keyset"
CLOB_BOOKS = "https://clob.polymarket.com/books"
UA = {"user-agent": "claude-quantos-research"}
WANT = [r"^americanfootball_nfl$", r"^basketball_nba$", r"^icehockey_nhl$", r"^baseball_mlb", r"^soccer_epl$",
        r"^soccer_spain_la_liga$", r"^soccer_italy_serie_a$", r"^soccer_germany_bundesliga$",
        r"^soccer_france_ligue_one$", r"^soccer_uefa_champs_league$", r"^soccer_uefa_europa_league$",
        r"^soccer_usa_mls$", r"^tennis_atp", r"^tennis_wta", r"^mma_mixed_martial_arts$"]
SHARP_FALLBACK = "pinnacle,betfair_ex_eu,matchbook,smarkets"
FALLBACK_FEE = 0.05


def read_key():
    path = os.path.join(ROOT, ".odds_api_key")
    if not os.path.exists(path):
        sys.exit(f"missing {path}: create it with the key on one line and chmod 600")
    with open(path) as f:
        return f.read().strip()


class Quota(Exception):
    pass


def odds_get(path, params, key, min_remaining, log):
    q = urllib.parse.urlencode(dict(params, apiKey=key))
    req = urllib.request.Request(f"{ODDS}{path}?{q}", headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            rem = r.headers.get("x-requests-remaining")
            used = r.headers.get("x-requests-used")
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:  # never echo the URL (it contains the key)
        raise RuntimeError(f"odds api HTTP {e.code} on {path}") from None
    log.append({"path": path, "remaining": rem, "used": used})
    if rem is not None and float(rem) < min_remaining:
        raise Quota(f"requests remaining {rem} < {min_remaining}; stopping")
    return data


def http(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=dict(UA, **{"content-type": "application/json"}))
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def norm(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(fc|cf|afc|sc|ac|ssc|club|de|the|cd|ud|rc|sd)\b", " ", s)
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s).split())


def same_team(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    if ta <= tb or tb <= ta:  # "Lakers" vs "Los Angeles Lakers"
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def devig(prices):
    inv = [1 / p for p in prices]
    s = sum(inv)
    return [x / s for x in inv]


def fair_probs(game):
    """Pinnacle if present, else median of sharp fallbacks. Returns ({outcome_name: prob}, source)."""
    books = {b["key"]: b for b in game.get("bookmakers") or []}
    order = ["pinnacle"] + [k for k in SHARP_FALLBACK.split(",") if k != "pinnacle"]
    per_book = []
    for k in order:
        b = books.get(k)
        if not b:
            continue
        mk = next((m for m in b.get("markets") or [] if m["key"] == "h2h"), None)
        if not mk or len(mk["outcomes"]) < 2:
            continue
        names = [o["name"] for o in mk["outcomes"]]
        probs = devig([float(o["price"]) for o in mk["outcomes"]])
        per_book.append((k, dict(zip(names, probs))))
        if k == "pinnacle":
            return per_book[0][1], "pinnacle"
    if not per_book:
        return None, None
    names = per_book[0][1].keys()
    med = {}
    for n in names:
        vals = sorted(p[n] for _, p in per_book if n in p)
        med[n] = vals[len(vals) // 2]
    s = sum(med.values())
    return {n: v / s for n, v in med.items()}, "consensus:" + "+".join(k for k, _ in per_book)


def polymarket_games(hours):
    now = datetime.now(timezone.utc)
    f = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (f"{GAMMA}?tag_slug=sports&closed=false&limit=100&end_date_min={f(now)}"
           f"&end_date_max={f(now + timedelta(days=8))}")
    out, cur = [], None
    while True:
        d = http(url + (f"&after_cursor={cur}" if cur else ""))
        for e in d.get("events") or []:
            mls = [m for m in e.get("markets") or [] if m.get("sportsMarketType") == "moneyline"
                   and m.get("gameStartTime") and not m.get("closed") and m.get("acceptingOrders", True)]
            if not mls:
                continue
            try:
                start = datetime.fromisoformat(mls[0]["gameStartTime"].replace("+00", "+00:00").replace(" ", "T"))
            except ValueError:
                continue
            if not (now <= start <= now + timedelta(hours=hours)):
                continue
            out.append({"event": e, "markets": mls, "start": start})
        cur = d.get("next_cursor")
        if not d.get("events") or not cur:
            return out


def outcome_tokens(pg):
    """[(label, token, fee_rate)] for every tradable outcome of a Polymarket game."""
    res = []
    for m in pg["markets"]:
        toks = m.get("clobTokenIds")
        toks = json.loads(toks) if isinstance(toks, str) else toks
        outs = m.get("outcomes")
        outs = json.loads(outs) if isinstance(outs, str) else outs
        fs = m.get("feeSchedule") or {}
        rate = 0.0 if m.get("feesEnabled") is False else float(fs.get("rate", FALLBACK_FEE))
        if outs == ["Yes", "No"]:  # soccer style: one Yes/No market per result (home / draw / away)
            label = m.get("groupItemTitle") or m.get("question")
            res.append((label, toks[0], rate))
        else:                       # two-way style: outcomes are the team names
            res += [(o, t, rate) for o, t in zip(outs, toks)]
    return res


def best_ask(book):
    asks = [(float(l["price"]), float(l["size"])) for l in (book or {}).get("asks") or []]
    if not asks:
        return None, 0.0
    p = min(a for a, _ in asks)
    return p, sum(s for a, s in asks if a == p)


def match(game, pms):
    """Unique Polymarket game with both teams matching and start within 3h; else None (ambiguous/none)."""
    t0 = datetime.fromisoformat(game["commence_time"].replace("Z", "+00:00"))
    cands = []
    for pg in pms:
        if abs((pg["start"] - t0).total_seconds()) > 3 * 3600:
            continue
        title = pg["event"].get("title") or ""
        parts = re.split(r"\s+(?:vs\.?|v\.?|@)\s+", title, flags=re.I)
        if len(parts) != 2:
            continue
        a, b = parts
        if (same_team(a, game["home_team"]) and same_team(b, game["away_team"])) or \
           (same_team(a, game["away_team"]) and same_team(b, game["home_team"])):
            cands.append(pg)
    return cands[0] if len(cands) == 1 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--min-remaining", type=float, default=100)
    ap.add_argument("--cache", action="store_true", help="reuse sports/data/odds_cache.json (no API requests)")
    a = ap.parse_args()
    log = []
    now = datetime.now(timezone.utc)
    games, rows, matched = [], [], 0
    pms = polymarket_games(a.hours)
    if a.cache:
        games = json.load(open(os.path.join(ROOT, "sports", "data", "odds_cache.json")))["games"]
        keys = sorted({g["sport_key"] for g in games})
    else:
        key = read_key()
        sports = odds_get("/sports", {}, key, a.min_remaining, log)  # /sports does not count against quota
        keys = [s["key"] for s in sports if s.get("active") and not s.get("has_outrights")
                and any(re.search(w, s["key"]) for w in WANT)]
    try:
        for sk in ([] if a.cache else keys):
            try:
                data = odds_get(f"/sports/{sk}/odds", {"regions": "eu,us", "markets": "h2h", "oddsFormat": "decimal",
                                                       "bookmakers": SHARP_FALLBACK}, key, a.min_remaining, log)
            except RuntimeError as e:
                print(f"skip {sk}: {e}")
                continue
            for g in data:
                t0 = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
                if now <= t0 <= now + timedelta(hours=a.hours):
                    g["sport_key"] = sk
                    games.append(g)
    except Quota as q:
        print(f"QUOTA STOP: {q}")
    if not a.cache:
      with open(os.path.join(ROOT, "sports", "data", "odds_cache.json"), "w") as f:
        json.dump({"ts": now.isoformat(), "games": games}, f)  # key is never stored
    pairs = []
    for g in games:
        pg = match(g, pms)
        fair, src = fair_probs(g)
        if pg and fair:
            matched += 1
            pairs.append((g, pg, fair, src))
    toks = [t for _, pg, _, _ in pairs for _, t, _ in outcome_tokens(pg)]
    books = {}
    for i in range(0, len(toks), 200):
        for b in http(CLOB_BOOKS, [{"token_id": t} for t in toks[i:i + 200]]):
            books[b["asset_id"]] = b
    for g, pg, fair, src in pairs:
        for label, tok, rate in outcome_tokens(pg):
            if re.search(r"\bdraw\b", label or "", re.I):  # "Draw (A vs. B)" contains team names: check first
                name = "Draw" if "Draw" in fair else None
            else:
                hits = [n for n in fair if n != "Draw" and same_team(label, n)]
                name = hits[0] if len(hits) == 1 else None  # strict: skip ambiguous
            if name is None:
                continue
            ask, depth = best_ask(books.get(tok))
            if ask is None:
                continue
            fee = rate * ask * (1 - ask)
            rows.append({"sport": g["sport_key"], "game": f"{g['home_team']} vs {g['away_team']}",
                         "outcome": name, "start": g["commence_time"], "fair": fair[name], "ask": ask,
                         "fee": fee, "edge": fair[name] - ask - fee, "depth_shares": depth,
                         "depth_usd": depth * ask, "source": src, "pm_event": pg["event"].get("slug")})
    rows.sort(key=lambda r: -r["edge"])
    os.makedirs(os.path.join(ROOT, "sports", "data"), exist_ok=True)
    with open(os.path.join(ROOT, "sports", "data", "live_scan.jsonl"), "a") as f:
        f.write(json.dumps({"ts": now.isoformat(), "rows": rows, "quota": log[-1] if log else None}) + "\n")
    print(f"{now.isoformat()[:19]} sports={len(keys)} odds games(<{a.hours:.0f}h)={len(games)} "
          f"polymarket games={len(pms)} matched={matched} outcomes priced={len(rows)}")
    print(f"{'game':<44}{'outcome':<24}{'start':>17}{'fair':>6}{'ask':>6}{'fee':>6}{'edge':>7}{'depth$':>8}  src")
    for r in rows[:a.top]:
        print(f"{r['game'][:43]:<44}{r['outcome'][:23]:<24}{r['start'][5:16]:>17}{r['fair']:6.3f}{r['ask']:6.3f}"
              f"{r['fee']:6.3f}{r['edge']:+7.3f}{r['depth_usd']:8.0f}  {r['source']}")
    print(f"odds api requests remaining: {log[-1]['remaining'] if log else '?'} (used {log[-1]['used'] if log else '?'})")


if __name__ == "__main__":
    main()
