"""Build tidy hourly OHLC tables (UTC open_time ms) + hourly OI for MAJORS. Reads manifest/zips read-only."""
import os, sys, io, json, csv, zipfile, glob, datetime as dt
import numpy as np
sys.path.insert(0, os.path.expanduser("~/Desktop/PythonProject/QuantOS-mainnet-ready/src"))
from quantos.paper.strategies.candidate_features import ALT_SOURCE_UNIVERSE as U
from quantos.paper.strategies.crash_flush_e2 import MAJORS
HERE = os.path.dirname(os.path.abspath(__file__)); QOS = os.path.expanduser("~/Desktop/PythonProject/QuantOS")
MAN = QOS + "/data/portfolio/manifests/train_2021-10-01_to_2024-12-31.json"
H = 3600_000
T0 = int(dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
T1 = int(dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
N = (T1 - T0) // H
man = json.load(open(MAN)); tasks = man["tasks"]

def parse_kl(raw):
    out = []
    for line in raw.decode().splitlines():
        p = line.split(",")
        if not p or not p[0].strip().lstrip("-").isdigit(): continue  # header
        t = int(p[0]); t = t // 1000 if t > 10**14 else t
        out.append((t, float(p[1]), float(p[2]), float(p[3]), float(p[4])))
    return out

rows = {s: {} for s in U}; src = {s: {"train": 0, "dl": 0} for s in U}
for t in tasks:
    if t["dataset"] == "klines" and t["interval"] == "1h" and t["cached_path"] and t["symbol"] in rows:
        z = zipfile.ZipFile(os.path.join(QOS, t["cached_path"]))
        for r in parse_kl(z.read(z.namelist()[0])): rows[t["symbol"]][r[0]] = r[1:]
        src[t["symbol"]]["train"] += 1
for f in sorted(glob.glob(HERE + "/raw/*.zip")):
    s = os.path.basename(f).split("-1h-")[0]
    if s not in rows: continue
    z = zipfile.ZipFile(f)
    for r in parse_kl(z.read(z.namelist()[0])): rows[s].setdefault(r[0], r[1:])
    src[s]["dl"] += 1

O = np.full((len(U), N), np.nan); Hh = O.copy(); L = O.copy(); C = O.copy()
cov = {}
for i, s in enumerate(U):
    ts = sorted(rows[s]); 
    with open(f"{HERE}/data/klines/{s}.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["open_time_ms", "open", "high", "low", "close"])
        for t in ts:
            if not (T0 <= t < T1) or (t - T0) % H: continue
            o, h, l, c = rows[s][t]; k = (t - T0) // H
            O[i, k], Hh[i, k], L[i, k], C[i, k] = o, h, l, c; w.writerow([t, o, h, l, c])
    ok = ~np.isnan(C[i]); idx = np.where(ok)[0]
    gaps = []
    if len(idx):
        d = np.diff(idx); 
        for j in np.where(d > 1)[0]:
            gaps.append([int(idx[j] + 1), int(d[j] - 1)])  # hour index of first missing, length
    cov[s] = {"first": dt.datetime.fromtimestamp((T0 + idx[0] * H) / 1000, dt.timezone.utc).isoformat() if len(idx) else None,
              "last": dt.datetime.fromtimestamp((T0 + idx[-1] * H) / 1000, dt.timezone.utc).isoformat() if len(idx) else None,
              "bars": int(ok.sum()), "internal_gaps": [[dt.datetime.fromtimestamp((T0 + a * H) / 1000, dt.timezone.utc).isoformat(), n] for a, n in gaps],
              "train_zips": src[s]["train"], "dl_zips": src[s]["dl"]}
np.savez_compressed(HERE + "/data/prices.npz", O=O, H=Hh, L=L, C=C, T0=T0, symbols=np.array(U))

# OI for MAJORS (hourly label = floor(create_time), exact-hour rows only; avail = label+1h)
oi = {s: {} for s in MAJORS}; nfiles = {s: 0 for s in MAJORS}
for t in tasks:
    if t["dataset"] == "metrics" and t["cached_path"] and t["symbol"] in MAJORS:
        z = zipfile.ZipFile(os.path.join(QOS, t["cached_path"])); nfiles[t["symbol"]] += 1
        rd = csv.DictReader(io.StringIO(z.read(z.namelist()[0]).decode()))
        for r in rd:
            ct = dt.datetime.strptime(r["create_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
            if ct.minute != 0 or ct.second != 0: continue
            try: v = float(r["sum_open_interest_value"])
            except Exception: continue
            oi[r["symbol"]][int(ct.timestamp() * 1000)] = v
oi_out = {s: sorted(v.items()) for s, v in oi.items()}
json.dump(oi_out, open(HERE + "/data/oi_hourly.json", "w"))
oicov = {s: {"files": nfiles[s], "rows": len(oi_out[s]), "first": dt.datetime.fromtimestamp(oi_out[s][0][0] / 1000, dt.timezone.utc).isoformat() if oi_out[s] else None,
             "nonpositive_rows": sum(1 for _, v in oi_out[s] if v <= 0)} for s in sorted(MAJORS)}
json.dump({"klines": cov, "oi": oicov, "hours_total": N}, open(HERE + "/data_coverage.json", "w"), indent=1)
for s in U: print(s, cov[s]["first"][:10], cov[s]["bars"], "gaps", len(cov[s]["internal_gaps"]), cov[s]["train_zips"], cov[s]["dl_zips"])
print(oicov)
