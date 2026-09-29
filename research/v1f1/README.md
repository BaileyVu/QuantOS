# V1-F1 source feasibility audit

`audit_sources.py` is a pre-experiment public-data probe, not a backtester or a
production adapter. It never calculates strategy returns or grants economic
access. Run from the repository root with Python 3.11+:

```powershell
python research/v1f1/audit_sources.py --output G:\QuantOS-Data\v1f1\source-audit
python -m unittest tests.unit.test_v1f1_source_audit -v
```

The probe stores content-addressed raw responses, exact probe source, and an
immutable JSON manifest outside Git. It enumerates the complete paginated
Binance USD-M archive symbol directory independently of current exchangeInfo,
records daily/monthly archive families, captures unsigned current exchangeInfo,
and validates BTCUSDT/ETHUSDT development samples from January 2020 and December
2024. Those two symbols are diagnostic samples, not the experiment universe.

ZIP names and SHA-256 checksums, ordering, completed-minute bounds, OHLC,
volume/trade consistency, funding schema and finite values are checked. Gapped
or invalid samples are marked quarantined in the manifest and never published
as canonical datasets. Raw originals remain intact. Sample validation does not
prove whole-period coverage, event availability or funding completeness. Archive
publication time is not treated as the time a historical observation became
available to a trading decision. Pre-final/final observations are disallowed.

Historical trading status and filter validity are deliberately **unverified**:
archive directory membership is not a historical eligibility record, and the
Binance `exchangeInfo` endpoint documents current rules only. A successful probe
process means evidence was collected, not that the data or Alpha gate passed.

Before economic evaluation can be implemented or authorized, supply a
provenance-bound historical contract/status/filter timeline, including delisted
contracts, effective dates, market lot sizes, tick sizes and minimum notionals.
Reconstructing from official listing/delisting/rule-change notices is acceptable
only with demonstrated coverage; isolated notices do not prove completeness.
Do not substitute current filters or infer past executable orders from candles.
Then validate full causal data coverage, resolve quarantines, freeze/hash the
complete preregistration and machine config, and archive evaluated source
identity. The requested 2025 and 2026 economic periods remain untouched until
their respective gates are earned.

Official references:

- [Binance public archives and checksums](https://github.com/binance/binance-public-data)
- [USD-M market data and current exchange information](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)
- [Example historical minimum-notional changes](https://www.binance.com/en/support/announcement/detail/47fcf9ed148e485c8f467f9c867c0a35)

The specification amendment in `docs/000_READ_FIRST.md` §0 governs this phase.
No futures execution subsystem or exchange order capability is added here.
