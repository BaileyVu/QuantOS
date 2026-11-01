"""Live sentiment from Reddit, news RSS feeds and (optionally) X. Free sources, no keys except X.

There is no free history for these sources, so they cannot be backtested; they only feed the paper loop.
Each coin gets a score in [-1, 1]: half from headlines that mention it, half from the overall market mood.
"""
import html, json, math, os, re, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

UA = {"User-Agent": "Mozilla/5.0 (sentibot paper research by u/anon)"}
COINS = {"BTCUSDT": r"\b(bitcoin|btc)\b", "ETHUSDT": r"\b(ethereum|eth|ether)\b", "SOLUSDT": r"\b(solana|sol)\b"}
SUBREDDITS = ["CryptoCurrency", "Bitcoin", "ethereum", "solana", "CryptoMarkets"]
FEEDS = {
    "yahoo": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD,ETH-USD,SOL-USD&region=US&lang=en-US",
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss",
    "cointelegraph": "https://cointelegraph.com/rss",
    "decrypt": "https://decrypt.co/feed",
    "google": "https://news.google.com/rss/search?q=bitcoin+OR+ethereum+OR+solana+crypto&hl=en-US&gl=US&ceid=US:en",
}
WINDOW_H = 24
SHRINK = 10  # score *= n / (n + SHRINK): a handful of headlines barely moves it

# Crypto words VADER does not know or misreads. Values on VADER's -4..+4 scale.
CRYPTO_LEXICON = {
    "bullish": 2.5, "bearish": -2.5, "rally": 2.0, "rallies": 2.0, "surge": 2.2, "surges": 2.2, "soar": 2.4, "soars": 2.4,
    "breakout": 1.8, "ath": 2.5, "moon": 2.0, "mooning": 2.2, "pump": 1.2, "pumps": 1.2, "inflows": 1.8, "inflow": 1.8,
    "adoption": 1.5, "approval": 1.8, "approved": 1.8, "accumulate": 1.2, "accumulating": 1.2, "rebound": 1.6, "recovers": 1.5,
    "crash": -3.0, "crashes": -3.0, "plunge": -2.6, "plunges": -2.6, "dump": -2.2, "dumps": -2.2, "selloff": -2.3,
    "sell-off": -2.3, "liquidation": -2.0, "liquidations": -2.0, "liquidated": -2.2, "hack": -3.0, "hacked": -3.0,
    "exploit": -2.8, "exploited": -2.8, "outflows": -1.8, "outflow": -1.8, "rug": -3.0, "rugpull": -3.2, "scam": -3.0,
    "lawsuit": -2.0, "sec": -0.5, "ban": -2.2, "bans": -2.2, "fud": -1.5, "bleeding": -2.2, "capitulation": -2.5,
    "slump": -2.2, "tumbles": -2.4, "tumble": -2.4, "dip": -0.8, "correction": -1.2, "rekt": -2.5, "delisting": -2.4,
}

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _vader = SentimentIntensityAnalyzer()
    _vader.lexicon.update(CRYPTO_LEXICON)
except Exception:  # fall back to the crypto lexicon alone
    _vader = None


def polarity(text):
    """Sentiment of one headline in [-1, 1]."""
    if _vader:
        return _vader.polarity_scores(text)["compound"]
    words = re.findall(r"[a-z\-]+", text.lower())
    s = sum(CRYPTO_LEXICON.get(w, 0) for w in words)
    return s / math.sqrt(s * s + 15) if s else 0.0  # same normalisation VADER uses


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


STATUS = {}  # source -> "ok" or why it returned nothing; logged every cycle so a dead source is never silent


