"""Live sentiment from Reddit, news RSS feeds and (optionally) X. Free sources, no keys except X.

There is no free history for these sources, so they cannot be backtested; they only feed the paper loop.
Each coin gets a score in [-1, 1]: half from headlines that mention it, half from the overall market mood.
"""
import hashlib, html, json, math, re, time, urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

try:
    from .sentibot_reach_adapter import _safe_open, normalize_public_http_url
except ImportError:  # direct script execution
    from sentibot_reach_adapter import _safe_open, normalize_public_http_url

UA = {"User-Agent": "Mozilla/5.0 (sentibot paper research)"}
# QuantOS candidate_features.ALT_SOURCE_UNIVERSE, translated from USDT twins to
# base-asset news aliases. Keep this source list aligned when that universe changes.
COIN_ALIASES = {
    "BTCUSDT": ("bitcoin", "btc", "xbt"), "ETHUSDT": ("ethereum", "eth", "ether"),
    "SOLUSDT": ("solana", "sol"), "BNBUSDT": ("binance coin", "bnb"),
    "XRPUSDT": ("xrp", "ripple"), "DOGEUSDT": ("dogecoin", "doge"),
    "ADAUSDT": ("cardano", "ada"), "AVAXUSDT": ("avalanche", "avax"),
    "LINKUSDT": ("chainlink", "link"), "DOTUSDT": ("polkadot", "dot"),
    "LTCUSDT": ("litecoin", "ltc"), "BCHUSDT": ("bitcoin cash", "bch"),
    "TRXUSDT": ("tron", "trx"), "ATOMUSDT": ("cosmos", "atom"),
    "NEARUSDT": ("near protocol", "near"), "UNIUSDT": ("uniswap", "uni"),
    "FILUSDT": ("filecoin", "fil"), "ETCUSDT": ("ethereum classic", "etc"),
    "AAVEUSDT": ("aave",), "XLMUSDT": ("stellar", "xlm"),
    "ALGOUSDT": ("algorand", "algo"), "SANDUSDT": ("the sandbox", "sand"),
    "MANAUSDT": ("decentraland", "mana"), "AXSUSDT": ("axie infinity", "axs"),
    "CRVUSDT": ("curve finance", "crv"), "RUNEUSDT": ("thorchain", "rune"),
    "THETAUSDT": ("theta network", "theta"), "VETUSDT": ("vechain", "vet"),
    "GALAUSDT": ("gala games", "gala"),
}
COINS = {symbol: r"\b(?:" + "|".join(re.escape(x) for x in aliases) + r")\b"
         for symbol, aliases in COIN_ALIASES.items()}
COIN_SUBREDDITS = {
    "BTCUSDT": "Bitcoin", "ETHUSDT": "ethereum", "SOLUSDT": "solana",
    "BNBUSDT": "BNBChain", "XRPUSDT": "XRP", "DOGEUSDT": "dogecoin",
    "ADAUSDT": "cardano", "AVAXUSDT": "Avax", "LINKUSDT": "Chainlink",
    "DOTUSDT": "Polkadot", "LTCUSDT": "litecoin", "BCHUSDT": "bitcoincash",
    "TRXUSDT": "tronix", "ATOMUSDT": "cosmosnetwork", "NEARUSDT": "NEARprotocol",
    "UNIUSDT": "Uniswap", "FILUSDT": "filecoin", "ETCUSDT": "EthereumClassic",
    "AAVEUSDT": "Aave_Official", "XLMUSDT": "stellar", "ALGOUSDT": "Algorand",
    "SANDUSDT": "TheSandboxGaming", "MANAUSDT": "decentraland",
    "AXSUSDT": "AxieInfinity", "CRVUSDT": "CurveFinance", "RUNEUSDT": "THORChain",
    "THETAUSDT": "ThetaNetwork", "VETUSDT": "VeChain", "GALAUSDT": "GalaGames",
}
SUBREDDITS = tuple(dict.fromkeys(("CryptoCurrency", "CryptoMarkets", *COIN_SUBREDDITS.values())))
LAST_LINKS = {}  # RSS title -> canonical article URL; consumed by the Jina shadow reader.
MAX_BODY_BYTES = 5 * 1024 * 1024
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

