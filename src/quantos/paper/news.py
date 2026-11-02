"""Read-only crypto/finance headlines for the dashboard: CoinDesk + Cointelegraph RSS, Watcher Guru's public
Telegram preview (t.me/s/...; no free API). Text only is shown; links and image URLs must be https.
Cached in memory for 5 minutes; a failing source is reported, never raised."""
from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from urllib.request import Request, urlopen

SOURCES = (("CoinDesk", "rss", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
           ("Cointelegraph", "rss", "https://cointelegraph.com/rss"),
           ("Watcher Guru", "telegram", "https://t.me/s/WatcherGuru"))
TTL_S, PER_SOURCE, MAX_BYTES = 300, 15, 3_000_000
MEDIA = "{http://search.yahoo.com/mrss/}"
_cache: dict = {"at": 0.0, "data": None}


def _get(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (QuantOS dashboard; read-only)"})
    with urlopen(req, timeout=15) as r:
        return r.read(MAX_BYTES)


def _https(u) -> str | None:
    u = html.unescape(str(u or "")).strip()
    return u if u.startswith("https://") and len(u) < 2000 and not re.search(r"[\s\"'<>]", u) else None


def _text(s, n=300) -> str:
    s = re.sub(r"<[^>]+>", " ", html.unescape(str(s or "")))
    return re.sub(r"\s+", " ", s).strip()[:n]


def parse_rss(raw: bytes, source: str) -> list[dict]:
    out = []
    for it in ET.fromstring(raw).iter("item"):
        img = None
        for tag in (MEDIA + "content", MEDIA + "thumbnail", "enclosure"):
            e = it.find(tag)
            if e is not None and _https(e.get("url")):
                img = _https(e.get("url")); break
        out.append({"source": source, "title": _text(it.findtext("title"), 200), "link": _https(it.findtext("link")),
                    "image": img, "time": (it.findtext("pubDate") or "").strip()[:40],
                    "summary": _text(it.findtext("description"), 240)})
    return [x for x in out if x["title"] and x["link"]][:PER_SOURCE]


def parse_telegram(raw: bytes, source: str) -> list[dict]:
    page, out = raw.decode("utf-8", "replace"), []
    for block in re.split(r'<div class="tgme_widget_message_wrap', page)[1:]:
        m = re.search(r'data-post="([\w]+/\d+)"', block)
        txt = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.S)
        if not m or not txt: continue
        img = re.search(r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)", block)
        when = re.search(r'<time[^>]*datetime="([^"]+)"', block)
        out.append({"source": source, "title": _text(txt.group(1), 200), "link": "https://t.me/" + m.group(1),
                    "image": _https(img.group(1)) if img else None, "time": when.group(1) if when else "", "summary": ""})
    return out[::-1][:PER_SOURCE]  # page lists oldest first


def news(now=time.time) -> dict:
    if _cache["data"] is not None and now() - _cache["at"] < TTL_S:
        return _cache["data"]
    items, status = [], {}
    for name, kind, url in SOURCES:
        try:
            got = (parse_rss if kind == "rss" else parse_telegram)(_get(url), name)
            items += got; status[name] = f"ok ({len(got)})"
        except Exception as e:  # network, HTTP or parse failure: report and continue
            status[name] = f"error: {type(e).__name__}"
    _cache.update(at=now(), data={"fetched_s": int(now()), "status": status, "items": items})
    return _cache["data"]
