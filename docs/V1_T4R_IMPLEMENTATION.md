# V1-T4R implementation and operating guide

This sprint preserves TSMOM's NO_GO, evaluates only the preregistered TRB family,
and develops independent Testnet infrastructure. Mainnet authenticated and economic
requests are hard-disabled in the REST boundary. The Execution runtime is Testnet
only. Paper remains the normal application default. No production Alpha has been selected.

## Preservation and data

Preservation commit: bce6fae072d549daf4bf7e01f3118bebf4dc7ba5, following c317fef.
The preservation suite passed all 989 tests with a clean worktree. Later recovery
changes are separate work; this guide does not imply promotion.

The archive begins 2017-08-17 04:00 UTC, but a gap on 2023-03-24 from 12:40 through
13:59 UTC breaks continuity. The new immutable canonical dataset spans 2023-03-24
14:00 through 2026-09-27 23:59 UTC: 1,848,120 candles. No gaps were repaired.
Dataset: acff43b039c17bd740a191928e5a8b33651811a3ef9e21d979a12ae47da9e052.
Every old 2024-2026 candle was compared exactly after publication/read-back; an
additional refetched 1,440-minute overlap was verified by canonical ingestion.

`prepend_and_persist_historical_range` reuses the existing ingestion validator and
immutable publisher. Acquisition uses the existing daily archive adapter,
checksum verification and range fetcher. Evidence lives under
G:/QuantOS-Data/t4r/history. Operational acquisition scripts remain ignored under
artifacts/t4r; raw resources and Parquet stay outside Git.

## TRB

The shared daily state supports an explicit trb-daily-v1 schema, retaining 201
completed daily closes for 50/150/200 ranges. Its 11 values stay within V1 limits.
The batch calibration projection shares the day-coverage reducer. Initial partial
days are excluded. Historical and paper paths use the same state and immutable
Execution account context; Alpha has no hidden holdings.

Preregistration and external TOML hashes are checked before calibration. The run
archives its Python source and binds the immutable market-data identity. Training
ended 2025-01-01. Completed episodes were 3, 1, 0 for 50/150/200 days. All fail the
frozen minimum 12: NO_GO_ALPHA. Positive small-sample means do not qualify as
expected-edge evidence. Later economic validation and holdout were not run.

## Execution and persistence

ExchangeExecutionEngine owns submission, actual-fill accounting and recovery.
It reuses Risk-approved OrderIntent evidence, AccountSnapshot and the durable
compare-and-append ledger. It does not use PaperFillProvider.

A prepared intent is persisted before any economic POST. A deterministic
36-character client ID binds to the request identity. Submission exceptions
record UNKNOWN and query the exchange; economic POSTs are never retried blindly.
Missing query results, stale approvals, unowned open orders, account scope changes,
balance mismatches, torn ledgers and unsupported fees block new decisions.
Restart must reconcile before another order.

Order/trade IDs, cumulative quantity, quote amount, weighted average price, partial
and terminal status, and actual commission asset/amount are retained. Accounting
supports USDT or traded-base commissions. Other commission assets block. Fills
must respect the approved LIMIT price. Cumulative observations recompute from the
pre-order snapshot, preventing duplicate fills.

Open orders and all account asset totals are compared against a recorded exchange
baseline plus the local allocation's changes. Existing Testnet faucet assets are
not invented QuantOS positions. External activity causes a mismatch. Use a
dedicated Testnet account/key and one durable allocation folder.

## REST and filters

HMAC-SHA256 signs the exact URL-encoded bytes. Server time has a bounded round-trip
check; recvWindow is 5000 ms. Redirects are refused. Transport exception details,
server error messages, keys and signatures are not logged. Rate limits block until
Retry-After. No economic request is retried automatically.

Current exchange info supplies quantity, market lot, price and notional limits.
Risk sizes down using the current grid. Local checks plus signed /order/test check
dynamic/account filters before prepare; the exchange also enforces filters at
submission. The harness uses LIMIT IOC to bound adverse price. A 9.90-USDT request
unable to meet current filters is rejected; order size is never increased.
A response containing 1,000 fills is blocked as potentially truncated history.

Official references:
- https://developers.binance.com/en/docs/products/spot/rest-api
- https://developers.binance.com/en/docs/products/spot/filters
- https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/testnet/rest-api.md

## Testnet-only operation

Set QUANTOS_TESTNET_ENV_FILE to the exact external Testnet credential file.
The Infrastructure loader accepts exactly BINANCE_TESTNET_API_KEY and
BINANCE_TESTNET_API_SECRET, rejecting missing, duplicate, blank or unexpected
keys without exposing values. Never paste or commit their values. Use a
trading-enabled Testnet key; do not supply Mainnet keys.
No withdrawal endpoint exists in this harness. The Testnet /account canWithdraw
field describes account capability, not Mainnet key withdrawal permission.
Mainnet permission verification and activation are unavailable.

From this worktree:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
& 'G:\QuantOS\.venv\Scripts\python.exe' -m quantos.interfaces.testnet_execution --state-dir 'G:\QuantOS-Data\t4r\testnet' --action preflight
& 'G:\QuantOS\.venv\Scripts\python.exe' -m quantos.interfaces.testnet_execution --state-dir 'G:\QuantOS-Data\t4r\testnet' --action buy --approve-synthetic-intent
& 'G:\QuantOS\.venv\Scripts\python.exe' -m quantos.interfaces.testnet_execution --state-dir 'G:\QuantOS-Data\t4r\testnet' --action recover
& 'G:\QuantOS\.venv\Scripts\python.exe' -m quantos.interfaces.testnet_execution --state-dir 'G:\QuantOS-Data\t4r\testnet' --action sell --approve-synthetic-intent
```

These are explicit infrastructure tests using a synthetic Risk edge input, not
Alpha or profitability evidence. One BUY cycle is allowed per allocation. Do not
create another folder to bypass uncertain state or reuse capital. IOC partial
fills/expiry can leave residual assets. Dust below current filters stays explicit;
never add funds or round upward automatically.

For known open orders, `--action cancel` persists a cancellation request and
re-queries state. A not-found UNKNOWN order is never recreated. Preserve damaged
ledger/head/lock files or evidence affected by a Testnet reset, and reconcile
manually against exchange records; no automatic repair/new allocation is offered.
The CLI exits 2 on credential/reconciliation blocks. Recovery alone does not prove
a successful authenticated order cycle.

## Validation limits

The initial run used public Testnet time and BTCUSDT exchange information only.
Authenticated Track B validation subsequently passed on 2026-09-29; see
V1_T4R_TESTNET_VALIDATION.md for actual exchange evidence and test scope.
Offline tests cover signing, Risk rejection, a synthetic BUY/SELL cycle,
partial/base-commission fills, UNKNOWN and crash recovery, concurrent writers,
cancellation, corrupt ledgers and Mainnet refusal. These fixtures alone do not
establish authenticated Testnet acceptance.

The unchanged AF4B production-preservation test requires zero src diff versus HEAD
and fails while recovery changes remain uncommitted. Its assertions are not
weakened. The operator authorized a separate reviewed T4R commit after successful
authenticated validation if this is the only remaining failure. Documentation
pins reflect only the pre-existing authorized deterministic amendments.

configs/mainnet.locked.toml is a dormant design record, not an activation route.
Mainnet still requires Alpha, holdout, continuous Paper, Testnet, reconciliation
and later explicit micro-live approval. None is inferred here.
