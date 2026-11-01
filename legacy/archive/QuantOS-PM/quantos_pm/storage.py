"""Append-only audit storage outside all six business boundaries."""
import hashlib
import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from .config import ROOT, local_path


def canonical(value):
    def encode(x):
        if isinstance(x, Decimal):
            return str(x)
        raise TypeError(type(x).__name__)
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=encode)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def source_digest():
    h = hashlib.sha256()
    for p in sorted((ROOT/'quantos_pm').rglob('*.py')):
        h.update(str(p.relative_to(ROOT)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


class Journal:
    def __init__(self, directory, manifest):
        self.directory = local_path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        import fcntl
        lockpath = local_path(self.directory/'writer.lock')
        self.lock = lockpath.open('a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise ValueError('run has an active QuantOS-PM writer')
        self.db = sqlite3.connect(local_path(self.directory/'journal.sqlite'))
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS journal (seq INTEGER PRIMARY KEY, kind TEXT NOT NULL,
            body TEXT NOT NULL, previous TEXT NOT NULL, hash TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS no_update BEFORE UPDATE ON journal
            BEGIN SELECT RAISE(ABORT, 'append only'); END;
        CREATE TRIGGER IF NOT EXISTS no_delete BEFORE DELETE ON journal
            BEGIN SELECT RAISE(ABORT, 'append only'); END;
        ''')
        self.cursor, self.previous = 0, '0'*64
        try:
            self.append('manifest', manifest)
        except Exception:
            self.close()
            raise

    def append(self, kind, value):
        body = canonical(value)
        hash_ = digest((self.previous+kind+body).encode())
        expected = (kind, body, self.previous, hash_)
        row = self.db.execute('SELECT kind,body,previous,hash FROM journal WHERE seq=?', (self.cursor,)).fetchone()
        if row is not None and row != expected:
            raise ValueError('journal replay mismatch; refusing silent repair')
        if row is None:
            with self.db:
                self.db.execute('INSERT INTO journal VALUES (?,?,?,?,?)', (self.cursor, *expected))
        self.previous = hash_
        self.cursor += 1

    def close(self):
        self.db.close()
        self.lock.close()


def read_report(run_id):
    import re
    if not re.fullmatch(r'[0-9a-f]{20}', run_id):
        raise ValueError('invalid run id')
    path = local_path(f'artifacts/runs/{run_id}/journal.sqlite', must_exist=True)
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    previous, report = '0'*64, None
    try:
        for expected_seq, (seq, kind, body, prev, h) in enumerate(db.execute('SELECT * FROM journal ORDER BY seq')):
            if seq != expected_seq or prev != previous or h != digest((prev+kind+body).encode()):
                raise ValueError('journal integrity failure')
            previous = h
            if kind == 'report':
                report = json.loads(body)
    finally:
        db.close()
    if report is None:
        raise ValueError('run incomplete; replay original command to resume')
    return report


def capture_parquet(path, rows):
    import pyarrow as pa
    import pyarrow.parquet as pq
    path = local_path(path)
    # Exclusive final creation. All source payloads retained; money remains strings.
    with path.open('xb') as output:
        pq.write_table(pa.Table.from_pylist([{'payload': canonical(r)} for r in rows]), output,
                       compression='zstd')
    path.chmod(0o444)
    return digest(path.read_bytes())
