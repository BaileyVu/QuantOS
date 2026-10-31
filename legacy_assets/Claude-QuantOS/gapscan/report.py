"""Rank gaps by realistic $/day at small size. A one-off gap is noise; a gap that keeps coming back with real size is a lead.

Episode = consecutive sightings no more than 5 minutes apart; assume one capture per episode at min(top-of-book, size).
Funding gaps are scored by average $/day at `size` per leg and by how often they were present.
  python3 report.py [--days 3] [--size 100]
"""
import argparse, glob, json, os, statistics
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))


def key(r):
    if r["kind"] == "cex":
        return ("cex", r["base"], r["buy"], r["sell"])
    if r["kind"] == "dex":
        return ("dex", r["base"], r["route"])
    return ("funding", r["base"], r["short"], r["long"])


def t(s):
    return datetime.fromisoformat(s).timestamp()


def episodes(ts_list, gap_s=300):
    ts = sorted(set(t(x) for x in ts_list))
    return sum(1 for i, x in enumerate(ts) if i == 0 or x - ts[i - 1] > gap_s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--size", type=float, default=100.0)
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()
    files = sorted(glob.glob(os.path.join(HERE, "data", "gaps_*.jsonl")))[-a.days:]
    groups, snaps = defaultdict(list), defaultdict(set)
    for path in files:
        for line in open(path):
            r = json.loads(line)
            if r.get("suspect_collision"):
                continue
            groups[key(r)].append(r)
            snaps[r["kind"]].add(r["ts"])
    allts = sorted(t(x) for s in snaps.values() for x in s)
    span_days = max((allts[-1] - allts[0]) / 86400, 1 / 24) if allts else 1
    print(f"files: {[os.path.basename(p) for p in files]}  span {span_days:.2f} days  size ${a.size:.0f}/leg")
    for kind in ("cex", "dex", "funding"):
        scored = []
        for k, rs in groups.items():
            if k[0] != kind:
                continue
            ts = [r["ts"] for r in rs]
            if kind == "funding":
                presence = len(set(ts)) / max(len(snaps["funding"]), 1)
                per_day = statistics.mean(r["net_usd_hold"] for r in rs) * a.size / 100 * presence
                extra = f"present {presence*100:5.1f}% of scans"
            else:
                ep = episodes(ts)
                net = statistics.median(r["net"] for r in rs)
                cap = min(statistics.median(r.get("top_size_usd") or a.size for r in rs), a.size)
                per_day = ep * net * cap / span_days
                extra = f"episodes {ep:4d}  median net {net*100:5.2f}%  size ${cap:.0f}"
            scored.append((per_day, extra, ts[0], ts[-1], k))
        scored.sort(reverse=True)
        print(f"\n== {kind.upper()}: {len(scored)} distinct gaps, ranked by est. $/day ==")
        for per_day, extra, t0, t1, k in scored[:a.top]:
            print(f"  ${per_day:6.2f}/day  {extra}  {t0[5:16]} -> {t1[5:16]}  {' '.join(k[1:])}")
    print("\nUpper bounds: assumes every episode is captured, transfers are instant and free, and funding persists.")


if __name__ == "__main__":
    main()
