# QuantOS-PM V0

An independent Python 3.11+ paper engine for BTC five-minute binary markets.
**Virtual capital only. No keys, wallet SDK, signing, authenticated client, or live-order route.**

The offline engine runs and has tested accounting/risk controls. Public mainnet
capture works. Live quoting is deliberately blocked until settlement rules, fee
metadata, stream continuity, and an independently validated calibration are
available. This delivery does **not** establish a repeatable trading edge, exact
wallet replication, or readiness for promotion.

## Install and run

From this repository directory, using any Python 3.11+ executable:

```bash
python3.13 -m venv .venv
.venv/bin/python -m quantos_pm paper --config configs/paper_200.toml --feed fixtures/btc_5m.jsonl
.venv/bin/python -m quantos_pm paper --config configs/paper_350.toml --feed fixtures/btc_5m.jsonl
.venv/bin/python -m quantos_pm paper --config configs/paper_500.toml --feed fixtures/btc_5m.jsonl
.venv/bin/python -m quantos_pm compare --feed fixtures/btc_5m.jsonl
.venv/bin/python -m unittest discover -s tests -v
```

Paper mode has **zero third-party dependencies** and needs no network, API key or
blockchain connection. The module is runnable directly from the repository;
installing the package is optional. The environment already created for this
checkout is `.venv` with Python 3.13.

Each paper command prints its content-derived run ID. Use it verbatim:

```bash
.venv/bin/python -m quantos_pm report --run-id RUN_ID
.venv/bin/python -m quantos_pm report --run-id RUN_ID --json
```

Concrete successful commands and run IDs are in [evidence/PAPER_RUNS.md](evidence/PAPER_RUNS.md).
Re-running the same inputs/configuration/code verifies the same journal and
produces the same result without duplicating orders or ledger entries. Changed
code, calibration, or input produces a new run identity.

For public capture and immutable Parquet output:

```bash
.venv/bin/python -m pip install 'websockets==16.0' 'pyarrow==23.0.1'
.venv/bin/python -m quantos_pm shadow --config configs/shadow_200.toml --seconds 60
```

This is a bounded **single-market** session (default 60 seconds, maximum one
hour), clipped at the chosen market's expiry. Re-run it to discover the next
window. It discovers current/next BTC five-minute metadata, checks CLOB token
mapping and server time, captures REST books and WebSocket snapshots, increments,
trades, tick changes and resolution messages, and runs the same engine on receipt.
The canonical JSONL is flushed during capture and replayed into the audit journal
at completion; an interrupted capture remains replayable. Only stream snapshots
initialize canonical L2: unsequenced REST/WS splicing is not declared synchronized.

The public book stream does not supply exchange sequence numbers. Locally assigned
event IDs are **not** used as proof of exchange continuity. Raw public trade `side`
also does not establish our virtual queue execution. Neither issue is silently
repaired. Stop events, unobservable fills, and skipped opportunities are reported.
A zero-order live capture is data-adapter evidence, not strategy validation.

## Wallet research

```bash
.venv/bin/python -m quantos_pm research \
  --wallet 0x56f609c609975454bf851f0d91079fbc4a5fe251 --max-pages 10000
# Continue a page-limited run, retaining its exact original time window/cursors:
.venv/bin/python -m quantos_pm research --max-pages 10000 --resume artifacts/research/DIRECTORY
```

Reads documented Data API v2 envelopes and opaque cursors to exhaustion, including
`start=1`, maker-inclusive trades, deposits/withdrawals, opt-in tips, and open and
closed positions. Monthly April/May/June queries are independent of the full-walk
page cap. `complete=false`, errors and missing evidence remain explicit.

Maker/taker labels come from documented taker-filter cohort membership with
identical bounds and full walks, never from price. Duplicate/ambiguous records
stay `UNKNOWN`. All transaction hashes are retained with Polygon explorer links. A separate
read-only Polygon command can verify a bounded receipt sample; the full receipt
and token-burn history is not reconstructed. Partial WAC reconstruction
supports binary splits, merges, buys and sells, but aggregate redemption burns
remain unallocated. Profile PnL atoms disclose credited fees/rebates/rewards
separately. Historical completeness and full attribution are different claims.
See [evidence/WALLET_RESEARCH.md](evidence/WALLET_RESEARCH.md).

```bash
.venv/bin/python -m quantos_pm research-chain --directory artifacts/research/DIRECTORY --limit 12
```

This research-only RPC client permits exactly `eth_chainId`,
`eth_getTransactionReceipt` and `eth_getBlockByNumber` at a fixed public Polygon
endpoint. It cannot send a transaction or invoke signing methods. It verifies
canonical block identity, success, API/block timestamp agreement and wallet
involvement, and preserves ERC-20 transfer amounts in raw token units. No token
price/decimal assumption or full cash-flow reconciliation is inferred from a sample.


## Six business boundaries

| Boundary | Ownership |
| --- | --- |
| Market Data | Metadata, timestamp/sequence checks, L2 eligibility |
| Feature | Causal probability bounds, day-block calibration resampling |
| Alpha | HEDGED_MAKER_V0 paired passive bid intent and gross/net hurdle |
| Risk | Final admission, pending fill scenarios, capital/loss limits |
| Execution | Virtual lifecycle, queue, collateral, inventory, merges/redemptions |
| Evaluation | PnL, utilization, drawdown, latency, attribution, block bootstrap |

