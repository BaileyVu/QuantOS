"""S3 shadow: read-only simulation of quoting for Polymarket liquidity rewards. No keys, no orders.

Every 60s, for a watchlist of reward markets (top N from pm_rewards; markets ending after the run window are
preferred; closed/rolled markets such as daily weather buckets are replaced at the hourly re-pick), we pretend to
rest a bid at the best bid and an ask at the best ask of the YES book, each max($50, min_size shares), and record:
  * estimated reward for that minute = daily_rate/1440 * our_Q / (competitor_Q + our_Q), with the documented
    quadratic score size*((v-s)/v)^2 (s cents from mid, v = max_spread), and Q = max(min(Qb,Qa), max(Qb,Qa)/3)
    when mid is in [0.10, 0.90], else min(Qb,Qa) (two-sided required)
  * cross fills (inventory): next snapshot best ask <= our prior bid, or best bid >= our prior ask
  * level_gone (pessimistic diagnostic only): our prior bid level vanished (best bid < our bid) / same for ask
  * public trades (data-api /trades) since the last minute, as YES-equivalent prices, and whether any printed
    at or through our resting levels
  * markouts of each cross fill: mid move vs fill price 1, 5 and 30 minutes later (positive = good for us)
Inventory is marked to mid; exposure per market is capped at 2 quote sizes. All numbers are hypothetical.
Usage: python3 -u pm_rewards_shadow.py --hours 24 --markets 30 --out data/pm_rewards_shadow.jsonl
"""
import argparse, json, os, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from pm_rewards import fetch_books, http, scan

QUOTE_SIDE_USD = 50.0
DATA_API = "https://data-api.polymarket.com/trades"
MARKOUTS = (1, 5, 30)


def two_sided_q(qb, qa, mid):
    if 0.10 <= mid <= 0.90:
        return max(min(qb, qa), max(qb, qa) / 3)
    return min(qb, qa)


def side_q(levels, mid, v, side):
    q, usd = 0.0, 0.0
    for l in levels:
        p, size = float(l["price"]), float(l["size"])
        s = abs(mid - p) * 100
        if s <= v and ((side == "bid" and p <= mid) or (side == "ask" and p >= mid)):
            q += size * ((v - s) / v) ** 2
            usd += size * (p if side == "bid" else 1 - p)
    return q, usd


def recent_trades(cid):
    try:
        rows = http(f"{DATA_API}?market={cid}&limit=100")
    except Exception:
        return None
    out = []
    for t in rows or []:
        px = float(t["price"])
        yes_px = px if int(t.get("outcomeIndex", 0)) == 0 else 1 - px
        # YES-equivalent aggressor side: BUY YES or SELL NO lifts the ask; SELL YES or BUY NO hits the bid
        lifts = (t.get("side") == "BUY") == (int(t.get("outcomeIndex", 0)) == 0)
        out.append({"ts": int(t["timestamp"]), "yes_px": round(yes_px, 4), "size": float(t["size"]),
                    "hits": "ask" if lifts else "bid", "tx": t.get("transactionHash")})
    return out


