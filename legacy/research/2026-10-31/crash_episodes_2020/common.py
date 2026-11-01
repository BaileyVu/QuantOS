import os, sys, datetime as dt
import numpy as np
sys.path.insert(0, os.path.expanduser("~/Desktop/PythonProject/QuantOS-mainnet-ready/src"))
HERE = os.path.dirname(os.path.abspath(__file__)); H = 3600_000
D = np.load(HERE + "/data/prices.npz")
O, HI, LO, C = D["O"], D["H"], D["L"], D["C"]; T0 = int(D["T0"]); SYMS = [str(s) for s in D["symbols"]]
NS, N = C.shape
def ms(y, m, d): return int(dt.datetime(y, m, d, tzinfo=dt.timezone.utc).timestamp() * 1000)
SPLIT_K = (ms(2023, 7, 1) - T0) // H          # first bar index of HELD-OUT
def iso(k): return dt.datetime.fromtimestamp((T0 + k * H) / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
def split_of(k): return "TRAIN" if k < SPLIT_K else "HELD"

def index_series():
    lr = np.full((NS, N), np.nan); lr[:, 1:] = np.log(C[:, 1:] / C[:, :-1])
    cnt = np.sum(~np.isnan(lr), axis=0)
    r = np.where(cnt > 0, np.nansum(lr, axis=0) / np.maximum(cnt, 1), 0.0)
    lvl = np.exp(np.cumsum(r)); return lvl, r
LVL, IR = index_series()

def signals(breadth_thr=0.5, idx_thr=-0.07, ret_thr=-0.08, cool=72, min_syms=5):
    """Return list of (k, breadth, idx24, dd7d, navail). Decision at close of bar k."""
    out = []; last = -10**9
    for k in range(168, N):
        a = ~np.isnan(C[:, k]) & ~np.isnan(C[:, k - 24])
        n = int(a.sum())
        if n < min_syms: continue
        r24 = C[a, k] / C[a, k - 24] - 1
        br = float((r24 <= ret_thr).mean()); i24 = LVL[k] / LVL[k - 24] - 1
        if br >= breadth_thr and i24 <= idx_thr and k - last >= cool:
            out.append((k, br, float(i24), float(LVL[k] / LVL[k - 167:k + 1].max() - 1), n)); last = k
    return out
