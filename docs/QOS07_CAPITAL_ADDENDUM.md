# QOS-07 owner-authorized retail capital profile

QOS-07 explicitly supersedes the standalone 1% modeled trade-risk cap only for
`retail-full-equity-v1`. HFT and QOS03–06 retain their original policies.
New accounts start at $200. Realized reconciled equity funds up to 1x notional,
reserving full round-trip costs, execution buffers and adverse-entry allowance.
Quantity is limited by venue rules, displayed liquidity and remaining 3% daily
and peak drawdown headroom. No deposit, borrowing, averaging or pyramiding.

Every opening decision requires an absolute protective stop and take-profit
price, plus a positive bounded holding horizon. Modeled loss includes actual
stop distance, round-trip fees/spread/slippage/latency/funding allowances and
20 bps additional adverse-gap reserve. This finite stress assumption is not a
maximum loss guarantee. A larger real gap or insufficient liquidity can exceed
it. Stop exits wait for acknowledgment and later valid executable depth; they
never assume a fill at the stop price. Residual exposure survives shutdown.
Restart cancels stale orders, preserves losses and stops, and requests an exit
on fresh reconciled depth before permitting new entries.

The existing frozen Q05 Alpha is unchanged. Its adapter uses the tighter of
its invalidation boundary and existing 20 bps safety stop, plus its existing
target and horizon. Future Alpha may return a typed ProtectedRetailSignal or
None; economic admission, Risk and Execution remain the sole existing path.
Synthetic test Alpha is never selectable from an operational command.
