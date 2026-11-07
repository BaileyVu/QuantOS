"""Public Exa/Jina adapter adapted from Agent Reach v1.6.1 (MIT, commit 422478a).

The vendored copy keeps the public URL guard, Exa search, Jina article reader,
and text sanitizer. It adds DNS-pinned public connections and redirect checks.
It omits browser automation, cookies, login scraping, YouTube subprocesses,
installers, and update checks. See THIRD_PARTY_NOTICES.md for attribution.
"""
from __future__ import annotations

import ipaddress
import http.client
import json
import re
import socket
import sys
import urllib.request
from urllib.parse import urljoin, urlsplit

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
MAX_BYTES = 5 * 1024 * 1024
EXA_MCP = "https://mcp.exa.ai/mcp"

# ── URL guard (verbatim logic from agent_reach/utils/url.py) ──
_BLOCKED_HOSTS = {"home.arpa", "instance-data", "internal", "ip6-localhost", "ip6-loopback",
                  "lan", "local", "localdomain", "localhost", "metadata.google.internal"}
_BLOCKED_SUFFIXES = (".home.arpa", ".internal", ".lan", ".local", ".localdomain", ".localhost")


def _literal_ip(host: str):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        return ipaddress.IPv4Address(socket.inet_aton(host))
    except OSError:
        return None


def _public_host(host: str) -> tuple[str, ...]:
    """Return resolved public IPs, rejecting mixed public/private DNS answers."""
    try:
        answers = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("URL host did not resolve to a public address") from exc
    if not answers:
        raise ValueError("URL host did not resolve to a public address")
    addresses = []
    for answer in answers:
        try:
            address = ipaddress.ip_address(answer[4][0].split("%", 1)[0])
        except ValueError:
            raise ValueError("URL host returned an invalid address") from None
        if not address.is_global:
            raise ValueError("only public HTTP(S) URLs are allowed")
        if str(address) not in addresses:
            addresses.append(str(address))
    return tuple(addresses)


def normalize_public_http_url(url: str, *, resolve: bool = True) -> str:
    c = str(url or "").strip()
    if not c or "\\" in c or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in c):
        raise ValueError("only public HTTP(S) URLs are allowed")
    if "://" not in c:
        c = "https://" + c
    p = urlsplit(c)
    host = (p.hostname or "").lower().rstrip(".")
    _ = p.port  # raises on bad port
    ip = _literal_ip(host)
    if (p.scheme.lower() not in {"http", "https"} or not host or p.username or p.password
            or "%" in host or host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_SUFFIXES)
            or ("." not in host and ip is None) or (ip is not None and not ip.is_global)):
        raise ValueError("only public HTTP(S) URLs are allowed")
    if resolve and ip is None:
        _public_host(host)
    return p.geturl()


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = normalize_public_http_url(urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, target)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, *args, connect_addresses, **kwargs):
        self._connect_addresses = connect_addresses
        super().__init__(host, *args, **kwargs)

    def connect(self):
        if self._tunnel_host:
            raise ValueError("HTTP proxy tunnels are disabled")
        last_error = None
        for address in self._connect_addresses:
            try:
                self.sock = socket.create_connection((address, self.port), self.timeout, self.source_address)
                return
            except OSError as exc:
                last_error = exc
        if last_error:
            raise last_error
        raise OSError("no public connection address")


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, *args, connect_addresses, **kwargs):
        self._connect_addresses = connect_addresses
        super().__init__(host, *args, **kwargs)

    def connect(self):
        if self._tunnel_host:
            raise ValueError("HTTPS proxy tunnels are disabled")
        last_error = None
        for address in self._connect_addresses:
            try:
                raw = socket.create_connection((address, self.port), self.timeout, self.source_address)
                server_name = urlsplit("//" + self.host).hostname
                self.sock = self._context.wrap_socket(raw, server_hostname=server_name)
                return
            except OSError as exc:
                last_error = exc
        if last_error:
            raise last_error
        raise OSError("no public connection address")


class _PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        host = urlsplit(req.full_url).hostname
        addresses = _public_host(host)
        return self.do_open(lambda name, **kw: _PinnedHTTPConnection(
            name, connect_addresses=addresses, **kw), req)


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        host = urlsplit(req.full_url).hostname
        addresses = _public_host(host)
        return self.do_open(lambda name, **kw: _PinnedHTTPSConnection(
            name, connect_addresses=addresses, **kw), req,
            context=self._context)


