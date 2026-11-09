"""Sentibot v2 public-source shadow collector.

Run as a standalone process with a dedicated state directory. This CLI writes
only ``reach_shadow.jsonl`` there; it never imports QuantOS risk or paper code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import sys
from contextlib import contextmanager
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import sentiment as sb
    from .sentibot_reach_adapter import (binance_usdc_perpetuals, exa_search, jina_read,
                                         normalize_public_http_url, sanitize_text)
except ImportError:  # direct script execution: python sentibot/sentibot_reach_shadow.py
    import sentiment as sb
    from sentibot_reach_adapter import (binance_usdc_perpetuals, exa_search, jina_read,
                                         normalize_public_http_url, sanitize_text)

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
MAX_EXA_QUERIES_PER_SAMPLE = 12


def _key(title: str) -> str:
    normalized = " ".join(title.casefold().split())[:300]
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _published(value, now: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
        return result
    if not isinstance(value, str) or not value.strip():
        return now
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return float("nan")
        return parsed.timestamp()
    except ValueError:
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(value[:40], fmt)
                if parsed.tzinfo is None:
                    return float("nan")
                return parsed.timestamp()
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
    published = _published(event.get("published"), now)
    if not math.isfinite(published) or published > now:
        return None
    return {
        "src": str(event.get("src") or "unknown")[:16],
        "sym_hint": event.get("sym_hint"),
        "title": title,
        "url": url,
        "published": published,
        "text": text,
    }


def collect(*, state_dir: str | Path, now: float | None = None,
            baseline_module=None, exa_search_fn=None, jina_read_fn=None,
            universe_fn=None, clock_fn=None) -> dict:
    """Fetch one shadow sample and append it to the dedicated output JSONL."""
    started_at = time.time() if now is None else float(now)
    clock = clock_fn or time.time
    state = Path(state_dir).expanduser().resolve()
    source_root = Path(__file__).resolve().parent
    if state == source_root or source_root in state.parents:
        raise ValueError("state directory must be outside the vendored source directory")
    output = state / "reach_shadow.jsonl"
    if output.is_symlink():
        raise ValueError("shadow output must not be a symlink")
    seen = _previous_first_seen(output, started_at)
    base = baseline_module or sb
    search = exa_search_fn or exa_search
    read = jina_read_fn or jina_read
    status: dict[str, str] = {}
    events = []
    universe = {}
    universe_complete = False
    try:
        contracts = (universe_fn or binance_usdc_perpetuals)()
        aliases = getattr(base, "COIN_ALIASES", {})
        for contract in contracts:
            if isinstance(contract, str) and contract.endswith("USDC"):
                asset = contract[:-4] + "USDT"
                known = aliases.get(asset)
                universe.setdefault(asset, known[0] if known else ASSETS.get(asset, contract[:-4].lower()))
        if not universe:
            raise ValueError("empty USDC perpetual universe")
        universe_complete = True
    except Exception as exc:
        universe = dict(ASSETS)
        status["universe"] = f"INCOMPLETE ({type(exc).__name__}; {len(ASSETS)} fallback assets)"
    symbols = list(universe.items())
    try:
        if output.exists():
            with output.open(encoding="utf-8") as history:
                prior_cycles = sum(1 for _ in history)
        else:
            prior_cycles = 0
    except OSError:
        prior_cycles = 0
    start_index = (prior_cycles * MAX_EXA_QUERIES_PER_SAMPLE) % max(1, len(symbols))
    sampled = [symbols[(start_index + i) % len(symbols)]
               for i in range(min(MAX_EXA_QUERIES_PER_SAMPLE, len(symbols)))]
    for symbol, name in sampled:
        try:
            for item in search(f"{name} cryptocurrency news", EXA_LIMIT):
                published = _published(item.get("published"), started_at)
                if published <= started_at and started_at - published <= WINDOW_H * 3600:
                    events.append(_safe_event({**item, "src": "exa", "sym_hint": symbol}, started_at))
            status[f"exa_{symbol}"] = "ok"
        except Exception as exc:
            status[f"exa_{symbol}"] = f"EMPTY ({type(exc).__name__})"
    coverage = ("complete" if universe_complete and len(sampled) == len(symbols)
                else f"INCOMPLETE ({len(sampled)}/{len(symbols)} queried this sample)")
    status["universe_coverage"] = coverage

    rss_items = base.news_items(started_at) if hasattr(base, "news_items") else []
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
                                 "published": published, "text": article.get("text", "")}, started_at)
            if event:
                events.append(event)
                jina_ok += 1
        except Exception:
            continue
    status["jina"] = f"ok {jina_ok}/{len(rss_sorted)}" if title_links else "skipped (RSS title-link map empty)"

    reddit_items = base.reddit_items(started_at) if hasattr(base, "reddit_items") else []
    status.update(getattr(base, "STATUS", {}))
    rss_items = [item for item in rss_items if len(item) >= 3 and
                 isinstance(item[2], (int, float)) and math.isfinite(item[2]) and item[2] <= started_at]
    reddit_items = [item for item in reddit_items if len(item) >= 3 and
                    isinstance(item[2], (int, float)) and math.isfinite(item[2]) and item[2] <= started_at]
    unique: dict[str, dict] = {}
    for event in events:
        if not event:
            continue
        key = _key(event["title"])
        current = unique.get(key)
        if current is None or len(event["text"]) > len(current["text"]):
            unique[key] = event
    reach_items = [(event["title"] + ". " + event["text"], 1.0, event["published"])
                   for event in unique.values()]
    baseline_items = list(rss_items) + list(reddit_items)
    score_symbols = {asset: getattr(base, "COIN_ALIASES", {}).get(asset, (name, asset[:-4]))
                     for asset, name in universe.items()}
    score = lambda items: base.score_items(items, symbols=score_symbols)
    try:
        reach_score, baseline_score = score(reach_items), score(baseline_items)
    except TypeError:
        # Keep injected legacy test scorers usable; production sentiment supports symbols.
        reach_score, baseline_score = base.score_items(reach_items), base.score_items(baseline_items)
    available_at = float(clock())
    if not math.isfinite(available_at) or available_at < started_at:
        raise ValueError("completion clock must be finite and not precede collection start")
    for key, event in unique.items():
        first_seen = seen.get(key)
        event["first_seen"] = available_at if first_seen is None else first_seen
        event["novel"] = first_seen is None
    row = {
        "started_at": started_at,
        "available_at": available_at,
        "t": available_at,
        "universe_complete": universe_complete,
        "universe_size": len(universe),
        "universe_queried": len(sampled),
        "universe_coverage": coverage,
        "scorer": {"name": getattr(base, "SCORER_NAME", "injected-scorer"),
                   "version": getattr(base, "SCORER_VERSION", "unknown"),
                   "lexicon_sha256": getattr(base, "SCORER_LEXICON_SHA256", None)},
        "reach": reach_score,
        "baseline_news": baseline_score,
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


class CollectionDeadline(BaseException):
    """Escape source-level exception handlers without publishing a partial sample."""


@contextmanager
def cycle_deadline(seconds):
    """Hard wall-clock bound including DNS/redirects/slow reads (POSIX main thread)."""
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("collection deadline must be positive and finite")
    def expired(*_args):
        raise CollectionDeadline("collection cycle deadline exceeded; no sample published")
    previous = signal.signal(signal.SIGALRM, expired)
    old_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *old_timer)
        signal.signal(signal.SIGALRM, previous)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True,
                        help="dedicated state path outside sentibot source; only reach_shadow.jsonl is written")
    parser.add_argument("--once", action="store_true", help="collect one sample and exit")
    parser.add_argument("--every", type=int, default=900, help="seconds between samples")
    parser.add_argument("--cycle-timeout", type=int, default=600,
                        help="hard per-cycle deadline in seconds; timed-out samples are not published")
    args = parser.parse_args(argv)
    if args.every < 60:
        parser.error("--every must be at least 60 seconds")
    if args.cycle_timeout < 1 or args.cycle_timeout > args.every:
        parser.error("--cycle-timeout must be positive and no greater than --every")
    while True:
        started = time.monotonic()
        try:
            with cycle_deadline(args.cycle_timeout):
                row = collect(state_dir=args.state_dir)
        except CollectionDeadline as exc:
            print(str(exc), file=sys.stderr, flush=True)
            if args.once:
                return 1
            time.sleep(max(0, args.every - (time.monotonic() - started)))
            continue
        print(time.strftime("%H:%M:%S"), "events", row["n_events"], "novel", row["n_novel"],
              "reach", row["reach"], flush=True)
        if args.once:
            return 0
        time.sleep(max(0, args.every - (time.monotonic() - started)))


if __name__ == "__main__":
    raise SystemExit(main())
