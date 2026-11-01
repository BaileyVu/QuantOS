"""Paper trading loop: technical + Fear & Greed + Reddit/X + news sentiment. No orders are ever sent anywhere.

Every hour (a minute after the candle closes) it records all signals; every DECIDE_EVERY hours it rebalances
a paper portfolio of $CAPITAL split into equal sleeves per coin, filling at the latest close plus fee and slippage.
Usage:
  python3 paper.py --once            # one cycle, print it
  python3 paper.py --loop            # run forever (restart-safe: state is in paper_state.json)
  python3 paper.py --report          # summary of the paper run so far
  add --perp to any of these for the leveraged perpetual-futures run (3-20x, long/short, rules in leverage.py);
  it keeps its own state files, so the spot and perp runs go side by side.
"""
import argparse, json, os, sys, time, traceback
from datetime import datetime, timezone
import numpy as np
import data, signals, sentiment
import leverage as L
from strategy import WEIGHTS, DECIDE_EVERY, composite, decide

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
CAPITAL = 200.0
FEE, SLIP = 0.001, 0.0005
SENT_SCALE = 4.0          # headline averages sit near 0 (+-0.1); x4 then clip so a clear mood can reach +-0.4..1
DAILY_STOP = -0.05        # paper kill switch: flat for the rest of the UTC day after -5% on the day
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "paper_state.json")
LEDGER = os.path.join(HERE, "paper_ledger.jsonl")
SIGNALS_LOG = os.path.join(HERE, "paper_signals.jsonl")
PERP = False


def use_perp():
    global PERP, STATE, LEDGER, SIGNALS_LOG
    PERP = True
    STATE, LEDGER, SIGNALS_LOG = (os.path.join(HERE, f) for f in ("paper_perp_state.json", "paper_perp_ledger.jsonl",
                                                                   "paper_perp_signals.jsonl"))


def now_utc():
    return datetime.now(timezone.utc)


def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    if PERP:
        return {"started": now_utc().isoformat(), "cash": CAPITAL, "pos": {s: None for s in SYMBOLS}, "last_bar": None,
                "day": None, "day_start_equity": CAPITAL, "stopped_day": None, "fees": 0.0, "funding": 0.0, "trades": 0,
                "stops": 0, "liquidations": 0}
    return {"started": now_utc().isoformat(), "cash": CAPITAL, "qty": {s: 0.0 for s in SYMBOLS},
            "held": {s: False for s in SYMBOLS}, "weight": {s: 0.0 for s in SYMBOLS},
            "day": None, "day_start_equity": CAPITAL, "stopped_day": None, "fees": 0.0, "trades": 0}


def save_state(st):
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=1)
    os.replace(tmp, STATE)


def log(path, row):
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")


def clip(x):
    return float(max(-1.0, min(1.0, x)))


def signal_rows(frames, fng, sent, price):
    rows = {}
    for s, df in frames.items():
        sc = {"momentum": float(signals.momentum(df).iloc[-1]), "pattern": float(signals.pattern(df).iloc[-1]),
              "fear_greed": float(signals.fear_greed_signal(fng, df.index).iloc[-1])}
        if "reddit" in sent and sent["counts"]["reddit"] + sent["counts"]["x"] > 0:
            sc["reddit"] = clip(sent["reddit"][s] * SENT_SCALE)
        if "news" in sent and sent["counts"]["news"] > 0:
            sc["news"] = clip(sent["news"][s] * SENT_SCALE)
        rows[s] = {"scores": sc, "composite": round(float(composite(sc)), 4),
                   "size": round(float(signals.vol_size(df).iloc[-1]), 4),
                   "vol": round(float(signals.realised_vol(df).iloc[-1]), 4), "price": price[s]}
    return rows


def perp_equity(st, price):
    return st["cash"] + sum(L.upnl(p, price[s]) for s, p in st["pos"].items() if p)


