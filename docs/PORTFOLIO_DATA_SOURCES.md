# Portfolio research data sources

## Source and contract

Research archives use Binance's public USD-M Futures archive at
[`data.binance.vision`](https://data.binance.vision/) and its official
[`binance-public-data` README](https://github.com/binance/binance-public-data).
Binance publishes daily and monthly ZIP partitions and provides a SHA-256
`.CHECKSUM` sidecar for each archive. New daily files are published the next
day; monthly files are published after month end. The normalized reader checks
the sidecar, ZIP integrity, CSV schema, timestamp alignment, and numeric values
before returning records. Raw files are stored by SHA-256 under the ignored
`data/portfolio/raw/sha256/` directory. An upstream correction creates a new
blob; an old blob is never overwritten.

Supported public paths are USD-M `klines` at `5m` and `1h`, `markPriceKlines`
at `5m` and `1h`, monthly `fundingRate`, and daily `metrics`. `ArchiveStore`
can list monthly and daily candle partitions and daily metrics from the public
S3 listing without downloading archive contents. Downloads have a configured
byte cap, bounded retries, and a fixed public host. No credentials or trading
endpoints are used.

`Bar.close_ms` preserves Binance's inclusive close timestamp; the completed
bar boundary is `close_ms + 1`. Open times must align to the interval grid.
Missing bars are preserved as missing: readers do not synthesize candles or
forward-fill values. Funding `calc_time` is an event timestamp and is kept
separate from price; a realized funding PnL calculation must explicitly join
the event to the correct mark price. `metrics.create_time` is retained as
`label_ms` exactly as published. It is not asserted to be the time the row
became observable and is never shifted, deduplicated, or silently aligned.
For cautious research joins, `conservative_available_ms` exposes `label_ms + 1h`
as a safety proxy. Binance does not publish row-level release times for these
metrics, so this proxy is not evidence of actual publication latency or proof
that each metric was available then. Metrics remain subject to a point-in-time
audit before use.

## Schema samples

Three small archive files were fetched read-only and their checksums matched the
official sidecars:

| Archive | Size | SHA-256 | Observed CSV header |
| --- | ---: | --- | --- |
| [BTCUSDT 1h klines, 2022-01](https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2022-01.zip) | 39,953 B | `5281a9c866e7d54b0a5b5e6a7dad5575ad7b96df67f05f74d614c54190ffff5f` | `open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore` |
| [BTCUSDT funding, 2022-01](https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2022-01.zip) | 906 B | `22ee19079b620f5c6d820e7d7f8bafa7fde866d89bd664863b8bd527749c12cb` | `calc_time,funding_interval_hours,last_funding_rate` |
| [BTCUSDT metrics, 2026-10-27](https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2026-10-27.zip) | 11,441 B | `dd56c7638bc2bf9092d0349cee0a89ee9e10428149245b5dc1cc5ff7874f8843` | `create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio` |

USD-M kline files contain 12 columns, timestamps in milliseconds, and a close
time one millisecond before the next bar boundary. Funding archive timestamps
can have a few milliseconds after the nominal funding time; those event times
are retained as-is. The parser converts numeric columns to `Decimal`.

## Archive coverage audit (2026-10-28 UTC)

This is an S3 object-name audit, not a full row-continuity or instrument-status
audit. Monthly klines, mark-price klines, and funding archives run through
**2026-09** for every listed pair below. Daily `1h` and `5m` klines were listed
through **2026-10-27** for all 18 tested USDT and USDC pairs. Daily metrics archives were listed through
**2026-10-27** for all 18. The start column gives the first observed monthly
partition for candles, mark-price candles, and funding, and the first daily
partition for metrics. These are archive partition dates, not claims that the
first or last archive contains every expected row.

| Base | USDT 1h / 5m / funding / mark start | USDT metrics start | USDC 1h / 5m / funding / mark start | USDC metrics start |
| --- | --- | --- | --- | --- |
| BTC | 2020-01 | 2020-09-01 | 2024-01 | 2024-01-04 |
| ETH | 2020-01 | 2021-12-01 | 2024-01 | 2024-01-04 |
| SOL | 2020-09 | 2021-12-01 | 2024-01 | 2024-01-04 |
| BNB | 2020-02 | 2021-12-01 | 2024-01 | 2024-01-04 |
| XRP | 2020-01 | 2021-12-01 | 2024-01 | 2024-01-04 |
| DOGE | 2020-07 | 2021-12-01 | 2024-01 | 2024-01-18 |
| ADA | 2020-01 | 2021-12-01 | 2025-03 | 2025-03-06 |
| AVAX | 2020-09 | 2021-12-01 | 2024-03 | 2024-03-20 |
| LINK | 2020-01 | 2021-12-01 | 2024-02 | 2024-02-16 |

All nine USDT symbols therefore have OHLCV partitions through the latest
completed UTC day checked and funding/mark-price monthly partitions from
2022-01-01 through 2026-09. The October funding month is not yet published as a
monthly archive, and no daily funding partitions were found. Current-month
funding requires another public source path or waiting for the monthly upload.
Metrics partitions begin before 2022-01-01. **USDC history does not cover the
2022 start date:** most pairs begin in 2024, ADAUSDC in March 2025. USDT and
USDC contracts are different instruments and must not be spliced to pretend a
single continuous series. The 29-symbol study universe still needs its own
frozen symbol list and point-in-time eligibility history; this nine-major audit
does not establish complete coverage for every one of those 29 symbols.

The source archive itself has reported data-quality concerns. Binance's public
repository has open reports about missing metrics dates ([issue #287](https://github.com/binance/binance-public-data/issues/287)),
metrics timestamp-label changes and duplicate labels from 2026-06-25 ([issue
#491](https://github.com/binance/binance-public-data/issues/491)), and gaps or
duplicates in metrics/mark-price klines ([issue #484](https://github.com/binance/binance-public-data/issues/484)). Those reports are not a
substitute for verifying every requested partition. In particular, do not use
metrics as a point-in-time feature until its per-row publication/availability
time and label convention have been established. Binance's USD-M
[`Open Interest Statistics` REST endpoint](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest-Statistics)
is limited to the latest 30 days; the older daily archive is required for
historical OI, with the timestamp caveat above.

The current sample covered a downloaded `BTCUSDT` metrics archive for Oct 27
and metadata partitions for the exact same day across the 18 USDT/USDC markets.
No multi-year dataset was downloaded and no out-of-sample results were read.

## Commands

Run only the focused parser/cache checks:

```sh
.venv/bin/python -m unittest tests.unit.test_portfolio_data
```

Example bounded archive fetch (writes only the ignored local data cache):

```sh
.venv/bin/python - <<'PY'
from quantos.paper.research.data import ArchiveStore

store = ArchiveStore()
path = store.fetch("klines", "BTCUSDT", "2022-01", "1h")
print(path)
PY
```

Inspect actual available partitions without downloading them:

```sh
.venv/bin/python - <<'PY'
from datetime import date
from quantos.paper.research.data import ArchiveStore

coverage = ArchiveStore().coverage(
    "klines", "BTCUSDT", "1h", date(2022, 1, 1), date(2026, 10, 28))
print(coverage.first_period, coverage.last_period, len(coverage.periods))
PY
```