class Sim:
    def __init__(self, m):
        self.m = m
        self.pos = self.cash = self.reward = 0.0
        self.cross_fills = self.level_gone = self.trades_at_bid = self.trades_at_ask = 0
        self.quote = None
        self.mid = None
        self.minutes = self.quoted_minutes = 0
        self.closed = False
        self.seen_tx = set()
        self.last_trade_ts = int(time.time())
        self.pending = []  # [fill_ts, side, price, {1: None, 5: None, 30: None}]
        self.markouts = {k: [] for k in MARKOUTS}

    def step(self, book, trades, now):
        bids, asks = book.get("bids") or [], book.get("asks") or []
        if not bids or not asks:
            return None
        bb = max(float(l["price"]) for l in bids)
        ba = min(float(l["price"]) for l in asks)
        mid, v = (bb + ba) / 2, self.m["max_spread"]
        rec = {"bb": bb, "ba": ba, "mid": mid, "fills": [], "level_gone": []}

        new_trades = []
        if trades is not None:
            for t in trades:
                key = (t["tx"], t["yes_px"], t["size"], t["ts"])
                if t["ts"] >= self.last_trade_ts - 5 and key not in self.seen_tx:
                    self.seen_tx.add(key)
                    new_trades.append(t)
            self.last_trade_ts = max([self.last_trade_ts] + [t["ts"] for t in new_trades])
        rec["trades"] = [{k: t[k] for k in ("ts", "yes_px", "size", "hits")} for t in new_trades]

        if self.quote:
            qbp, qbs, qap, qas = self.quote
            if qbs and any(t["hits"] == "bid" and t["yes_px"] <= qbp + 1e-9 for t in new_trades):
                self.trades_at_bid += 1
                rec["trade_at_our_bid"] = True
            if qas and any(t["hits"] == "ask" and t["yes_px"] >= qap - 1e-9 for t in new_trades):
                self.trades_at_ask += 1
                rec["trade_at_our_ask"] = True
            if qbs and ba <= qbp + 1e-9:
                self.pos += qbs; self.cash -= qbs * qbp; self.cross_fills += 1
                rec["fills"].append(["bid", qbp, round(qbs, 2)]); self.pending.append([now, "bid", qbp, {}])
            elif qbs and bb < qbp - 1e-9:
                self.level_gone += 1; rec["level_gone"].append("bid")
            if qas and bb >= qap - 1e-9:
                self.pos -= qas; self.cash += qas * qap; self.cross_fills += 1
                rec["fills"].append(["ask", qap, round(qas, 2)]); self.pending.append([now, "ask", qap, {}])
            elif qas and ba > qap + 1e-9:
                self.level_gone += 1; rec["level_gone"].append("ask")

        done = []
        for p in self.pending:
            for k in MARKOUTS:
                if k not in p[3] and now - p[0] >= k * 60 - 5:
                    mo = (mid - p[2]) if p[1] == "bid" else (p[2] - mid)
                    p[3][k] = round(mo, 4)
                    self.markouts[k].append(mo)
            if len(p[3]) == len(MARKOUTS):
                done.append(p)
                rec.setdefault("markouts", []).append({"side": p[1], "px": p[2], **{f"m{k}": p[3][k] for k in MARKOUTS}})
        self.pending = [p for p in self.pending if p not in done]

        bid_sz = max(QUOTE_SIDE_USD / bb, self.m["min_size"]) if bb > 0 else 0
        ask_sz = max(QUOTE_SIDE_USD / (1 - ba), self.m["min_size"]) if ba < 1 else 0
        cap = 2 * max(bid_sz, ask_sz)
        if self.pos >= cap:
            bid_sz = 0
        if self.pos <= -cap:
            ask_sz = 0
        s_b, s_a = (mid - bb) * 100, (ba - mid) * 100
        our_b = bid_sz * ((v - s_b) / v) ** 2 if s_b <= v and bid_sz else 0.0
        our_a = ask_sz * ((v - s_a) / v) ** 2 if s_a <= v and ask_sz else 0.0
        cq_b, cusd_b = side_q(bids, mid, v, "bid")
        cq_a, cusd_a = side_q(asks, mid, v, "ask")
        comp, ours = two_sided_q(cq_b, cq_a, mid), two_sided_q(our_b, our_a, mid)
        share = ours / (comp + ours) if ours else 0.0
        r = self.m["rate"] / 1440 * share
        self.reward += r
        self.minutes += 1
        self.quoted_minutes += 1 if ours else 0
        self.quote = (bb, bid_sz, ba, ask_sz)
        self.mid = mid
        rec.update(our_bid_sz=round(bid_sz, 2), our_ask_sz=round(ask_sz, 2),
                   quote_usd=round(bid_sz * bb + ask_sz * (1 - ba), 2), comp_q=round(comp, 1),
                   comp_depth_usd=round(cusd_b + cusd_a, 2), our_q=round(ours, 1), share=round(share, 4),
                   reward_min=round(r, 5), pos=round(self.pos, 2), last_trade=book.get("last_trade_price"))
        return rec

    def summary(self):
        inv = self.cash + self.pos * (self.mid or 0)
        avg = lambda xs: round(sum(xs) / len(xs), 4) if xs else None
        return {"slug": self.m["slug"], "rate": self.m["rate"], "end": self.m.get("end"), "minutes": self.minutes,
                "quoted_minutes": self.quoted_minutes, "reward_est": round(self.reward, 4),
                "cross_fills": self.cross_fills, "level_gone": self.level_gone, "trades_at_bid": self.trades_at_bid,
                "trades_at_ask": self.trades_at_ask, "pos_shares": round(self.pos, 2), "inventory_pnl": round(inv, 4),
                "net": round(self.reward + inv, 4), **{f"avg_markout_{k}m": avg(self.markouts[k]) for k in MARKOUTS},
                "stopped": self.closed}


