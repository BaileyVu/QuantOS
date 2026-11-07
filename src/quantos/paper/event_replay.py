"""Offline replay of schema-2 runtime recordings through runtime.dispatch and PaperEngine."""
from __future__ import annotations

import gzip
import json
from pathlib import Path
import sqlite3

from .adaptive import AdaptiveSizer
from .engine import PaperEngine
from .journal import Journal
from .report import atomic_json, write_reports
from .risk import Demo200Policy, LivePolicy, Policy
from .runtime import dispatch
from .strategies import library, live_library
from .types import D, Funding, Quote, Trade
from .replay import decode, snapshot


PROFILE_DECISIONS = {
    "SCAN", "CANDIDATE", "NO_SIGNAL", "CONFLICT", "SENTIMENT_SHADOW",
    "SIGNAL", "APPROVED", "REJECTED_RISK", "DUPLICATE", "ADAPTIVE_SIZING",
    "CREATED", "SUBMITTING", "ACKNOWLEDGED", "OPEN", "FILLED", "PARTIAL",
    "PROTECTED", "PROTECTION_REJECTED", "CANCELED", "EXPIRED", "CLOSING", "CLOSED",
    "STOP_UPDATED", "LOSS_BREAKER", "REALISED_RISK_CHECKED", "REALISED_RISK_EXCEEDED",
    "RECONCILED", "RECONCILIATION_FAILED", "CONNECTED", "DISCONNECTED", "FUNDING",
}


class RecordedSentiment:
    """Return the exact verdict captured alongside a runtime scan."""
    def __init__(self, verdicts):
        if not isinstance(verdicts, dict):
            raise ValueError("recorded sentiment verdicts must be an object")
        self.verdicts = verdicts

    def judge(self, signal, now_ms):
        try:
            return self.verdicts[signal.signal_id]
        except KeyError:
            raise ValueError(f"recorded scan lacks sentiment verdict for {signal.signal_id}") from None


