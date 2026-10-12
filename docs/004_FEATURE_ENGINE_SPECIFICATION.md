# QuantOS HFT V1 Feature Engine Specification

Version: 3.0.0-V1
Status: Authorized

Features are deterministic, causal, explainable, and calculated only after a
valid sequenced book event. Required values are mid price, spread, best bid/ask
quantity, static OBI, rolling standardized OBI, VAMP at configured levels or
notional depth, microprice, signed aggregate-trade flow, short realized
volatility, inventory, exchange-to-receive latency, processing latency, and
event age.

For paired bid/ask levels, VAMP cross-weights bid prices by ask quantity and ask
prices by bid quantity over the selected depth, divided by total paired
quantity. Microprice cross-weights best prices by opposite best quantity.
Static OBI is (bid quantity - ask quantity) / (bid quantity + ask quantity).
Standardized OBI uses only prior/current observations in a bounded rolling
window and is zero until a nonzero variance exists.

`alpha_bps = (vamp_price - mid_price) / mid_price * 10_000`.

Signed trade flow is positive for buyer-aggressor quantity and negative for
seller-aggressor quantity over configured short windows. Realized volatility
uses causal mid-price returns over configured seconds.

Invalid denominators, insufficient depth, nonpositive values, a stale/invalid
book, or missing required lookback make the event ineligible for quoting. No
one-minute indicator may generate HFT Alpha. Retained candle features remain
isolated to the comparison runtime.
