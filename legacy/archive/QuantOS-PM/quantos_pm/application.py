import json
from pathlib import Path
from dataclasses import replace
from decimal import localcontext
from .domain.common import CONTEXT
from .config import ROOT, local_path, load
from .engine import Engine
from .storage import Journal, canonical, digest, source_digest


def summary(report):
    return (f"QuantOS-PM {report['run_id']} — {report['mode']} / {report['model']} (VIRTUAL ONLY)\n"
            f"Equity: ${report['starting_equity']} → ${report['ending_equity']}\n"
            f"P&L: ${report['pnl_excluding_incentives']} | realized ${report['realized_pnl']} | "
            f"drawdown {report['max_drawdown']} (fraction)\n"
            f"Fills: {report['orders']['fills']} | pairs: {report['complete_pairs']} | "
            f"skipped: {report['skipped_fraction']} | stops: {len(report['stop_events'])}\n"
            "No demonstrated edge; no promotion or live authority.\n")


def run(config_path, feed_path, model=None):
    with localcontext(CONTEXT):
        return _run(config_path, feed_path, model)


def _run(config_path, feed_path, model=None):
    c = load(config_path)
    if model:
        if model not in ('optimistic', 'baseline', 'pessimistic'):
            raise ValueError('invalid model')
        c = replace(c, model=model)
    feed = local_path(feed_path, must_exist=True).read_bytes()
    calibration_bytes = local_path(c.calibration, must_exist=True).read_bytes() if c.calibration else b'null'
    calibration = json.loads(calibration_bytes)
    manifest = dict(schema=1, version='0.1.0', config=c.raw, model=c.model,
                    feed_sha256=digest(feed), calibration_sha256=digest(calibration_bytes),
                    code_sha256=source_digest(), virtual_only=True,
                    data_provenance='synthetic' if c.mode == 'paper' and calibration and calibration.get('synthetic') else 'public_capture',
                    timestamps='UTC; event_time=source, receipt_time=local arrival; decisions use receipt order')
    run_id = digest(canonical(manifest).encode())[:20]
    directory = ROOT/'artifacts'/'runs'/run_id
    journal = Journal(directory, manifest)
    engine = Engine(c, calibration)
    try:
        for line in feed.splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            start = len(engine.execution.records)
            engine.process(e)
            x = engine.execution
            journal.append('event', dict(input=e, effects=x.records[start:],
                           cash=x.cash, reserved=x.reserved, realized=x.realized,
                           equity=x.equity(engine.md, engine.now), uncertain=x.uncertain,
                           stop_count=len(engine.evaluation.stops)))
        final_record_start = len(engine.execution.records)
        report = engine.finish()
        journal.append('end_of_capture', engine.execution.records[final_record_start:])
        report.update(run_id=run_id, manifest=manifest,
                      results_under_pessimistic_assumptions=c.model == 'pessimistic')
        journal.append('report', report)
        # Exports can always be regenerated from the verified journal.
        local_path(directory/'report.json').write_text(canonical(report)+'\n')
        local_path(directory/'summary.txt').write_text(summary(report))
        local_path(directory/'manifest.json').write_text(canonical(manifest)+'\n')
        return report
    finally:
        journal.close()


def compare(feed):
    reports = [run(f'configs/paper_{capital}.toml', feed, model)
               for capital in (200, 350, 500) for model in ('optimistic', 'baseline', 'pessimistic')]
    rows = [{k: r[k] for k in ('run_id', 'starting_equity', 'model', 'ending_equity',
                             'pnl_excluding_incentives', 'max_drawdown', 'complete_pairs', 'edge_demonstrated')}
            for r in reports]
    output = local_path('artifacts/comparison.json')
    output.parent.mkdir(exist_ok=True)
    output.write_text(canonical(rows)+'\n')
    return rows
