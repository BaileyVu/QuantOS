"""Immutable training-only TRB evidence gate. No exchange or holdout performance access."""
import argparse
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import tomllib
from quantos.application.trb import calibrate_trb
from quantos.domain.features.daily import completed_daily_closes
from quantos.domain.runtime_contracts import identity
from quantos.infrastructure.storage.parquet import ParquetCandleDatasetStore
from quantos.infrastructure.storage.duckdb_query import DuckDBCandleDatasetQuery
from quantos.interfaces.tsmom_screen import publish

PREREG_HASH='34cf597256a7f09fba589e81f87c9f7ebfb54773c3748185acd3560e4d58cba7'
CONFIG_HASH='06cda1a0f977425d9f57b0e212df4ac2366d1e546522a8ff3e5580f88b0f070b'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--dataset',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    prereg=Path('docs/V1_T4R_TRB_PREREGISTRATION.md').read_bytes();config=Path('configs/trb.preregistered.toml').read_bytes()
    if sha256(prereg).hexdigest()!=PREREG_HASH or sha256(config).hexdigest()!=CONFIG_HASH:raise ValueError('frozen TRB preregistration changed')
    protocol=tomllib.loads(config.decode('utf-8'))['protocol']
    if args.dataset.stem!=protocol['dataset_id']:raise ValueError('unregistered canonical dataset')
    digest=sha256()
    with args.dataset.open('rb') as f:
        for block in iter(lambda:f.read(1048576),b''):digest.update(block)
    if digest.hexdigest()!='69ec0ef0e22d354221ae41614f55d39b39e3781de5d03ad9a97d1e983af01c8e':raise ValueError('canonical file content changed')
    sources={};package=Path(__file__).resolve().parents[1]
    for source in sorted(package.rglob('*.py')):
        name=source.relative_to(package).as_posix();raw=source.read_bytes();sources[name]=sha256(raw).hexdigest()
        dest=args.output/'source-bundle'/name;dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists():
            if dest.read_bytes()!=raw:raise ValueError('immutable source snapshot collision')
        else:
            with dest.open('xb') as f:f.write(raw)
    publication={'protocol':protocol,'preregistration_sha256':PREREG_HASH,'config_sha256':CONFIG_HASH,
        'source_dataset_sha256':digest.hexdigest(),'code_identity':identity(sources),'holdout_performance_opened':False}
    publish(args.output/'code-manifest.json',sources);publish(args.output/'protocol.json',publication)
    start=datetime.fromisoformat(protocol['training_start']);end=datetime.fromisoformat(protocol['training_end_exclusive'])
    print('Reading canonical training only',start,end,flush=True)
    data=DuckDBCandleDatasetQuery(ParquetCandleDatasetStore(args.root)).query_open_time_range(args.dataset,start_open_time=start,end_open_time_exclusive=end)
    daily=completed_daily_closes(data);print('Completed training daily closes',len(daily),flush=True)
    calibrations=[]
    for horizon in protocol['horizons']:
        value=calibrate_trb(daily,horizon=horizon,source_identity=identity((data.identity,daily)),end_exclusive=end,
            minimum_samples=protocol['minimum_calibration_samples'],simulations=protocol['bootstrap_simulations'],
            seed=protocol['seed'],mean_block=protocol['bootstrap_mean_block_episodes'],quantile_denominator=protocol['bootstrap_lower_quantile_denominator'],
            fee=Decimal(protocol['fee_rate']),stress_slippage=Decimal(protocol['stress_slippage_rate']))
        publish(args.output/f'calibration-{horizon}.json',{'calibration':value,'artifact_id':value.artifact_id,'eligible':value.eligible})
        calibrations.append(value);print(horizon,'episodes',len(value.episodes),'mean',value.mean,'lower',value.lower,'eligible',value.eligible,flush=True)
    eligible=tuple(c.horizon for c in calibrations if c.eligible)
    result={'verdict':'ELIGIBLE_FOR_BACKTEST' if eligible else 'NO_GO_ALPHA','eligible_horizons':eligible,
        'calibration_ids':tuple(c.artifact_id for c in calibrations),'source':publication,'holdout_opened':False,
        'not_run':('Backtest','Walk-Forward performance','cost-stress performance','Monte Carlo','selection','2026 holdout','continuous Paper'),
        'reason':'initial training calibration gate; later economic stages require eligible expected-edge evidence',
        'track_b_must_continue':True}
    publish(args.output/'screen.json',{'result':result,'artifact_id':identity(result)})
    print(result['verdict'],flush=True)
    return 0 if eligible else 2

if __name__=='__main__':raise SystemExit(main())