def cycle_perp(st, t, frames, price, bar_time, fng, sent, rows, force_decide):
    """Leveraged run: replay stops, liquidations and funding on every bar closed since the last cycle, then decide."""
    fills = []

    def book(s, pnl, px, reason):
        p = st["pos"][s]
        st["cash"] += pnl
        st["trades"] += 1
        st["stops"] += reason == "stop"
        st["liquidations"] += reason == "liquidated"
        fill = {"time": t.isoformat(), "symbol": s, "side": "CLOSE " + ("LONG" if p["side"] > 0 else "SHORT"),
                "price": px, "lev": p["lev"], "margin": p["margin"], "pnl": pnl, "reason": reason,
                "composite": rows[s]["composite"]}
        fills.append(fill)
        log(LEDGER, fill)
        st["pos"][s] = None

    last = st["last_bar"]
    for s, df in frames.items():
        new_bars = df if last is None else df[df.index > last]
        for ts, b in new_bars.iloc[-48:].iterrows():  # at most 2 days of catch-up after a sleep
            p = st["pos"][s]
            if not p or (last is None):
                continue
            hit = L.check_bar(p, b["open"], b["high"], b["low"])
            if hit:
                book(s, *hit)
            elif ts.hour % 8 == 0:
                f = L.funding(p, b["open"])
                st["cash"] -= f
                st["funding"] += f
    st["last_bar"] = bar_time

    equity = perp_equity(st, price)
    day = t.strftime("%Y-%m-%d")
    if st["day"] != day:
        st["day"], st["day_start_equity"] = day, equity
    if equity / st["day_start_equity"] - 1 <= DAILY_STOP:
        st["stopped_day"] = day
    stopped = st["stopped_day"] == day

    decided = force_decide or t.hour % DECIDE_EVERY == 0
    if decided or stopped:
        sleeve = equity / len(SYMBOLS)
        for s in SYMBOLS:
            r, p = rows[s], st["pos"][s]
            side = p["side"] if p else 0
            new = 0 if stopped else L.want_side(r["composite"], side)
            if p and new != side:
                book(s, *L.close_pos(p, price[s]))
                new = 0 if stopped else L.want_side(r["composite"], 0)
            if decided and not st["pos"][s] and new and r["size"] > 0 and sleeve * r["size"] >= 5:
                lev = L.choose_leverage(r["composite"], r["vol"])
                p, fee = L.open_pos(new, price[s], sleeve * r["size"], lev)
                st["cash"] -= fee
                st["fees"] += fee
                st["trades"] += 1
                st["pos"][s] = p
                fill = {"time": t.isoformat(), "symbol": s, "side": "OPEN " + ("LONG" if new > 0 else "SHORT"),
                        "price": p["entry"], "lev": lev, "margin": p["margin"], "notional": p["notional"],
                        "stop": p["stop"], "liq": p["liq"], "fee": fee, "composite": r["composite"]}
                fills.append(fill)
                log(LEDGER, fill)
    equity = perp_equity(st, price)
    snap = {"time": t.isoformat(), "bar": bar_time, "decided": decided, "stopped": stopped, "equity": round(equity, 4),
            "pnl": round(equity - CAPITAL, 4), "signals": rows, "positions": st["pos"],
            "fear_greed": float(fng.iloc[-1]) if fng is not None else None,
            "sentiment": {k: sent.get(k) for k in ("counts", "top_headlines", "error", "status") if k in sent},
            "sentiment_raw": {k: sent[k] for k in ("reddit", "news") if k in sent}}
    log(SIGNALS_LOG, snap)
    save_state(st)
    return snap, fills


def cycle(st, force_decide=False):
    t = now_utc()
    frames = {s: data.klines(s, refresh_after=0) for s in SYMBOLS}
    price = {s: float(df["close"].iloc[-1]) for s, df in frames.items()}
    bar_time = str(frames["BTCUSDT"].index[-1])
    fng = data.fear_greed()
    try:
        sent = sentiment.snapshot()
    except Exception as e:
        sent = {"error": repr(e)}
    for source, state in (sent.get("status") or {"sentiment": sent.get("error", "unknown")}).items():
        if state != "ok" and not state.startswith("disabled"):
            print(f"WARNING {t}: sentiment source {source} unavailable: {state}", file=sys.stderr, flush=True)
    rows = signal_rows(frames, fng, sent, price)
    if PERP:
        return cycle_perp(st, t, frames, price, bar_time, fng, sent, rows, force_decide)
    equity = st["cash"] + sum(st["qty"][s] * price[s] for s in SYMBOLS)
    day = t.strftime("%Y-%m-%d")
    if st["day"] != day:
        st["day"], st["day_start_equity"] = day, equity
    day_ret = equity / st["day_start_equity"] - 1
    if day_ret <= DAILY_STOP:
        st["stopped_day"] = day
    stopped = st["stopped_day"] == day


    decided = force_decide or t.hour % DECIDE_EVERY == 0
    fills = []
    if decided:
        sleeve = equity / len(SYMBOLS)
        for s in SYMBOLS:
            r = rows[s]
            held, w = decide(r["composite"], r["size"], st["held"][s], st["weight"][s])
            if stopped:
                held, w = False, 0.0
            st["held"][s] = held
            target_qty = w * sleeve / price[s]
            dq = target_qty - st["qty"][s]
            notional = abs(dq) * price[s]
            if notional < 5.0 and not (w == 0 and st["qty"][s] > 0):
                st["weight"][s] = w if notional == 0 else st["weight"][s]
                continue
            fill_px = price[s] * (1 + SLIP if dq > 0 else 1 - SLIP)
            fee = abs(dq) * fill_px * FEE
            st["cash"] -= dq * fill_px + fee
            st["qty"][s] = target_qty
            st["weight"][s] = w
            st["fees"] += fee
            st["trades"] += 1
            fill = {"time": t.isoformat(), "symbol": s, "side": "BUY" if dq > 0 else "SELL", "qty": dq,
                    "price": fill_px, "fee": fee, "composite": r["composite"], "target_weight": w}
            fills.append(fill)
            log(LEDGER, fill)
    equity = st["cash"] + sum(st["qty"][s] * price[s] for s in SYMBOLS)
    snap = {"time": t.isoformat(), "bar": bar_time, "decided": decided, "stopped": stopped, "equity": round(equity, 4),
            "pnl": round(equity - CAPITAL, 4), "signals": rows,
            "fear_greed": float(fng.iloc[-1]) if fng is not None else None,
            "sentiment": {k: sent.get(k) for k in ("counts", "top_headlines", "error", "status") if k in sent},
            "sentiment_raw": {k: sent[k] for k in ("reddit", "news") if k in sent}}
    log(SIGNALS_LOG, snap)
    save_state(st)
    return snap, fills


