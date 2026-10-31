# quantos.live

Signed Binance USD-M futures layer (stdlib only, Decimal, fail-closed). Nothing here is wired into the paper engine.

## Modes
- `dry-run`: requests are built and signed-shape logged (JSONL, signature and api key `REDACTED`); the transport is never called; synthetic acks are returned.
- `testnet`: `https://testnet.binancefuture.com`.
- `mainnet`: `https://fapi.binance.com`. The constructor raises unless `allow_mainnet=True` AND `confirm_file` exists containing exactly `I CONFIRM MAINNET QUANTOS`.

## Key file
JSON `{"api_key": "...", "api_secret": "..."}`, path from `QUANTOS_KEY_FILE` or explicit argument. Must be `chmod 600`, a regular file, and outside any git repository. Keys print as `Keys(****)`.

## Orders
Client order ids are deterministic from (run_id, signal_id, leg) so retries are idempotent. Ambiguous POST failures (timeout/5xx/-1007) are never blindly retried: the order is queried by `origClientOrderId` first. `place_with_protection` places entry, then reduce-only/closePosition STOP_MARKET (MARK_PRICE, priceProtect); if the stop fails it closes reduce-only and raises `ProtectionFailed`.

## Kill switch
`KillSwitch(dir)` is active when `dir/KILL` exists (remove manually to clear). `DailyLossGuard` engages it on a 10% daily loss (UTC reset).
`python -m quantos.live kill --state DIR [--flatten --mode testnet|dry-run|mainnet --symbols A,B --key-file F]` engages the switch and optionally cancels all orders and closes every position. Mainnet flatten also needs `--allow-mainnet --confirm-file F`.

## Unverified
Endpoint versions (v2 positionRisk/account) and whether conditional orders (STOP_MARKET/TAKE_PROFIT_MARKET) must now use Binance's algo-order endpoint were not checked against live docs; verify on testnet before mainnet.
