"""Sentibot v2 public-source shadow collector.

Run as a standalone process with a dedicated state directory. This CLI writes
only ``reach_shadow.jsonl`` there; it never imports QuantOS risk or paper code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import sentiment as sb
    from .sentibot_reach_adapter import exa_search, jina_read, normalize_public_http_url, sanitize_text
except ImportError:  # direct script execution: python sentibot/sentibot_reach_shadow.py
    import sentiment as sb
    from sentibot_reach_adapter import exa_search, jina_read, normalize_public_http_url, sanitize_text

ASSETS = {
    "BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "SOLUSDT": "solana",
    "BNBUSDT": "binance coin", "XRPUSDT": "xrp", "DOGEUSDT": "dogecoin",
    "ADAUSDT": "cardano", "AVAXUSDT": "avalanche", "LINKUSDT": "chainlink",
    "DOTUSDT": "polkadot", "LTCUSDT": "litecoin", "BCHUSDT": "bitcoin cash",
    "TRXUSDT": "tron", "ATOMUSDT": "cosmos", "NEARUSDT": "near protocol",
    "UNIUSDT": "uniswap", "FILUSDT": "filecoin", "ETCUSDT": "ethereum classic",
    "AAVEUSDT": "aave", "XLMUSDT": "stellar", "ALGOUSDT": "algorand",
    "SANDUSDT": "the sandbox", "MANAUSDT": "decentraland", "AXSUSDT": "axie infinity",
    "CRVUSDT": "curve finance", "RUNEUSDT": "thorchain", "THETAUSDT": "theta network",
    "VETUSDT": "vechain", "GALAUSDT": "gala games",
}
JINA_MAX = 8
EXA_LIMIT = 3
BODY_CHARS = 1500
WINDOW_H = 24
SEEN_DAYS = 7


def _key(title: str) -> str:
    normalized = " ".join(title.casefold().split())[:300]
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _published(value, now: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return now
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d"):
            try:
                return datetime.strptime(value[:40], fmt).replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                continue
    return now


def _previous_first_seen(path: Path, now: float) -> dict[str, float]:
    if not path.exists():
        return {}
    seen: dict[str, float] = {}
    cutoff = now - SEEN_DAYS * 86400
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                raise ValueError(f"invalid existing shadow log at line {line_number}") from None
            if row.get("t", 0) < cutoff:
                continue
            for event in row.get("events", []):
                title = event.get("title")
                first_seen = event.get("first_seen")
                if isinstance(title, str) and isinstance(first_seen, (int, float)):
                    key = _key(title)
                    seen[key] = min(seen.get(key, float(first_seen)), float(first_seen))
    return seen


def _safe_event(event: dict, now: float) -> dict | None:
    title = sanitize_text(str(event.get("title") or ""), 300).strip()
    url = event.get("url")
    if not title or not isinstance(url, str) or not url.strip():
        return None
    url = normalize_public_http_url(url)
    text = sanitize_text(str(event.get("text") or ""), BODY_CHARS)
    return {
        "src": str(event.get("src") or "unknown")[:16],
        "sym_hint": event.get("sym_hint"),
        "title": title,
        "url": url,
        "published": _published(event.get("published"), now),
        "text": text,
    }


def collect(*, state_dir: str | Path, now: float | None = None,
            baseline_module=None, exa_search_fn=None, jina_read_fn=None) -> dict:
    """Fetch one shadow sample and append it to the dedicated output JSONL."""
    now = time.time() if now is None else now
    state = Path(state_dir).expanduser().resolve()
    source_root = Path(__file__).resolve().parent
    if state == source_root or source_root in state.parents:
        raise ValueError("state directory must be outside the vendored source directory")
    output = state / "reach_shadow.jsonl"
    if output.is_symlink():
        raise ValueError("shadow output must not be a symlink")
    seen = _previous_first_seen(output, now)
    base = baseline_module or sb
    search = exa_search_fn or exa_search
    read = jina_read_fn or jina_read
    status: dict[str, str] = {}
    events = []

    for symbol, name in ASSETS.items():
        try:
            for item in search(f"{name} cryptocurrency news", EXA_LIMIT):
                published = _published(item.get("published"), now)
                if now - published <= WINDOW_H * 3600:
                    events.append(_safe_event({**item, "src": "exa", "sym_hint": symbol}, now))
            status[f"exa_{symbol}"] = "ok"
        except Exception as exc:
            status[f"exa_{symbol}"] = f"EMPTY ({type(exc).__name__})"

    rss_items = base.news_items(now) if hasattr(base, "news_items") else []
    title_links = getattr(base, "LAST_LINKS", {})
    rss_sorted = sorted(rss_items, key=lambda row: -row[2])[:JINA_MAX]
    jina_ok = 0
    for title, _weight, published in rss_sorted:
        url = title_links.get(title)
        if not url:
            continue
        try:
            article = read(url)
            event = _safe_event({"src": "jina", "title": title, "url": url,
                                 "published": published, "text": article.get("text", "")}, now)
            if event:
                events.append(event)
                jina_ok += 1
        except Exception:
            continue
    status["jina"] = f"ok {jina_ok}/{len(rss_sorted)}" if title_links else "skipped (RSS title-link map empty)"

    reddit_items = base.reddit_items(now) if hasattr(base, "reddit_items") else []
    status.update(getattr(base, "STATUS", {}))
    unique: dict[str, dict] = {}
    for event in events:
        if not event:
            continue
        key = _key(event["title"])
        current = unique.get(key)
        if current is None or len(event["text"]) > len(current["text"]):
            unique[key] = event
    for key, event in unique.items():
        first_seen = seen.get(key)
        event["first_seen"] = now if first_seen is None else first_seen
        event["novel"] = first_seen is None

    reach_items = [(event["title"] + ". " + event["text"], 1.0, event["published"])
                   for event in unique.values()]
    baseline_items = list(rss_items) + list(reddit_items)
    row = {
        "t": now,
        "reach": base.score_items(reach_items),
        "baseline_news": base.score_items(baseline_items),
        "status": status,
        "n_events": len(unique),
        "n_novel": sum(event["novel"] for event in unique.values()),
        "events": [{key: event[key] for key in
                    ("src", "sym_hint", "title", "url", "published", "first_seen", "novel")}
                   for event in unique.values()],
    }
    state.mkdir(parents=True, exist_ok=True)
    if not hasattr(os, "O_NOFOLLOW"):
        raise OSError("platform does not support no-follow shadow output")
    fd = os.open(output, os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    return row


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True,
                        help="dedicated state path outside sentibot source; only reach_shadow.jsonl is written")
    parser.add_argument("--once", action="store_true", help="collect one sample and exit")
    parser.add_argument("--every", type=int, default=900, help="seconds between samples")
    args = parser.parse_args(argv)
    if args.every < 60:
        parser.error("--every must be at least 60 seconds")
    while True:
        row = collect(state_dir=args.state_dir)
        print(time.strftime("%H:%M:%S"), "events", row["n_events"], "novel", row["n_novel"],
              "reach", row["reach"], flush=True)
        if args.once:
            return 0
        time.sleep(args.every)


if __name__ == "__main__":
    raise SystemExit(main())