def _safe_open(req: urllib.request.Request, timeout: float):
    normalize_public_http_url(req.full_url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _PublicHTTPHandler(),
                                         _PublicHTTPSHandler(), _PublicRedirectHandler())
    return opener.open(req, timeout=timeout)


# ── prompt-injection hygiene (new, not in Agent Reach) ──
_INJECT_RE = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts?)"
    r"|you are now|system prompt|<\s*/?\s*(system|assistant|instructions?)\s*>",
    re.IGNORECASE)


def sanitize_text(text: str, limit: int = 4000) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f​-‏‪-‮⁦-⁩]", "", text or "")
    return _INJECT_RE.sub("[filtered]", text)[:limit]


def _post(url: str, body: bytes, headers: dict, timeout: float) -> bytes:
    url = normalize_public_http_url(url)
    req = urllib.request.Request(url, data=body, headers={"User-Agent": UA, **headers})
    with _safe_open(req, timeout=timeout) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("response too large")
    return data


# ── Exa hosted MCP (free, no key; rate limited) ──
def _exa_call(tool: str, args: dict, timeout: float = 30) -> str:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": args}}).encode()
    raw = _post(EXA_MCP, body, {"Content-Type": "application/json",
                                "Accept": "application/json, text/event-stream"}, timeout).decode("utf-8", "replace")
    payloads = [ln[5:].strip() for ln in raw.splitlines() if ln.startswith("data:")] or [raw]
    msg = json.loads(payloads[-1])
    if "error" in msg:
        raise RuntimeError(f"exa: {str(msg['error'])[:300]}")
    res = msg.get("result") or {}
    text = "\n".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
    if res.get("isError"):
        raise RuntimeError(f"exa: {text[:300]}")
    return text


_HDR = re.compile(r"^(Title|URL|Published|Author):\s*(.*)$")


def _parse_exa(text: str) -> list[dict]:
    items = []
    for block in re.split(r"\n(?=Title: )", "\n" + text.strip()):
        block = block.strip()
        if not block.startswith("Title:"):
            continue
        fields, lines, i = {}, block.splitlines(), 0
        while i < len(lines) and (m := _HDR.match(lines[i])):
            fields[m.group(1).lower()] = m.group(2).strip()
            i += 1
        body = "\n".join(ln for ln in lines[i:] if ln.strip() not in ("Highlights:", "Text:"))
        na = lambda v: None if v in (None, "", "N/A") else v  # noqa: E731
        items.append({"source": "exa", "title": sanitize_text(na(fields.get("title")) or "", 300),
                      "url": na(fields.get("url")), "published": na(fields.get("published")),
                      "text": sanitize_text(body)})
    return items


def exa_search(query: str, limit: int = 10) -> list[dict]:
    """e.g. exa_search('SOL news last 24h') -> [{title,url,published,text}]"""
    return _parse_exa(_exa_call("web_search_exa", {"query": query, "objective": query, "numResults": limit}))[:limit]


# ── Jina reader: full article text for an RSS link ──
def _is_antibot(body: bytes) -> bool:
    s = body[:4096].decode("utf-8", "ignore").casefold()
    return ("title: just a moment..." in s or "## performing security verification" in s
            or "title: attention required! | cloudflare" in s)


def jina_read(url: str, timeout: float = 30) -> dict:
    url = normalize_public_http_url(url)
    req = urllib.request.Request(f"https://r.jina.ai/{url}", headers={"User-Agent": UA, "Accept": "text/plain"})
    with _safe_open(req, timeout=timeout) as r:
        body = r.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES or _is_antibot(body):
        raise RuntimeError("jina: too large or anti-bot page")
    text = body.decode("utf-8", "replace")
    title = re.search(r"^Title:\s*(.*)$", text, re.M)
    pub = re.search(r"^Published Time:\s*(.*)$", text, re.M)
    content = text.split("Markdown Content:", 1)[-1]
    return {"source": "jina", "url": url, "title": sanitize_text(title.group(1) if title else "", 300),
            "published": pub.group(1).strip() if pub else None, "text": sanitize_text(content)}


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "bitcoin price news today"
    for it in exa_search(q, 5):
        print(it["published"], it["title"], it["url"])
