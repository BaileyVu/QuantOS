"""Paper engine for Polymarket liquidity-rewards rule v2. Public data only, no keys, no orders. Python 3.9 stdlib.

Rule: notes/rewards_rule_v2.json. Every minute, for up to 10 selected markets, it virtually rests quotes at the
best bid/ask of the YES book, detects fills (book-cross or public trade at/through our level), keeps a cash and
inventory ledger in SQLite, applies per-market and daily loss stops, and accrues ESTIMATED (unverified) rewards.

  python3 paper/rewards_paper.py run [--minutes N] [--db data/rewards_paper.db]
  python3 paper/rewards_paper.py status [--db ...]
"""
import argparse, json, math, os, re, sqlite3, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_rule(path):
    global RULE, SEL, SIZ, RISK
    RULE = json.load(open(path))
    SEL, SIZ, RISK = RULE["selection"], RULE["sizing"], RULE["risk"]


load_rule(os.path.join(ROOT, "notes", "rewards_rule_v2.json"))
CLOB = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com/trades"
STALE_QUOTE_S = 150  # quotes older than this (e.g. after a restart) are void: no fills against them
EPS = 1e-9


# ---------------------------------------------------------------- pure logic (unit-tested)

def side_size(min_size):
    """Shares per side: full fill + adverse move must lose <= max_loss; None if min_size breaks the cap."""
    cap = min(SIZ["max_shares"], math.floor(SIZ["max_loss_per_side_usd"] / SIZ["adverse_move"] + EPS))
    return float(cap) if min_size <= cap + EPS else None


def keyword_hit(text):
    t = re.sub(r"[-_]", " ", (text or "").lower())
    hits = [k for k in SEL["exclude_keywords"] if re.search(r"\b" + re.escape(k.replace("-", " ")), t)]
    return hits + [rx for rx in SEL.get("exclude_regex", []) if re.search(rx, t)]


def dollar_capped(shares, px, min_size):
    """Shares we may rest at price px (cost per share px) so a fill to zero loses <= max_cost_per_side_usd.
    Returns 0 if the cap cannot meet the reward min_size. No-op when the rule has no dollar cap."""
    max_cost = SIZ.get("max_cost_per_side_usd")
    if max_cost is None or shares <= EPS:
        return shares
    capped = min(shares, math.floor(max_cost / px + EPS)) if px > EPS else shares
    return float(capped) if capped >= min_size - EPS else 0.0


def quad_q(levels, mid, v, side):
    q = 0.0
    for p, size in levels:
        s = abs(mid - p) * 100
        if s <= v and ((side == "bid" and p <= mid + EPS) or (side == "ask" and p >= mid - EPS)):
            q += size * ((v - s) / v) ** 2
    return q


def combine_q(qb, qa, mid):
    if 0.10 <= mid <= 0.90:
        return max(min(qb, qa), max(qb, qa) / 3)
    return min(qb, qa)


def desired_quotes(net, cap, cash, bb, ba, min_size=0.0):
    """(bid_shares, ask_shares). Flat -> both sides; with inventory -> only the reducing side.
    net > 0 means long YES; net < 0 means long NO. A side is quoted only if cash covers it."""
    if net > EPS:
        return 0.0, min(cap, net)            # selling YES we hold needs no cash
    if net < -EPS:
        return min(cap, -net), 0.0           # bid fill sells NO we hold (merge-equivalent): no cash needed
    bid = dollar_capped(cap, bb, min_size)
    ask = dollar_capped(cap, 1 - ba, min_size)
    bid = bid if cash >= bid * bb - EPS else 0.0
    ask = ask if cash >= ask * (1 - ba) - EPS else 0.0
    return bid, ask


def apply_fill(pos, side, px, shares):
    """pos = dict(yes, no, cash). Bid fill = buy YES (or sell NO if long NO). Ask fill = sell YES (or buy NO)."""
    yes, no, cash = pos["yes"], pos["no"], pos["cash"]
    if side == "bid":
        close = min(no, shares)              # sell NO at (1 - px)
        no -= close; cash += close * (1 - px)
        rest = shares - close
        yes += rest; cash -= rest * px
    else:
        close = min(yes, shares)             # sell YES at px
        yes -= close; cash += close * px
        rest = shares - close
        no += rest; cash -= rest * (1 - px)
    return {"yes": yes, "no": no, "cash": cash}


