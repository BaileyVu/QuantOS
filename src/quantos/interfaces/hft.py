"""CLI composition for event-driven HFT paper mode."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

from quantos.application.hft_trader import (
    hft_state_identity,
    run_hft_paper_session,
    select_hft_contract,
)
from quantos.infrastructure.binance.hft import BinanceHftPublicClient
from quantos.infrastructure.binance.hft import BinanceHftStream
from quantos.infrastructure.configuration.hft import load_hft_config
from quantos.infrastructure.storage.hft_events import HftEventRecorder
from quantos.infrastructure.storage.hft_paper import HftPaperStateStore


def add_hft_parsers(operations) -> None:
    reset = operations.add_parser(
        "hft-paper-reset",
        help="Explicitly create a separate queue-aware HFT paper account.",
    )
    _common(reset)
    reset.add_argument("--confirm-new-paper-account", action="store_true")

    paper = operations.add_parser(
        "hft-paper",
        help="Run event-driven public-data HFT paper mode; real orders impossible.",
    )
    _common(paper)
    paper.add_argument(
        "--cycles", type=int, default=0,
        help="Maximum processed events; 0 runs continuously.",
    )
    paper.add_argument(
        "--duration-seconds", type=int, default=0,
        help="Wall-clock limit; 0 runs continuously.",
    )


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--hft-config",
        type=Path,
        default=Path("configs/futures_hft_paper.toml"),
    )
    parser.add_argument(
        "--state-store",
        type=Path,
        default=Path("artifacts/futures-paper/hft-btc-paper.db"),
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("artifacts/futures-hft-data"),
    )


def _json(value: object) -> str:
    def encode(item):
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, datetime):
            return item.isoformat()
        if hasattr(item, "value"):
            return item.value
        raise TypeError(type(item).__name__)
    return json.dumps(
        value, default=encode, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    )


def hft_command(args: argparse.Namespace) -> int:
    config = load_hft_config(args.hft_config)
    client = BinanceHftPublicClient()
    rules = select_hft_contract(client.exchange_info(), config)
    fees = config.fees[rules.symbol]
    identity = hft_state_identity(config, rules)
    store = HftPaperStateStore(args.state_store)
    now = datetime.now(timezone.utc)
    if args.futures_operation == "hft-paper-reset":
        if not args.confirm_new_paper_account:
            raise ValueError(
                "hft-paper-reset requires --confirm-new-paper-account"
            )
        account, _, _ = store.reset(
            identity, rules.symbol, config.starting_equity, fees, now
        )
        print(_json({
            "event": "hft_paper_account_reset",
            "mode": "PAPER MODE",
            "real_order_submission": "IMPOSSIBLE",
            "symbol": rules.symbol,
            "state_store": str(args.state_store),
            "starting_equity": account.starting_equity,
            "maker_fee_rate": fees.maker_rate,
            "taker_fee_rate": fees.taker_rate,
            "rules": asdict(rules),
        }))
        return 0
    if args.futures_operation != "hft-paper":
        raise ValueError("unknown HFT operation")
    if args.cycles < 0 or args.duration_seconds < 0:
        raise ValueError("cycles and duration-seconds must be non-negative")
    account, evaluation, runtime = store.load(identity, fees)
    print(_json({
        "event": "hft_paper_started",
        "mode": "PAPER MODE",
        "real_order_submission": "IMPOSSIBLE",
        "symbol": rules.symbol,
        "state_store": str(args.state_store),
        "data_root": str(args.data_root),
        "starting_equity": account.starting_equity,
        "current_balance": account.balance,
        "maker_fee_rate": fees.maker_rate,
        "maker_fee_bps": fees.maker_rate * Decimal("10000"),
        "taker_fee_rate": fees.taker_rate,
        "taker_fee_bps": fees.taker_rate * Decimal("10000"),
        "maximum_leverage": config.risk.maximum_leverage,
        "maximum_account_loss_fraction": (
            config.risk.maximum_account_loss_fraction
        ),
    }))

    def emit(payload: dict) -> None:
        if payload.get("event") not in {"book_ticker", "aggregate_trade", "hft_hold"}:
            print(_json(payload))

    try:
        session_id = now.strftime("%Y%m%dT%H%M%S.%fZ") + "-" + rules.symbol
        metrics = asyncio.run(run_hft_paper_session(
            config=config,
            rules=rules,
            account=account,
            evaluation=evaluation,
            state_store=store,
            identity=identity,
            runtime=runtime,
            cycles=args.cycles,
            duration_seconds=args.duration_seconds,
            client=client,
            stream=BinanceHftStream(),
            recorder=HftEventRecorder(
                args.data_root, session_id, config.checkpoint_events
            ),
            emit=emit,
        ))
    except KeyboardInterrupt:
        metrics = evaluation.report(account, Decimal("0"))
    print(_json({
        "event": "hft_paper_stopped",
        "real_order_submission": "IMPOSSIBLE",
        "metrics": metrics,
    }))
    return 0
