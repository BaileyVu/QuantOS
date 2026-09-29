"""Offline-only secret loader and authenticated origin boundary checks."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from quantos.infrastructure.configuration.testnet_credentials import load_testnet_credentials, TestnetCredentialError
from quantos.infrastructure.binance.spot_rest import SpotRest, SpotError, Credentials


class TestnetSecurityTests(TestCase):
    def test_strict_file_loader(self):
        good = "BINANCE_TESTNET_API_KEY=fixture-key\nBINANCE_TESTNET_API_SECRET=fixture-secret\n"
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/"testnet.env"
            with patch.dict(os.environ, {"QUANTOS_TESTNET_ENV_FILE": str(path)}):
                with self.assertRaises(TestnetCredentialError): load_testnet_credentials()
                for raw in ("", good.splitlines()[0], good+"BINANCE_TESTNET_API_KEY=duplicate", good.replace("fixture-key", ""), good+"BINANCE_API_KEY=forbidden", good+"malformed", good.replace("fixture-secret", "two words")):
                    path.write_text(raw)
                    with self.assertRaises(TestnetCredentialError) as error: load_testnet_credentials()
                    self.assertNotIn("fixture", str(error.exception))
                path.write_text(good)
                credentials = load_testnet_credentials()
                self.assertEqual(credentials.key, "fixture-key")
                self.assertNotIn("fixture", repr(credentials))
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(TestnetCredentialError): load_testnet_credentials()

    def test_all_authenticated_non_testnet_routes_block_before_transport(self):
        calls = []
        for testnet, base in ((False, "https://api.binance.com"), (True, "https://api.binance.com"), (True, "https://testnet.binance.vision.evil.example")):
            port = SpotRest(Credentials("fixture-key", "fixture-secret"), testnet=testnet, http=lambda *args: calls.append(args))
            port.base = base
            for method, path in (("GET", "/api/v3/account"), ("GET", "/api/v3/order"), ("POST", "/api/v3/order")):
                with self.assertRaises(SpotError): port._call(method, path, signed=True)
        self.assertEqual(calls, [])
