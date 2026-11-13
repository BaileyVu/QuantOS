"""Local live dashboard: `python -m quantos.paper.webdash --run NAME=STATE ... [--sentibot APP_DIR] [--port 8787]`.

Serves http://127.0.0.1:PORT/ and /api/state (localhost only). Reads each run's status.json, heartbeat, alerts and its journal
(SQLite read-only) plus sentibot's latest perp line. One write: POST /api/close drops a close request file in
the run's state dir (the bot itself closes the position and cancels its stop/TP). Network only for the cached proxies: /api/news,
/api/klines (Binance, 15 s) and /api/fng (alternative.me, 10 min).
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

DECISIONS = ("SIGNAL", "APPROVED", "REJECTED_RISK", "FILLED", "PARTIAL", "CLOSED", "CANCELED",
             "SENTIMENT_SHADOW", "SHADOW_ERROR", "KILL_SWITCH", "LOSS_BREAKER", "EXCHANGE_RECONCILE_FAILED")


def _json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _tail(path: Path, n: int) -> list:
    try:
        lines = path.read_text().splitlines()[-n:]
    except OSError:
        return []
    out = []
    for line in lines:
        try: out.append(json.loads(line))
        except ValueError: pass
    return out


def _price(p: dict) -> str:
    """Short human text for fill/close events: price to 6 significant digits, plus gross PnL."""
    try:
        text = f"price {float(p['price']):.6g}" if p.get("price") else ""
        if p.get("pnl_gross"): text += f", pnl {float(p['pnl_gross']):+.4f}"
        return text
    except (TypeError, ValueError):
        return ""


def run_view(name: str, state: Path, now_ms: int, profile: str = "pL") -> dict:
    view = {"name": name, "state": str(state), "run": _json(state / "run.json", {}),
            "status": _json(state / "dashboard" / "status.json", {}),
            "heartbeat": _json(state / "heartbeat.json", {}), "alerts": _tail(state / "alerts.jsonl", 50),
            "mirror": _json(state / "mirror.json")}
    hb = view["heartbeat"].get("updated_ms")
    view["heartbeat_age_s"] = None if hb is None else round((now_ms - hb) / 1000, 1)
    # One status.json holds every profile of a run; show only this profile's engine.
    status = view["status"]
    if status.get("engines"):
        status = view["status"] = {**status, "engines": [e for e in status["engines"] if e.get("profile") == profile]
                                   or status["engines"][:1]}
    view["profile"] = profile
    from .shadow import summary as shadow_summary
    try: view["shadow"] = shadow_summary(state / f"shadow_{profile}.db")
    except (OSError, sqlite3.Error, ValueError) as error:
        view["shadow"] = {"available": False, "error": str(error)}
    db_path = state / f"journal_{profile}.db"
    if not db_path.exists():
        return view
    db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        since = now_ms - 3600_000
        view["scans_last_hour"] = db.execute(
            "SELECT COUNT(DISTINCT time_ms) FROM events WHERE kind='SCAN' AND time_ms>=?", (since,)).fetchone()[0]
        view["errors_last_hour"] = db.execute(
            "SELECT COUNT(*) FROM events WHERE kind='ERROR' AND time_ms>=?", (since,)).fetchone()[0]
        # Fail-closed stale-quote rejections (quiet USDC books) are expected and reported apart from real errors.
        # Equity curve: last observation per 15 minutes over the run (cheap, bounded to ~300 points).
        # Incremental: only observations newer than the last poll (index range), merged into 15-min buckets.
        curve = _EQUITY.setdefault(str(db_path), {"after": -1, "buckets": {}})
        for t, e in db.execute(
                "SELECT MAX(time_ms) AS t, json_extract(payload,'$.equity') FROM events WHERE kind='OBSERVATION' "
                "AND time_ms>? GROUP BY time_ms/900000", (curve["after"],)):
            curve["buckets"][t // 900000] = [t, float(e)]
            curve["after"] = max(curve["after"], t)
        view["equity_history"] = [curve["buckets"][b] for b in sorted(curve["buckets"])[-300:]]
        view["errors_last_15m"], view["stale_quotes_last_15m"] = db.execute(
            "SELECT COALESCE(SUM(payload NOT LIKE '%invalid or stale quote%'),0), "
            "COALESCE(SUM(payload LIKE '%invalid or stale quote%'),0) FROM events WHERE kind='ERROR' AND time_ms>=?",
            (now_ms - 900_000,)).fetchone()
        row = db.execute("SELECT time_ms,payload FROM events WHERE kind='SELF_CHECK' ORDER BY id DESC LIMIT 1").fetchone()
        view["self_check"] = None if row is None else {"time_ms": row[0], "checks": json.loads(row[1])}
        if view["self_check"]:
            live_scorer_checks(view["self_check"], now_ms)
        view["counts"] = dict(db.execute(
            "SELECT kind, COUNT(*) FROM events WHERE kind IN ('SIGNAL','APPROVED','REJECTED_RISK','FILLED','CLOSED') "
            "GROUP BY kind").fetchall())
        reasons = {}
        for (payload,) in db.execute("SELECT payload FROM events WHERE kind='REJECTED_RISK'"):
            r = json.loads(payload).get("reason", "?"); reasons[r] = reasons.get(r, 0) + 1
        view["reject_reasons"] = dict(sorted(reasons.items(), key=lambda kv: -kv[1]))
        signals = {}
        for (payload,) in db.execute("SELECT payload FROM events WHERE kind='SIGNAL' ORDER BY id DESC LIMIT 300"):
            p = json.loads(payload); signals[p.get("signal_id")] = p
        # Fill/close events carry the order id; map it back to the order's signal.
        by_order = {}
        for (payload,) in db.execute("SELECT payload FROM orders"):
            o = json.loads(payload); by_order[o["id"]] = o["signal"]
        feed = []
        marks = ",".join("?" * len(DECISIONS))
        for t, kind, payload in db.execute(
                f"SELECT time_ms,kind,payload FROM events WHERE kind IN ({marks}) ORDER BY id DESC LIMIT 40", DECISIONS):
            p = json.loads(payload)
            s = signals.get(p.get("signal_id")) or by_order.get(p.get("id")) or (p if kind == "SIGNAL" else {})
            feed.append({"time_ms": t, "kind": kind, "strategy": s.get("strategy_id"), "symbol": s.get("symbol"),
                         "side": s.get("side"), "reason": p.get("reason") or p.get("verdict") or "",
                         "detail": p.get("detail") or p.get("binding_constraint") or _price(p) or ""})
        view["feed"] = feed
        closed = db.execute("SELECT COALESCE(SUM(CAST(pnl AS REAL)),0), COALESCE(SUM(CAST(fee AS REAL)),0) FROM fills").fetchone()
        view["realized"] = {"pnl": closed[0], "fees": closed[1]}
        from .risk import Demo200Policy, LivePolicy, policy_slot
        slot_policy = Demo200Policy if profile == "pD" else LivePolicy
        used = {}
        for pos in view["status"].get("engines", [{}])[0].get("open_positions", []):
            slot = policy_slot(slot_policy, pos.get("strategy_id") or ""); used[slot] = used.get(slot, 0) + 1
        view["slots"] = {slot: {"used": used.get(slot, 0), "max": n} for slot, n in slot_policy.slots}
        view["fills"] = [{"time_ms": t, "kind": k, "order_id": o, "quantity": q, "price": pr, "fee": f, "pnl": pn}
                         for t, k, o, q, pr, f, pn in db.execute(
                             "SELECT time_ms,kind,order_id,quantity,price,fee,pnl FROM fills ORDER BY time_ms DESC LIMIT 30")]
    finally:
        db.close()
    return view


def sentibot_view(path: Path | None, now_ms: int) -> dict | None:
    if path is None:
        return None
    rows = _tail(path, 1)
    if not rows:
        return {"available": False}
    r = rows[-1]
    return {"available": True, "time": r.get("time"), "equity": r.get("equity"),
            "composite": {k: v.get("composite") for k, v in (r.get("signals") or {}).items() if isinstance(v, dict)},
            "news": {k: v for k, v in ((r.get("sentiment_raw") or {}).get("news") or {}).items() if not k.startswith("_n")},
            "sources": (r.get("sentiment") or {}).get("status"), "counts": (r.get("sentiment") or {}).get("counts")}


# Combined $200 demo run (2026-11-02); the earlier paper/demo/hl-copy services are retired.
SERVICES = (("demo", "com.quantos.v4.demo200"), ("sentibot spot", "com.sentibot.spot"),
            ("sentibot perp", "com.sentibot.perp"), ("dashboard", "com.quantos.dashboard"),
            ("5m scorer", "com.quantos.ml5m.scorer"), ("1h-ML", "com.quantos.ml1h"))  # HL copy dropped 2026-11-03 (no edge: short beta only)
HLCOPY = Path.home() / "Library/Application Support/hl_copy/state"
HLREVERSE = Path.home() / "Library/Application Support/hl_copy_reverse/state"
ML1H = Path.home() / "Library/Application Support/ml1h/app"
# Scorer signal files the bot reads (same defaults as its launchd env) and the bot's self-check age limits.
SCORER_SIGNALS = {
    "ml1h_pattern": (os.environ.get("QUANTOS_ML1H_SIGNALS") or str(ML1H / "signals.json"), 2 * 3600),
    "ml5m_alts": (os.environ.get("QUANTOS_ML5M_SIGNALS")
                  or str(Path.home() / "Library/Application Support/QuantOS/ml5m-scoring/signals.json"), 20 * 60)}


def live_scorer_checks(self_check: dict, now_ms: int, scorers: dict | None = None) -> dict:
    """11-13: the bot re-runs its self-check only hourly (24h once healthy), so a scorer that was late at startup showed
    'Check failed' for up to an hour after it recovered. Replace the scorer entries with a live signal-file age check;
    the bot's last verdict stays as c["bot"] (with its time) for reference."""
    checks = self_check.get("checks") or {}
    for sid, (path, max_age) in (SCORER_SIGNALS if scorers is None else scorers).items():
        if sid not in checks:
            continue
        try:
            age_s = max(0.0, now_ms / 1000 - os.stat(path).st_mtime)
        except OSError:
            age_s = None
        ok = age_s is not None and age_s < max_age
        state = "Missing" if age_s is None else "Scanning" if ok else "Stale"
        detail = ("signals file missing" if age_s is None else
                  f"signals updated {int(age_s // 60)} min ago" if ok else f"signals {int(age_s // 60)} min old (limit {max_age // 60})")
        checks[sid] = {"ok": ok, "level": "ok" if ok else "critical", "state": state, "detail": detail, "live": True,
                       "bot": checks[sid], "bot_time_ms": self_check.get("time_ms")}
    return self_check


