import base64
import gzip
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from quantos.paper.capture import CaptureWriter
from quantos.paper.engine import PaperEngine
from quantos.paper.journal import Journal
from quantos.paper.risk import Policy
from quantos.paper.runtime import Runtime
from quantos.paper.types import D, Trade
from tests.unit.test_thin_book import T, INS, signal, quote


def records(state):
    out = []
    for path in sorted((state/'capture').glob('*.jsonl.gz')):
        with gzip.open(path, 'rt') as stream: out.extend(json.loads(line) for line in stream)
    return out


class CaptureTests(unittest.TestCase):
    def test_readonly_stream_mode_keeps_separate_order_client(self):
        with tempfile.TemporaryDirectory() as folder:
            calls=[]
            def factory(mode): calls.append(mode); return mode
            runtime=Runtime(Path(folder),portfolio='live50',execution='dry-run',
                            observe_testnet_stream=True,client_factory=factory,stream=False)
            self.assertEqual(runtime._client(),'dry-run')
            self.assertEqual(runtime._client('testnet'),'testnet')
            self.assertEqual(runtime.execution,'dry-run')
            self.assertEqual(calls,['dry-run','testnet'])
            with self.assertRaises(ValueError):
                Runtime(Path(folder),portfolio='live50',execution='testnet',observe_testnet_stream=True)

    def test_exact_bytes_and_concurrent_append(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder); writer = CaptureWriter(state)
            raw = b'{"signals":[]}\n'
            writer.input_bytes('ml1h', raw, T)
            threads = [threading.Thread(target=writer.record, args=('test', {'i':i}, T)) for i in range(20)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            rows = records(state)
            self.assertEqual(len(rows), 21)
            self.assertEqual(base64.b64decode(rows[0]['payload']['bytes']), raw)
            self.assertEqual({row['payload']['i'] for row in rows[1:]}, set(range(20)))
            self.assertEqual(writer.errors, 0)

    def test_writer_failure_is_counted_and_never_raised(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder); (state/'capture').write_text('occupied')
            writer = CaptureWriter(state)
            writer.record('test', {}, T)
            self.assertEqual(writer.errors, 1)
            self.assertEqual((state/'capture').read_text(), 'occupied')

    def test_fill_buffer_flushes_only_committed_transactions(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder); writer = CaptureWriter(state)
            journal = Journal(state/'journal.db', 'p1')
            try:
                engine = PaperEngine(journal, Policy(), fill_observer=writer.buffer_fill)
                engine.recover(T); engine.connection(True, T)
                engine.propose(signal(), INS, quote(), T)
                with self.assertRaises(RuntimeError):
                    with journal.transaction():
                        engine.trade(Trade('AAVEUSDC',1,T+1000,D('99.99'),D(1000),True),T+1000)
                        writer.flush_fills([engine])
                        self.assertEqual(records(state), [])
                        raise RuntimeError('rollback')
                writer.flush_fills([engine])
                self.assertEqual(records(state), [])
                engine.trade(Trade('AAVEUSDC',2,T+2000,D('99.99'),D(1000),True),T+2000)
                self.assertEqual(records(state), [])
                writer.flush_fills([engine])
                self.assertEqual([r['kind'] for r in records(state)], ['model_fill'])
            finally: journal.close()

    def test_snapshot_is_consistent_and_leaves_journal_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder); writer=CaptureWriter(state); journal=Journal(state/'journal.db','p1')
            try:
                with journal.transaction(): journal.event(T,'TEST',{'a':1})
                writer.snapshot(journal,'start',T)
                record=records(state)[0]
                snapshot=state/'capture'/record['payload']['file']
                db=sqlite3.connect(snapshot)
                self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],1); db.close()
                self.assertEqual(journal.db.execute('SELECT count(*) FROM events').fetchone()[0],1)
                self.assertEqual(writer.errors,0)
            finally: journal.close()

    def test_market_pruning_does_not_touch_capture(self):
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder); (state/'market').mkdir(); (state/'capture').mkdir()
            old=state/'capture'/'2000-01-01.jsonl.gz'; old.write_bytes(b'retained')
            for day in range(1,8): (state/'market'/f'2000-01-0{day}.jsonl.gz').write_bytes(b'old')
            runtime=Runtime(state,notify=False,stream=False)
            runtime._record_day='2000-01-07'
            runtime.record('feed',[],T)
            self.assertEqual(old.read_bytes(),b'retained')
            self.assertLess(len(list((state/'market').glob('*'))),8)


if __name__ == '__main__': unittest.main()
