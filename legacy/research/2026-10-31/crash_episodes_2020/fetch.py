"""Download 2020-01..2021-09 monthly 1h klines from data.binance.vision (public archive). Re-runnable."""
import os, sys, time, json, urllib.request, urllib.error
sys.path.insert(0, os.path.expanduser("~/Desktop/PythonProject/QuantOS-mainnet-ready/src"))
from quantos.paper.strategies.candidate_features import ALT_SOURCE_UNIVERSE
HERE = os.path.dirname(os.path.abspath(__file__)); RAW = os.path.join(HERE, "raw"); os.makedirs(RAW, exist_ok=True)
months = [f"{y}-{m:02d}" for y in (2020, 2021) for m in range(1, 13) if (y, m) <= (2021, 9)]
status = {}; last = 0.0
for sym in ALT_SOURCE_UNIVERSE:
    for mo in months:
        fn = f"{sym}-1h-{mo}.zip"; path = os.path.join(RAW, fn)
        if os.path.exists(path) and os.path.getsize(path) > 0: status[fn] = "ok"; continue
        if os.path.exists(path + ".404"): status[fn] = "404"; continue
        url = f"https://data.binance.vision/data/futures/um/monthly/klines/{sym}/1h/{fn}"
        for att in range(3):
            time.sleep(max(0, 0.5 - (time.time() - last))); last = time.time()
            try:
                with urllib.request.urlopen(url, timeout=60) as r: data = r.read()
                open(path, "wb").write(data); status[fn] = "ok"; break
            except urllib.error.HTTPError as e:
                if e.code == 404: open(path + ".404", "w").close(); status[fn] = "404"; break
                status[fn] = f"http{e.code}"; time.sleep(2 ** (att + 1))
            except Exception as e:
                status[fn] = f"err {e}"; time.sleep(2 ** (att + 1))
    print(sym, sum(1 for k, v in status.items() if k.startswith(sym) and v == "ok"), flush=True)
json.dump(status, open(os.path.join(HERE, "fetch_status.json"), "w"), indent=0)
print("done", sum(v == "ok" for v in status.values()), "ok;", sum(v == "404" for v in status.values()), "404;", sum(v not in ("ok", "404") for v in status.values()), "failed")