_TTL, _TTL_LOCK, _EQUITY = {}, threading.RLock(), {}  # re-entrant: "state" builds "services"


def _cached(key, ttl_s, build):
    """11-11: every 2s poll re-scanned the 144 MB journal and forked launchctl per service (~40% CPU).
    Share one result per key for ttl_s across polls and tabs."""
    with _TTL_LOCK:
        hit = _TTL.get(key)
        if hit and time.monotonic() - hit[0] < ttl_s:
            return hit[1]
        value = build()
        _TTL[key] = (time.monotonic(), value)
        return value


def services_view() -> list:
    """launchd state per service (read-only `launchctl print`)."""
    out = []
    try:
        uid = subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip()
    except OSError:  # no `id` (non-POSIX): report every service as unknown
        uid = None
    for name, label in SERVICES:
        running = None
        if uid is not None:
            try:
                r = subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"], capture_output=True, text=True)
                running = "state = running" in r.stdout
            except OSError:  # launchctl only exists on macOS
                running = None
        out.append({"name": name, "label": label, "running": running})
    return out


def hlcopy_view(now_ms: int, HLCOPY: Path = HLCOPY) -> dict | None:
    """Wallet-copy paper books (0/30/60 s delay); only numbers/tickers, already validated by the recorder."""
    if not HLCOPY.exists():
        return None
    books = {}
    for delay in ("0", "30", "60"):
        b = _json(HLCOPY / f"books_{delay}.json", {})
        opened = [{"symbol": o.get("sym"), "side": "LONG" if o.get("side") == 1 else "SHORT", "qty": o.get("qty"),
                   "entry": o.get("entry"), "stop": o.get("stop"), "t_entry": o.get("t_entry")}
                  for o in (b.get("open") or {}).values()]
        books[delay] = {"equity": _json(HLCOPY / "summary.json", {}).get("books", {}).get(delay, {}).get("equity"),
                        "start": b.get("equity0"), "realized": b.get("realized"), "open": opened,
                        "closed": len(b.get("closed") or []), "skips": b.get("skip_counts")}
    hb = _json(HLCOPY / "heartbeat.json", {})
    return {"books": books, "heartbeat_utc": hb.get("utc"), "errors": hb.get("errors"),
            "start_ts": _json(HLCOPY / "summary.json", {}).get("start_ts")}