def report():
    if not os.path.exists(SIGNALS_LOG):
        print("No paper run yet.")
        return
    snaps = [json.loads(l) for l in open(SIGNALS_LOG)]
    fills = [json.loads(l) for l in open(LEDGER)] if os.path.exists(LEDGER) else []
    st = load_state()
    first, last = snaps[0], snaps[-1]
    hold = np.mean([last["signals"][s]["price"] / first["signals"][s]["price"] for s in SYMBOLS]) * CAPITAL
    eq = [x["equity"] for x in snaps]
    dd = min(e / max(eq[:i + 1]) - 1 for i, e in enumerate(eq))
    print(f"Paper run {first['time'][:16]} -> {last['time'][:16]} UTC, {len(snaps)} hourly snapshots")
    print(f"Equity ${last['equity']:.2f} (PnL ${last['pnl']:+.2f}, {last['pnl']/CAPITAL:+.2%}); max drawdown {dd:.2%}")
    print(f"Buy-and-hold the same 3 coins over the same window: ${hold:.2f} ({hold/CAPITAL-1:+.2%})")
    if PERP:
        print(f"Fills {st['trades']}, stops {st['stops']}, liquidations {st['liquidations']}, "
              f"fees ${st['fees']:.2f}, funding ${st['funding']:.2f}")
        for s, p in st["pos"].items():
            if p:
                print(f"  open {s} {'LONG' if p['side'] > 0 else 'SHORT'} {p['lev']}x margin ${p['margin']:.2f} "
                      f"entry {p['entry']:.2f} stop {p['stop']:.2f} liq {p['liq']:.2f}")
        print("Now:", {s: last['signals'][s]['composite'] for s in SYMBOLS},
              "F&G", last.get("fear_greed"), "headline counts", last["sentiment"].get("counts"))
        for f in fills[-10:]:
            print(f"  {f['time'][:16]} {f['side']:11} {f['symbol']} {f['lev']}x @ {f['price']:.2f} margin ${f['margin']:.2f}"
                  + (f" pnl ${f['pnl']:+.2f} ({f['reason']})" if "pnl" in f else "") + f" score {f['composite']:+.2f}")
        return
    print(f"Trades {st['trades']}, fees paid ${st['fees']:.2f}")
    print("Now:", {s: (last['signals'][s]['composite'], st['weight'][s]) for s in SYMBOLS},
          "F&G", last.get("fear_greed"), "headline counts", last["sentiment"].get("counts"))
    for f in fills[-10:]:
        print(f"  {f['time'][:16]} {f['side']:4} {f['symbol']} {abs(f['qty']):.6f} @ {f['price']:.2f} score {f['composite']:+.2f}")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true")
    g.add_argument("--loop", action="store_true")
    g.add_argument("--report", action="store_true")
    ap.add_argument("--decide-now", action="store_true", help="rebalance on this cycle even off-schedule")
    ap.add_argument("--perp", action="store_true", help="leveraged perpetual-futures paper run (own state files)")
    a = ap.parse_args()
    if a.perp:
        use_perp()
    if a.report:
        return report()
    st = load_state()
    if a.once:
        snap, fills = cycle(st, a.decide_now)
        print(json.dumps({k: snap[k] for k in ("time", "equity", "decided", "signals", "fear_greed", "sentiment")}, indent=1))
        print("fills:", fills)
        return
    first = True
    while True:
        try:
            snap, fills = cycle(st, a.decide_now and first)
            first = False
            print(f"{snap['time'][:16]} {'perp ' if PERP else ''}equity ${snap['equity']:.2f} decided={snap['decided']} fills={len(fills)} "
                  + " ".join(f"{s[:3]}:{snap['signals'][s]['composite']:+.2f}" for s in SYMBOLS), flush=True)
        except Exception:
            traceback.print_exc()
        t = time.time()
        time.sleep(3600 - t % 3600 + 70)  # wake 70 s after each hourly candle closes


if __name__ == "__main__":
    sys.exit(main())
