"""Recorded exchange observations applied only to an offline paper engine."""
from .types import D

METHODS = {"book_real_fills", "check_realised_risk", "void_entry", "close_external"}


class TraceEngine:
    """Delegate mirror calls unchanged and capture committed paper-book effects."""
    def __init__(self, engine):
        object.__setattr__(self, "engine", engine)
        object.__setattr__(self, "events", [])

    def __getattr__(self, name):
        method = getattr(self.engine, name)
        if name not in METHODS: return method
        def call(*args, **kwargs):
            result = method(*args, **kwargs)
            self.events.append({"method": name, "args": args, "kwargs": kwargs})
            return result
        return call

    def __setattr__(self, name, value):
        setattr(self.engine, name, value)


def apply_effects(engine, payload):
    """No order clients: only explicitly allowlisted paper methods can be replayed."""
    if engine.journal.get("identity")["evidence"] != "replay":
        raise ValueError("recorded exchange effects are replay-only")
    with engine.journal.transaction():
        for event in payload["effects"]:
            name, args, kwargs = event["method"], list(event["args"]), dict(event["kwargs"])
            if name not in METHODS: raise ValueError("unknown exchange replay effect")
            if name == "book_real_fills":
                kwargs = {k: tuple(D(v) for v in values) if values is not None else None
                          for k, values in kwargs.items()}
            elif name == "check_realised_risk":
                args[2:] = [D(v) if v is not None else None for v in args[2:]]
                kwargs = {k: D(v) if v is not None else None for k, v in kwargs.items()}
            getattr(engine, name)(*args, **kwargs)
        engine.exchange_hold = bool(payload["exchange_hold"])