def ml1h_view(now_ms: int) -> dict | None:
    """1h pattern-ML scorer: latest scores and recent signals (orders go through QuantOS pD as ml1h_pattern)."""
    s = _json(ML1H / "last_scores.json", None)
    if s is None:
        return None
    return {"ts_ms": s.get("ts_ms"), "thr": s.get("thr"), "scores": s.get("scores"),
            "signals": [{k: r.get(k) for k in ("generated_ms", "symbol", "side", "score")} for r in _tail(ML1H / "signal_log.jsonl", 10)]}


def snapshot(runs, sentibot) -> dict:
    """The /api/state contract: {time_ms, services, runs: [run_view...], hlcopy, sentibot: {perp, spot}}."""
    now = int(time.time() * 1000)
    bots = None if sentibot is None else {
        "perp": sentibot_view(sentibot / "paper_perp_signals.jsonl", now),
        "spot": sentibot_view(sentibot / "paper_signals.jsonl", now)}
    return {"time_ms": now, "services": _cached("services", 30, services_view), "runs": [run_view(n, s, now, *p) for n, s, *p in runs],
            "hlcopy": None, "hlreverse": None, "ml1h": ml1h_view(now),
            "sentibot": bots}


def strategy_view(runs) -> dict:
    """/api/strategies: per run, fills enriched with symbol/strategy and per-strategy fill count, realised PnL, fees."""
    out = {}
    for name, state, *p in runs:
        profile = p[0] if p else "pL"
        db_path = state / f"journal_{profile}.db"
        if not db_path.exists():
            continue
        db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            orders = {}
            for (payload,) in db.execute("SELECT payload FROM orders"):
                o = json.loads(payload); sig = o.get("signal") or {}
                orders[o["id"]] = {"symbol": sig.get("symbol"), "strategy": sig.get("strategy_id"), "side": sig.get("side")}
            per, fills = {}, []
            for t, k, oid, q, pr, f, pn in db.execute(
                    "SELECT time_ms,kind,order_id,quantity,price,fee,pnl FROM fills ORDER BY time_ms DESC"):
                o = orders.get(oid, {}); sid = o.get("strategy") or "?"
                agg = per.setdefault(sid, {"fills": 0, "entries": 0, "pnl": 0.0, "fees": 0.0})
                agg["fills"] += 1; agg["entries"] += k == "ENTRY"
                agg["pnl"] += float(pn or 0); agg["fees"] += float(f or 0)
                fills.append({"time_ms": t, "kind": k, "order_id": oid, "quantity": q, "price": pr, "fee": f, "pnl": pn, **o})
            out[name] = {"profile": profile, "strategies": per, "fills": fills[:50]}
        finally:
            db.close()
    return out


