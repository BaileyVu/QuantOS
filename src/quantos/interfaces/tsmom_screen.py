"""T4 training-only evidence screen. This command can never submit orders."""
import argparse
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import tomllib
from quantos.application.tsmom_screen import screen_entry_evidence
from quantos.infrastructure.storage.parquet import ParquetCandleDatasetStore, dataset_id
from quantos.infrastructure.storage.duckdb_query import DuckDBCandleDatasetQuery
from quantos.domain.runtime_contracts import canonical, identity


def publish(path, value):
    payload=(canonical(value)+'\n').encode('ascii')
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_bytes()!=payload:
            raise ValueError('immutable T4 evidence collision')
    else:
        with path.open('xb') as stream:
            stream.write(payload)
            stream.flush()
            import os
            os.fsync(stream.fileno())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',type=Path,required=True)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--protocol',type=Path,default=Path('configs/tsmom.preregistered.toml'))
    parser.add_argument('--preregistration',type=Path,default=Path('docs/V1_T4_PREREGISTRATION.md'))
    args=parser.parse_args()
    protocol_bytes=args.protocol.read_bytes(); prereg=args.preregistration.read_bytes()
    # This exact preregistration is fixed before first performance observation.
    if sha256(protocol_bytes).hexdigest()!='a169e40d29ba48704990ed403f2987b2f6670117cf02be6653261ce750bae1f5':
        raise ValueError('T4 protocol changed after preregistration')
    if sha256(prereg).hexdigest()!='2b3b34bd57468d5300b0e233c99c3ce6a3896be071c1c1ba5eb8d76fd4fad362':
        raise ValueError('T4 preregistration changed')
    config=tomllib.loads(protocol_bytes.decode('utf-8'))['protocol']
    protocol_id=identity((config,sha256(prereg).hexdigest()))
    start=datetime.fromisoformat(config['development_start']); end=datetime.fromisoformat(config['holdout_start'])
    digest=sha256()
    with args.dataset.open('rb') as source:
        for block in iter(lambda:source.read(1048576),b''): digest.update(block)
    publication={'protocol':config,'protocol_identity':protocol_id,
                 'preregistration_sha256':sha256(prereg).hexdigest(),
                 'source_parquet_sha256':digest.hexdigest(),'source_dataset_id':args.dataset.stem,
                 'holdout_performance_opened':False}
    publish(args.output/'protocol.json',publication)
    sources = {}
    package_root = Path(__file__).resolve().parents[1]
    for source_path in sorted(package_root.rglob('*.py')):
        relative = source_path.relative_to(package_root).as_posix()
        content = source_path.read_bytes()
        sources[relative] = sha256(content).hexdigest()
        copy = args.output/'source-bundle'/relative
        copy.parent.mkdir(parents=True, exist_ok=True)
        if copy.exists():
            if copy.read_bytes() != content:
                raise ValueError('source snapshot changed; use a new explicit evidence directory')
        else:
            with copy.open('xb') as stream:
                stream.write(content)
    publish(args.output/'code-manifest.json', sources)
    publication['code_identity'] = identity(sources)
    print('Validating canonical dataset and querying development only',flush=True)
    development=DuckDBCandleDatasetQuery(ParquetCandleDatasetStore(args.data_root)).query_open_time_range(
        args.dataset,start_open_time=start,end_open_time_exclusive=end)
    print('Development candles',len(development.candles),flush=True)
    def progress(kind,index,values):
        publish(args.output/f'calibration-{index}.json',{'kind':kind,'artifacts':values,
                                                       'artifact_ids':tuple(v.artifact_id for v in values)})
        print(kind,index,[(v.horizon,len(v.episodes),str(v.lower_estimate),v.eligible) for v in values],flush=True)
    report=screen_entry_evidence(development=development,protocol_identity=protocol_id,
        train_days=config['train_days'],validation_days=config['validation_days'],step_days=config['step_days'],progress=progress)
    publish(args.output/'screen.json',{'screen':report,'artifact_id':identity((report,publication)),
        'source':publication,'not_run':('backtest PnL','walk-forward PnL','Monte Carlo','cost-stress PnL','final holdout','paper validation','testnet','mainnet')})
    print(report.verdict,'eligible_horizons=',report.eligible_horizons,'artifact_id=',report.artifact_id,flush=True)
    return 2 if report.verdict=='NO_GO_FOR_LIVE' else 0


if __name__=='__main__':
    raise SystemExit(main())
