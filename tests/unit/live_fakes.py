import json
from urllib.parse import urlparse, parse_qs

from quantos.live.keys import Keys
from quantos.paper.types import D, Instrument

KEYS = Keys("AK", "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j")
INST = Instrument("SOLUSDC", "USDC", "TRADING", tick=D("0.01"), step=D("0.01"), min_qty=D("0.01"),
                  max_qty=D("1000"), min_notional=D("5"), market_step=D("0.01"), market_min_qty=D("0.01"),
                  market_max_qty=D("1000"))


class Fake:
    """Records requests; handler(method, path, params) -> (status, obj) or raises."""
    def __init__(self, handler):
        self.handler, self.requests = handler, []
    def __call__(self, request, timeout):
        u = urlparse(request.full_url)
        params = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.requests.append((request.get_method(), u.path, params, request))
        if u.path == "/fapi/v1/time": return 200, json.dumps({"serverTime": 1000}).encode(), {}
        status, obj = self.handler(request.get_method(), u.path, params)
        return status, json.dumps(obj).encode(), {}
    def calls(self, method, path):
        return [r for r in self.requests if r[0] == method and r[1] == path]
