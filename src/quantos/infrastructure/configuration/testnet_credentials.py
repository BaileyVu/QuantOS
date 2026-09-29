"""Strict external Testnet credential loading; never expose source text."""
import os
from pathlib import Path

from quantos.infrastructure.binance.spot_rest import Credentials


class TestnetCredentialError(ValueError):
    pass


def load_testnet_credentials():
    path = os.environ.get("QUANTOS_TESTNET_ENV_FILE")
    if not path:
        raise TestnetCredentialError("Testnet credential file variable missing")
    expected = {"BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"}
    try:
        raw = Path(path).read_text(encoding="utf-8-sig")
        values = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not separator or key not in expected or key in values:
                raise ValueError()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in (chr(34), chr(39)):
                value = value[1:-1]
            if not value or any(c.isspace() for c in value):
                raise ValueError()
            values[key] = value
        if set(values) != expected:
            raise ValueError()
        return Credentials(values["BINANCE_TESTNET_API_KEY"], values["BINANCE_TESTNET_API_SECRET"])
    except Exception:
        raise TestnetCredentialError("Testnet credential file unavailable or invalid") from None