def detect_fills(quote, bb, ba, trades):
    """quote = (bid_px, bid_sz, ask_px, ask_sz). trades: list of (yes_px, hits) since the quote."""
    qbp, qbs, qap, qas = quote
    out = []
    if qbs > EPS and (ba <= qbp + EPS or any(h == "bid" and p <= qbp + EPS for p, h in trades)):
        out.append(("bid", qbp, qbs))
    if qas > EPS and (bb >= qap - EPS or any(h == "ask" and p >= qap - EPS for p, h in trades)):
        out.append(("ask", qap, qas))
    return out


def stop_state(market_pnl_today, total_pnl_today):
    """Returns (market_stopped, all_stopped)."""
    return market_pnl_today <= RISK["per_market_loss_stop_usd"] + EPS, total_pnl_today <= RISK["daily_stop_usd"] + EPS


# ---------------------------------------------------------------- IO

def http(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json",
                                                          "user-agent": "claude-quantos-paper"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def pmap(fn, items, workers=8):
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, items))


def fetch_books(tokens):
    books = {}
    for i in range(0, len(tokens), 200):
        for b in http(f"{CLOB}/books", [{"token_id": t} for t in tokens[i:i + 200]]):
            books[b["asset_id"]] = b
    return books


def parse_book(b):
    bids = [(float(l["price"]), float(l["size"])) for l in (b or {}).get("bids") or []]
    asks = [(float(l["price"]), float(l["size"])) for l in (b or {}).get("asks") or []]
    if not bids or not asks:
        return None
    return bids, asks, max(p for p, _ in bids), min(p for p, _ in asks)


def recent_trades(cid, since_ts):
    try:
        rows = http(f"{DATA_API}?market={cid}&limit=100")
    except Exception:
        return []
    out = []
    for t in rows or []:
        if int(t["timestamp"]) < since_ts:
            continue
        idx = int(t.get("outcomeIndex", 0))
        px = float(t["price"])
        yes_px = px if idx == 0 else 1 - px
        lifts = (t.get("side") == "BUY") == (idx == 0)
        out.append((round(yes_px, 4), "ask" if lifts else "bid"))
    return out


def vol_cents(token):
    try:
        h = http(f"{CLOB}/prices-history?market={token}&interval=1d&fidelity=5").get("history") or []
    except Exception:
        return None
    ps = [float(x["p"]) for x in h]
    d = [(b - a) * 100 for a, b in zip(ps, ps[1:])]
    if len(d) < 10:
        return None
    mu = sum(d) / len(d)
    return math.sqrt(sum((x - mu) ** 2 for x in d) / (len(d) - 1))


def resolution(cid):
    """'Up'-style index of winning outcome: 1.0 if YES won, 0.0 if NO won, None if unresolved."""
    try:
        g = http(f"{GAMMA}/markets?condition_ids={cid}&closed=true")
    except Exception:
        return None
    if not g:
        return None
    p = g[0].get("outcomePrices")
    p = json.loads(p) if isinstance(p, str) else p
    if p and sorted(p) == ["0", "1"]:
        return 1.0 if p[0] == "1" else 0.0
    return None


