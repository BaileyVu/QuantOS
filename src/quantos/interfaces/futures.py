"""Operator CLI for the autonomous BTCUSDT Futures MVP."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import time

from quantos.application.futures_trader import (
    AutonomousFuturesPaperTrader, run_futures_replay,
)
from quantos.domain.market_data import Candle
from quantos.domain.market_data.futures import parse_usdm_exchange_info
from quantos.domain.market_data.timeframes import timeframe_minutes
from quantos.infrastructure.binance.futures import BinanceUsdmPublicClient
from quantos.infrastructure.binance.futures import BinanceFuturesError
from quantos.infrastructure.configuration.futures import load_futures_config
from quantos.infrastructure.storage.futures_paper import (
    FuturesPaperStateError, FuturesPaperStateStore,
)


def add_futures_parser(commands) -> None:
    futures = commands.add_parser("futures", help="Autonomous Binance USD-M Futures MVP.")
    operations = futures.add_subparsers(dest="futures_operation", required=True)
    info = operations.add_parser("exchange-info", help="Validate live BTCUSDT exchange filters.")
    info.add_argument("--output", type=Path)
    download = operations.add_parser("download", help="Download completed BTCUSDT 1m Futures candles.")
    download.add_argument("--start", required=True)
    download.add_argument("--end", required=True)
    download.add_argument("--output", type=Path, required=True)
    replay = operations.add_parser("replay", help="Run accelerated autonomous paper replay.")
    replay.add_argument("--data", type=Path, required=True)
    replay.add_argument("--futures-config", type=Path, default=Path("configs/futures.toml"))
    replay.add_argument("--exchange-info", type=Path)
    replay.add_argument(
        "--start",
        help="Optional inclusive UTC replay-window start for a cached data file.",
    )
    replay.add_argument(
        "--end",
        help="Optional exclusive UTC replay-window end for a cached data file.",
    )
    paper = operations.add_parser("paper", help="Run live-market paper trading; never submits orders.")
    paper.add_argument(
        "--futures-config", type=Path,
        default=Path("configs/futures_100usdt_paper.toml"),
    )
    paper.add_argument(
        "--state-store", type=Path,
        default=Path("artifacts/futures-paper/state.db"),
    )
    paper.add_argument("--cycles", type=int, default=0,
                       help="Poll cycles; 0 runs until interrupted.")
    paper.add_argument("--poll-seconds", type=int, default=15)
    paper.add_argument("--max-staleness-seconds", type=int, default=90)
    paper.add_argument("--max-retries", type=int, default=4)
    paper.add_argument("--retry-base-seconds", type=int, default=2)
    reset = operations.add_parser(
        "paper-reset", help="Explicitly create a new durable paper account."
    )
    reset.add_argument(
        "--futures-config", type=Path,
        default=Path("configs/futures_100usdt_paper.toml"),
    )
    reset.add_argument(
        "--state-store", type=Path,
        default=Path("artifacts/futures-paper/state.db"),
    )
    reset.add_argument("--confirm-new-paper-account", action="store_true")
    from quantos.interfaces.hft import add_hft_parsers
    add_hft_parsers(operations)


def _json(value) -> str:
    def encode(item):
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, datetime):
            return item.isoformat()
        raise TypeError(type(item).__name__)
    return json.dumps(value, default=encode, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def _time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as error:
        raise ValueError("time must be ISO-8601, for example 2026-01-01T00:00:00Z") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("time must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _candle_record(candle: Candle) -> dict:
    return {
        "symbol": candle.symbol, "interval": candle.interval,
        "open_time": candle.open_time.isoformat(), "close_time": candle.close_time.isoformat(),
        "open": str(candle.open), "high": str(candle.high), "low": str(candle.low),
        "close": str(candle.close), "volume": str(candle.volume),
        "quote_volume": str(candle.quote_volume), "trade_count": candle.trade_count,
    }


def _write_candles(path: Path, candles: tuple[Candle, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="ascii", newline="\n") as stream:
        for candle in candles:
            stream.write(_json(_candle_record(candle)) + "\n")


def _read_candles(
    path: Path,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[Candle, ...]:
    result = []
    with path.open("r", encoding="ascii") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                candle = Candle(
                    symbol=row["symbol"], interval=row["interval"],
                    open_time=_time(row["open_time"]), close_time=_time(row["close_time"]),
                    open=Decimal(row["open"]), high=Decimal(row["high"]),
                    low=Decimal(row["low"]), close=Decimal(row["close"]),
                    volume=Decimal(row["volume"]), quote_volume=Decimal(row["quote_volume"]),
                    trade_count=int(row["trade_count"]),
                )
                if start is not None and candle.open_time < start:
                    continue
                if end is not None and candle.open_time >= end:
                    continue
                result.append(candle)
            except Exception as error:
                raise ValueError(f"invalid candle on line {number}: {error}") from error
    return tuple(result)


def _rules(client: BinanceUsdmPublicClient, path: Path | None):
    if path is None:
        return client.symbol_rules()
    return parse_usdm_exchange_info(json.loads(path.read_text(encoding="utf-8")))


class FuturesLiveDataError(RuntimeError):
    pass


def _retry_public_call(operation, max_retries: int, base_seconds: int,
                       sleep=time.sleep):
    retry_count = 0
    while True:
        try:
            return operation(), retry_count
        except BinanceFuturesError as error:
            if not error.retryable or retry_count >= max_retries:
                raise
            delay = min(30, base_seconds * (2 ** retry_count))
            print(_json({
                "event": "futures_public_api_retry",
                "attempt": retry_count + 1,
                "delay_seconds": delay,
                "status_code": error.status_code,
                "error": str(error),
            }))
            retry_count += 1
            sleep(delay)


def _validate_live_batch(candles, previous: Candle | None,
                         now: datetime, max_staleness_seconds: int) -> None:
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise FuturesLiveDataError("runtime clock must be UTC")
    if not candles:
        if previous is None or (
            now - previous.close_time
        ).total_seconds() > max_staleness_seconds:
            raise FuturesLiveDataError("completed market data is stale or missing")
        return
    expected = (
        previous.open_time + timedelta(minutes=1)
        if previous is not None else candles[0].open_time
    )
    for candle in candles:
        if candle.open_time != expected:
            raise FuturesLiveDataError("completed one-minute market data has a gap")
        if candle.close_time >= now:
            raise FuturesLiveDataError("open or future candle reached paper runtime")
        expected += timedelta(minutes=1)
    age = (now - candles[-1].close_time).total_seconds()
    if age < 0 or age > max_staleness_seconds:
        raise FuturesLiveDataError("latest completed market data is stale")


def _position_summary(trader: AutonomousFuturesPaperTrader) -> dict | None:
    position = trader.execution.position
    if position is None:
        return None
    return {
        "direction": position.direction,
        "quantity": position.quantity,
        "entry_price": position.entry_price,
        "active_stop": position.stop,
        "target": position.target,
        "strategy": position.strategy_id,
        "timeframe": position.timeframe,
        "management_state": position.state,
        "bars_held": position.bars_held,
        "mfe": position.mfe_price,
        "mae": position.mae_price,
    }


def _paper_health(trader: AutonomousFuturesPaperTrader, runtime: dict,
                  retry_count: int, healthy: bool, error: str | None = None) -> dict:
    price = trader.last_candle.close if trader.last_candle else None
    equity = (
        trader.execution.marked_equity(price)
        if price is not None else trader.execution.balance
    )
    return {
        "event": "futures_live_paper_health",
        "healthy": healthy,
        "error": error,
        "current_equity": equity,
        "open_position": _position_summary(trader),
        "last_processed_candle": (
            trader.last_candle.close_time if trader.last_candle else None
        ),
        "breaker_active": trader.breaker_state.active,
        "consecutive_losses": trader.breaker_state.consecutive_losses,
        "daily_loss_active": trader.daily_loss_active_day is not None,
        "reconnect_retry_count": retry_count,
        "last_healthy_runtime_timestamp": runtime.get(
            "last_healthy_runtime_timestamp"
        ),
    }


def futures_command(args: argparse.Namespace) -> int:
    if args.futures_operation in {"hft-paper", "hft-paper-reset"}:
        from quantos.interfaces.hft import hft_command
        return hft_command(args)
    client = BinanceUsdmPublicClient()
    operation = args.futures_operation
    if operation == "exchange-info":
        payload = client.exchange_info()
        rules = parse_usdm_exchange_info(payload)
        if args.output is not None:
            args.output.write_text(_json(payload) + "\n", encoding="utf-8")
        print(_json({"event": "futures_exchange_rules", "rules": asdict(rules)}))
        return 0
    if operation == "download":
        start, end = _time(args.start), _time(args.end)
        candles = tuple(c for c in client.historical_klines(start, end)
                        if c.close_time < datetime.now(timezone.utc))
        _write_candles(args.output, candles)
        print(_json({"event": "futures_download", "candles": len(candles),
                     "output": str(args.output), "start": start, "end": end}))
        return 0
    if operation == "replay":
        if (args.start is None) != (args.end is None):
            raise ValueError("replay --start and --end must be supplied together")
        start = _time(args.start) if args.start is not None else None
        end = _time(args.end) if args.end is not None else None
        if start is not None and end is not None and start >= end:
            raise ValueError("replay start must be before end")
        config = load_futures_config(args.futures_config)
        candles = _read_candles(args.data, start, end)
        if not candles:
            raise ValueError("cached data contains no candles in the replay window")
        metrics, _ = run_futures_replay(
            candles, config, _rules(client, args.exchange_info)
        )
        print(_json({
            "event": "futures_replay",
            "data": str(args.data),
            "window": {
                "start": start or candles[0].open_time,
                "end": end or candles[-1].close_time,
                "candles": len(candles),
            },
            "metrics": metrics,
        }))
        return 0
    if operation == "paper-reset":
        if not args.confirm_new_paper_account:
            raise ValueError(
                "paper-reset requires --confirm-new-paper-account"
            )
        config = load_futures_config(args.futures_config)
        rules = client.symbol_rules()
        now = datetime.now(timezone.utc)
        store = FuturesPaperStateStore(args.state_store)
        trader = store.reset(config, rules, now)
        print(_json({
            "event": "futures_live_paper_account_reset",
            "mode": "PAPER MODE",
            "real_order_submission": "DISABLED",
            "state_store": str(args.state_store),
            "starting_equity": trader.execution.balance,
            "enabled_strategies": config.enabled_strategies,
        }))
        return 0
    if operation == "paper":
        if (args.cycles < 0 or args.poll_seconds < 1
                or args.max_staleness_seconds < 1 or args.max_retries < 0
                or args.retry_base_seconds < 1):
            raise ValueError("invalid live paper polling options")
        config = load_futures_config(args.futures_config)
        rules, startup_retries = _retry_public_call(
            client.symbol_rules, args.max_retries, args.retry_base_seconds
        )
        store = FuturesPaperStateStore(args.state_store)
        trader, runtime = store.load(config, rules)
        restored = (
            trader.last_candle is not None
            or trader.execution.position is not None
            or bool(trader.execution.trades)
        )
        print(_json({
            "event": "futures_live_paper_started",
            "mode": "PAPER MODE",
            "real_order_submission": "DISABLED",
            "symbol": config.symbol,
            "configured_starting_equity": config.starting_equity,
            "current_equity": (
                trader.execution.marked_equity(trader.last_candle.close)
                if trader.last_candle else trader.execution.balance
            ),
            "state": "restored" if restored else "new",
            "state_store": str(args.state_store),
            "leverage_ceiling": config.risk.leverage_ceiling,
            "risk_fraction": config.risk.risk_fraction,
            "enabled_strategies": config.enabled_strategies,
            "last_processed_candle": (
                trader.last_candle.close_time if trader.last_candle else None
            ),
            "open_position": _position_summary(trader),
            "startup_retry_count": startup_retries,
        }))
        cycle = 0
        total_retries = int(runtime.get("retry_count", 0))
        try:
            while args.cycles == 0 or cycle < args.cycles:
                now = datetime.now(timezone.utc)
                cycle_retries = 0
                try:
                    server_time, retries = _retry_public_call(
                        client.server_time, args.max_retries,
                        args.retry_base_seconds,
                    )
                    cycle_retries += retries
                    if abs((server_time - now).total_seconds()) > 5:
                        raise FuturesLiveDataError(
                            "local clock differs from Binance by more than 5 seconds"
                        )
                    if trader.last_candle is None:
                        warmup = config.lookback * max(
                            timeframe_minutes(value) for value in config.timeframes
                        )
                        start = now - timedelta(minutes=warmup + 1)
                    else:
                        start = trader.last_candle.open_time + timedelta(minutes=1)
                    candles, retries = _retry_public_call(
                        lambda: client.historical_klines(start, now),
                        args.max_retries, args.retry_base_seconds,
                    )
                    cycle_retries += retries
                    new = tuple(
                        candle for candle in candles
                        if candle.close_time < now and (
                            trader.last_candle is None
                            or candle.open_time > trader.last_candle.open_time
                        )
                    )
                    _validate_live_batch(
                        new, trader.last_candle, now, args.max_staleness_seconds
                    )
                    funding, retries = _retry_public_call(
                        lambda: client.funding_history(start, now),
                        args.max_retries, args.retry_base_seconds,
                    )
                    cycle_retries += retries
                    pending_funding = list(funding)
                    warming_up = trader.last_candle is None
                    for candle in new:
                        for item in pending_funding:
                            position = trader.execution.position
                            if item.timestamp > candle.close_time:
                                break
                            if (position is not None
                                    and position.opened_at < item.timestamp
                                    and item.funding_id
                                    not in trader.execution._funding_ids):
                                trader.execution.apply_funding(
                                    item.rate, item.mark_price, item.timestamp,
                                    item.funding_id,
                                )
                        pending_funding = [
                            item for item in pending_funding
                            if item.timestamp > candle.close_time
                        ]
                        decision = trader.on_candle(
                            candle, allow_new_entries=not warming_up
                        )
                        runtime.update({
                            "last_healthy_runtime_timestamp": now,
                            "last_runtime_timestamp": now,
                            "retry_count": total_retries + cycle_retries,
                        })
                        if not warming_up:
                            store.save(trader, runtime, now)
                        print(_json({
                            "event": "futures_live_paper_decision",
                            "decision": decision,
                        }))
                    total_retries += cycle_retries
                    runtime.update({
                        "last_healthy_runtime_timestamp": now,
                        "last_runtime_timestamp": now,
                        "retry_count": total_retries,
                    })
                    if warming_up or not new:
                        store.save(trader, runtime, now)
                    if warming_up:
                        print(_json({
                            "event": "futures_live_paper_warmup_complete",
                            "candles": len(new),
                            "entries_enabled": False,
                            "last_processed_candle": (
                                trader.last_candle.close_time
                                if trader.last_candle else None
                            ),
                        }))
                    print(_json(_paper_health(
                        trader, runtime, total_retries, True
                    )))
                    print(_json({
                        "event": "futures_live_paper_cycle",
                        "cycle": cycle + 1,
                        "new_candles": len(new),
                        "metrics": trader.metrics(),
                        "last_decision": (
                            trader.decisions[-1] if trader.decisions else None
                        ),
                    }))
                except (BinanceFuturesError, FuturesLiveDataError) as error:
                    total_retries += cycle_retries
                    runtime.update({
                        "last_runtime_timestamp": now,
                        "retry_count": total_retries,
                    })
                    store.save(trader, runtime, now)
                    print(_json(_paper_health(
                        trader, runtime, total_retries, False, str(error)
                    )))
                cycle += 1
                if args.cycles == 0 or cycle < args.cycles:
                    time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            now = datetime.now(timezone.utc)
            runtime["last_runtime_timestamp"] = now
            store.save(trader, runtime, now)
            print(_json({
                "event": "futures_live_paper_stopped",
                "metrics": trader.metrics(),
            }))
        return 0
    raise ValueError("unknown Futures operation")

