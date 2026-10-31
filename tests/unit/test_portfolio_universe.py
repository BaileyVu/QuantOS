from __future__ import annotations

from datetime import date, datetime, timezone
import json
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from quantos.paper.research.acquire import MAJORS
from quantos.paper.research.data import ArchiveRef
from quantos.paper.research.universe import build_universe, list_usdt_symbols, verify_first_row_history


class Response:
    def __init__(self, body): self.body = body
    def read(self, size=-1): return self.body if size < 0 else self.body[:size]
    def close(self): pass


def dec_archive(symbol: str, quote_volume: int) -> bytes:
    text = []
    start = 1733011200000  # 2024-12-01 00:00 UTC
    for i in range(744):
        opened = start + i * 3_600_000
        text.append(f"{opened},10,12,9,11,3,{opened+3599999},{quote_volume},2,1,10,0\n")
    raw = "".join(text).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{symbol}-1h-2024-12.csv", raw)
    return out.getvalue()


class PortfolioUniverseTests(unittest.TestCase):
    def test_lists_historical_usdt_archive_prefixes_not_current_symbols(self):
        class FakeStore:
            timeout = 1
            def __init__(self): self.opener = lambda request, timeout: Response(
                b"<ListBucketResult xmlns='http://s3.amazonaws.com/doc/2006-03-01/'>"
                b"<CommonPrefixes><Prefix>data/futures/um/monthly/klines/OLDUSDT/</Prefix></CommonPrefixes>"
                b"<CommonPrefixes><Prefix>data/futures/um/monthly/klines/AIUSDT/</Prefix></CommonPrefixes>"
                b"<CommonPrefixes><Prefix>data/futures/um/monthly/klines/COINUSDC/</Prefix></CommonPrefixes>"
                b"<IsTruncated>false</IsTruncated></ListBucketResult>")
        self.assertEqual(list_usdt_symbols(FakeStore()), ("AIUSDT", "OLDUSDT"))

    def test_rank_is_deterministic_and_downloads_december_only(self):
        nonmajors = tuple(f"COIN{i:02d}USDT" for i in range(20))
        symbols = (*MAJORS, *nonmajors)
        class FakeStore:
            def __init__(self, root): self.root = Path(root); self.fetches = []
            def list_archives(self, dataset, symbol, interval, start=None, end=None, *, granularity):
                return tuple(ArchiveRef(dataset, symbol, "2024-" + m, "https://example/", interval)
                             for m in ("09", "10", "11", "12"))
            def fetch(self, dataset, symbol, period, interval):
                self.fetches.append(period)
                path = self.root / f"{symbol}.zip"
                volume = (5 if symbol in MAJORS else
                          1 if symbol == "COIN00USDT" else
                          20 if symbol in {"COIN01USDT", "COIN02USDT"} else int(symbol[4:6]))
                path.write_bytes(dec_archive(symbol, volume))
                return path

        with tempfile.TemporaryDirectory() as tmp:
            store = FakeStore(tmp)
            manifest = Path(tmp) / "universe.json"
            result = build_universe(manifest, store=store, symbols=symbols,
                                    reuse_manifest=Path(tmp) / "none.json", workers=4)
            first_fetch_count = len(store.fetches)
            resumed = build_universe(manifest, store=store, symbols=symbols, workers=4)
        self.assertEqual(result["status"], "pending_first_row_verification")
        self.assertEqual(resumed["selected_symbols"], result["selected_symbols"])
        self.assertEqual(len(store.fetches), first_fetch_count)
        self.assertEqual(result["selected_symbols"][:9], list(MAJORS))
        # Equal quote-volume tie is broken lexically.
        self.assertEqual(result["selected_nonmajors"][0]["symbol"], "COIN01USDT")
        self.assertEqual(set(store.fetches), {"2024-12"})
        self.assertTrue(all(row["december_rows"] == 744 for row in result["selected_nonmajors"]))

    def test_first_row_history_verification_uses_actual_first_open_time(self):
        class FirstStore:
            def __init__(self, root, opened): self.root = Path(root); self.opened = opened; self.calls = 0
            def fetch(self, dataset, symbol, period, interval):
                self.calls += 1
                path = self.root / f"{symbol}-{period}.zip"
                raw = (f"{self.opened},10,12,9,11,3,{self.opened+3599999},31,2,1,10,0\n").encode()
                stream = io.BytesIO()
                with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive_file:
                    archive_file.writestr(f"{symbol}-1h-{period}.csv", raw)
                path.write_bytes(stream.getvalue())
                return path

        cutoff = int(datetime(2024, 10, 3, tzinfo=timezone.utc).timestamp() * 1000)
        eligible_open = int(datetime(2024, 9, 30, 23, tzinfo=timezone.utc).timestamp() * 1000)
        ineligible_open = int(datetime(2024, 10, 4, tzinfo=timezone.utc).timestamp() * 1000)
        for opened, expected in ((eligible_open, "verified_90d"),
                                 (ineligible_open, "incomplete_first_row_eligibility")):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "universe.json"
                first_period = "2024-09" if opened <= cutoff else "2024-10"
                path.write_text(json.dumps({"status": "pending_first_row_verification",
                                            "selected_symbols": ["BTCUSDT"],
                                            "market_metadata": {"BTCUSDT": {"first_partition": first_period}},
                                            "ranked_markets": [{"symbol": "BTCUSDT"}]}))
                store = FirstStore(tmp, opened)
                result = verify_first_row_history(path, store=store, workers=1)
                resumed = verify_first_row_history(path, store=store, workers=1)
                self.assertEqual(result["status"], expected, result["first_row_checks"])
                self.assertEqual(resumed["status"], expected)
                self.assertEqual(store.calls, 1)


if __name__ == "__main__":
    unittest.main()
