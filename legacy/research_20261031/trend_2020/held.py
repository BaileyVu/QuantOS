"""One-shot HELD-OUT run for the pre-registered finalists + current config (PREREG.txt). Run once."""
import sys; sys.argv = ["x"]
import json, datetime as dt, numpy as np, pandas as pd
import sweep as S
if __name__ == "__main__":
    summ = json.load(open(S.HERE + "/build_summary.json")); ids = summ["finalists"]
    cfgs = {S.vid(c): c for c in S.GRID}; todo = [("CURRENT", S.CUR)] + [(i, cfgs[i]) for i in ids]
    pl = S.pools(S.SPLIT4, S.n4); days = (S.n4 - S.SPLIT4) * 4 / 24; res = {}; ledger = []
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for name, cfg in todo:
        tr = S.portfolio(cfg, S.SPLIT4, S.n4); st = S.stats(tr, cfg[5]); x = st.pop("x")
        pp = S.placebo_pct(pl[(cfg[2], cfg[3], cfg[6])], cfg, st["mean_bp"] / 1e4, st["trades"])
        pnl_day = float(np.sum(x) * 10 / days); dp = S.dsr_prob(x, st["sr"], summ["sr0"])
        res[name] = dict(variant_id=S.vid(cfg), cfg=list(map(str, cfg)), placebo_pct=pp, dsr_prob=dp, dsr_pos=bool(st["sr"] > summ["sr0"]), usd_per_day_at_10_notional=pnl_day, **{k: (float(v) if not isinstance(v, str) else v) for k, v in st.items()})
        ledger.append(dict(ts=now, family="tsmom_4h", variant_id=S.vid(cfg), params=dict(zip(["L", "ema", "stop_atr", "trail", "side", "vol_target", "entry"], cfg)), period="HELD-OUT", trades=int(st["trades"]),
                           mean_net_bp=round(st["mean_bp"], 2), ci90=[round(st["ci_lo"], 2), round(st["ci_hi"], 2)], verdict=("current-config reference" if name == "CURRENT" else "finalist HELD-OUT")))
        print(name, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in res[name].items() if k != "cfg"})
    with open(S.LEDGER, "a") as f:
        for l in ledger: f.write(json.dumps(l) + "\n")
    json.dump(res, open(S.HERE + "/results_heldout.json", "w"), indent=1)