def reddit_items(now=None):
    """[(text, weight, unix_time)] from new + hot posts. Weight grows with log(score + comments).

    Reddit closed most unauthenticated JSON access; failures are recorded in STATUS["reddit"].
    """
    now, out, errors = now or time.time(), [], []
    for sub in SUBREDDITS:
        for kind in ("new", "hot"):
            try:
                d = json.loads(_get(f"https://www.reddit.com/r/{sub}/{kind}.json?limit=100", timeout=8))
            except Exception as e:
                errors.append(f"{sub}/{kind}: {type(e).__name__}")
                continue
            for c in d.get("data", {}).get("children", []):
                p = c["data"]
                if now - p.get("created_utc", 0) > WINDOW_H * 3600 or p.get("stickied"):
                    continue
                w = 1 + math.log1p(max(0, p.get("score", 0)) + p.get("num_comments", 0))
                out.append((p.get("title", ""), w, p["created_utc"], p.get("id")))
    seen, uniq = set(), []
    for t in out:  # the same post can be in new and hot
        if t[3] not in seen:
            seen.add(t[3])
            uniq.append(t[:3])
    STATUS["reddit"] = "ok" if uniq else f"EMPTY ({len(errors)}/{2 * len(SUBREDDITS)} requests failed: {errors[:2]})"
    return uniq


def news_items(now=None):
    now, out = now or time.time(), []
    for name, url in FEEDS.items():
        try:
            root = ET.fromstring(_get(url))
        except Exception:
            continue
        for it in root.iter("item"):
            title = html.unescape(it.findtext("title") or "")
            try:
                ts = parsedate_to_datetime(it.findtext("pubDate")).timestamp()
            except Exception:
                continue
            if now - ts <= WINDOW_H * 3600:
                out.append((title, 1.0, ts))
    seen, uniq = set(), []
    for t in out:
        k = t[0].lower()[:80]
        if k not in seen:
            seen.add(k)
            uniq.append(t)
    return uniq


def x_items(now=None):
    """Recent X posts via the paid v2 API. Only runs when X_BEARER_TOKEN is set."""
    tok = os.environ.get("X_BEARER_TOKEN")
    if not tok:
        STATUS["x"] = "disabled (no X_BEARER_TOKEN)"
        return []
    q = urllib.parse.quote("(bitcoin OR ethereum OR solana OR $BTC OR $ETH OR $SOL) lang:en -is:retweet")
    req = urllib.request.Request(f"https://api.twitter.com/2/tweets/search/recent?query={q}&max_results=100"
                                 "&tweet.fields=public_metrics,created_at", headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
    except Exception as e:
        STATUS["x"] = f"EMPTY ({type(e).__name__})"
        return []
    out = []
    for t in d.get("data", []):
        m = t.get("public_metrics", {})
        w = 1 + math.log1p(m.get("like_count", 0) + m.get("retweet_count", 0))
        out.append((t["text"], w, time.time()))
    return out


def score_items(items):
    """Per-coin scores in [-1, 1] plus counts. Coin score = 0.5 * coin headlines + 0.5 * all headlines."""
    def agg(rows):
        if not rows:
            return 0.0
        tw = sum(w for _, w in rows)
        return sum(p * w for p, w in rows) / tw * len(rows) / (len(rows) + SHRINK)
    scored = [(polarity(t), w, t) for t, w, _ in items]
    market = agg([(p, w) for p, w, _ in scored])
    out = {"_market": round(market, 4), "_n": len(scored)}
    for sym, pat in COINS.items():
        rows = [(p, w) for p, w, t in scored if re.search(pat, t, re.I)]
        out[sym] = round(0.5 * agg(rows) + 0.5 * market, 4)
        out[f"_n_{sym}"] = len(rows)
    return out


def snapshot():
    """Everything the paper loop needs from the live sources, plus the headlines it saw (for the log)."""
    r, n, x = reddit_items(), news_items(), x_items()
    social = r + x
    STATUS["news"] = "ok" if n else "EMPTY (no headlines from any feed)"
    return {"reddit": score_items(social), "news": score_items(n), "status": dict(STATUS),
            "counts": {"reddit": len(r), "x": len(x), "news": len(n)},
            "top_headlines": sorted(((round(polarity(t), 2), t) for t, _, _ in n), key=lambda z: z[0])[:3]
                             + sorted(((round(polarity(t), 2), t) for t, _, _ in n), key=lambda z: -z[0])[:3]}


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=1))
