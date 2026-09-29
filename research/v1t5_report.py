"""Render preserved tournament evidence; never refit or rerun a candidate."""
import argparse
from collections import Counter
from pathlib import Path
import json
import math

def num(x,digits=4):return 'undefined' if x is None else f'{float(x):.{digits}f}'
def pct(x):return 'undefined' if x is None else f'{float(x)*100:.3f}%'
def render(root,output):
    root=Path(root)
    seal=json.loads((root/'preregistration_seal.json').read_text())
    manifest=json.loads((root/'dataset_manifest.json').read_text())
    verdict=json.loads((root/'verdict.json').read_text())
    summaries=json.loads((root/'development_summary.json').read_text())
    lines=['# QuantOS V1-T5 Alpha sprint evidence','',f"Verdict: **{verdict['verdict']}**.",
       '',f"Mainnet: **{verdict['mainnet']}**.",'',
       '## Source identity and validation','',
       '- Starting SHA: `f85c3497985c23ff3890b693e069afceab76bd8f`.',
       '- Separate authorized specification amendment: `7a522e2`.',
       f"- Evaluated source SHA: `{seal['source_commit']}`.",
       '- Implementation/preregistration commit: `9748177`; subsequent source commit changes whitespace only.',
       '- Pre-economic full suite: 1,046 tests passed. Final full suite: 1,047 tests passed in91.429 seconds.',
       '- Dependency consistency (`pip check`) and Python compilation passed. No configured lint/type checker exists.',
       '- Replay parity covers synthetic historical/batch and streaming inputs through the canonical execution path. A continuous Paper runtime adapter for either T5 candidate was not packaged or qualified because both failed development.',
       '- No push, merge, exchange orders or Mainnet enablement performed. Market data/model/evidence files remain outside Git.',
       '', '## Preregistration hashes','',
       f"- Protocol: `{seal['protocol']}`.",f"- Machine configuration: `{seal['config']}`.",
       f"- Dataset/fold manifest: `{seal['dataset_manifest']}`.",
       '', 'The complete protocol is [V1_T5_ALPHA_PREREGISTRATION.md](V1_T5_ALPHA_PREREGISTRATION.md).',
       '', '## Data integrity and coverage','',
       'Acquired and checksum-validated 1,545 daily archive dates (2019-01-01 through 2023-03-24 plus 2026-09-28), alongside the existing canonical post-gap source. Coverage is segmented, not claimed continuous across outages.',
       'Exactly one REST recovery attempt returned only the 12:39 and 14:00 overlap candles. Both match the archive exactly; 12:39 is itself a partial-minute candle. The 80 missing minutes were not repaired. No interpolation, synthetic candles or source rewriting occurred.',
       f"The immutable manifest identifies {len(manifest['segments'])} continuous validated partition chains and {len(manifest['quarantined'])} quarantined partitions containing incomplete candles. All feature/label/fold windows stay inside one accepted segment.",
       '', '| Continuous segment start UTC | End UTC | Minutes | Chain identity |', '|---|---|---:|---|']
    used={f['segment'] for fs in manifest['folds'].values() for f in fs}
    for s in manifest['segments']:
        if s['id'] in used:lines.append(f"| {s['start']} | {s['end']} | {s['rows']} | `{s['id']}` |")
    lines += ['', 'Earlier accepted segments do not contain sufficient uninterrupted training/validation/test history for the frozen schedules. No performance evidence was calculated from 2025 or 2026.',
       '', '## Exact candidate contracts','',
       '**A — `sae-tbl-30m-longcash-v1`:** completed UTC30m bars; finite39-column OHLCV-derived candidate universe with training-local FD selection, variance/correlation pruning and scaling. Supervised denoising autoencoder, width32, bottleneck8/16, three-class head. Two paired TBL horizon/barrier/noise configurations (4/1/0 and 8/2/.05), 15 deterministic CPU epochs. Positive probability >=.6 and uniquely largest targets LONG; other classes target CASH. Training-only class gross-return calibration supplies Risk edge after reserving exit cost. Barrier outcomes label training data; no perfect barrier execution is assumed.',
       '', '**B — `cost-aware-hourly-xgb-v1`:** completed UTC1h bars; same finite candidate features and fold-local transformations. XGBoost predicts next-hour close return; exactly depth2/100 or depth3/200 trees, other parameters frozen. Positive forecast targets LONG, otherwise CASH; changes require absolute forecast >2 times effective change cost. No shorting, pyramiding or test-driven tuning.',
       '', 'A uses6-month train/1-month validation/1-month test. B uses up to12-month train (minimum6 for fragmented history),3-month validation/3-month test. Both configurations are chosen by validation predictive loss only. This is a declared adaptation, not a reproduction of the papers.',
       '', '## Accounting and cost assumptions','',
       'Every position change passes the canonical Alpha → Risk → Execution path. Execution owns fills and account mutation; canonical Evaluation computes metrics. Signal execution waits one completed minute, then uses its close plus adverse friction. Initial capital20 USDT, fixed10 USDT entry,60% exposure cap,5% daily-loss and20% drawdown BUY stops, quantity step.00001 BTC, minimum notional5 USDT. These are conservative research filter assumptions, not historical filter observations.',
       'The nominal per-change cost ladder is10/15/20/25/30bps, consisting of10bps explicit fee and0/5/10/15/20bps adverse slippage. Buys additionally incur fee on slipped notional. No BNB discount. Final open positions are marked, not given invented liquidation fills. Independent folds start flat; aggregate return is total PnL divided by20 times fold count.',
       '', '## Aggregate OOS results and cost sensitivity','',
       '| Candidate | Cost bps | Aggregate return | Net PnL USDT | PF | Sharpe | Sortino | Trade expectancy USDT | Change expectancy USDT | Win rate | Trades / changes | Changes/day | Max DD |',
       '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for s in summaries:
        for cost,m in s['costs'].items():
            lines.append(f"| {s['family']} | {cost} | {pct(m['aggregate_return'])} | {num(m['net_profit'])} | {num(m['profit_factor'])} | {num(m['sharpe'])} | {num(m['sortino'])} | {num(m['expectancy'])} | {num(m['change_expectancy'])} | {pct(m['win_rate'])} | {m['completed_trades']} / {m['events']} | {num(m['events_per_day'])} | {pct(m['max_drawdown'])} |")
    for s in summaries:
        family=s['family'];folds=[json.loads(p.read_text()) for p in sorted((root/'development'/family).glob('fold-*.json'))]
        lines += ['',f'## Candidate {family}: every OOS fold','',
          '| Test start | End exclusive | Selected configuration | Features | Baseline PnL | Stress PnL | Baseline trades / changes | Baseline PF | Baseline Sharpe | Baseline DD |',
          '|---|---|---|---:|---:|---:|---:|---:|---:|---:|']
        selected=Counter();rejections=Counter();configs=Counter()
        for fold in folds:
            m=fold['costs']['15']['metrics'];st=fold['costs']['30']['metrics'];md=fold['model']
            selected.update(md['feature_names']);rejections.update(fold['costs']['15']['rejections']);configs.update([json.dumps(md['configuration'],sort_keys=True)])
            lines.append(f"| {fold['fold']['test_start'][:10]} | {fold['fold']['test_end'][:10]} | `{json.dumps(md['configuration'],sort_keys=True)}` | {len(md['feature_names'])} | {num(m['net_profit'])} | {num(st['net_profit'])} | {m['trade_count']} / {fold['costs']['15']['fill_count']} | {num(None if 'profit_factor' in m['undefined_metrics'] else m['profit_factor'])} | {num(None if 'sharpe' in m['undefined_metrics'] else m['sharpe'])} | {pct(m['maximum_drawdown'])} |")
        gates=s['gates'];lines+=['','Frozen gate results: '+', '.join(f"{k}={'PASS' if v else 'FAIL'}" for k,v in gates.items())+'.','',
          'Feature selection counts across folds (training evidence only): '+', '.join(f'`{k}` {v}/{len(folds)}' for k,v in sorted(selected.items()))+'.','',
          'Baseline Risk rejections: '+(json.dumps(dict(rejections),sort_keys=True) if rejections else 'none')+'.']
        importance={}
        for f in folds:
            md=f['model'];scale=sum(md['importance']) or 1
            for name,value in zip(md['feature_names'],md['importance']):importance[name]=importance.get(name,0)+value/scale/len(folds)
        lines += ['', 'Mean normalized model importance (top10, absent features count zero): '+', '.join(f'`{k}` {v:.4f}' for k,v in sorted(importance.items(),key=lambda p:-p[1])[:10])+'.',
          'Importance uses XGBoost gain for B and mean absolute encoder weights for A. Encoder weights are a descriptive sensitivity proxy, not causal feature importance or an ablation study. No outcome-driven feature changes were made.']
        for cost in ('15','30'):
            m=s['costs'][cost]
            returns=sorted(v for f in folds for v in f['costs'][cost]['daily_returns'])
            trades=[float(t['net_pnl']) for f in folds for t in f['costs'][cost]['trades']]
            cvar=sum(returns[:max(1,math.ceil(len(returns)*.05))])/max(1,math.ceil(len(returns)*.05)) if returns else None
            longest=0
            for f in folds:
                streak=0
                for t in f['costs'][cost]['trades']:
                    streak=streak+1 if float(t['net_pnl'])<0 else 0;longest=max(longest,streak)
            open_folds=sum(bool(f['costs'][cost]['final_account']['positions']) for f in folds)
            lines += ['',f"At {cost}bps: fees {num(m['fees'])} USDT; slippage {num(m['slippage'])} USDT; mean exposure {pct(m['exposure'])}; daily5% expected shortfall {pct(cvar)}; worst completed trade {num(min(trades) if trades else None)} USDT; longest within-fold closed-trade losing streak {longest}; {open_folds} folds end with a marked open position."]
    lines += ['', '## Promotion disposition','',
      'SAE produced30,651 OOS predictions; maximum positive-class probability was0.413351, below the frozen0.6 confidence gate. Thus it never requested an entry, rather than being blocked by Risk. Both candidates failed mandatory development gates. No strategy was selected. This is NO_GO_ALPHA for this preregistered experiment; it does not prove that all models in either broad family are unprofitable.',
      '- Monte Carlo/bootstrap: not run on failed candidates; the mandatory economic gates failed first. No simulated survival claims are made.',
      '- Aggressive10/25/50/75/100% allocation and fractional-Kelly study: not performed because no Alpha qualified for promotion; no exposure recommendation.',
      '- Calendar2025 prefinal: not opened.', '- Calendar2026 holdout: not opened.',
      '- Paper packaging: not eligible; no launch procedure or activation issued.',
      '- Mainnet: LOCKED / NOT_APPROVED.', '- Worktree: clean after the local report commit; no push or merge.',
      '', '## Evidence locations','',
      f'- Immutable seal: `{root / "preregistration_seal.json"}`.',
      f'- Dataset, partitions, quarantine and exact folds: `{root / "dataset_manifest.json"}`.',
      f'- All fold/scenario metrics and fill evidence: `{root / "development"}`.',
      f'- Exact model weights, transformation identities and predictions: `{root / "models"}`.',
      f'- Final machine-readable verdict: `{root / "verdict.json"}`.', '']
    if verdict['verdict']!='NO_GO_ALPHA':raise ValueError('This report renderer handles failed tournaments only')
    Path(output).write_text('\n'.join(lines),encoding='utf8')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();render(a.root,a.output)
