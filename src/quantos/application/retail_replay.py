"""One causal command processor shared by public paper and deterministic replay."""
from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json

from quantos.application.retail_trader import RetailPaperEngine
from quantos.domain.market_data.hft import HftBookError


def stable_json(value):
    def encode(item):
        if is_dataclass(item):
            return asdict(item)
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, datetime):
            return item.isoformat()
        if hasattr(item, "value"):
            return item.value
        raise TypeError(type(item).__name__)
    return json.dumps(value, default=encode, sort_keys=True, separators=(",", ":"), allow_nan=False)


class RetailCausalProcessor:
    def __init__(self, engine: RetailPaperEngine, *, counterfactual_clock: bool = False):
        self.counterfactual_clock = counterfactual_clock
        self.engine = engine
        self.digest = "0" * 64
        self.count = 0
        self.last_processing_ns = -1
        self.source_counts: dict[str, int] = {}
        self.last_result = None
        self.last_decisions = []
        self.first_event_at = self.last_event_at = None

    def apply(self, kind: str, payload: dict, *, clock_ns=None):
        engine = self.engine
        result = None
        if kind == "bootstrap":
            engine.bootstrap(payload["snapshot"], tuple(payload["buffered"]))
        elif kind == "event":
            processing_ns = payload["processing_ns"]
            if self.first_event_at is None:
                self.first_event_at = payload["event"].received_at
            self.last_event_at = payload["event"].received_at
            if processing_ns < self.last_processing_ns:
                raise ValueError("capture processing clock regressed")
            self.last_processing_ns = processing_ns
            if engine.evaluation.reliability:
                from decimal import Decimal
                received_ns = payload["event"].received_monotonic_ns
                queue_ms = (Decimal(processing_ns - received_ns) / Decimal(1_000_000)
                            if received_ns is not None else Decimal(-1))
                engine.evaluation.reliability.observe_timing(queue_ms, payload["feed_latency_ms"])
            # Live samples both feature-processing and actual submission clocks.
            # Replay consumes those exact samples, never a clock reconstructed
            # from feed receipt time. Legacy captures without samples use their
            # explicit processing clock and remain conservatively replayable.
            samples = [] if clock_ns is not None else list(payload.get("clock_samples_ns", []))
            cursor = 0
            last_clock = processing_ns
            def sampled_clock():
                nonlocal cursor, last_clock
                if clock_ns is not None:
                    value = clock_ns()
                    samples.append(value)
                elif samples and self.counterfactual_clock:
                    value = max(samples)
                elif samples:
                    if cursor >= len(samples):
                        raise ValueError("replay clock samples exhausted")
                    value = samples[cursor]
                else:
                    value = processing_ns
                cursor += 1
                if type(value) is not int or value < last_clock:
                    raise ValueError("submission clock precedes processing or regresses")
                last_clock = value
                return value
            engine.processing_clock_ns = sampled_clock
            try:
                result = engine.on_event(payload["event"], feed_latency_ms=payload["feed_latency_ms"], clock_healthy=payload["clock_healthy"])
            except HftBookError as error:
                result = {"event": "invalid_book", "reason": str(error)}
            self.last_processing_ns = last_clock
            if clock_ns is not None:
                payload["clock_samples_ns"] = samples
            elif samples and not self.counterfactual_clock and cursor != len(samples):
                raise ValueError("replay clock samples not fully consumed")
        elif kind == "external":
            event = payload["event"]
            key = event.provider + ":" + event.kind
            self.source_counts[key] = self.source_counts.get(key, 0) + 1
            # Preserve normalized identity: external information never changes target.
            if event.kind == "liquidation" and event.instrument != engine.rules.symbol:
                reason = "wrong_target_instrument"
                engine.intelligence.observations.pop(event.kind, None)
            elif event.kind == "leader_quote" and event.market != "BTC/USD:spot":
                reason = "wrong_leader_market"
                engine.intelligence.observations.pop(event.kind, None)
            else:
                reason = engine.intelligence.ingest(event)
            result = {"event": "intelligence_ingested", "provider": event.provider, "reason": reason}
        elif kind == "source_failure":
            engine.intelligence.observations.pop(payload["source_kind"], None)
            key = payload["provider"] + ":failure"
            self.source_counts[key] = self.source_counts.get(key, 0) + 1
            result = {"event": "source_failure", **payload}
        elif kind == "disarm":
            engine.disarm(payload["reason"], payload["timestamp"])
        elif kind == "resume":
            engine.disarmed = False
        elif kind == "tick":
            if payload["kill"]:
                engine.account.risk_off = True
                engine.disarm("operator_kill", payload["timestamp"])
            elif engine.book.valid:
                try:
                    engine.book.require_fresh_monotonic(payload["processing_ns"], engine.config.maximum_staleness)
                except HftBookError:
                    engine.disarm("stalled_depth_pipeline", payload["timestamp"])
        else:
            raise ValueError("unknown causal retail command")
        decisions = engine.decision_records
        self.last_decisions = decisions
        engine.decision_records = []
        self.digest = sha256((self.digest + stable_json({"kind": kind, "result": result, "decisions": decisions})).encode()).hexdigest()
        self.count += 1
        self.last_result = result
        return result

    def report(self):
        engine = self.engine
        account = engine.account
        economic = engine.evaluation.report(account)
        if engine.reclaim:
            from quantos.domain.evaluation.reclaim import trade_statistics
            duration = max(0, (self.last_event_at - self.first_event_at).total_seconds()) if self.first_event_at else 0
            economic["qualification"] = trade_statistics(account, duration)
            economic["qualification"]["unique_confirmed_signals"] = sorted(engine.confirmed_signal_ids)
        ledger = {"balance": account.balance, "position": account.position,
                  "pending": account.pending, "fills": account.fills,
                  "lifecycle": account.lifecycle, "funding": account.funding_ledger}
        return {**economic, "source_counts": self.source_counts,
                "ingestion_reasons": engine.intelligence.reasons,
                "decision_digest": self.digest, "commands": self.count,
                "ledger_digest": sha256(stable_json(ledger).encode()).hexdigest()}


def replay_commands(processor, records):
    expected = None
    for row in records:
        if expected is not None or row["kind"] == "aborted":
            raise ValueError("incomplete or nonterminal capture")
        if row["kind"] == "command":
            processor.apply(row["payload"]["action"], row["payload"]["arguments"])
        elif row["kind"] == "complete":
            expected = row["payload"]["report"]
    report = processor.report()
    if expected is None:
        raise ValueError("incomplete capture: no terminal report")
    return report, expected
