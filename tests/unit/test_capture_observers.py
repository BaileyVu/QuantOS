import json
from pathlib import Path
import tempfile
import unittest

from quantos.live.client import SignedClient, AmbiguousOrderState
from quantos.paper.sentiment import SentimentShadow
from quantos.paper.strategies.ml1h_pattern import Ml1hPatternStrategy
from quantos.paper.stream import UserStream
from tests.unit.live_fakes import Fake, KEYS
from quantos.paper.engine import PaperEngine
from quantos.paper.journal import Journal
from quantos.paper.risk import Policy
from quantos.paper.types import D, Trade
from tests.unit.test_thin_book import T, INS, signal, quote


class ObserverTests(unittest.TestCase):
    def test_client_captures_unsigned_response_without_changing_it(self):
        rows = []
        def observe(row):
            rows.append(json.loads(json.dumps(row)))
            row['response']['orderId'] = -1
        c = SignedClient('testnet', KEYS, transport=Fake(lambda *a: (200, {'orderId': 7})), observer=observe)
        self.assertEqual(c.new_order({'symbol': 'BTCUSDC', 'side': 'BUY'})['orderId'], 7)
        self.assertEqual(len(rows), 1)  # public clock sync was not captured
        self.assertEqual(rows[0]['response']['orderId'], 7)
        self.assertNotIn('signature', json.dumps(rows))
        self.assertNotIn('api_key', json.dumps(rows))

    def test_failed_observer_does_not_make_accepted_order_ambiguous(self):
        def fail(row): raise OSError('full disk')
        c = SignedClient('testnet', KEYS, transport=Fake(lambda *a: (200, {'orderId': 7})), observer=fail)
        self.assertEqual(c.new_order({'symbol': 'BTCUSDC'})['orderId'], 7)
        self.assertEqual(c.observation_error, 'OSError')

    def test_transport_failure_keeps_ambiguity_and_no_error_secrets(self):
        rows = []
        def fail(*args): raise TimeoutError('signed URL must not enter capture')
        c = SignedClient('testnet', KEYS, transport=fail, observer=rows.append)
        c._offset = 0
        with self.assertRaises(AmbiguousOrderState): c.new_order({'symbol': 'BTCUSDC'})
        self.assertEqual(rows[0]['error'], 'TimeoutError')
        self.assertNotIn('signed URL', json.dumps(rows))

    def test_listen_key_never_captured(self):
        rows = []
        c = SignedClient('testnet', KEYS, transport=Fake(lambda *a: (200, {'listenKey': 'private'})), observer=rows.append)
        self.assertEqual(c.listen_key(), 'private')
        self.assertEqual(rows, [])

    def test_input_hook_uses_same_consumed_version(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)/'signals.json'
            before = b'{"bar_close_ms":1,"signals":[]}'
            p.write_bytes(before)
            captured = []
            def observe(source, raw, now):
                captured.append(raw)
                p.write_text('{"bar_close_ms":2,"signals":[]}')
            scorer = Ml1hPatternStrategy(str(p), input_observer=observe)
            self.assertEqual(scorer._signals()['bar_close_ms'], 1)
            self.assertEqual(captured, [before])
            p.write_text('{"time":"2026-11-01T00:00:00+00:00"}\n')
            seen = []
            SentimentShadow(p, lambda *x: seen.append(x)).read(0)
            self.assertEqual(seen[0][1], b'{"time":"2026-11-01T00:00:00+00:00"}')

    def test_user_stream_observer_cannot_mutate_consumed_event(self):
        class Client: ws_host = 'example'
        def observe(msg, now): msg['o']['t'] = -1
        stream = UserStream(Client(), input_observer=observe)
        stream.handle(json.dumps({'e':'ORDER_TRADE_UPDATE', 'o':{'t': 19}}))
        self.assertEqual(stream.drain()[0]['o']['t'], 19)

    def test_model_fill_is_preserved_before_real_book_correction(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = Journal(Path(folder)/'journal.db', 'p1')
            observed = []
            try:
                engine = PaperEngine(journal, Policy(), fill_observer=observed.append)
                engine.recover(T); engine.connection(True, T)
                oid = engine.propose(signal(), INS, quote(), T)
                engine.trade(Trade('AAVEUSDC', 1, T+1000, D('99.99'), D(1000), True), T+1000)
                self.assertEqual(len(observed), 1)
                original = observed[0]['model_price']
                engine.book_real_fills(oid, T+2000, entry=(D('100.07'), None))
                self.assertEqual(observed[0]['model_price'], original)
                self.assertNotEqual(original, '100.07')
                self.assertIsNone(engine.observation_error)
            finally: journal.close()


if __name__ == '__main__': unittest.main()
