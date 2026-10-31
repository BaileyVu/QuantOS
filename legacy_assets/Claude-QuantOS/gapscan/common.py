"""Shared helpers. Public endpoints only, no keys, no orders. Python 3.9, stdlib only."""
import json, os, urllib.request
from datetime import datetime, timezone

UA = {"user-agent": "claude-quantos-gapscan", "accept": "application/json"}


def http(url, body=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    headers = dict(UA, **({"content-type": "application/json"} if body is not None else {}))
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_jsonl(path, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0
