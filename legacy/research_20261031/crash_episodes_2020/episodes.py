"""Causal crash-episode detection + outcomes. Writes episodes.csv, episode_symbol_fwd.csv, sensitivity.json"""
import csv, json
from common import *
EP = signals()
rows = []; srows = []
for j, (k, br, i24, dd, n) in enumerate(EP):
    e = k + 1; row = dict(id=j, start_utc=iso(k), start_k=k, split=split_of(k), breadth=round(br, 4), n_avail=n,
                          idx_ret24=round(i24, 4), idx_dd7d=round(dd, 4))
    av = ~np.isnan(O[:, e]) if e < N else np.zeros(NS, bool)
    for h in (12, 24, 72):
        if e + h - 1 < N and av.any():
            fr = C[av, e + h - 1] / O[av, e] - 1; row[f"idx_fwd{h}"] = round(float(np.nanmean(fr)), 4)
            for i in np.where(av)[0]:
                v = C[i, e + h - 1] / O[i, e] - 1
                if not np.isnan(v): srows.append(dict(id=j, symbol=SYMS[i], h=h, fwd=round(float(v), 5)))
        else: row[f"idx_fwd{h}"] = ""
    if e + 71 < N and av.any():
        path = np.nanmean(C[av, e:e + 72] / O[av, e][:, None] - 1, axis=0)
        row["mae72_index_close"] = round(float(min(0, np.nanmin(path))), 4)
        row["mae72_mean_symbol_low"] = round(float(np.nanmean(np.minimum(0, np.nanmin(LO[av, e:e + 72], axis=1) / O[av, e] - 1))), 4)
        row["mae72_worst_symbol_low"] = round(float(np.nanmin(np.nanmin(LO[av, e:e + 72], axis=1) / O[av, e] - 1)), 4)
    else: row["mae72_index_close"] = row["mae72_mean_symbol_low"] = row["mae72_worst_symbol_low"] = ""
    rows.append(row)
with open(HERE + "/episodes.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
with open(HERE + "/episode_symbol_fwd.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["id", "symbol", "h", "fwd"]); w.writeheader(); w.writerows(srows)
sens = {}
for b in (0.4, 0.5, 0.6):
    for t in (-0.05, -0.07, -0.10):
        s = signals(breadth_thr=b, idx_thr=t)
        sens[f"breadth>={b}|idx24<={int(t*100)}%"] = dict(total=len(s), train=sum(1 for x in s if x[0] < SPLIT_K), held=sum(1 for x in s if x[0] >= SPLIT_K))
json.dump(sens, open(HERE + "/sensitivity.json", "w"), indent=1)
print(len(EP), "episodes;", sum(r["split"] == "TRAIN" for r in rows), "train"); [print(r["start_utc"], r["breadth"], r["n_avail"], r["idx_ret24"], r["idx_dd7d"], r["idx_fwd24"]) for r in rows]
print(json.dumps(sens, indent=0))