_CACHE, _LOCK = {}, threading.Lock()


def _cached_get(url: str, ttl: float):
    """GET url as JSON, cached for ttl seconds; serves the stale copy if a refresh fails."""
    with _LOCK:
        hit = _CACHE.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        with urlopen(Request(url, headers={"User-Agent": "quantos-webdash"}), timeout=6) as r:
            data = json.loads(r.read())
    except (OSError, ValueError):
        if hit:
            return hit[1]
        raise
    with _LOCK:
        _CACHE[url] = (time.time(), data)
    return data


def klines(query: str):
    q = parse_qs(query)
    symbol = (q.get("symbol") or ["BTCUSDC"])[0].upper()
    interval = (q.get("interval") or ["15m"])[0]
    limit = min(max(int((q.get("limit") or ["96"])[0]), 1), 500)
    if not re.fullmatch(r"[A-Z0-9]{2,20}", symbol) or interval not in ("1m", "5m", "15m", "1h", "4h", "1d"):
        raise ValueError("bad symbol or interval")
    # Candles from the execution venue's own futures market (Binance Demo when --exchange demo), so the chart, the live
    # mark and the positions all come from one market; spot candles drifted from the demo futures price.
    base = getattr(_EXCHANGE.get("client"), "base", None) or "https://fapi.binance.com"
    return _cached_get(f"{base}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}", 15)


