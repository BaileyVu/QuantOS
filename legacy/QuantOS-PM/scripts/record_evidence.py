"""Rebuild compact evidence from this repository's actual local run journals."""
import json
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timezone
from collections import Counter, defaultdict
from quantos_pm.application import compare, run, summary
from quantos_pm.storage import canonical, read_report
from quantos_pm.config import ROOT


def main():
    evidence=ROOT/'evidence'
    rows=compare('fixtures/btc_5m.jsonl')
    (evidence/'comparison.json').write_text(canonical(rows)+'\n')
    lines=['# Actual synthetic paper runs','',
           'Virtual capital only. Synthetic mechanics cases; no demonstrated edge.', '',
           '| Capital | Model | Ending equity | Net P&L | Maximum drawdown | Complete pairs | Run ID |',
           '| ---: | --- | ---: | ---: | ---: | ---: | --- |']
    commands=[]
    for r in rows:
        lines.append(f"| ${r['starting_equity']} | {r['model']} | ${r['ending_equity']} | ${r['pnl_excluding_incentives']} | {r['max_drawdown']*100:.6f}% | {r['complete_pairs']} | `{r['run_id']}` |")
        if r['model']=='pessimistic':
            report=read_report(r['run_id'])
            (evidence/f"paper_{r['starting_equity']}.json").write_text(json.dumps(report,indent=2)+'\n')
            commands.append(f".venv/bin/python -m quantos_pm report --run-id {r['run_id']}")
    lines.extend(['','## Reproduction commands','','```bash',
                  *[f'.venv/bin/python -m quantos_pm paper --config configs/paper_{b}.toml --feed fixtures/btc_5m.jsonl' for b in (200,350,500)],
                  '.venv/bin/python -m quantos_pm compare --feed fixtures/btc_5m.jsonl',*commands,
                  '.venv/bin/python -m unittest discover -s tests -v',
                  '.venv/bin/python -m compileall -q quantos_pm tests',
                  '.venv/bin/python -m pip check','```','',
                  'Run manifests contain exact code/config/feed/calibration hashes. Journals and full reports are in `artifacts/runs/RUN_ID/`. Synthetic calibration is disallowed in live shadow. Pessimistic residual inventory explains the larger losses at $350/$500.'])
    (evidence/'PAPER_RUNS.md').write_text('\n'.join(lines)+'\n')
    s=json.loads((evidence/'shadow-command.json').read_text())
    sr=read_report(s['run_id'])
    shadow_note=['# Actual public shadow capture','',f"- Public mainnet feed worked: {s['public_feed_worked']}.",
                 f"- {s['raw_messages']} raw messages, {s['canonical_events']} canonical events.",
                 f"- Event counts: `{canonical(s['event_types'])}`.",
                 f"- Run: `{s['run_id']}`; virtual orders: {s['simulated_orders']}; virtual PnL: ${s['simulated_pnl']}.",
                 f"- Capture: `{s['capture']}`.",
                 f"- Immutable Parquet SHA-256: `{s['parquet_sha256']}`.",
                 f"- Integrity/lifecycle stops: {len(sr['stop_events'])}; first reasons: `{canonical(sr['stop_events'][:3])}`.",
                 '', 'Transport success is separate from book/execution validity. This stream failed eligibility and continuity checks, and the strategy remained halted. No live fill or strategy success is claimed. Snapshots and deltas were preserved without silent repairs.',
                 '', 'Command actually executed:', '```bash',
                 '.venv/bin/python -m quantos_pm shadow --config configs/shadow_200.toml --seconds 30', '```',
                 '', *['- '+x for x in s['limitations']]]
    (evidence/'SHADOW_RUN.md').write_text('\n'.join(shadow_note)+'\n')
    w=json.loads((evidence/'research-command-full.json').read_text())
    d=Path(w['evidence_directory']);full=json.loads((d/'report.json').read_text())
    activity=json.loads((d/'activity.json').read_text())
    counts=Counter(r['type'] for r in activity)
    sides=Counter(r['side'] for r in activity if r['type']=='TRADE')
    prof=full['profile'];atoms=full['accounting']['credited_api_atoms']
    join=datetime.fromtimestamp(prof['join_date'],timezone.utc).isoformat()
    june=json.loads((d/'2026-06.json').read_text())
    june_counts=Counter(r['type'] for r in june)
    june_sides=Counter(r['side'] for r in june if r['type']=='TRADE')
    gap_counts=Counter(g['reason'] for g in full['accounting']['gaps'])
    compact=dict(w,join_date_utc=join,activity_types=dict(counts),trade_sides=dict(sides),
                 june_activity_types=dict(june_counts),june_trade_sides=dict(june_sides),
                 credited_api_atoms=atoms,partial_attribution=full['accounting']['attribution'],
                 unexplained_position_pnl_difference=full['accounting']['unexplained_difference'],
                 incentive_discrepancies=full['accounting']['incentive_discrepancies'],gap_counts=dict(gap_counts))
    (evidence/'wallet_summary.json').write_text(json.dumps(compact,indent=2)+'\n')
    text=['# Wallet evidence — incomplete economic attribution','',f"Wallet: `{w['wallet']}`",'',
          f"Public cursor walks exhausted: **{w['full_public_api_walk']}**. Status: **{w['status']}**. Exact replica: false.",
          f"Profile join date: {join}. API activity starts in June, not the May join month.",'',
          '| Dataset | Rows | Cursor exhausted |','| --- | ---: | --- |']
    text.extend(f"| {k} | {v['rows']} | {v['complete']} |" for k,v in w['coverage'].items())
    text.extend(['','## April–June 2026',''])
    text.extend(f"- {k}: {v['conclusion']}; {v['activity_rows']} rows; complete={v['complete']}." for k,v in w['months'].items())
    text.extend(['',f'June activity types: `{canonical(june_counts)}`; trade sides: `{canonical(june_sides)}`.',
                 '', '## Hypotheses ranked by observed support','',
                 '1. **Maker liquidity provision: strongest support.** Documented v2 cohort classification gives '+canonical(w['execution_classification'])+'. Classification uses complete maker-inclusive versus taker-only query cohorts, with duplicate signatures left UNKNOWN. It does not recover quotes, queue rank or intent.',
                 '2. **Split/sell/redemption inventory workflow: direct activity support.** Full-history activity types are '+canonical(counts)+'; trade sides are '+canonical(sides)+'. The offline paired-bid engine is therefore a research strategy, not a replica of this observed workflow.',
                 '3. **Rebate contribution: credited API evidence, unresolved totals.** Profile PnL atoms and the activity sum disagree. No aggregate activity rebate is assumed to equal independently reconciled income.',
                 '4. **Spread versus directional/early-exit alpha: unresolved.** Gross realized sales, remaining split-token basis and aggregate redemptions cannot yet be cleanly decomposed. No conditional reversal study has been validated.',
                 '', '## PnL evidence and reconciliation gaps','',
                 'The API profile currently reports these credited atoms (USD, timestamp included):','```json',json.dumps(atoms,indent=2),'```',
                 'These public numbers are not audited profits and are not comparable to the small-capital synthetic runs. The profile snapshot, cursor-bounded activity and position pages are not atomic.',
                 '',f"Partial reconstruction attribution: `{canonical(full['accounting']['attribution'])}`.",
                 f"Unexplained position-PnL difference: **${full['accounting']['unexplained_difference']}**. This large residual includes redemption cost/payout attribution and is not silently assigned to spread profit.",
                 f"Activity-minus-profile incentive discrepancies: `{canonical(full['accounting']['incentive_discrepancies'])}`.",
                 f"Unresolved-event reasons: `{canonical(gap_counts)}`.",
                 '', 'Polygon transaction hashes/explorer references are retained. The full receipt and token-burn history is not reconstructed; a bounded read-only sample is reported separately below. Inactive positions may be excluded even after API cursor exhaustion. Deposits/withdrawals are requested explicitly; the profile deposit/withdrawal atoms are null. A complete chain reconciliation and exact strategy reconstruction remain blocked.',
                 '', '## Reproduction','', '```bash',
                 '.venv/bin/python -m quantos_pm research --max-pages 50',
                 f'.venv/bin/python -m quantos_pm research --max-pages 1000 --resume {d.relative_to(ROOT)}','```','',
                 f'Raw pages, classified trades, positions, monthly data and the full report: `{d}`. No wallet key or account permission was used.'])
    chain_path=evidence/'polygon-sample.json'
    if chain_path.exists():
        chain=json.loads(chain_path.read_text())
        text.extend(['','## Supplemental Polygon receipt sample','',
                     f"Verified {chain['verified']} of {chain['selected']} purposively selected transactions through the fixed public Polygon RPC. {len(chain['errors'])} reads failed; observed provider failures were HTTP 403.",
                     'Verified receipts matched successful canonical blocks, API/block timestamps and wallet involvement. Transfer amounts remain raw units; no USD identity or full attribution is inferred.',
                     f"Raw receipt/block evidence: `{chain['evidence_file']}`. Full reconciliation remains false."])
    (evidence/'WALLET_RESEARCH.md').write_text('\n'.join(text)+'\n')

if __name__=='__main__':main()