def candidates(now):
    """Reward markets passing rule v2 static filters, with live book checks; ranked by est. reward."""
    out, cursor = [], ""
    while True:
        page = http(f"{CLOB}/sampling-markets" + (f"?next_cursor={cursor}" if cursor else ""))
        for m in page.get("data") or []:
            rw = m.get("rewards") or {}
            rate = sum(float(r.get("rewards_daily_rate") or 0) for r in rw.get("rates") or [])
            toks = m.get("tokens") or []
            if not (0 < rate <= SEL["daily_reward_pool_usd_max"] and m.get("accepting_orders") and not m.get("closed")
                    and len(toks) == 2 and rw.get("max_spread")):
                continue
            end = m.get("end_date_iso")
            try:
                e = datetime.fromisoformat(end.replace("Z", "+00:00"))
                e = e if e.tzinfo else e.replace(tzinfo=timezone.utc)
                days = (e - now).total_seconds() / 86400
            except (AttributeError, ValueError):
                continue
            if days < SEL["min_days_to_resolution"]:
                continue
            if keyword_hit(m.get("market_slug")) or keyword_hit(m.get("question")):
                continue
            size = side_size(float(rw.get("min_size") or 0))
            if size is None:
                continue
            out.append({"cid": m["condition_id"], "slug": m.get("market_slug"), "yes": toks[0]["token_id"],
                        "rate": rate, "min_size": float(rw.get("min_size") or 0), "v": float(rw["max_spread"]),
                        "end": end, "size": size})
        cursor = page.get("next_cursor")
        if not cursor or cursor == "LTE=":
            break
    books = fetch_books([c["yes"] for c in out])
    ranked = []
    for c in out:
        pb = parse_book(books.get(c["yes"]))
        if not pb:
            continue
        bids, asks, bb, ba = pb
        mid = (bb + ba) / 2
        lo, hi = SEL["mid_range"]
        if not (lo <= mid <= hi) or (ba - bb) * 100 / 2 > c["v"]:
            continue
        w = lambda s: ((c["v"] - s) / c["v"]) ** 2
        bsz, asz = dollar_capped(c["size"], bb, c["min_size"]), dollar_capped(c["size"], 1 - ba, c["min_size"])
        if bsz <= EPS and asz <= EPS:
            continue  # dollar cap cannot meet min_size on either side
        ours = combine_q(bsz * w((mid - bb) * 100), asz * w((ba - mid) * 100), mid)
        comp = combine_q(quad_q(bids, mid, c["v"], "bid"), quad_q(asks, mid, c["v"], "ask"), mid)
        share = ours / (comp + ours) if ours else 0
        if share > SEL["max_share_of_qualifying_q"]:
            continue
        c["est_reward_day"] = c["rate"] * share
        ranked.append(c)
    ranked.sort(key=lambda c: -c["est_reward_day"])
    final = []
    top = ranked[:SEL.get("vol_rank_top_n", 40)]
    for c, vol in zip(top, pmap(lambda c: vol_cents(c["yes"]), top)):
        if vol is not None and vol < SEL["vol_5m_cents_max"]:
            c["vol"] = vol
            final.append(c)
    return final


# ---------------------------------------------------------------- ledger

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS markets (cid TEXT PRIMARY KEY, meta TEXT, status TEXT, stopped_day TEXT,
    yes REAL DEFAULT 0, no REAL DEFAULT 0, cash_flow REAL DEFAULT 0, last_mid REAL, quote TEXT, quote_ts REAL,
    day TEXT, day_start_value REAL DEFAULT 0, reward_est REAL DEFAULT 0, settled REAL);
CREATE TABLE IF NOT EXISTS fills (id INTEGER PRIMARY KEY, tick INTEGER, ts TEXT, cid TEXT, side TEXT, px REAL,
    shares REAL, UNIQUE(tick, cid, side));
CREATE TABLE IF NOT EXISTS ticks (tick INTEGER PRIMARY KEY, ts TEXT, equity REAL, cash REAL, reward_est REAL,
    inv_pnl REAL, live INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts TEXT, cid TEXT, kind TEXT, detail TEXT);
"""


class Ledger:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        if self.get("cash") is None:
            self.set("cash", RISK["bankroll_usd"])
            self.set("bankroll", RISK["bankroll_usd"])
            self.set("rule_id", RULE["rule_id"])
            self.db.commit()

    def get(self, k):
        r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else None

    def set(self, k, v):
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, json.dumps(v)))

    def event(self, cid, kind, detail):
        self.db.execute("INSERT INTO events (ts,cid,kind,detail) VALUES (?,?,?,?)",
                        (datetime.now(timezone.utc).isoformat(), cid, kind, json.dumps(detail)))

    def markets(self, where="1=1", args=()):
        cols = [d[1] for d in self.db.execute("PRAGMA table_info(markets)")]
        return [dict(zip(cols, r)) for r in self.db.execute(f"SELECT * FROM markets WHERE {where}", args)]

    def update_market(self, cid, **kw):
        sets = ", ".join(f"{k}=?" for k in kw)
        self.db.execute(f"UPDATE markets SET {sets} WHERE cid=?", (*kw.values(), cid))


def value(m, mid=None):
    """Mark-to-mid value of a market's inventory plus its cash flow (inventory PnL since entry)."""
    if m["settled"] is not None:
        mid = m["settled"]
    mid = m["last_mid"] if mid is None else mid
    return m["cash_flow"] + m["yes"] * (mid or 0) + m["no"] * (1 - (mid or 0))


# ---------------------------------------------------------------- engine

def utc_day(ts=None):
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).strftime("%Y-%m-%d")


