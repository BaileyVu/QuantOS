"""Local append-only paper ledger with exclusive compare-and-append writes."""
import os
from pathlib import Path

from quantos.domain.execution.core import ExecutionError
from quantos.domain.execution.evidence import canonical, identity


class JsonlExecutionLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.head_path = self.path.with_name(self.path.name+'.head')

    @staticmethod
    def _head(records: tuple[str, ...]) -> bytes:
        return (canonical({'count': len(records), 'tail': identity(records[-1])})+'\n').encode('ascii')

    def read(self) -> tuple[str, ...]:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            if self.head_path.exists():
                raise ExecutionError('execution ledger missing but durable head exists')
            return ()
        if not raw or not raw.endswith(b'\n'):
            raise ExecutionError('empty or torn execution ledger')
        try:
            lines = raw.decode('ascii').split('\n')[:-1]
        except UnicodeError as error:
            raise ExecutionError('corrupt execution ledger encoding') from error
        if any(not line or '\r' in line for line in lines):
            raise ExecutionError('invalid execution ledger framing')
        records = tuple(lines)
        try:
            head = self.head_path.read_bytes()
        except FileNotFoundError as error:
            raise ExecutionError('missing durable ledger head; reconciliation required') from error
        if head != self._head(records):
            raise ExecutionError('durable ledger head mismatch; reconciliation required')
        return records

    def append(self, record: str, expected: tuple[str, ...]) -> None:
        if not record or '\n' in record or '\r' in record:
            raise ExecutionError('invalid ledger record framing')
        payload = (record+'\n').encode('ascii')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_name(self.path.name+'.lock')
        head_temp = self.head_path.with_name(self.head_path.name+'.tmp')
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as error:
            raise ExecutionError('ledger writer lock exists; reconcile before retry') from error
        try:
            if self.read() != expected:
                raise ExecutionError('ledger changed before append')
            with self.path.open('ab' if expected else 'xb') as stream:
                if stream.write(payload) != len(payload):
                    raise ExecutionError('incomplete ledger append')
                stream.flush()
                os.fsync(stream.fileno())
            # Publish a separate high-water mark to detect valid-prefix truncation
            # after restart. A crash between the two writes fails closed on read;
            # neither incomplete evidence nor an orphan tail is silently repaired.
            with head_temp.open('xb') as stream:
                head = self._head(expected+(record,))
                if stream.write(head) != len(head):
                    raise ExecutionError('incomplete ledger head write')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(head_temp, self.head_path)
        finally:
            os.close(descriptor)
            lock.unlink()
