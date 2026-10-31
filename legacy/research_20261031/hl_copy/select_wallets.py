"""Run ONCE: select & freeze HL wallets for the copy experiment. Refuses to overwrite."""
import json, os, sys, time, statistics, datetime as dt
import hl_common as C

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "wallets_frozen.json")
LB = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
DAY = 86400000


def win(row, name):
    for n, p in row.get("windowPerformances", []):
        if n == name:
            return C.num(p.get("pnl", 0))
    return None


def fetch_fills(addr, start_ms, sleep=1.1, max_pages=15, http=None):
    http = http or C.http_json
    fills, seen, st = [], set(), start_ms
    for _ in range(max_pages):
        page = None
        for k in range(5):
            try:
                page = http(C.HL_INFO, {"type": "userFillsByTime", "user": addr, "startTime": st})
                break
            except Exception:
                time.sleep(sleep * 3 * (k + 1))   # backoff (HL weight limit)
        if page is None:
            raise RuntimeError("fetch failed")
        time.sleep(sleep)
        new = [f for f in page if f.get("tid") not in seen]
        for f in new:
            seen.add(f.get("tid"))
        fills += new
        if len(page) < 2000 or not new:
            return fills, False
        st = max(f["time"] for f in page)
        if len(fills) > 27000:
            break
    return fills, True  # truncated


def clean_fills(fills):
    """Keep only fills with validated coin and finite numbers; drop all free text."""
    out = []
    for f in fills:
        try:
            if not C.valid_coin(f.get("coin")) or f.get("side") not in ("B", "A"):
                continue
            d = str(f.get("dir", ""))
            d = d if d.startswith("Close") or d.startswith("Open") or ">" in d else ""
            out.append({"coin": f["coin"], "side": f["side"], "time": int(f["time"]),
                        "tid": int(f.get("tid", 0)), "sz": C.num(f["sz"]), "px": C.num(f["px"]),
                        "closedPnl": C.num(f["closedPnl"]), "fee": C.num(f.get("fee", 0)),
                        "startPosition": C.num(f.get("startPosition", 0)),
                        "dir": "Close" if d.startswith("Close") else ("Flip" if ">" in d else "Open" if d else "")})
        except (ValueError, TypeError, KeyError):
            continue
    return out


