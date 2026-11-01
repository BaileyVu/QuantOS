from decimal import Decimal, Context, ROUND_HALF_EVEN, FloatOperation
from datetime import datetime, timezone

ZERO = Decimal('0')
ONE = Decimal('1')
CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
CONTEXT.traps[FloatOperation] = True


def D(value):
    if isinstance(value, (float, bool)):
        raise ValueError('money must be decimal strings, never floats/bools')
    x = Decimal(value)
    if not x.is_finite():
        raise ValueError('nonfinite decimal')
    return x


def utc_ms(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.utcoffset() is None or dt.utcoffset().total_seconds() != 0:
        raise ValueError('timestamp must be UTC')
    return int(dt.timestamp() * 1000)


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