def ranked(min_rate, run_hours):
    rows = scan(min_rate, 60)
    now = datetime.now(timezone.utc)

    def left(r):
        try:
            return (datetime.fromisoformat(r["end"].replace("Z", "+00:00")) - now).total_seconds()
        except (AttributeError, ValueError, TypeError):
            return float("inf")
    rows = [r for r in rows if left(r) > 2 * 3600]  # skip markets about to resolve
    return sorted(rows, key=lambda r: left(r) < run_hours * 3600)  # stable: long-dated first, rank kept


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=float, default=24.0)
    p.add_argument("--markets", type=int, default=30)
    p.add_argument("--min-rate", type=float, default=20.0)
    p.add_argument("--refresh-min", type=int, default=60)
    p.add_argument("--out", default="data/pm_rewards_shadow.jsonl")
    p.add_argument("--summary", default="data/pm_rewards_shadow_summary.json")
    a = p.parse_args()
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    stop_at = time.time() + a.hours * 3600
    sims, last_pick = {}, 0.0
    started = datetime.now(timezone.utc).isoformat()
    while time.time() < stop_at:
        t0 = time.time()
        try:
            if t0 - last_pick >= a.refresh_min * 60:
                active = sum(1 for s in sims.values() if not s.closed)
                for r in ranked(a.min_rate, a.hours):
                    if active >= a.markets:
                        break
                    if r["yes"] not in sims:
                        sims[r["yes"]] = Sim(r)
                        active += 1
                last_pick = t0
                print(f"{datetime.now(timezone.utc).isoformat()[:19]} watchlist: {active} active / {len(sims)} total")
            live = [k for k, s in sims.items() if not s.closed]
            books = fetch_books(live)
            with ThreadPoolExecutor(8) as ex:
                trades = dict(zip(live, ex.map(lambda k: recent_trades(sims[k].m["condition_id"]), live)))
            now = time.time()
            ts = datetime.now(timezone.utc).isoformat()
            with open(a.out, "a") as f:
                for k in live:
                    s = sims[k]
                    rec = s.step(books[k], trades.get(k), now) if k in books else None
                    if rec is None:
                        s.closed = True  # book gone/one-sided (resolved or halted): stop, keep last mark
                        rec = {"stopped": True}
                    f.write(json.dumps(dict(rec, ts=ts, slug=s.m["slug"], condition_id=s.m["condition_id"])) + "\n")
            summ = [s.summary() for s in sims.values()]
            tot = {k: round(sum(x[k] for x in summ), 4)
                   for k in ("reward_est", "inventory_pnl", "net", "cross_fills", "trades_at_bid", "trades_at_ask")}
            with open(a.summary, "w") as f:
                json.dump({"started": started, "updated": ts, "totals": tot, "markets": summ}, f, indent=1)
            print(f"{ts[:19]} live={len(live)} reward_est=${tot['reward_est']:.2f} inv=${tot['inventory_pnl']:+.2f} "
                  f"net=${tot['net']:+.2f} cross_fills={tot['cross_fills']:.0f} "
                  f"trades@levels={tot['trades_at_bid'] + tot['trades_at_ask']:.0f}")
        except Exception as e:
            print("shadow error:", e)
        time.sleep(max(1.0, 60 - (time.time() - t0)))
    print("done: reached --hours limit")


if __name__ == "__main__":
    main()