def metrics(fills, acct_value, days, bases):
    fills = sorted(fills, key=lambda f: (f["time"], f.get("tid", 0)))
    m = {"n_fills": len(fills)}
    net = sum(float(f["closedPnl"]) - float(f.get("fee", 0)) for f in fills)
    m["pnl_90d_net"] = net
    closes = [f for f in fills if f.get("dir") in ("Close", "Flip")]
    m["closed_trades"] = len(closes)
    agg = {}
    for f in closes:
        k = (f["coin"], f["time"] // 60000)
        agg[k] = agg.get(k, 0) + float(f["closedPnl"])
    top2 = sum(sorted(agg.values(), reverse=True)[:2])
    m["top2_share"] = (top2 / net) if net > 0 else None
    # hold times + time-weighted gross notional from reconstructed positions
    start, holds, pos, px = {}, [], {}, {}
    cur_t, cur, acc, tot = None, 0.0, 0.0, 0.0
    for f in fills:
        c, t, sz, p = f["coin"], f["time"], float(f["sz"]), float(f["px"])
        s = float(f.get("startPosition", 0))
        after = s + (sz if f["side"] == "B" else -sz)
        if cur_t is not None:
            acc += cur * (t - cur_t); tot += (t - cur_t)
        if c not in pos:
            pos[c] = s
        px[c] = p
        pos[c] = after
        cur = sum(abs(pos[k]) * px[k] for k in pos)
        cur_t = t
        e = 1e-8
        if abs(s) < e:
            start[c] = t                      # opened from flat
        elif s * after > 0 and abs(after) > abs(s):
            start[c] = (start.get(c, t) * abs(s) + t * sz) / (abs(s) + sz)   # size-weighted avg open time
        else:                                  # reduce, close or flip
            holds.append(t - start.get(c, t))
            if s * after < 0:
                start[c] = t
            elif abs(after) < e:
                start.pop(c, None)
    m["median_hold_min"] = statistics.median(holds) / 60000 if holds else None
    m["n_holds"] = len(holds)
    m["fills_per_day"] = len(fills) / days
    m["mean_gross_pos_notional"] = acc / tot if tot else 0.0
    m["mean_pos_frac_of_acct"] = m["mean_gross_pos_notional"] / acct_value if acct_value else 0
    vol = {}
    for f in fills:
        vol[f["coin"]] = vol.get(f["coin"], 0) + float(f["sz"]) * float(f["px"])
    tv = sum(vol.values())
    ok = sum(v for c, v in vol.items() if C.map_coin(c, bases))
    m["binance_notional_frac"] = ok / tv if tv else 0
    return m


def passes(m):
    r = []
    if m["closed_trades"] < 30: r.append("closed_trades<30")
    if m["top2_share"] is None or m["top2_share"] >= 0.5: r.append("top2>=50%")
    if m["median_hold_min"] is None or m["median_hold_min"] <= 15: r.append("median_hold<=15m")
    if m["fills_per_day"] >= 300: r.append("fills/day>=300")
    if m["mean_pos_frac_of_acct"] <= 0.20: r.append("pos<=20%acct")
    if m["binance_notional_frac"] < 0.70: r.append("binance_frac<70%")
    if m["pnl_90d_net"] <= 0: r.append("pnl90<=0")
    return r


def main(out=OUT, top=60, http=None, sleep=1.1):
    if os.path.exists(out):
        print("REFUSING: %s exists; wallet set is frozen." % out, file=sys.stderr)
        raise SystemExit(2)
    http = http or C.http_json
    rows = http(LB)["leaderboardRows"]
    cand = []
    for r in rows:
        try:
            if not C.valid_addr(r.get("ethAddress")):
                continue
            av, mo, at = C.num(r["accountValue"]), win(r, "month"), win(r, "allTime")
            if mo is not None: C.num(mo)
            if at is not None: C.num(at)
        except (ValueError, TypeError, KeyError):
            continue
        if av >= 50000 and mo and mo > 0 and at and at > 0:
            cand.append((mo, r, av, at))
    cand.sort(key=lambda x: -x[0])
    cand = cand[:top]
    bases, _ = C.binance_perps(http(C.BN + "/fapi/v1/exchangeInfo"))
    now = int(time.time() * 1000)
    kept, rej = [], {}
    for mo, r, av, at in cand:
        a = r["ethAddress"]
        try:
            fills, trunc = fetch_fills(a, now - 90 * DAY, sleep, http=http)
        except Exception:
            rej[a] = ["fetch_error"]; continue
        if trunc:
            rej[a] = ["too_many_fills"]; continue
        fills = clean_fills(fills)
        m = metrics(fills, av, 90, bases)
        why = passes(m)
        if why:
            rej[a] = why; continue
        m.update(address=a, account_value=av, pnl_30d=mo, pnl_alltime=at)
        kept.append(m)
        print("keep", a, round(m["pnl_90d_net"]))
    kept.sort(key=lambda m: -m["pnl_90d_net"])
    kept = kept[:20]
    res = {"frozen_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "criteria_note": "Leaderboard exposes day/week/month/allTime only (no 90d): required month>0 and allTime>0, "
                            "AV>=50k; 90d net PnL (closedPnl-fee) computed from fills and ranks the final list. "
                            "Top 60 by 30d PnL screened. 'closed trades' = closing fills; position notional is gross, time-weighted.",
           "n_candidates_screened": len(cand), "n_rejected": len(rej),
           "reject_reasons": {a: v for a, v in rej.items()},
           "wallets": [{"address": m["address"], "metrics": m} for m in kept]}
    tmp = out + ".tmp"
    with open(tmp, "w") as f:
        json.dump(res, f, indent=1)
    os.replace(tmp, out)
    print("frozen %d wallets" % len(kept))


if __name__ == "__main__":
    main()