SCORER_NAME = "sentibot-crypto-lexicon"
SCORER_VERSION = "1"
SCORER_LEXICON_SHA256 = hashlib.sha256(
    json.dumps(CRYPTO_LEXICON, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


def polarity(text):
    """Sentiment of one headline in [-1, 1]."""
    words = re.findall(r"[a-z\-]+", text.lower())
    s = sum(CRYPTO_LEXICON.get(w, 0) for w in words)
    return s / math.sqrt(s * s + 15) if s else 0.0  # same normalisation VADER uses


def _get(url, timeout=15):
    url = normalize_public_http_url(url)
    req = urllib.request.Request(url, headers=UA)
    with _safe_open(req, timeout=timeout) as r:
        body = r.read(MAX_BODY_BYTES + 1)
    if len(body) > MAX_BODY_BYTES:
        raise ValueError("response too large")
    return body


STATUS = {}  # source -> "ok" or why it returned nothing; logged every cycle so a dead source is never silent


def reddit_items(now=None):
    """[(text, weight, unix_time)] from new + hot posts. Weight grows with log(score + comments).

    Reddit closed most unauthenticated JSON access; failures are recorded in STATUS["reddit"].
    """
    now, out, errors = now or time.time(), [], []
    for sub in SUBREDDITS:
        # Arctic Shift (public Reddit archive, minutes behind live) first: reddit.com is blocked in Vietnam and
        # refuses most anonymous JSON. Falls back to reddit.com new/hot if the archive is down.
        try:
            d = json.loads(_get(f"https://arctic-shift.photon-reddit.com/api/posts/search?subreddit={sub}"
                                f"&limit=100&sort=desc&after={int(now - WINDOW_H * 3600)}", timeout=10))
            rows = [{"data": p} for p in d.get("data") or []]
            source = "arctic"
        except Exception as e:
            errors.append(f"{sub}/arctic: {type(e).__name__}")
            rows, source = None, "reddit"
        for kind in (("archive",) if rows is not None else ("new", "hot")):
            if rows is None:
                try:
                    d = json.loads(_get(f"https://www.reddit.com/r/{sub}/{kind}.json?limit=100", timeout=8))
                except Exception as e:
                    errors.append(f"{sub}/{kind}: {type(e).__name__}")
                    continue
            for c in (rows if rows is not None else d.get("data", {}).get("children", [])):
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
    STATUS["reddit"] = (f"ok ({len(uniq)} posts, arctic-shift)" if uniq and not errors else "ok" if uniq
                        else f"EMPTY ({len(errors)} requests failed: {errors[:2]})")
    return uniq


def news_items(now=None):
    now, out = now or time.time(), []
    LAST_LINKS.clear()
    for name, url in FEEDS.items():
        try:
            root = ET.fromstring(_get(url))
        except Exception:
            continue
        for it in root.iter("item"):
            title = html.unescape(it.findtext("title") or "")
            link = html.unescape(it.findtext("link") or "").strip()
            try:
                ts = parsedate_to_datetime(it.findtext("pubDate")).timestamp()
            except Exception:
                continue
            if now - ts <= WINDOW_H * 3600:
                out.append((title, 1.0, ts))
                if link:
                    LAST_LINKS.setdefault(title, link)
    seen, uniq = set(), []
    for t in out:
        k = t[0].lower()[:80]
        if k not in seen:
            seen.add(k)
            uniq.append(t)
    return uniq


def score_items(items, symbols=None):
    """Per-coin scores in [-1, 1] plus counts. Coin score = 0.5 * coin headlines + 0.5 * all headlines."""
    def agg(rows):
        if not rows:
            return 0.0
        tw = sum(w for _, w in rows)
        return sum(p * w for p, w in rows) / tw * len(rows) / (len(rows) + SHRINK)
    scored = [(polarity(t), w, t) for t, w, _ in items]
    market = agg([(p, w) for p, w, _ in scored])
    out = {"_market": round(market, 4), "_n": len(scored)}
    patterns = dict(COINS)
    if symbols:
        for sym, aliases in symbols.items():
            escaped = "|".join(re.escape(str(alias)) for alias in aliases if str(alias))
            if escaped:
                patterns[sym] = r"\b(?:" + escaped + r")\b"
    for sym, pat in patterns.items():
        rows = [(p, w) for p, w, t in scored if re.search(pat, t, re.I)]
        out[sym] = round(0.5 * agg(rows) + 0.5 * market, 4)
        out[f"_n_{sym}"] = len(rows)
    return out


def snapshot():
    """Public RSS and Reddit sentiment sample used by the shadow baseline."""
    r, n = reddit_items(), news_items()
    STATUS["news"] = "ok" if n else "EMPTY (no headlines from any feed)"
    return {"reddit": score_items(r), "news": score_items(n), "status": dict(STATUS),
            "counts": {"reddit": len(r), "news": len(n)},
            "top_headlines": sorted(((round(polarity(t), 2), t) for t, _, _ in n), key=lambda z: z[0])[:3]
                             + sorted(((round(polarity(t), 2), t) for t, _, _ in n), key=lambda z: -z[0])[:3]}


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=1))