`engine.py` orchestrates those boundaries. Configuration, CLI, public adapters,
SQLite/Parquet storage and wallet research are outside the business modules.
There are no imports from the original QuantOS repository.

## Economic and execution assumptions

- All money/share arithmetic uses finite Decimal values. Engine/replay run within
  an explicit precision-34 context. Prices and quantities are JSON/TOML strings;
  no binary floats enter trading arithmetic.
- Each market receives at most one paired entry episode. No averaging, leverage,
  automatic minimum-size increase, martingale or wallet copying.
- Actual weighted fill cost determines profit. Two shares bought at 50 cents each
  make zero gross settlement profit; 49 + 49 cents locks 2 cents **only for the
  actually matched quantity**. A losing unmatched leg loses its full cost.
- Risk reserves every pending order, including submission/cancel uncertainty.
  Market/global unmatched caps are 2%/3%; matched collateral caps 10%/20%.
  Day/week/peak loss breakers are 3%/6%/10%. No quotes in the last 60 seconds.
  Existing exposure and all-outstanding-order scenarios are considered jointly.
- The quote size rounds down to six decimal places. A venue minimum larger than
  the allowable size causes a skip, never an upward adjustment.
- A virtual post-only order is checked again on activation against current books,
  tick, minimum and status. Activation is delayed by model-specific latency.
- Price-time queues include visible better prices. Ambiguous depth removal never
  counts as execution. Better-priced new depth adds queue ahead; pessimistic
  mode also reserves ambiguous same-price replenishment. A trade must have verified
  sell aggressor evidence at the exact quote price after activation. Insufficient
  attribution means `unobservable`, including all current live public trades.
- Optimistic/baseline/pessimistic initial queues are 1x/1.5x/2x visible depth;
  submission latency is 1x/2x/3x; pessimistic executable trade volume is halved;
  cancellation latency is 3x in pessimistic mode. These are sensitivity assumptions,
  not fitted venue parameters. Cancellation failures and late trades require
  reconciliation. EOF does not fabricate an acknowledgement.
- Matched sets are virtually merged for $1 collateral per pair immediately.
  Resolution redeems residual winning shares. Gas, relayer and chain finality
  are not actually exercised. No incentive is credited to paper PnL.
- Current documented crypto taker fees are `shares * .07 * p * (1-p)`, rounded to
  five decimals; makers pay zero. The live adapter requires matching verified
  metadata. An observed legacy `mbf=1000` alongside `fd.to=true` is treated as
  unresolved metadata, rather than assuming a valid schedule. See [sources](docs/SOURCES.md).

The supplied calibration and fixture are **synthetic mechanics examples**.
The training timestamps precede replay, probability bounds combine whole-day
bootstrap with a finite-sample penalty, and unmatched inventory can lose its
entire cost. Quotes require positive conservative expected net PnL with an
explicit operational/slippage allowance. Calibration excludes incentives.
Synthetic assumptions are refused in shadow. Bounds on synthetic observations
are not statistical evidence about Polymarket.

## Audit and reporting

`artifacts/runs/RUN_ID/` holds an append-only SQLite journal, manifest, machine
report and summary. SQL update/delete triggers and a SHA-256 chain protect
ordinary audit operations. One writer lock belongs only to this project. Replay
recomputes every event and verifies prior durable effects; it refuses mismatches.
This is integrity checking, not protection against an administrator rewriting
both the database and its checksums.

`artifacts/captures/` holds canonical JSONL and exclusive-create, read-only Parquet
captures with hashes. SQLite SQL is the analytical query engine for V0; DuckDB is
not required. Storage paths are confined to this project, including symlink
resolution. Run reports include the required balances, PnL, fees, incentives,
turnover, orders, partial/missed/unobservable fills, matched/unmatched amounts,
duration, spreads, utilization, drawdown, daily results, stop/skip reasons,
latency and block bootstrap summaries. Matched-set spread is reported once,
not double-counted as separate spread PnL.

Unmatched inventory is marked at fresh bid; missing/stale bids conservatively
mark it at zero. Matched validated pairs retain their $1 value. Liquidity/gas
costs to monetize that value are not empirically validated. Reports expose
pending capital and uncertain execution rather than pretending the run is flat.

## Remaining gates

See [PROMOTION_GATES.md](PROMOTION_GATES.md) and [ISOLATION_AUDIT.md](ISOLATION_AUDIT.md).
The early-loser-exit feature remains disabled. There is no empirical reversal
model: linked oracle/TWAP/reference history and sufficiently independent blocks
have not been acquired. Future work must condition on outcome price, seconds to
expiry, BTC price, oracle/TWAP, volatility and price gap, and compare an upper
reversal-probability bound against net executable exit proceeds. Win rate alone
cannot authorize that strategy.

`enable_live_orders=true` is rejected unconditionally, as are the other three
research flags. V0 has no live implementation that a configuration change can
unlock. Any future live version requires a separate design and explicit owner
approval.
