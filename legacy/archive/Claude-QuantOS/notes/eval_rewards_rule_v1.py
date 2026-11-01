"""Out-of-sample evaluation of notes/rewards_rule_v1.json on data/pm_rewards_shadow.jsonl. Read-only."""
import json, math, re, sys
from collections import defaultdict
from datetime import datetime

BASE = sys.argv[1] if len(sys.argv) > 1 else "."
rule = json.load(open(f"{BASE}/notes/rewards_rule_v1.json"))
inc = rule["include_if_all"]
T0 = datetime.fromisoformat(rule["frozen_at_utc"].replace("Z", "+00:00"))
summary = json.load(open(f"{BASE}/data/pm_rewards_shadow_summary.json"))
meta = {m["slug"]: m for m in summary["markets"]}
kw = inc["exclude_keywords_in_slug_or_question"]

rows = defaultdict(list)
for line in open(f"{BASE}/data/pm_rewards_shadow.jsonl"):
    r = json.loads(line)
    rows[r["slug"]].append(r)


def ts(r):
    return datetime.fromisoformat(r["ts"])


def vol_cents(rs):
    mids = [r["mid"] for r in rs if "mid" in r]
    ch = [(b - a) * 100 for a, b in zip(mids[::5], mids[5::5])]
    if len(ch) < 6:
        return None
    mu = sum(ch) / len(ch)
    return math.sqrt(sum((x - mu) ** 2 for x in ch) / (len(ch) - 1))


selected, reasons = [], {}
for slug, rs in rows.items():
    m = meta.get(slug, {})
    pre = [r for r in rs if ts(r) < T0]
    if pre:
        eval_rows = [r for r in rs if ts(r) >= T0]
    else:  # added after freeze: first 60 rows for vol, rest evaluated
        pre, eval_rows = rs[:60], rs[60:]
    v = vol_cents(pre)
    end = m.get("end")
    days = None
    if end:
        try:
            e = datetime.fromisoformat(end.replace("Z", "+00:00"))
            if e.tzinfo is None:
                e = e.replace(tzinfo=T0.tzinfo)
            days = (e - T0).total_seconds() / 86400
        except ValueError:
            pass
    why = []
    if m.get("rate", 1e9) > inc["daily_reward_pool_usd_max"]: why.append("pool")
    if v is None or v >= inc["vol_5m_cents_max"]: why.append(f"vol={v}")
    if days is None or days < inc["min_days_to_resolution"]: why.append(f"days={days}")
    hit = [k for k in kw if re.search(r"(^|-)" + re.escape(k) + r"($|-)", slug)]
    if hit: why.append(f"kw={hit}")
    reasons[slug] = why
    if not why and eval_rows:
        selected.append((slug, eval_rows, v, days))


def simulate(eval_rows, model):
    pos = cash = reward = 0.0
    fills, mos = 0, {5: [], 30: []}
    q = None
    pend = []
    usd = []
    last_mid = 0.0
    for r in eval_rows:
        if "mid" not in r:
            break  # stopped
        mid = r["mid"]
        if q:
            qbp, qbs, qap, qas = q
            fb = fa = False
            if model in ("A", "B"):
                fb = qbs and r["ba"] <= qbp + 1e-9
                fa = qas and r["bb"] >= qap - 1e-9
            if model == "B":
                fb = fb or (qbs and r.get("trade_at_our_bid"))
                fa = fa or (qas and r.get("trade_at_our_ask"))
            if fb:
                pos += qbs; cash -= qbs * qbp; fills += 1; pend.append([ts(r), "bid", qbp, set()])
            if fa:
                pos -= qas; cash += qas * qap; fills += 1; pend.append([ts(r), "ask", qap, set()])
        for p in pend:
            for k in (5, 30):
                if k not in p[3] and (ts(r) - p[0]).total_seconds() >= k * 60 - 5:
                    p[3].add(k); mos[k].append((mid - p[2]) if p[1] == "bid" else (p[2] - mid))
        reward += r.get("reward_min", 0.0)  # shadow's own reward estimate for this minute
        usd.append(r.get("quote_usd", 0))
        q = (r["bb"], r["our_bid_sz"], r["ba"], r["our_ask_sz"])
        last_mid = mid
    inv = cash + pos * last_mid
    avg = lambda x: round(sum(x) / len(x), 4) if x else None
    return {"reward": reward, "inv": inv, "net": reward + inv, "fills": fills, "mo5": avg(mos[5]), "mo30": avg(mos[30]),
            "usd": sum(usd) / len(usd) if usd else 0, "minutes": len(usd)}


print(f"rule frozen {rule['frozen_at_utc']}; markets in data {len(rows)}; selected {len(selected)}")
for model in ("A", "B"):
    tot = defaultdict(float)
    print(f"\nFill model {model}: {rule['fill_models']['A_book_cross' if model == 'A' else 'B_trade_print']}")
    print(f"{'market':<48}{'pool':>5}{'vol':>6}{'days':>6}{'min':>5}{'q$':>6}{'rew':>8}{'fills':>6}{'inv':>9}{'net':>9}{'mo5':>7}{'mo30':>7}")
    for slug, er, v, d in sorted(selected):
        s = simulate(er, model)
        for k in ("reward", "inv", "net", "fills", "usd"):
            tot[k] += s[k]
        hrs = max(tot.get("hrs", 0), s["minutes"] / 60)
        tot["hrs"] = hrs
        print(f"{slug[:48]:<48}{meta[slug]['rate']:5.0f}{v:6.2f}{d:6.1f}{s['minutes']:5d}{s['usd']:6.0f}{s['reward']:8.2f}"
              f"{s['fills']:6d}{s['inv']:9.2f}{s['net']:9.2f}{s['mo5'] if s['mo5'] is not None else float('nan'):7.3f}"
              f"{s['mo30'] if s['mo30'] is not None else float('nan'):7.3f}")
    print(f"TOTAL quoted ${tot['usd']:.0f}  rewards ${tot['reward']:.2f}  inventory ${tot['inv']:+.2f}  net ${tot['net']:+.2f}  "
          f"fills {tot['fills']:.0f}  window {tot['hrs']:.1f}h  net/day ${tot['net'] / max(tot['hrs'], 1e-9) * 24:+.2f}")
excluded = {k: v for k, v in reasons.items() if v}
print(f"\nexcluded {len(excluded)}:")
for k, v in sorted(excluded.items()):
    print(f"  {k[:50]:<50} {', '.join(map(str, v))}")
