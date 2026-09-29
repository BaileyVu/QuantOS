# V1-T4R authenticated Testnet validation - 2026-09-29

Execution infrastructure only. Alpha: `NO_GO_ALPHA`. Mainnet: `LOCKED / NOT_APPROVED`.
No strategy research or holdout performance was rerun.

## Actual authenticated Binance Spot Testnet evidence

Exact origin: https://testnet.binance.vision. Credentials loaded only through the
Infrastructure loader from QUANTOS_TESTNET_ENV_FILE. No credential values,
signatures, signed queries or raw secret file contents are recorded.
Account preflight: canTrade=true, fake USDT=10000, BTC=1, ETH=1, BNB=1; no locks.
BTCUSDT: TRADING, Spot enabled. LOT_SIZE min/step 0.00001 BTC, max 9000 BTC;
PRICE_FILTER min/tick 0.01 USDT, max 1000000; NOTIONAL min 5, max 9000000 USDT.
PERCENT_PRICE_BY_SIDE: bid 0.5-1.2, ask 0.8-2, average window 5 minutes.
Both signed /api/v3/order/test calls returned HTTP 200 before their economic orders.
Both LIMIT IOC intents passed operator-authorized synthetic Alpha -> Risk -> Execution.

### BUY

- QuantOS request: `c2e15ff7bbcf505f84a5d75a04b89ac8943e69d2184eae877d1b8b498e96bc82`
- Client order: `qos-4e3bb13a05967102961099b23b4b2b53`
- Binance Testnet order: `7613956`; status: FILLED
- Requested/executed BTC: 0.00011 / 0.00011
- Weighted fill price: 84014.36 USDT
- Quote quantity: 9.2415796 USDT
- Commission: 0 BTC
- Independent order/fill reconciliation: MATCH

### SELL

- QuantOS request: `2b8e53528d5e58b94b8d3eed0a041f3b391031bfaded51e366e86bad952b8f1e`
- Client order: `qos-35b2b41c1e4d723b427bddf98e47c3d6`
- Binance Testnet order: `7614087`; status: FILLED
- Requested/executed BTC: 0.00011 / 0.00011
- Weighted fill price: 84025.33 USDT
- Quote quantity: 9.2427863 USDT
- Commission: 0 USDT
- Independent order/fill reconciliation: MATCH

BUY account: 9990.7584204 USDT, 1.00011 BTC. Final: 10000.0012067 USDT,
1 BTC; all other baseline assets unchanged, no locked balance or open orders.
Fake allocation: 20.0012067 USDT, zero BTC, no positions or test-created dust.
This is not strategy profitability evidence.

A fresh Python process restored durable state and reconciled actual orders, fills
and all account asset totals before replay. Both original intents were replayed:
zero economic POSTs; independent BTCUSDT order IDs before/after were exactly
[7613956, 7614087].

## Controlled UNKNOWN exercise with actual authenticated queries

A separate forensic ledger retained actual genesis/prepared intents, omitted the
SELL observation and recorded UNKNOWN to model a lost acknowledgement.
The authoritative operational ledger was not changed. Recovery queried the
existing Testnet SELL and fills and reproduced the flat final state with zero
economic POSTs. The fault was injected locally; no natural Binance timeout occurred.

## Offline fixture and unit/integration evidence

The existing T4R suite separately tests timeout after fill, not-found UNKNOWN,
partial fills, commissions, corrupt ledgers, concurrent writers, Risk rejection
and Mainnet refusal. New tests cover strict credential parsing and signed GET/POST
rejection outside the Testnet origin, without transport calls. These fixtures
are separate from the actual authenticated results above.

Sanitized local evidence: artifacts/t4r/authenticated-track-b/ (ignored), including
preflight/buy/sell/recover/duplicate/unknown JSON, validation script and test logs.
Authoritative ledger: G:/QuantOS-Data/t4r/testnet/execution.jsonl.
Generated operational evidence and secrets are excluded from Git. All actual
network calls in this validation used the exact Testnet origin; zero Mainnet requests.

## Code validation

Targeted suite: 36 tests passed. Initial full suite: 1025 tests, with only
the unchanged production-preservation assertion failing because src differed
from HEAD. This met the operator's conditional authorization for a separate
reviewed T4R commit; the full suite is rerun afterward. CLI help/import and
compileall checks passed. No lint or type-check command is configured.
The existing CRLF files require `git -c core.whitespace=cr-at-eol diff --check`;
that check passed without rewriting frozen specifications.