def select(led, now):
    active = led.markets("status='active'")
    slots = SEL["max_markets"] - len(active)
    if slots <= 0:
        return
    today = utc_day()
    known = {m["cid"]: m for m in led.markets()}
    for c in candidates(now):
        if slots <= 0:
            break
        k = known.get(c["cid"])
        if k and (k["status"] == "active" or k["stopped_day"] == today or k["status"] == "settled"):
            continue
        if k:
            led.update_market(c["cid"], status="active", meta=json.dumps(c))
        else:
            led.db.execute("INSERT INTO markets (cid, meta, status, day) VALUES (?,?,?,?)",
                           (c["cid"], json.dumps(c), "active", today))
        led.event(c["cid"], "select", {k2: c[k2] for k2 in ("slug", "rate", "min_size", "v", "vol", "est_reward_day")})
        slots -= 1
    led.db.commit()


def tick(led, tick_id):
    if led.get("last_tick") is not None and tick_id <= led.get("last_tick"):
        return None  # idempotent: tick already processed
    now = time.time()
    today = utc_day(now)
    if led.get("day") != today:  # new UTC day: reset day baselines and stops
        for m in led.markets("settled IS NULL"):
            led.update_market(m["cid"], day=today, day_start_value=value(m))
            if m["status"] == "stopped":
                led.update_market(m["cid"], status="idle")
        led.set("day", today)
        led.set("all_stopped", False)
    live = led.markets("settled IS NULL AND (status='active' OR yes>0 OR no>0)")
    books = fetch_books([json.loads(m["meta"])["yes"] for m in live]) if live else {}
    since = int(now) - 75
    trades = dict(zip([m["cid"] for m in live], pmap(lambda m: recent_trades(m["cid"], since), live)))
    cash = led.get("cash")
    rewards_tick = 0.0
    for m in live:
        meta = json.loads(m["meta"])
        pb = parse_book(books.get(meta["yes"]))
        if pb is None:  # book gone or one-sided: stop quoting, try to settle
            r = resolution(m["cid"])
            if r is not None:
                payout = m["yes"] * r + m["no"] * (1 - r)
                cash += payout
                led.update_market(m["cid"], settled=r, status="settled", cash_flow=m["cash_flow"] + payout,
                                  yes=0, no=0, quote=None)
                led.event(m["cid"], "settle", {"yes_won": r, "payout": payout})
            else:
                led.update_market(m["cid"], quote=None)
            continue
        bids, asks, bb, ba = pb
        mid = (bb + ba) / 2
        pos = {"yes": m["yes"], "no": m["no"], "cash": m["cash_flow"]}
        # fills against the previous (non-stale) quote
        if m["quote"] and m["quote_ts"] and now - m["quote_ts"] <= STALE_QUOTE_S:
            for side, px, sh in detect_fills(json.loads(m["quote"]), bb, ba, trades.get(m["cid"], [])):
                cur = led.db.execute("INSERT OR IGNORE INTO fills (tick,ts,cid,side,px,shares) VALUES (?,?,?,?,?,?)",
                                     (tick_id, datetime.now(timezone.utc).isoformat(), m["cid"], side, px, sh))
                if cur.rowcount:
                    before = pos["cash"]
                    pos = apply_fill(pos, side, px, sh)
                    cash += pos["cash"] - before
        m.update(yes=pos["yes"], no=pos["no"], cash_flow=pos["cash"], last_mid=mid)
        led.update_market(m["cid"], yes=pos["yes"], no=pos["no"], cash_flow=pos["cash"], last_mid=mid)
        if m["day"] != today:
            led.update_market(m["cid"], day=today, day_start_value=value(m))
            m["day_start_value"] = value(m)
    # stops (inventory PnL only; rewards are unverified)
    led.db.commit()
    live = led.markets("settled IS NULL AND (status='active' OR yes>0 OR no>0)")
    day_pnl = sum(value(m) - m["day_start_value"] for m in led.markets())
    m_stop_all = stop_state(0, day_pnl)[1]
    if m_stop_all and not led.get("all_stopped"):
        led.set("all_stopped", True)
        led.event(None, "daily_stop", {"day_pnl": day_pnl})
    for m in live:
        meta = json.loads(m["meta"])
        pb = parse_book(books.get(meta["yes"]))
        mstop, _ = stop_state(value(m) - m["day_start_value"], day_pnl)
        if mstop and m["status"] == "active":
            led.update_market(m["cid"], status="stopped", stopped_day=today)
            led.event(m["cid"], "market_stop", {"day_pnl": value(m) - m["day_start_value"]})
            m["status"] = "stopped"
        if pb is None or m["status"] != "active" or led.get("all_stopped"):
            led.update_market(m["cid"], quote=None)
            continue
        bids, asks, bb, ba = pb
        mid, v = (bb + ba) / 2, meta["v"]
        lo, hi = SEL["mid_range"]
        net = m["yes"] - m["no"]
        if not (lo <= mid <= hi) and abs(net) < EPS:
            led.update_market(m["cid"], quote=None)  # out of range and flat: sit out
            continue
        bsz, asz = desired_quotes(net, meta["size"], cash, bb, ba, meta.get("min_size", 0.0))
        w = lambda s: ((v - s) / v) ** 2 if s <= v else 0.0
        ours = combine_q(bsz * w((mid - bb) * 100), asz * w((ba - mid) * 100), mid)
        comp = combine_q(quad_q(bids, mid, v, "bid"), quad_q(asks, mid, v, "ask"), mid)
        share = min(SEL["max_share_of_qualifying_q"], ours / (comp + ours)) if ours else 0.0
        r = meta["rate"] / 1440 * share
        rewards_tick += r
        led.update_market(m["cid"], quote=json.dumps([bb, bsz, ba, asz]), quote_ts=now,
                          reward_est=m["reward_est"] + r)
    led.set("cash", cash)
    allm = led.markets()
    inv_pnl = sum(value(m) for m in allm)
    reward_total = sum(m["reward_est"] for m in allm)
    equity = led.get("bankroll") + inv_pnl
    led.db.execute("INSERT OR REPLACE INTO ticks VALUES (?,?,?,?,?,?,?,?)",
                   (tick_id, datetime.now(timezone.utc).isoformat(), equity, cash, reward_total, inv_pnl,
                    len(live), "all_stopped" if led.get("all_stopped") else ""))
    led.set("last_tick", tick_id)
    led.db.commit()
    return {"equity": equity, "cash": cash, "inv_pnl": inv_pnl, "reward_est": reward_total, "live": len(live),
            "day_pnl": day_pnl, "rewards_tick": rewards_tick}


