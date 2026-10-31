"""Paper 'buy the dump' watcher. Never sends orders.

Rule (from pump_study.py / pump_account.py, fixed before paper): when a coin closes a 1h candle down 6% or more,
go long at 3x with 10% of equity as margin, close after 24h. At most 3 open positions, one per coin.
Liquidation if the low reaches entry * (1 - 1/3 + 0.5%). Costs 0.2% round trip on notional, funding 0.01%/8h.
+6% pumps are logged (not traded) so we can see them live.
Usage: python3 spike_paper.py --loop | --once | --report
"""
import argparse, json, os, time, traceback
from datetime import datetime, timezone
import pandas as pd
import data

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT",
         "DOTUSDT", "NEARUSDT", "ATOMUSDT", "FILUSDT", "APTUSDT", "ARBUSDT", "INJUSDT", "SUIUSDT", "PEPEUSDT", "WIFUSDT"]
THRESH, LEV, MARGIN_FRAC, HOLD_H, MAX_OPEN = 0.06, 3.0, 0.10, 24, 3
COST, MAINT, FUNDING_8H, CAPITAL = 0.001, 0.005, 0.0001, 200.0
HERE = os.path.dirname(os.path.abspath(__file__))
STATE, LEDGER = os.path.join(HERE, "spike_state.json"), os.path.join(HERE, "spike_ledger.jsonl")


def log(row):
    with open(LEDGER, "a") as f:
        f.write(json.dumps(row) + "\n")


def load():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"started": datetime.now(timezone.utc).isoformat(), "cash": CAPITAL, "pos": {}, "seen": {}, "trades": 0, "liqs": 0}


def save(st):
    json.dump(st, open(STATE + ".tmp", "w"), indent=1)
    os.replace(STATE + ".tmp", STATE)


def close(st, s, px, reason, now):
    p = st["pos"].pop(s)
    hours = (pd.Timestamp(now) - pd.Timestamp(p["entry_time"])).total_seconds() / 3600
    pnl = -p["margin"] if reason == "liquidated" else (
        p["notional"] * (px / p["entry"] - 1) - p["notional"] * COST - p["notional"] * FUNDING_8H * hours / 8)
    pnl = max(pnl, -p["margin"])
    st["cash"] += p["margin"] + pnl
    st["liqs"] += reason == "liquidated"
    log({"time": now, "symbol": s, "action": "CLOSE", "reason": reason, "price": px, "pnl": round(pnl, 4),
         "equity_after": round(equity(st, {}), 4)})


def equity(st, prices):
    return st["cash"] + sum(p["margin"] + p["notional"] * (prices.get(s, p["entry"]) / p["entry"] - 1) for s, p in st["pos"].items())


def cycle(st):
    now = datetime.now(timezone.utc).isoformat()
    frames, prices = {}, {}
    for s in COINS:
        try:
            frames[s] = data.klines(s, refresh_after=0)
            prices[s] = float(frames[s]["close"].iloc[-1])
        except Exception as e:
            print("skip", s, e)
    for s in list(st["pos"]):  # liquidation and time exits, replaying bars since entry
        p, df = st["pos"][s], frames.get(s)
        if df is None:
            continue
        bars = df[df.index > p["entry_bar"]]
        liq = p["entry"] * (1 - 1 / LEV + MAINT)
        if len(bars) and bars["low"].min() <= liq:
            close(st, s, liq, "liquidated", now)
        elif len(bars) >= HOLD_H:
            close(st, s, float(bars["close"].iloc[HOLD_H - 1]), "24h", now)
    for s, df in frames.items():
        bar = df.iloc[-1]
        key = str(df.index[-1])
        if st["seen"].get(s) == key:
            continue
        st["seen"][s] = key
        r = bar["close"] / bar["open"] - 1
        if r >= THRESH:
            log({"time": now, "symbol": s, "action": "PUMP_SEEN", "bar": key, "ret": round(r, 4)})
        if r <= -THRESH:
            ok = s not in st["pos"] and len(st["pos"]) < MAX_OPEN
            log({"time": now, "symbol": s, "action": "DUMP_SEEN", "bar": key, "ret": round(r, 4), "traded": ok})
            if ok:
                margin = equity(st, prices) * MARGIN_FRAC
                notional = margin * LEV
                st["cash"] -= margin + notional * COST
                st["pos"][s] = {"entry": float(bar["close"]), "entry_bar": key, "entry_time": now,
                                "margin": margin, "notional": notional}
                st["trades"] += 1
                log({"time": now, "symbol": s, "action": "OPEN LONG 3x", "price": float(bar["close"]), "margin": round(margin, 4)})
    save(st)
    return equity(st, prices)


def report():
    st = load()
    rows = [json.loads(l) for l in open(LEDGER)] if os.path.exists(LEDGER) else []
    print(f"Buy-the-dump paper since {st['started'][:16]} UTC: cash ${st['cash']:.2f}, open {list(st['pos'])}, "
          f"trades {st['trades']}, liquidations {st['liqs']}")
    for r in rows[-15:]:
        print(" ", r)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true")
    g.add_argument("--loop", action="store_true")
    g.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        return report()
    st = load()
    if not st["seen"]:  # don't trade candles that closed before the bot started
        for s in COINS:
            try:
                st["seen"][s] = str(data.klines(s, refresh_after=0).index[-1])
            except Exception:
                pass
        save(st)
    while True:
        try:
            eq = cycle(st)
            print(f"{datetime.now(timezone.utc).isoformat()[:16]} spike equity ${eq:.2f} open {list(st['pos'])}", flush=True)
        except Exception:
            traceback.print_exc()
        if a.once:
            return
        time.sleep(3600 - time.time() % 3600 + 90)


if __name__ == "__main__":
    main()
