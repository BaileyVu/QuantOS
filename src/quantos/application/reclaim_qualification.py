"""One transparent counterfactual pass over preserved source availability order."""
import json
from quantos.application.retail_replay import stable_json


def qualify_commands(processor, records, journal, source_digest, stress):
    terminal = None
    start = end = None
    for row in records:
        if row["kind"] == "complete":
            terminal = row["payload"]
        if row["kind"] != "command":
            continue
        action, args = row["payload"]["action"], dict(row["payload"]["arguments"])
        if action == "event":
            timestamp = args["event"].received_at
            start = timestamp if start is None else start
            end = timestamp
            clock = max([args["processing_ns"], *args.get("clock_samples_ns", [])]) + 100_000_000
            args["processing_ns"] = clock
            processor.apply(action, args, clock_ns=lambda: clock)
        else:
            processor.apply(action, args)
        journal.append("command", action=action, arguments=args, source_sequence=row["sequence"])
        if processor.last_decisions:
            journal.append("decisions", source_sequence=row["sequence"], records=json.loads(stable_json(processor.last_decisions)))
    if terminal is None:
        raise ValueError("incomplete source capture")
    report = processor.report()
    journal.append("complete", report=report, source_sha256=source_digest)
    return {"qualification": "Q05-LR-v1", "stress": stress,
            "source_sha256": source_digest, "start": start, "end": end,
            "source_shutdown": terminal.get("shutdown_reason"), "metrics": report}
