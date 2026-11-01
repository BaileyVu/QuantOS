# Primary sources and verification notes

Read during implementation on 2026-10-18 UTC (2026-10-18/19 Asia/Ho_Chi_Minh).

- https://docs.polymarket.com/trading/fees — current maker/taker schedule,
  crypto price curve and five-decimal rounding. The synthetic fixture uses the
  documented current rate, not a claim about historical April–June fees.
- https://docs.polymarket.com/market-data/realtime-data — public market WebSocket,
  PING/PONG heartbeat, book/delta/trade/tick/lifecycle message shapes.
- https://docs.polymarket.com/api-reference/wss/market — captured in market-stream.md.
- https://docs.polymarket.com/api-reference/markets/get-clob-market-info — compact
  CLOB metadata including tokens, minimum size/tick and fee details.
- https://docs.polymarket.com/migrate/data-api-v1-to-v2 — v2 envelopes,
  snake_case fields, lifecycle positions and opaque cursor pagination.
- https://data-api.polymarket.com/v2/openapi.json — captured API schema;
  full-history start=1, deposits/withdrawals opt-in, TIP opt-in, taker-only filter,
  position filters, credited cumulative PnL atoms and nullable economics.
- https://polymarket.com/profile/0x56f609c609975454bf851f0d91079fbc4a5fe251 —
  owner-provided profile; browser fetch unavailable. Research uses public Data API.

Public production hosts actually called: gamma-api.polymarket.com,
clob.polymarket.com, ws-subscriptions-clob.polymarket.com,
data-api.polymarket.com. No authenticated route was used.

Observed mainnet metadata included a 60-second Chainlink BTC TWAP settlement
rule and `fd={r:0.07,e:1,to:true}` with legacy `mbf=1000`. The adapter preserves
the raw payload and treats the fee/rule combination as unverified. No real
orders or executable fee assumptions are based on an unresolved field conflict.


Supplemental receipt evidence:

- https://polygon.publicnode.com/ — provider's public Polygon RPC endpoint.
- https://docs.polymarket.com/resources/contracts — Polygon mainnet chain ID 137.
- https://eips.ethereum.org/EIPS/eip-20 — Transfer event layout. Receipt reports
  retain raw integer units and do not assume USD identity or token decimals.

The independent research RPC client uses only chain ID, receipt and block reads.
It is not imported by the paper or shadow execution path.
