# QuantOS HFT V1 Risk and Execution Specification

Version: 3.0.0-V1
Status: Authorized

Risk runs before every new exposure or exposure increase; rejection is final.
It validates a current uncrossed book, event age, exchange rules, configured
fees, quote economics, post-only price, quantity/notional, leverage, bounded
inventory, mark-to-market loss, daily loss, consecutive adverse fills, and kill
switches.

The quote hurdle is expressed in basis points and includes maker entry,
expected maker exit, configured emergency taker fee, current spread/adverse
selection allowance, funding when applicable, and safety buffer. Expected
Alpha must strictly exceed the applicable expected round-trip hurdle. BTCUSDC
and BTCUSDT have independent explicit maker/taker fee settings.

Initial order quantity is the smallest exchange-valid quantity satisfying
minimum notional, capped by maximum quantity, inventory notional, and 5x
leverage. Normal worst-case inventory loss may not exceed 1% of current equity.
No risk calculation may assume a maker rebate unless configuration explicitly
contains one.

Execution supports one working directional entry quote and one bounded
directional inventory. Simulated orders carry GTX/post-only intent and are
rejected if they would cross. Placement records price, visible quantity ahead,
submission time, book identity, and acknowledgement latency.

A passive fill occurs only after conservative queue-ahead consumption from
subsequent trades at the quote price and unambiguously attributable depth
reductions. Ambiguous cancellation/trade overlap does not improve queue
position. Partial fills are allowed and exact Decimal accounting applies.

Normal exits are opposite-side post-only orders. Taker exits are allowed only
for maximum account-risk breach, severe Alpha reversal, stale/broken data while
exposed, kill switch, or liquidation safety. Maker/taker fees and exits are
recorded separately.

On invalid/stale market state Execution cancels simulated working quotes and
opens no inventory. Restart restores account, fills, inventory, and breakers
but cancels persisted working quotes and requires a freshly reconstructed book.
Corrupt, incompatible, or ambiguous state fails closed and is never silently
reset.

The HFT runtime has no authenticated order adapter and cannot submit real
orders. Future testnet/mainnet capabilities require separate explicit approval.
