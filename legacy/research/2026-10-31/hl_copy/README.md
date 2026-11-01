# HL copy-trading paper experiment

Purpose: forward-only paper test of whether copying selected Hyperliquid (HL) wallets, priced on Binance USD-M
book prices after a 0/30/60 s delay, keeps any edge after fees and slippage. No API keys, no orders, public
read-only endpoints only (host allowlist: api.hyperliquid.xyz, stats-data.hyperliquid.xyz, fapi.binance.com).

## Frozen wallets (wallets_frozen.json, frozen 2026-10-31T16:13Z, never re-selected)
60 candidates screened (top 30d PnL, month>0, allTime>0, account value >= $50k; leaderboard has no 90d window,
so 90d net PnL is computed from fills). 12 pass: >=30 closing fills, top-2 trades <50% of PnL, median hold >15 min
(size-weighted average-entry age at each reducing fill), fills/day <300, mean gross position >20% of account,
>=70% of notional in coins with a TRADING Binance perp. Full metrics and reject reasons are in the JSON.

## Copier rules
Entry on wallet OPEN/FLIP (detected by 30 s polling; delays count from detection) in three books (delay 0/30/60 s),
price = bookTicker ask (long) / bid (short) +/-0.05% slippage, taker 0.04%/side. $50 equity per book, 2% risk,
stop 2.5 x ATR(1h,14), notional <= 3x equity, Binance min notional respected (skips logged), max 5 open per book.
Exit on wallet CLOSE/FLIP (same delay), stop (checked every 60 s), or 7 days. Prefers USDC perps, else USDT.

## Run (continuously; not installed as a service)
    cd ~/Desktop/PythonProject/edge_data/hl_copy && ~/Desktop/PythonProject/QuantOS/.venv/bin/python recorder.py --state state
Use a fresh `state/` (a 3-minute test run is kept in `state_test_3min/`). Safe to stop with SIGTERM/SIGINT and restart
(state resumes). Outputs: events.jsonl, books_{0,30,60}.json, summary.json (copy PnL vs wallets' realized closedPnl net
of fees since start), heartbeat.json. Tests: `python -m unittest test_hl_copy`.

Results need about 7 days (exit horizon) of running before any conclusion; fewer than ~30 closed copy trades per book is noise.
