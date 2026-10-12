"""Explicit public-only CLI for experimental retail directional paper mode."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from quantos.application.hft_trader import select_hft_contract
from quantos.application.retail_trader import run_retail_paper_session
from quantos.domain.evaluation.retail import RetailEvaluation
from quantos.domain.risk.retail import RetailRiskPolicy
from quantos.infrastructure.binance.hft import BinanceHftPublicClient, BinanceHftStream
from quantos.infrastructure.configuration.hft import load_hft_config
from quantos.infrastructure.storage.retail_paper import RetailPaperStateStore


def add_retail_parsers(operations) -> None:
    reset = operations.add_parser(
        "retail-paper-reset", help="Create a separate 200-unit retail paper account."
    )
    paper = operations.add_parser(
        "retail-paper", help="Run bounded public-feed directional paper mode."
    )
    for parser in (reset, paper):
        parser.add_argument(
            "--market-config", type=Path,
            default=Path("configs/futures_hft_200_paper.toml"),
        )
        parser.add_argument("--state-store", type=Path, required=True)
    reset.add_argument("--confirm-new-paper-account", action="store_true")
    paper.add_argument("--duration-seconds", type=int, required=True)


def _json(payload: dict) -> str:
    def encode(value):
        if is_dataclass(value) and not isinstance(value, type):
            return asdict(value)
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if hasattr(value, "value"):
            return value.value
        raise TypeError(type(value).__name__)
    return json.dumps(payload, default=encode, sort_keys=True, separators=(",", ":"))


def retail_command(args: argparse.Namespace) -> int:
    config = load_hft_config(args.market_config)
    if config.starting_equity != Decimal("200") or config.research_only:
        raise ValueError("retail paper requires the operational 200 capital profile")
    policy = RetailRiskPolicy()
    client = BinanceHftPublicClient(
        request_timeout=config.snapshot_timeout.total_seconds()
    )
    rules = select_hft_contract(client.exchange_info(), config)
    if config.fees["BTCUSDC"].taker_rate < Decimal("0.0004") or (
        config.fees["BTCUSDT"].taker_rate < Decimal("0.0005")
    ):
        raise ValueError("retail paper taker fee below approved cost floor")
    identity = sha256(repr((config, policy, rules)).encode("utf-8")).hexdigest()
    store = RetailPaperStateStore(args.state_store)
    fees = config.fees[rules.symbol]
    if args.futures_operation == "retail-paper-reset":
        if not args.confirm_new_paper_account:
            raise ValueError("retail-paper-reset requires confirmation")
        account = store.reset(identity, fees, policy)
        print(_json({
            "event": "retail_paper_account_reset", "mode": "PAPER MODE",
            "real_order_submission": "IMPOSSIBLE", "symbol": rules.symbol,
            "starting_equity": account.starting_equity,
            "state_store": str(args.state_store),
            "taker_fee_rate": fees.taker_rate,
        }))
        return 0
    if args.duration_seconds < 1 or args.duration_seconds > 600:
        raise ValueError("retail paper duration must be within 1..600 seconds")
    account, _ = store.load(identity, fees, policy)
    evaluation = RetailEvaluation()
    print(_json({
        "event": "retail_paper_started", "mode": "PAPER MODE",
        "real_order_submission": "IMPOSSIBLE", "symbol": rules.symbol,
        "starting_equity": account.starting_equity,
        "balance": account.balance, "taker_fee_rate": fees.taker_rate,
        "risk_policy": asdict(policy),
        "state_store": str(args.state_store),
    }), flush=True)

    def emit(payload: dict) -> None:
        if payload.get("event") not in {"mark_price", "websocket_timeout"}:
            print(_json(payload), flush=True)

    metrics = asyncio.run(run_retail_paper_session(
        config=config, rules=rules, account=account, evaluation=evaluation,
        policy=policy, store=store, identity=identity,
        duration_seconds=args.duration_seconds, client=client,
        emit=emit,
        stream_factory=lambda: BinanceHftStream(
            open_timeout=config.websocket_open_timeout.total_seconds(),
            read_timeout=config.websocket_read_timeout.total_seconds(),
            shutdown_timeout=config.shutdown_timeout.total_seconds(),
            lifecycle=emit,
        ),
    ))
    print(_json({"event": "retail_paper_stopped", "metrics": metrics,
                 "real_order_submission": "IMPOSSIBLE"}), flush=True)
    return 0
