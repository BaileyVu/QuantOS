"""T4 canonical continuation via the existing checksum-verified daily archive path."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from quantos.application.historical_ingestion import extend_and_persist_historical_range
from quantos.infrastructure.binance.daily_archive import BinanceSpotDailyArchiveAdapter, _default_http_get
from quantos.infrastructure.binance.daily_archive_range_fetch import BinanceSpotDailyArchiveRangeFetcher
from quantos.infrastructure.storage.parquet import ParquetCandleDatasetStore, dataset_id
from quantos.interfaces.tsmom_screen import publish


def acquire(*, base_path, root, end_exclusive, ingestion_version, http_get=None):
    """Immutable publication; existing raw archives are verified again by the adapter."""
    root=Path(root); base_path=Path(base_path)
    store=ParquetCandleDatasetStore(root)
    base=store.read(base_path)
    if base_path.stem != dataset_id(base.identity):
        raise ValueError('base path does not identify its canonical dataset')
    if base.identity.symbol != 'BTCUSDT' or base.identity.source != 'binance-spot-daily-archive':
        raise ValueError('T4 requires canonical BTCUSDT daily-archive candles')
    evidence=root/'t4'/'ingestion'
    transport=http_get or _default_http_get
    def cached_get(url,timeout):
        target=evidence/'raw'/url.rsplit('/',1)[-1]
        if target.exists():
            return target.read_bytes()
        content=transport(url,timeout)
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as stream:
            stream.write(content)
            stream.flush()
            import os
            os.fsync(stream.fileno())
        return content
    output=extend_and_persist_historical_range(base,
        BinanceSpotDailyArchiveRangeFetcher(BinanceSpotDailyArchiveAdapter(http_get=cached_get,timeout_seconds=20)),
        store,end_open_time_exclusive=end_exclusive,ingestion_version=ingestion_version)
    sequence=store.read(output.path)
    digest=sha256()
    with output.path.open('rb') as stream:
        for block in iter(lambda:stream.read(1048576),b''): digest.update(block)
    record={'identity':sequence.identity,'dataset_id':dataset_id(sequence.identity),
        'content_sha256':digest.hexdigest(),'path':str(output.path),'source_dataset_id':dataset_id(base.identity),
        'count':len(sequence.candles),'first_open':sequence.candles[0].open_time,
        'last_open':sequence.candles[-1].open_time,'holdout_performance_opened':False,
        'continuity':'validated by existing canonical extension and read-back contracts'}
    publish(evidence/(dataset_id(sequence.identity)+'.json'),record)
    return output.path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',type=Path,required=True)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--end-exclusive',required=True,help='Completed UTC boundary, ISO-8601 with timezone')
    parser.add_argument('--ingestion-version',required=True)
    args=parser.parse_args()
    end=datetime.fromisoformat(args.end_exclusive)
    if end.tzinfo is None or end.utcoffset().total_seconds()!=0:
        raise ValueError('UTC required')
    if end>datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0):
        raise ValueError('cannot acquire an incomplete UTC day')
    print(acquire(base_path=args.base,root=args.root,end_exclusive=end,
                  ingestion_version=args.ingestion_version))


if __name__=='__main__':
    main()