_EXCHANGE = {}  # {"client": SignedClient, "mode": str}; set by --exchange


def exchange_view():
    """/api/exchange: the execution account's own positions (Binance mark price and unrealised PnL), so the
    dashboard shows what Binance shows instead of the paper engine's mainnet-priced marks. Signed GETs only,
    cached 2s (weight 10 per refresh)."""
    client = _EXCHANGE.get("client")
    if client is None:
        return {"enabled": False}
    def fetch():
        positions = [{"symbol": r["symbol"], "amount": float(r["positionAmt"]), "entry": float(r["entryPrice"]),
                      "mark": float(r["markPrice"]), "upnl": float(r["unRealizedProfit"])}
                     for r in client.position_risk() if float(r.get("positionAmt") or 0) != 0]
        acct = client.account()
        # Open orders as Binance holds them (limit TPs and conditional stops), for every bot on the account.
        # Best effort: a failure here leaves orders out (null) instead of hiding positions and wallet.
        try:
            orders = [{"symbol": o["symbol"], "side": o.get("side"), "type": o.get("type"), "price": float(o.get("price") or 0),
                       "qty": float(o.get("origQty") or 0), "id": o.get("clientOrderId")} for o in client.open_orders()]
            orders += [{"symbol": o.get("symbol"), "side": o.get("side"), "type": o.get("orderType"),
                        "price": float(o.get("triggerPrice") or 0), "qty": float(o.get("quantity") or 0),
                        "id": o.get("clientAlgoId")} for o in client.open_algo_orders()]
        except Exception:
            orders = None
        # QuantOS trades USDC margin, alt-ML trades USDT perps: report both, plus the sum of every position's uPnL.
        assets = {x["asset"]: {"wallet": float(x["walletBalance"]), "upnl": float(x.get("unrealizedProfit") or 0),
                               "available": float(x.get("availableBalance") or 0),
                               "margin": float(x.get("marginBalance") or 0)}
                  for x in acct.get("assets", []) if x.get("asset") in ("USDC", "USDT")}
        main = assets.get("USDC") or {"wallet": float(acct.get("totalWalletBalance") or 0),
                                      "upnl": float(acct.get("totalUnrealizedProfit") or 0)}
        return {"enabled": True, "mode": _EXCHANGE["mode"], "time_ms": int(time.time() * 1000), "positions": positions,
                "wallet": main["wallet"], "upnl": main["upnl"], "assets": assets, "orders": orders,
                # The page streams marks from here (e.g. demo-fstream.binance.com), the venue's own mark feed.
                "ws_host": getattr(client, "ws_host", None),
                # Where the money is: QUANTOS_TESTNET_HOST=demo makes a "testnet" client point at Binance Demo.
                "venue": "demo" if "demo" in str(getattr(client, "base", "")) else _EXCHANGE["mode"],
                "total_upnl": round(sum(p["upnl"] for p in positions), 8)}
    with _LOCK:
        hit = _CACHE.get("exchange")
    if hit and time.time() - hit[0] < 2:
        return hit[1]
    try:
        data = fetch()
    except Exception as error:  # never break the page; show the last good copy marked stale
        if hit: return {**hit[1], "stale": True, "error": type(error).__name__}
        return {"enabled": True, "error": f"{type(error).__name__}: {error}"[:200]}
    with _LOCK:
        _CACHE["exchange"] = (time.time(), data)
    return data


