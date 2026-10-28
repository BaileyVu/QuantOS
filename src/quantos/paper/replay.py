"""Causal replay of recorded V3 inputs through the same strategies/risk/execution."""
from dataclasses import fields
import gzip
import json
from pathlib import Path

from .adaptive import AdaptiveSizer
from .engine import PaperEngine
from .journal import Journal
from .report import atomic_json, write_reports
from .risk import Policy, ResearchPolicy
from .runtime import arbitrate, dispatch
from .strategies import library
from .strategies.manifest import manifest_for
from .types import D, Candle, DelistEvent, Funding, Instrument, OpenInterestValue, Quote, Snapshot, Trade


def decode(cls, data):
    text = {"symbol", "margin_asset", "status", "base_asset"}
    integers = {"time_ms", "received_ms", "mark_time_ms", "open_ms", "close_ms", "trade_id"}
    return cls(**{f.name: (data[f.name] if f.name in text | integers | {"buyer_maker"}
                          else D(data[f.name])) for f in fields(cls) if f.name in data})


def snapshot(data):
    return Snapshot(data["at_ms"], decode(Instrument, data["instrument"]), decode(Quote, data["quote"]),
                    tuple(decode(Candle, c) for c in data["candles_1m"]),
                    tuple(decode(Candle, c) for c in data["candles_5m"]),
                    **{key: tuple(decode(Candle, c) for c in data.get(key, ()))
                       for key in ("candles_1h", "candles_4h", "candles_1d", "candles_2h")},
                    delist_events=tuple(DelistEvent(**e) for e in data.get("delist_events", ())),
                    recent_trade_ms=data.get("recent_trade_ms"),
                    recent_trade_volume=D(data.get("recent_trade_volume", "0")),
                    entry_eligible=data.get("entry_eligible", True),
                    oi_values=tuple(OpenInterestValue(v["time_ms"], v["available_ms"], D(v["value"]))
                                    for v in data.get("oi_values", ())),
                    quarterly_return_cutoff=(D(data["quarterly_return_cutoff"])
                                             if data.get("quarterly_return_cutoff") is not None else None),
                    calibration_end_ms=data.get("calibration_end_ms"))


def replay(paths, state: Path, strategy="all", *, research=False):
    if state.exists(): raise ValueError("replay output must be a new directory")
    state.mkdir(parents=True)
    selected = manifest_for(strategy)
    strategies = [s for s in library(research) if strategy == "all" or s.strategy_id == strategy
                  or (selected and manifest_for(s.strategy_id) == selected)]
    if not strategies: raise ValueError("unknown strategy")
    profiles = ("pR",) if research else ("p1", "p5", "pA")
    journals = [Journal(state/f"journal_{p}.db", p, "replay") for p in profiles]
    engines = ([PaperEngine(journals[0], ResearchPolicy())] if research else
               [PaperEngine(j, Policy(risk_fraction=D(f)), adaptive=AdaptiveSizer(j, journals[0]) if p == "pA" else None)
                for j, p, f in zip(journals, profiles, (".01", ".05", ".05"))])
    first = previous = None
    try:
        for path in paths:
            opener = gzip.open if str(path).endswith(".gz") else open
            with opener(path, "rt") as source:
                for line in source:
                    row = json.loads(line); now = row["received_ms"]
                    if previous is not None and now < previous: raise ValueError("replay input out of chronological order")
                    if first is None:
                        first = now
                        for e in engines: e.recover(now)
                    if row.get("schema") == 2:
                        kind, payload = row["kind"], row["payload"]
                        if kind == "feed":
                            payload = [(event, decode({"quote": Quote, "trade": Trade, "funding": Funding}[event], value))
                                       for event, value in payload]
                        elif kind == "scan": payload = [snapshot(value) for value in payload]
                        elif kind == "funding": payload = [decode(Funding, value) for value in payload]
                        dispatch(engines, kind, payload, now, strategies=strategies,
                                 active_seconds=row.get("active_seconds", 0))
                        previous = now
                        continue
                    if previous is not None and now-previous > 5000:
                        for e in engines: e.connection(False, now, "REPLAY_GAP")
                    previous = now
                    for kind, value in row["records"]:
                        if kind in {"quote", "trade", "funding"}:
                            data = decode({"quote": Quote, "trade": Trade, "funding": Funding}[kind], value)
                            for e in engines:
                                if kind == "quote": e.connection(True, now)
                                getattr(e, kind)(data, now)
                        elif kind == "snapshots":
                            dispatch(engines, "scan", [snapshot(s) for s in value], now, strategies=strategies)
                        elif kind == "disconnected":
                            for e in engines: e.connection(False, now, str(value))
                        else: raise ValueError(f"unknown replay record: {kind}")
                    for e in engines: e.tick(now)
        if first is None: raise ValueError("empty replay")
        run = {"started_ms": first, "finished_ms": previous, "evidence": "replay",
               "status": "replay_finished", "strategy": strategy,
               "profiles": list(profiles),
               "warning": "Open end-of-input positions remain open; no synthetic closing quotes added."}
        atomic_json(state/"run.json", run)
    finally:
        for j in journals: j.close()
    return write_reports(state, run)
