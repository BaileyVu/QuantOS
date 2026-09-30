"""Operator CLI for the autonomous BTCUSDT Futures MVP."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import time

from quantos.application.futures_trader import (
    AutonomousFuturesPaperTrader, run_futures_replay,
)
from quantos.domain.market_data import Candle
from quantos.domain.market_data.futures import parse_usdm_exchange_info
from quantos.infrastructure.binance.futures import BinanceUsdmPublicClient
from quantos.infrastructure.configuration.futures import load_futures_config


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
    paper = operations.add_parser("paper", help="Run live-market paper trading; never submits orders.")
    paper.add_argument("--futures-config", type=Path, default=Path("configs/futures.toml"))
    paper.add_argument("--cycles", type=int, default=0,
                       help="Poll cycles; 0 runs until interrupted.")
    paper.add_argument("--poll-seconds", type=int, default=15)


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


def _read_candles(path: Path) -> tuple[Candle, ...]:
    result = []
    with path.open("r", encoding="ascii") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                result.append(Candle(
                    symbol=row["symbol"], interval=row["interval"],
                    open_time=_time(row["open_time"]), close_time=_time(row["close_time"]),
                    open=Decimal(row["open"]), high=Decimal(row["high"]),
                    low=Decimal(row["low"]), close=Decimal(row["close"]),
                    volume=Decimal(row["volume"]), quote_volume=Decimal(row["quote_volume"]),
                    trade_count=int(row["trade_count"]),
                ))
            except Exception as error:
                raise ValueError(f"invalid candle on line {number}: {error}") from error
    return tuple(result)


def _rules(client: BinanceUsdmPublicClient, path: Path | None):
    if path is None:
        return client.symbol_rules()
    return parse_usdm_exchange_info(json.loads(path.read_text(encoding="utf-8")))


def futures_command(args: argparse.Namespace) -> int:
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
        config = load_futures_config(args.futures_config)
        metrics, _ = run_futures_replay(_read_candles(args.data), config,
                                        _rules(client, args.exchange_info))
        print(_json({"event": "futures_replay", "metrics": metrics}))
        return 0
    if operation == "paper":
        if args.cycles < 0 or args.poll_seconds < 1:
            raise ValueError("invalid live paper polling options")
        config = load_futures_config(args.futures_config)
        trader = AutonomousFuturesPaperTrader(config, client.symbol_rules())
        seen = set()
        cycle = 0
        try:
            while args.cycles == 0 or cycle < args.cycles:
                now = datetime.now(timezone.utc)
                candles = client.klines(limit=max(150, config.lookback + 5))
                new = [c for c in candles if c.close_time < now and c.open_time not in seen]
                for candle in new:
                    trader.on_candle(candle)
                    seen.add(candle.open_time)
                print(_json({"event": "futures_live_paper_cycle", "cycle": cycle + 1,
                             "new_candles": len(new), "metrics": trader.metrics(),
                             "last_decision": trader.decisions[-1] if trader.decisions else None}))
                cycle += 1
                if args.cycles == 0 or cycle < args.cycles:
                    time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            print(_json({"event": "futures_live_paper_stopped", "metrics": trader.metrics()}))
        return 0
    raise ValueError("unknown Futures operation")