def fng():
    return _cached_get("https://api.alternative.me/fng/?limit=8", 600)


def request_close(runs, body: dict) -> tuple[int, dict]:
    """Queue a manual close for the bot: validate the position is open in that run, then write
    STATE/close_requests/<random>.json atomically. Never calls the exchange."""
    name, order_id = body.get("run"), body.get("order_id")
    if not isinstance(name, str) or not isinstance(order_id, str) or not 0 < len(order_id) <= 128:
        return 400, {"error": "run and order_id required"}
    match = [r for r in runs if r[0] == name]
    if not match:
        return 404, {"error": "unknown run"}
    _, state, *rest = match[0]
    profile = rest[0] if rest else "pL"
    status = _json(state / "dashboard" / "status.json", {})
    engines = [e for e in status.get("engines", []) if e.get("profile") == profile]
    open_pos = {p.get("id"): p for e in engines for p in e.get("open_positions", [])}
    if order_id not in open_pos:
        return 409, {"error": "position is not open (already closed or closing)"}
    if open_pos[order_id].get("state") == "CLOSING":
        return 409, {"error": "position is already closing"}
    folder = state / "close_requests"
    folder.mkdir(exist_ok=True)
    tmp = folder / f".{secrets.token_hex(8)}.tmp"
    tmp.write_text(json.dumps({"profile": profile, "order_id": order_id, "requested_ms": int(time.time() * 1000)}))
    os.replace(tmp, folder / f"{int(time.time() * 1000)}-{secrets.token_hex(4)}.json")
    return 202, {"ok": True, "symbol": open_pos[order_id].get("symbol"), "order_id": order_id}


PAGE = (Path(__file__).with_name("webdash.html"))
# Private strategy notes, kept on the Mac and out of git; the page shows them in each strategy's detail panel.
LIBRARY = Path("~/Library/Application Support/QuantOS/strategy_library.json").expanduser()


def library_view(path: Path | None = None) -> dict:
    """/api/library: the local strategy library JSON ({"strategies": {id: {...}}}), or available=false."""
    data = _json(path or LIBRARY, None)
    if not isinstance(data, dict) or not isinstance(data.get("strategies"), dict):
        return {"available": False, "path": str(path or LIBRARY), "strategies": {}}
    return {"available": True, **data}