def status(led):
    allm = led.markets()
    inv = sum(value(m) for m in allm)
    print(f"rule {led.get('rule_id')}  bankroll ${led.get('bankroll'):.2f}  cash ${led.get('cash'):.2f}  "
          f"equity (excl. rewards) ${led.get('bankroll') + inv:.2f}  inventory PnL ${inv:+.2f}")
    print(f"estimated rewards (UNVERIFIED) ${sum(m['reward_est'] for m in allm):.2f}  "
          f"fills {led.db.execute('SELECT COUNT(*) FROM fills').fetchone()[0]}  "
          f"daily stop {'ON' if led.get('all_stopped') else 'off'}  last tick {led.get('last_tick')}")
    print(f"{'market':<50}{'status':>9}{'yes':>7}{'no':>7}{'mid':>7}{'invPnL':>9}{'rewEst':>8}")
    for m in sorted(allm, key=lambda m: m["status"]):
        meta = json.loads(m["meta"])
        print(f"{(meta.get('slug') or '')[:50]:<50}{m['status']:>9}{m['yes']:7.1f}{m['no']:7.1f}"
              f"{m['last_mid'] or 0:7.3f}{value(m):9.2f}{m['reward_est']:8.2f}")


def run(led, minutes):
    end = time.time() + minutes * 60 if minutes else float("inf")
    last_sel = 0.0
    while time.time() < end:
        t0 = time.time()
        try:
            if t0 - last_sel >= 3600:
                select(led, datetime.now(timezone.utc))
                last_sel = t0
            res = tick(led, int(t0 // 60))
            if res:
                print(f"{datetime.now(timezone.utc).isoformat()[:19]} live={res['live']} equity=${res['equity']:.2f} "
                      f"cash=${res['cash']:.2f} invPnL=${res['inv_pnl']:+.2f} dayPnL=${res['day_pnl']:+.2f} "
                      f"rewardEst=${res['reward_est']:.3f} (unverified)", flush=True)
        except Exception as e:
            print(f"{datetime.now(timezone.utc).isoformat()[:19]} paper error: {e}", flush=True)
        time.sleep(max(1.0, 60 - (time.time() - t0)))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["run", "status"])
    p.add_argument("--minutes", type=float, default=0, help="0 = run until stopped")
    p.add_argument("--db", default=os.path.join(ROOT, "data", "rewards_paper.db"))
    p.add_argument("--rule", default=os.path.join(ROOT, "notes", "rewards_rule_v2.json"))
    a = p.parse_args()
    load_rule(a.rule)
    led = Ledger(a.db)
    if a.cmd == "status":
        status(led)
    else:
        run(led, a.minutes)
        status(led)


if __name__ == "__main__":
    main()
