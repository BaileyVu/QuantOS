# QuantOS HFT V1 — Source of Truth

Version: 3.0.0-V1
Status: Authorized V1 specification
Last Updated: 2026-10-01

## Mission

QuantOS V1 is a local, event-driven Binance USDⓈ-M Futures paper-trading
system. Its primary production research and paper candidate is directional,
maker-first quoting driven by Volume Adjusted Mid Price (VAMP), order-book
imbalance, and trade flow. Runtime trading contains no LLM calls.

The primary contract is BTCUSDC perpetual when current exchange metadata makes
it eligible. BTCUSDT perpetual is the explicit fallback and comparison
contract. Fees are configured per contract and displayed at startup; no fee or
rebate is inferred from historical schedules.

The former completed-candle Futures V1 remains available for comparison, but it
is superseded as the authoritative production alpha path.

## Authorized scope

- Binance USDⓈ-M public WebSocket depth, bookTicker, aggregate-trade, mark-price,
  and funding streams
- REST depth bootstrap followed by exact sequenced L2 reconstruction
- event-driven and sub-minute market-data, feature, Alpha, Risk, and paper
  Execution processing
- VAMP, static and standardized order-book imbalance, microprice, signed trade
  flow, short realized volatility, inventory, event age, and latency features
- directional post-only/GTX quoting, cancellation/requote, conservative
  queue-position simulation, partial fills, maker-first exits, and safety-only
  taker exits
- immutable replayable HFT data, durable HFT paper state, latency and adverse
  markout evaluation, and hftbacktest-compatible research export
- a separate `quantos futures hft-paper` runtime that does not interrupt the
  retained candle Futures runtime

## Capital and execution boundary

Initial HFT paper equity is 100 USDC or USDT-equivalent. Maximum leverage is
5x. One symbol and one bounded directional inventory are allowed at a time.
There is no martingale, averaging down, pyramiding, grid accumulation, or
loss-driven leverage increase. Normal inventory risk may not exceed 1% of
account equity.

HFT V1 is paper-only. No component in the HFT path may submit a real Binance
order. Risk evaluates every exposure before Execution and rejection is final.
Only Execution may create, cancel, fill, or close simulated orders.

## Fail-closed market state

The local book becomes ineligible for quoting on a sequence gap, crossed book,
invalid price or quantity, stale stream, reconnect ambiguity, or snapshot
bootstrap failure. Working simulated orders are cancelled, no new inventory is
opened, and a fresh snapshot plus buffered-delta synchronization is required.
Restart never restores a stale quote into the market.

Touching a passive quote is not a fill. A fill requires conservative evidence
that visible queue ahead was consumed. Uncertain queue attribution resolves to
NO_FILL.

## Economic and lifecycle authority

Every quote must clear an explicit basis-point hurdle containing maker entry,
expected maker exit, emergency taker cost, spread/adverse-selection allowance,
funding when relevant, and a safety buffer. Retail costs must never be weakened
to manufacture activity.

Research → HFT event replay → walk-forward/robustness validation → live-market
paper → authenticated testnet → mainnet shadow → explicit live approval.

No automated result grants order authority. Priorities remain capital
preservation, correctness, robustness, simplicity, explainability, performance,
then profitability.

## Migration note

On 2026-10-01 the project owner replaced the completed-candle-only Futures
direction with QuantOS HFT V1 because external microstructure research selected
VAMP/order-flow directional maker quoting as the primary candidate. Existing
candle code and results are preserved for comparison; obsolete prohibitions on
HFT, BTCUSDC, L2/order flow, maker quoting, event-driven data, and queue-aware
execution are removed from authoritative specifications 000–008.