def serve(runs, sentibot, port=8787, library=None):
    token = secrets.token_urlsafe(24)  # embedded in the page; another site cannot read it, so cannot POST
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, data):
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json"); self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(body)

        def do_POST(self):
            if urlparse(self.path).path != "/api/close":
                self.send_error(404); return
            origin = self.headers.get("Origin")
            if self.headers.get("Host") not in allowed or (origin and urlparse(origin).netloc not in allowed):
                self._send(403, {"error": "forbidden origin"}); return
            if not secrets.compare_digest(self.headers.get("X-QuantOS-Token", ""), token):
                self._send(403, {"error": "bad token; reload the page"}); return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(min(length, 4096)) or b"{}")
                if not isinstance(body, dict): raise ValueError
            except ValueError:
                self._send(400, {"error": "bad json"}); return
            self._send(*request_close(runs, body))

        def do_GET(self):
            if self.path.startswith("/api/state"):
                body = _cached("state", 5, lambda: json.dumps(snapshot(runs, sentibot), default=str).encode())
                kind = "application/json"
            elif self.path.startswith("/api/exchange"):
                body, kind = json.dumps(exchange_view(), default=str).encode(), "application/json"
            elif self.path.startswith("/api/news"):
                from .news import news
                body, kind = json.dumps(news(), default=str).encode(), "application/json"
            elif urlparse(self.path).path == "/api/library":
                body, kind = json.dumps(_cached("library", 10, lambda: library_view(library)), default=str).encode(), "application/json"
            elif self.path.startswith(("/api/klines", "/api/fng", "/api/strategies")):
                route = urlparse(self.path)
                try:
                    data = (klines(route.query) if route.path == "/api/klines" else fng() if route.path == "/api/fng"
                            else strategy_view(runs))
                except (OSError, ValueError) as e:
                    self.send_error(502 if isinstance(e, OSError) else 400, str(e)); return
                body, kind = json.dumps(data, default=str).encode(), "application/json"
            elif self.path in ("/", "/index.html"):
                body, kind = PAGE.read_bytes().replace(b"__QOS_TOKEN__", token.encode()), "text/html; charset=utf-8"
            elif urlparse(self.path).path.startswith("/static/aura-zenith/"):
                # Dashboard-owned assets only; no arbitrary paths or state access.
                asset = urlparse(self.path).path.removeprefix("/static/aura-zenith/")
                kinds = {".svg": "image/svg+xml", ".webp": "image/webp", ".ico": "image/x-icon", ".woff2": "font/woff2"}
                path = PAGE.parent / "static" / "aura-zenith" / asset
                if not asset or Path(asset).name != asset or path.suffix not in kinds or not path.is_file() or path.is_symlink():
                    self.send_error(404); return
                body, kind = path.read_bytes(), kinds[path.suffix]
            else:
                self.send_error(404); return
            self.send_response(200)
            self.send_header("Content-Type", kind); self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(body)

        def log_message(self, *args):
            pass

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main(argv=None):
    p = argparse.ArgumentParser(prog="quantos-webdash")
    p.add_argument("--run", action="append", required=True, help="NAME=STATE_DIR")
    p.add_argument("--sentibot", type=Path, help="sentibot app folder")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--library", type=Path, help="local strategy library JSON (default: %s)" % LIBRARY)
    p.add_argument("--exchange", help="MODE=KEY_FILE (e.g. demo=~/.quantos/demo.env): show the account's live "
                                      "Binance positions; read-only signed GETs")
    a = p.parse_args(argv)
    if a.exchange:
        from ..live.client import SignedClient
        from ..live.keys import load_keys
        mode, _, key_file = a.exchange.partition("=")
        if mode == "demo":  # Demo Trading is the testnet mode on the demo host
            os.environ["QUANTOS_TESTNET_HOST"], mode = "demo", "testnet"
        _EXCHANGE.update(mode=mode, client=SignedClient(mode, load_keys(str(Path(key_file).expanduser()))))
    runs = []
    for r in a.run:  # NAME=STATE or NAME=STATE#PROFILE
        name, rest = r.split("=", 1)
        path, _, profile = rest.partition("#")
        runs.append((name, Path(path).expanduser(), *([profile] if profile else [])))
    serve(runs, a.sentibot.expanduser() if a.sentibot else None, a.port, a.library.expanduser() if a.library else None)


if __name__ == "__main__":
    main()
