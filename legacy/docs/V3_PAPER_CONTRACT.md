# V3 paper contract — October 26, 2026

The current user request replaces the frozen V1 scope for this implementation.
Historical specifications and research remain evidence, not V3 runtime authority.

Deliver a local Python paper engine using public Binance USD-M Futures data only.
No callable real-order submission or authenticated endpoints in the new engine.
No keys required. No new exchange or asset transfers. Supported USDC perpetuals
only for the two independent $200 accounts p1 (1%) and p5 (5% risk ceiling).
Gross notional <= equity, leverage <=3, one position/account, 10% daily loss and
25% equity drawdown pause. Risk, order-state integration, accounting and final
review remain main-agent responsibilities. Strategies cannot execute orders.

EMA/ATR LIMIT and Donchian/ATR STOP are reference strategies, not verified edges.
Enable both only after deterministic causal tests and full paper lifecycle tests.
E17 stays ineligible until source identity, original timestamps and latency are
verified. Unknown evidence must never become a fill or a profitable result.
Limits need post-intent trade-through and conservative queue/participation;
market/stop execution needs a later fresh executable quote with adverse costs.

Durable intent, fills, protections, independent profile ledgers and recovery are
required. Local supervision and reports must not depend on Codex availability.
Keep synthetic evidence separate from natural observations. Start 72 hours only
after mandatory acceptance; report wall and measured active time separately.
Do not claim profitability, live approval or future elapsed observation time.

Preserve historical source and tests; the new compact `quantos.paper` runtime is
isolated from the retained V1 modules. Read IMPLEMENTATION_CHECKPOINT.md before
resuming. Use at most two lower-cost subagents for independent components.
