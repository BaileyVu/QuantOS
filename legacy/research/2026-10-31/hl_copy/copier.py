"""Paper copy-trading books (delays 0/30/60 s) priced on Binance USD-M bookTicker."""
import math, time
import hl_common as C

DELAYS = (0, 30, 60)
EQUITY0, RISK, STOP_ATR, MAXLEV, MAXOPEN = 50.0, 0.02, 2.5, 3.0, 5
FEE, SLIP, MAXHOLD = 0.0004, 0.0005, 7 * 86400
ENTRY_TYPES, EXIT_TYPES = ("OPEN", "FLIP"), ("CLOSE", "FLIP")


def compute_atr(rows, now_ms, n=14):
    """rows: klines (limit 20). Drops the still-forming candle. Returns ATR or None."""
    if rows and int(rows[-1][0]) + 3600000 > now_ms:
        rows = rows[:-1]
    trs = []
    for i in range(1, len(rows)):
        h, l, pc = C.num(rows[i][2]), C.num(rows[i][3]), C.num(rows[i - 1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < n:
        return None
    return sum(trs[-n:]) / n


def size_position(equity, entry, atr, side, info):
    """Returns (qty, stop, reason). side +1 long / -1 short."""
    dist = STOP_ATR * atr
    if not (dist > 0 and entry > 0):
        return 0.0, None, "bad_atr"
    qty = min(equity * RISK / dist, equity * MAXLEV / entry)
    step = info.get("step") or 0.001
    qty = math.floor(qty / step + 1e-9) * step
    stop = entry - side * dist
    if stop <= 0:
        return 0.0, None, "stop_nonpositive"
    if qty <= 0 or qty < info.get("minQty", 0) or qty * entry < info.get("minNotional", 5.0):
        return qty, stop, "min_notional"
    return qty, stop, None


class Copier:
    def __init__(self, bases, info, http=None, now=time.time, state=None):
        self.bases, self.info = bases, info
        self.http, self.now = http or C.http_json, now
        st = state or {}
        self.start_ts = st.get("start_ts", now())
        self.seq = st.get("seq", 0)
        self.pending = st.get("pending", [])
        self.books = st.get("books") or {str(d): {"delay": d, "equity0": EQUITY0, "realized": 0.0,
                                                  "open": {}, "closed": [], "skips": [], "skip_counts": {}}
                                         for d in DELAYS}
        self.unmapped = st.get("unmapped", 0)

    def state(self):
        return {"start_ts": self.start_ts, "seq": self.seq, "pending": self.pending,
                "books": self.books, "unmapped": self.unmapped}

    # ---- scheduling
    def on_event(self, ev, t=None):
        t = self.now() if t is None else t
        typ = ev["type"]
        sym = C.map_coin(ev["coin"], self.bases)
        if typ in ENTRY_TYPES and not sym:
            self.unmapped += 1
        if not sym:
            return
        for d in DELAYS:
            if typ in EXIT_TYPES:
                self._add(t + d, "exit", d, ev, sym, 0)
            if typ in ENTRY_TYPES:
                self._add(t + d, "entry", d, ev, sym, ev["side"])

    def _add(self, due, kind, d, ev, sym, side):
        self.seq += 1
        self.pending.append({"due": due, "seq": self.seq, "kind": kind, "book": str(d),
                             "wallet": ev["wallet"], "coin": ev["coin"], "sym": sym, "side": side})

    def process_due(self):
        t = self.now()
        due = sorted([p for p in self.pending if p["due"] <= t], key=lambda p: (p["due"], p["seq"]))
        self.pending = [p for p in self.pending if p["due"] > t]
        for p in due:
            try:
                if p["kind"] == "entry":
                    self._entry(p, t)
                else:
                    self._exit(self.books[p["book"]], "%s|%s" % (p["wallet"], p["coin"]), None, t, "wallet_exit")
            except Exception:
                self._skip(self.books[p["book"]], t, p, "fetch_error")

    # ---- helpers
    def _book_px(self, sym):
        d = self.http(C.BN + "/fapi/v1/ticker/bookTicker?symbol=" + sym)
        return C.num(d["bidPrice"]), C.num(d["askPrice"])

    def _skip(self, bk, t, p, reason):
        bk["skip_counts"][reason] = bk["skip_counts"].get(reason, 0) + 1
        bk["skips"] = (bk["skips"] + [{"t": t, "reason": reason, "wallet": p["wallet"], "coin": p["coin"]}])[-200:]

    def equity(self, bk):
        return bk["equity0"] + bk["realized"]

    def _entry(self, p, t):
        bk, key, sym, side = self.books[p["book"]], "%s|%s" % (p["wallet"], p["coin"]), p["sym"], p["side"]
        if key in bk["open"]:
            return self._skip(bk, t, p, "already_open")
        if len(bk["open"]) >= MAXOPEN:
            return self._skip(bk, t, p, "max_open")
        bid, ask = self._book_px(sym)
        px = ask if side > 0 else bid
        entry = px * (1 + SLIP * side)
        rows = self.http(C.BN + "/fapi/v1/klines?symbol=%s&interval=1h&limit=20" % sym)
        atr = compute_atr(rows, t * 1000)
        if atr is None:
            return self._skip(bk, t, p, "no_atr")
        qty, stop, why = size_position(self.equity(bk), entry, atr, side, self.info.get(sym, {}))
        if why:
            return self._skip(bk, t, p, why)
        bk["open"][key] = {"wallet": p["wallet"], "coin": p["coin"], "sym": sym, "side": side, "qty": qty,
                           "entry": entry, "stop": stop, "t_entry": t, "atr": atr, "book_px": px}

    def _exit(self, bk, key, prices, t, reason):
        pos = bk["open"].get(key)
        if not pos:
            return
        bid, ask = prices if prices else self._book_px(pos["sym"])
        s = pos["side"]
        px = (bid if s > 0 else ask) * (1 - SLIP * s)
        pnl = s * pos["qty"] * (px - pos["entry"]) - FEE * pos["qty"] * (px + pos["entry"])
        bk["realized"] += pnl
        bk["closed"].append(dict(pos, exit=px, t_exit=t, reason=reason, pnl=pnl))
        del bk["open"][key]

    def check_stops(self):
        """Once a minute: one bookTicker per open symbol; stop / 7-day exits."""
        t = self.now()
        cache = {}
        for bk in self.books.values():
            for key, pos in list(bk["open"].items()):
                try:
                    if pos["sym"] not in cache:
                        cache[pos["sym"]] = self._book_px(pos["sym"])
                    bid, ask = cache[pos["sym"]]
                except Exception:
                    continue
                s = pos["side"]
                if (s > 0 and bid <= pos["stop"]) or (s < 0 and ask >= pos["stop"]):
                    self._exit(bk, key, (bid, ask), t, "stop")
                elif t - pos["t_entry"] >= MAXHOLD:
                    self._exit(bk, key, (bid, ask), t, "timeout")

    def summary(self, wallet_stats=None):
        out = {"start_ts": self.start_ts, "now": self.now(), "unmapped_entry_events": self.unmapped, "books": {}}
        for d, bk in self.books.items():
            cl = bk["closed"]
            out["books"][d] = {"delay_s": bk["delay"], "equity": self.equity(bk), "net_pnl": bk["realized"],
                               "return_pct": 100 * bk["realized"] / bk["equity0"], "n_closed": len(cl),
                               "n_open": len(bk["open"]), "wins": sum(1 for c in cl if c["pnl"] > 0),
                               "skip_counts": bk["skip_counts"]}
        if wallet_stats is not None:
            out["wallets_realized"] = wallet_stats
            out["wallets_total_closedPnl_net_usd"] = sum(w["realized_net"] for w in wallet_stats.values())
            out["wallets_mean_return_pct_of_acct"] = (
                sum(w["return_pct"] for w in wallet_stats.values()) / len(wallet_stats)) if wallet_stats else None
        return out