def _sources(paths):
    for path in paths:
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rt") as stream:
            for line_number, line in enumerate(stream, 1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    raise ValueError(f"invalid JSON at {path}:{line_number}") from None
                if not isinstance(row, dict) or row.get("schema") != 2:
                    raise ValueError(f"event replay requires schema-2 runtime records ({path}:{line_number})")
                yield row


def _decode_payload(kind, payload):
    if kind == "feed":
        if not isinstance(payload, list): raise ValueError("feed payload must be a list")
        constructors = {"quote": Quote, "trade": Trade, "funding": Funding}
        out = []
        for item in payload:
            if not isinstance(item, list) or len(item) != 2 or item[0] not in constructors:
                raise ValueError("invalid feed event")
            out.append((item[0], decode(constructors[item[0]], item[1])))
        return out
    if kind == "scan":
        if not isinstance(payload, list): raise ValueError("scan payload must be a list")
        return [snapshot(value) for value in payload]
    if kind == "funding":
        return [decode(Funding, value) for value in payload]
    return payload


def _profile(profile, state):
    if profile == "legacy":
        profiles = ("p1", "p5", "pA")
        journals = [Journal(state / f"journal_{name}.db", name, "replay") for name in profiles]
        engines = [PaperEngine(journals[0], Policy(risk_fraction=D(".01"))),
                   PaperEngine(journals[1], Policy(risk_fraction=D(".05"))),
                   PaperEngine(journals[2], Policy(risk_fraction=D(".05")),
                               adaptive=AdaptiveSizer(journals[2], journals[0]))]
        return profiles, journals, engines, library()
    configured = {
        "pD": (Demo200Policy, "200", live_library),
        "pL": (LivePolicy, "50", live_library),
    }
    if profile not in configured:
        raise ValueError("profile must be legacy, pD, or pL")
    policy, balance, strategy_factory = configured[profile]
    journal = Journal(state / f"journal_{profile}.db", profile, "replay", initial_balance=balance)
    return (profile,), [journal], [PaperEngine(journal, policy())], strategy_factory()


def _decision_rows(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        identity = db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()
        if identity is None:
            raise ValueError(f"journal has no identity: {path}")
        profile = json.loads(identity[0]).get("profile")
        rows = [(r[0], r[1], json.dumps(json.loads(r[2]), sort_keys=True, separators=(",", ":")))
                for r in db.execute("SELECT time_ms,kind,payload FROM events ORDER BY id")
                if r[1] in PROFILE_DECISIONS]
        return profile, rows
    finally:
        db.close()


def compare_journal(actual_path, expected_path):
    """Compare decision audit rows only; ops/health cadence is intentionally excluded."""
    try:
        actual_profile, actual = _decision_rows(actual_path)
        expected_profile, expected = _decision_rows(expected_path)
    except sqlite3.Error:
        raise ValueError("unable to read the expected decision journal") from None
    if actual_profile != expected_profile:
        return {"exact": False, "reason": "journal profile mismatch", "actual_profile": actual_profile,
                "expected_profile": expected_profile, "actual_decisions": len(actual),
                "expected_decisions": len(expected), "mismatch_count": None,
                "first_mismatch_index": None}
    mismatches = [i for i in range(max(len(actual), len(expected)))
                  if i >= len(actual) or i >= len(expected) or actual[i] != expected[i]]
    return {"exact": not mismatches, "actual_profile": actual_profile, "expected_profile": expected_profile,
            "actual_decisions": len(actual),
            "expected_decisions": len(expected), "mismatch_count": len(mismatches),
            "first_mismatch_index": mismatches[0] if mismatches else None}


def event_replay(paths, state: Path, *, profile="pD", strategy="all", expected_journal=None):
    """Replay recorded runtime events using the same strategy, dispatch, risk and engine path.

    Inputs must be schema-2 recordings produced by Runtime.record. They carry closed-candle
    snapshots, marks, top-of-book depth, trades, funding and announcement observations.
    Engine's live paper LIMIT/STOP fill and depth-impact code remains the sole execution model.
    """
    state = Path(state)
    if state.exists(): raise ValueError("event replay output must be a new directory")
    sources = list(paths)
    if not sources: raise ValueError("at least one runtime recording is required")
    state.mkdir(parents=True)
    profiles, journals, engines, strategies = _profile(profile, state)
    selected = tuple(s for s in strategies if strategy == "all" or s.strategy_id == strategy)
    if not selected:
        for j in journals: j.close()
        raise ValueError(f"unknown strategy for {profile}: {strategy}")

    first = previous = None
    event_count, scans, complete_sentiment = 0, 0, True
    try:
        for row in _sources(sources):
            now, kind, raw = row.get("received_ms"), row.get("kind"), row.get("payload")
            if isinstance(now, bool) or not isinstance(now, int):
                raise ValueError("runtime event received_ms must be an integer")
            if previous is not None and now < previous:
                raise ValueError("event replay input out of chronological order")
            if kind not in {"restart", "feed", "scan", "funding", "diagnostic", "tick", "disconnected"}:
                raise ValueError(f"unsupported runtime event kind: {kind}")
            if first is None: first = now
            previous, event_count = now, event_count + 1

            if kind == "restart":
                for engine in engines:
                    engine.connected = False
                    engine.recover(now)
                continue

            payload = _decode_payload(kind, raw)
            sentiment = None
            sentiment_fail_closed = False
            if kind == "scan":
                scans += 1
                context = row.get("context")
                if (not isinstance(context, dict) or not isinstance(context.get("sentiment_enabled"), bool)
                        or not isinstance(context.get("sentiment_fail_closed"), bool)):
                    complete_sentiment = False
                else:
                    sentiment_fail_closed = context["sentiment_fail_closed"]
                if isinstance(context, dict) and context.get("sentiment_enabled") is True:
                    sentiment = RecordedSentiment(context.get("sentiment_verdicts"))
            dispatch(engines, kind, payload, now, strategies=selected,
                     active_seconds=row.get("active_seconds", 0), sentiment=sentiment,
                     sentiment_fail_closed=sentiment_fail_closed)
        if first is None: raise ValueError("empty runtime recordings")
        run = {"started_ms": first, "finished_ms": previous, "evidence": "replay",
               "status": "replay_finished", "strategy": strategy,
               "profiles": list(profiles), "event_count": event_count, "scan_count": scans,
               "sentiment_trace_complete": complete_sentiment,
               "warning": "Recorded decisions are replay evidence, not independent performance validation."}
        if expected_journal is not None:
            if not complete_sentiment:
                run["journal_parity"] = {"exact": False, "reason": "input lacks recorded sentiment context",
                                          "actual_decisions": None, "expected_decisions": None,
                                          "mismatch_count": None, "first_mismatch_index": None}
            else:
                run["journal_parity"] = compare_journal(state / f"journal_{profiles[0]}.db", expected_journal)
        atomic_json(state / "run.json", run)
    finally:
        for j in journals: j.close()
    result = write_reports(state, run, final=True)
    result["run"] = run
    return result
