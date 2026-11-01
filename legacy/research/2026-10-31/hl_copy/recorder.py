"""Forward-only recorder + paper copier. Usage: python recorder.py --state DIR [--minutes N]"""
import argparse, json, os, signal, sys, time, datetime as dt
import hl_common as C
from copier import Copier

HERE = os.path.dirname(os.path.abspath(__file__))
POLL_S, STOP_S, STATS_S, EPS = 30, 60, 600, 0.005
STOP = False


def _sig(*_):
    global STOP
    STOP = True


def atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def parse_positions(resp):
    """clearinghouseState -> ({coin: {szi, entryPx, mark}}, accountValue). Untrusted -> validated."""
    pos = {}
    for ap in resp.get("assetPositions", []):
        p = ap.get("position", {})
        coin = p.get("coin")
        if not C.valid_coin(coin):
            continue
        try:
            szi, ep, pv = C.num(p["szi"]), C.num(p.get("entryPx") or 0), C.num(p.get("positionValue") or 0)
        except (ValueError, TypeError, KeyError):
            continue
        if szi == 0:
            continue
        pos[coin] = {"szi": szi, "entryPx": ep, "mark": pv / abs(szi)}
    av = C.num(resp.get("marginSummary", {}).get("accountValue", 0))
    return pos, av


def diff_positions(prev, new):
    """-> list of event dicts (coin,type,side,size,prev_size,entry_px,mark)."""
    evs = []
    for coin in sorted(set(prev) | set(new)):
        o = prev.get(coin, {}).get("szi", 0.0)
        n = new.get(coin, {}).get("szi", 0.0)
        ref = new.get(coin) or prev.get(coin)
        base = dict(coin=coin, size=n, prev_size=o, entry_px=ref["entryPx"], mark=ref["mark"],
                    side=(1 if n > 0 else -1) if n else (1 if o > 0 else -1))
        if o == 0 and n != 0:
            t = "OPEN"
        elif o != 0 and n == 0:
            t = "CLOSE"
        elif o * n < 0:
            t = "FLIP"
        elif abs(n) > abs(o) * (1 + EPS):
            t = "INCREASE"
        elif abs(n) < abs(o) * (1 - EPS):
            t = "REDUCE"
        else:
            continue
        evs.append(dict(base, type=t))
    return evs


class Recorder:
    def __init__(self, state_dir, wallets, bases, info, http=None, interval=1.05, now=time.time, sleep=time.sleep):
        self.d, self.wallets = state_dir, wallets
        self.http, self.now, self.sleep, self.interval = http or C.http_json, now, sleep, interval
        os.makedirs(state_dir, exist_ok=True)
        self.p = lambda n: os.path.join(state_dir, n)
        snap = self._load("snapshot.json", {})
        self.snap, self.av = snap.get("positions", {}), snap.get("av", {})
        self.copier = Copier(bases, info, http=self.http, now=now, state=self._load("copier_state.json", None))
        self.cycles = self.errors = self.streak = 0
        self.last_stop = self.last_stats = 0
        self.wstats = {}
        self.last_hl = 0.0

    def _load(self, name, default):
        try:
            with open(self.p(name)) as f:
                return json.load(f)
        except Exception:
            return default

    def hl(self, payload):
        w = self.interval - (self.now() - self.last_hl)
        if w > 0:
            self.sleep(w)
        self.last_hl = self.now()
        return self.http(C.HL_INFO, payload)

    def poll_wallet(self, addr):
        resp = self.hl({"type": "clearinghouseState", "user": addr})
        new, av = parse_positions(resp)
        self.av[addr] = av
        prev = self.snap.get(addr)
        self.snap[addr] = new
        if prev is None:
            return []                        # baseline only, no events
        t = self.now()
        evs = diff_positions(prev, new)
        with open(self.p("events.jsonl"), "a") as f:
            for e in evs:
                e = dict(e, wallet=addr, ts=t, utc=dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="seconds"))
                f.write(json.dumps(e) + "\n")
                self.copier.on_event(e, t)
        return evs

    def wallet_stats(self):
        out = {}
        for addr in self.wallets:
            try:
                st, fills, seen = int(self.copier.start_ts * 1000), [], set()
                while True:
                    page = self.hl({"type": "userFillsByTime", "user": addr, "startTime": st})
                    new = [f for f in page if f.get("tid") not in seen]
                    for f in new:
                        seen.add(f.get("tid"))
                    fills += new
                    if len(page) < 2000 or not new:
                        break
                    st = max(int(f["time"]) for f in page)
                net = sum(C.num(f["closedPnl"]) - C.num(f.get("fee", 0)) for f in fills)
                av = self.av.get(addr) or 1.0
                out[addr] = {"realized_net": net, "n_fills": len(fills), "return_pct": 100 * net / av}
            except Exception:
                out[addr] = {"realized_net": 0.0, "n_fills": 0, "return_pct": 0.0, "error": True}
        self.wstats = out

    def save(self, final=False):
        atomic(self.p("snapshot.json"), {"positions": self.snap, "av": self.av})
        atomic(self.p("copier_state.json"), self.copier.state())
        for d, bk in self.copier.books.items():
            atomic(self.p("books_%s.json" % d), bk)
        atomic(self.p("summary.json"), self.copier.summary(self.wstats or None))
        atomic(self.p("heartbeat.json"), {
            "ts": self.now(), "utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "cycles": self.cycles, "errors": self.errors, "streak": self.streak,
            "pending": len(self.copier.pending),
            "open": {d: len(b["open"]) for d, b in self.copier.books.items()}, "final": final})

    def run(self, minutes=None):
        end = self.now() + minutes * 60 if minutes else None
        self.last_stats = self.now()
        while not STOP and (end is None or self.now() < end):
            c0 = self.now()
            for a in self.wallets:
                if STOP:
                    break
                try:
                    self.poll_wallet(a)
                    self.streak = 0
                except Exception:
                    self.errors += 1
                    self.streak += 1
                    self.sleep(min(60, 2 ** min(self.streak, 6)))
                self.copier.process_due()
            self.cycles += 1
            if self.now() - self.last_stop >= STOP_S:
                self.copier.check_stops()
                self.last_stop = self.now()
            if self.now() - self.last_stats >= STATS_S:
                self.last_stats = self.now()
                self.wallet_stats()
            self.save()
            while not STOP and self.now() < c0 + POLL_S and (end is None or self.now() < end):
                self.sleep(0.5)
                self.copier.process_due()
        self.copier.process_due()
        self.wallet_stats()
        self.save(final=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--minutes", type=float)
    ap.add_argument("--wallets", default=os.path.join(HERE, "wallets_frozen.json"))
    a = ap.parse_args(argv)
    wl = [w["address"] for w in json.load(open(a.wallets))["wallets"] if C.valid_addr(w["address"])]
    bases, info = C.binance_perps(C.http_json(C.BN + "/fapi/v1/exchangeInfo"))
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    r = Recorder(a.state, wl, bases, info)
    r.run(a.minutes)
    print("done cycles=%d errors=%d" % (r.cycles, r.errors))


if __name__ == "__main__":
    main()
