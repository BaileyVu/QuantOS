# QOS04 owner-authorized scope

This addendum extends QOS03 for public information, two experimental candidates,
replay and operations only. It reconciles the HFT-only/exchange-only clauses of
000–008 and AGENTS without changing their existing modes. Public external
adapters cannot place orders. The operating account is $200; historic profiles
and evidence are not migrated. No authenticated execution is authorized.

Information events distinguish source UTC, receipt UTC, receipt monotonic,
availability monotonic, provider/instrument/market, provenance, validity and
sequence quality. Decisions use receipt/availability ordering, never reorder by
source time. Regressions, unknown integrity, future source times, data older
than 2 seconds and absent inputs prohibit the dependent candidate. Duplicates
do not replenish features. Reconnect clears information and warmup.

Public Coinbase BTC-USD ticker supplies *relative five-second returns*, not an
assumed USD/stablecoin parity arbitrage. Ticker sequences are monotone snapshots,
not a contiguous full book. Public futures forced-order snapshots are explicitly
sampled: absence is missing evidence, never zero liquidations. No OI feed is
claimed. Adapters retain original market identity.

Protocol references: [Coinbase channels](https://docs.cdp.coinbase.com/exchange/websocket-feed/channels),
[forced-order stream](https://developers.binance.info/docs/derivatives/usds-margined-futures/websocket-market-streams/Liquidation-Order-Streams).

## Fixed experiments and selection

Q04-LL-v1: five-second leader return minus local five-second response. At least
2 bps same-direction residual and leader movement; use the smaller magnitude.
No exchange-level basis trade. Q04-LD-v1: sampled forced quantity at least twice
the target best opposing liquidity, at least 2 bps same-direction five-second
local response. Both require signed flow and OBI >=0.15 in direction, spread
<=10 bps and event volatility <=20 bps. Missing/stale/ambiguous inputs disarm
only the dependent candidate. Both subtract max(2 bps, half observed opportunity)
as uncertainty. These are deliberately uncalibrated fixed hypotheses, not
predicted or proven returns. No parameter search or ML training is authorized.

The largest credible opportunity is selected deterministically (strategy ID
breaks ties), then full Risk admission. At most one position, no addition to an
existing position. Intent expires at 2 seconds, hold at 30 seconds, adverse
stop at 20 bps; reversal, invalid data, emergency/drawdown/daily loss require
cancellation and a conservative exit when valid depth returns. Both candidates
are experimental until independent chronological evidence supports promotion.
QOS03 is retained solely as diagnostic baseline in unified mode.

## Economics and loss controls

Full hurdle: configured taker fees both legs (unchanged approved floors), spread,
>=1bp slippage/leg, measured latency plus acknowledgment, 2bp safety, absolute
public funding-rate reserve, plus candidate uncertainty. Risk budget also
includes 2bp stop execution uncertainty. Quantity floors to exchange step,
caps visible opposing liquidity, risk <=1% and notional <=1x including adverse
price/fee allowance. Equity for new sizing is realized balance. Daily realized
baseline and marked drawdown each stop at 3%; risk-off latches across restart.

At <=1x exposure, collateral/notional is >=100%; this unlevered paper envelope
keeps the 20bp stop plus uncertainty far inside a conservative 100bp maintenance
reserve. This is not an authenticated liquidation-price calculation: exchange
margin tiers and real liquidation distance remain a deployment blocker.

Funding reserves charge the absolute last public rate on positions crossing a
known settlement time, once per time, even where the account might earn funding.
They never fabricate favorable credits. Exit PnL allocates that debit. Missing
funding blocks new entries and requests exposed recovery exits. Actual settled
funding and real fee tier verification require future authorized reconciliation.

## Submission clock correction

Feed receipt time determines causal input availability, not order submission.
The engine samples the actual processing clock again immediately before entry
or exit submission. Ack delay starts at that submission sample. Every clock
sample is captured and consumed identically by replay; expired signals and
queued events preceding submission cannot fill. QOS04 runtime identity v2
isolates this corrected contract from earlier state. A failed/partial capture
without a terminal record remains unusable; it is never repaired silently.
Counterfactual cost stress can take different decision branches. It uses the
latest recorded processing/submission sample for that event for all clock reads,
plus the increased acknowledgment delay; it cannot borrow an earlier clock.
Exact baseline replay still requires consuming every captured clock sample.
