"""Collect data for weather_rule_v1 (public data only). Python 3.9 stdlib.

Outputs in weather/data/:
  events.json       parsed events with buckets, winner, station, coordinates
  forecasts.json    {station: {"utc_offset": s, "N1": {date: max}, "N2": {date: max}}} from Open-Meteo previous-runs
  archive.json      {station: {date: max}} ERA5 daily max (label-mismatch check only)
  prices.jsonl      one line per bucket token: {"token", "history": [[t, p], ...]}
"""
import json, os, re, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
H = {"user-agent": "claude-quantos-research"}
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
HKO = {"lat": 22.3019, "lon": 114.1742, "name": "Hong Kong Observatory"}


def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=40) as r:
                return json.loads(r.read())
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(2 ** i)


def parse_bucket(label):
    t = label.replace("º", "°").strip()
    unit = "C" if "°C" in t else "F"
    nums = [float(x) for x in re.findall(r"(?<![\d.])-?\d+(?:\.\d+)?", t)]  # "80-81" is a range, not -81
    low = re.search(r"or (below|lower|less)", t)
    high = re.search(r"or (higher|above|more)", t)
    if low:
        return unit, None, nums[0]
    if high:
        return unit, nums[0], None
    if len(nums) >= 2:
        return unit, nums[0], nums[1]
    return unit, nums[0], nums[0]


def parse_events():
    raw = json.load(open(os.path.join(D, "events_raw.json")))
    stations = json.load(open(os.path.join(D, "stations.json")))
    out = []
    for e in raw:
        m = re.search(r"on-([a-z]+)-(\d{1,2})-(\d{4})", e["slug"])
        if not m or m.group(1) not in MONTHS:
            continue
        day = date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)))
        desc = e.get("description") or ""
        sm = re.search(r"site=(\w+)", desc) or re.search(r"wunderground\.com/[^\s\"]*/(\w{4})\b", desc)
        if sm:
            st = sm.group(1).upper()
            loc = stations.get(st)
        elif "Hong Kong Observatory" in desc:
            st, loc = "HKO", HKO
        else:
            continue
        if not loc:
            continue
        buckets = []
        for mk in e.get("markets") or []:
            toks = mk.get("clobTokenIds")
            toks = json.loads(toks) if isinstance(toks, str) else toks
            prices = mk.get("outcomePrices")
            prices = json.loads(prices) if isinstance(prices, str) else prices
            if not toks or not prices:
                continue
            unit, lo, hi = parse_bucket(mk.get("groupItemTitle") or mk.get("question"))
            fs = mk.get("feeSchedule") or {}
            buckets.append({"label": mk.get("groupItemTitle"), "unit": unit, "lo": lo, "hi": hi, "yes": toks[0],
                            "won": prices[0] == "1", "fee_rate": fs.get("rate"),
                            "fees_enabled": mk.get("feesEnabled"), "volume": float(mk.get("volumeNum") or 0)})
        if sum(b["won"] for b in buckets) != 1:
            continue  # unresolved / ambiguous
        out.append({"id": e["id"], "slug": e["slug"], "city": e.get("seriesSlug"), "date": day.isoformat(),
                    "station": st, "lat": loc["lat"], "lon": loc["lon"], "unit": buckets[0]["unit"],
                    "buckets": buckets})
    json.dump(out, open(os.path.join(D, "events.json"), "w"))
    return out


def forecasts(events):
    by_st = {}
    for e in events:
        by_st.setdefault(e["station"], e)
    d0 = min(e["date"] for e in events)
    d1 = max(e["date"] for e in events)

    def one(e):
        unit = "fahrenheit" if e["unit"] == "F" else "celsius"
        q = urllib.parse.urlencode({"latitude": e["lat"], "longitude": e["lon"], "timezone": "auto",
                                    "temperature_unit": unit, "start_date": d0, "end_date": d1,
                                    "hourly": "temperature_2m_previous_day1,temperature_2m_previous_day2"})
        f = get(f"https://previous-runs-api.open-meteo.com/v1/forecast?{q}")
        res = {"utc_offset": f.get("utc_offset_seconds"), "tz": f.get("timezone"), "N1": {}, "N2": {}}
        h = f["hourly"]
        for i, t in enumerate(h["time"]):
            day = t[:10]
            for n in (1, 2):
                v = h[f"temperature_2m_previous_day{n}"][i]
                if v is not None:
                    res[f"N{n}"].setdefault(day, []).append(v)
        for n in (1, 2):  # require (nearly) full local day
            res[f"N{n}"] = {k: max(v) for k, v in res[f"N{n}"].items() if len(v) >= 22}
        a = get("https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(
            {"latitude": e["lat"], "longitude": e["lon"], "timezone": "auto", "temperature_unit": unit,
             "start_date": d0, "end_date": d1, "daily": "temperature_2m_max"}))
        arch = dict(zip(a["daily"]["time"], a["daily"]["temperature_2m_max"]))
        return e["station"], res, arch

    fc, ar = {}, {}
    with ThreadPoolExecutor(4) as ex:
        for st, res, arch in ex.map(one, list(by_st.values())):
            fc[st], ar[st] = res, arch
    json.dump(fc, open(os.path.join(D, "forecasts.json"), "w"))
    json.dump(ar, open(os.path.join(D, "archive.json"), "w"))
    return fc


def prices(events, fc):
    path = os.path.join(D, "prices.jsonl")
    done = set()
    if os.path.exists(path):
        done = {json.loads(l)["token"] for l in open(path)}
    jobs = []
    for e in events:
        off = fc.get(e["station"], {}).get("utc_offset") or 0
        day_end = datetime.fromisoformat(e["date"]).replace(tzinfo=timezone.utc) + timedelta(days=1) - timedelta(seconds=off)
        s, t = int((day_end - timedelta(hours=48)).timestamp()), int(day_end.timestamp())
        for b in e["buckets"]:
            if b["yes"] not in done:
                jobs.append((b["yes"], s, t))

    def one(j):
        tok, s, t = j
        try:
            h = get(f"https://clob.polymarket.com/prices-history?market={tok}&startTs={s}&endTs={t}&fidelity=60")
            return {"token": tok, "history": [[x["t"], x["p"]] for x in h.get("history") or []]}
        except Exception as ex:
            return {"token": tok, "error": str(ex)[:120]}

    with open(path, "a") as f, ThreadPoolExecutor(8) as ex:
        for i, r in enumerate(ex.map(one, jobs)):
            f.write(json.dumps(r) + "\n")
            if i % 2000 == 0:
                print(f"prices {i}/{len(jobs)}", flush=True)
    print(f"prices done: {len(jobs)} fetched, {len(done)} cached", flush=True)


if __name__ == "__main__":
    t0 = time.time()
    ev = parse_events()
    print(f"events parsed {len(ev)} cities {len({e['city'] for e in ev})} buckets {sum(len(e['buckets']) for e in ev)}", flush=True)
    fc = forecasts(ev)
    print(f"forecasts for {len(fc)} stations ({time.time() - t0:.0f}s)", flush=True)
    if "--no-prices" not in sys.argv:
        prices(ev, fc)
    print(f"total {time.time() - t0:.0f}s")
