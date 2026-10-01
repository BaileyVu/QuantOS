"""CLI composition for event-driven HFT paper mode."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
import platform
from pathlib import Path
import time

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


AUTH_ENVIRONMENTS = ("TESTNET", "MAINNET")


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

    preflight = operations.add_parser(
        "hft-auth-preflight",
        help="Authenticated account/config/commission and non-executing test-order preflight.",
    )
    _authenticated_common(preflight)
    preflight.add_argument("--confirm-new-auth-state", action="store_true")

    execute = operations.add_parser(
        "hft-execute",
        help="Authenticated TESTNET/explicitly-armed MAINNET HFT execution runtime.",
    )
    _authenticated_common(execute)
    execute.add_argument("--confirm-new-auth-state", action="store_true")
    execute.add_argument("--confirm-rearm")
    execute.add_argument("--duration-seconds", type=int, default=0)
    execute.add_argument("--protocol-smoke", action="store_true")
    execute.add_argument("--enable-mainnet", action="store_true")
    execute.add_argument("--mainnet-minimum-size", action="store_true")

    kill = operations.add_parser(
        "hft-kill", help="Persistently disable authenticated HFT and optionally flatten."
    )
    _authenticated_common(kill)
    kill.add_argument("--flatten", action="store_true")

    rearm = operations.add_parser(
        "hft-rearm", help="Explicitly re-arm a disabled authenticated HFT state."
    )
    _authenticated_common(rearm)
    rearm.add_argument("--confirm-rearm", required=True)


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


def _authenticated_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--environment", choices=AUTH_ENVIRONMENTS, required=True
    )
    parser.add_argument(
        "--hft-config", type=Path,
        default=Path("configs/futures_hft_paper.toml"),
    )
    parser.add_argument("--state-store", type=Path, required=True)
    parser.add_argument("--allow-testnet-btcusdt", action="store_true")


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
    if args.futures_operation in {
        "hft-auth-preflight", "hft-execute", "hft-kill", "hft-rearm",
    }:
        return _authenticated_command(args)
    config = load_hft_config(args.hft_config)
    client = BinanceHftPublicClient(
        request_timeout=config.snapshot_timeout.total_seconds()
    )
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
        if payload.get("event") not in {
            "book_ticker",
            "aggregate_trade",
            "hft_hold",
            "quote_resting",
            "risk_rejected",
        }:
            print(_json(payload))

    wall_clock = time.get_clock_info("time")
    monotonic_clock = time.get_clock_info("monotonic")
    emit({
        "event": "local_clock_diagnostics",
        "platform": platform.platform(),
        "utc_now": datetime.now(timezone.utc),
        "local_timezone": datetime.now().astimezone().tzname(),
        "wall_clock": {
            "implementation": wall_clock.implementation,
            "resolution_seconds": wall_clock.resolution,
            "adjustable": wall_clock.adjustable,
            "monotonic": wall_clock.monotonic,
        },
        "monotonic_clock": {
            "implementation": monotonic_clock.implementation,
            "resolution_seconds": monotonic_clock.resolution,
            "adjustable": monotonic_clock.adjustable,
            "monotonic": monotonic_clock.monotonic,
        },
        "clock_domains": {
            "exchange_event": "UTC epoch milliseconds from Binance E",
            "exchange_transaction": "UTC epoch milliseconds from Binance T when applicable",
            "local_receive_wall": "timezone-aware UTC wall clock",
            "local_receive_monotonic": "monotonic_ns",
            "processing": "monotonic",
            "simulated_order_send": "monotonic",
            "simulated_acknowledgement": "monotonic",
        },
        "system_clock_mutation_required": False,
    })

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
            stream_factory=lambda: BinanceHftStream(
                open_timeout=config.websocket_open_timeout.total_seconds(),
                read_timeout=config.websocket_read_timeout.total_seconds(),
                shutdown_timeout=config.shutdown_timeout.total_seconds(),
                lifecycle=emit,
            ),
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


def _auth_identity(config: object, environment: object) -> str:
    payload = json.dumps({
        "config": repr(config),
        "environment": environment.value,
        "version": "hft-authenticated-v1",
    }, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _authenticated_components(args: argparse.Namespace):
    from quantos.domain.execution.hft_authenticated import HftExecutionEnvironment
    from quantos.infrastructure.binance.hft_authenticated import (
        BinanceCredentials,
        BinanceUsdmAuthenticatedClient,
    )
    from quantos.infrastructure.storage.hft_authenticated import (
        HftAuthenticatedStateStore,
    )
    config = load_hft_config(args.hft_config)
    environment = HftExecutionEnvironment(args.environment)
    credentials = BinanceCredentials.from_environment()
    client = BinanceUsdmAuthenticatedClient(
        environment,
        credentials,
        request_timeout=config.snapshot_timeout.total_seconds(),
    )
    store = HftAuthenticatedStateStore(
        args.state_store, environment, _auth_identity(config, environment)
    )
    return config, environment, client, store


async def _authenticated_runtime(
    args: argparse.Namespace,
    environment: object,
    client: object,
    store: object,
    execution: object,
    rules: object,
) -> None:
    from quantos.application.hft_authenticated import (
        apply_user_data_event,
        authenticated_heartbeat,
        minimum_gtx_order,
        reconcile_authenticated_state,
    )
    from quantos.domain.alpha.hft import HftSide
    from quantos.domain.execution.hft_authenticated import HftReadiness
    from quantos.infrastructure.binance.hft_authenticated import (
        BinanceHftUserDataStream,
    )
    ready = asyncio.Event()

    def lifecycle(payload: dict) -> None:
        print(_json(payload))
        if payload.get("event") == "user_data_ready":
            ready.set()

    user_stream = BinanceHftUserDataStream(client, lifecycle=lifecycle)

    async def consume() -> None:
        async for payload in user_stream.events():
            print(_json(apply_user_data_event(payload, store)))

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(ready.wait(), timeout=10)
        execution.set_readiness(HftReadiness(
            authenticated_preflight=True,
            account_reconciled=True,
            clock_healthy=True,
            market_data_ready=True,
            user_data_ready=True,
            risk_ready=True,
            state_ready=True,
        ))
        if args.protocol_smoke:
            if environment.value != "TESTNET":
                raise ValueError("--protocol-smoke is TESTNET-only")
            ticker = await asyncio.to_thread(client.book_ticker, rules.symbol)
            price, quantity = minimum_gtx_order(rules, ticker)
            order = await asyncio.to_thread(
                execution.submit_testnet_protocol_quote,
                HftSide.BUY, price, quantity,
            )
            print(_json({
                "event": "hft_testnet_gtx_submitted",
                "client_order_id": order.client_order_id,
                "symbol": rules.symbol,
                "transport_only": True,
            }))
            cancelled = await asyncio.to_thread(execution.cancel_owned_orders)
            print(_json({
                "event": "hft_testnet_gtx_cancelled",
                "client_order_ids": cancelled,
            }))
            try:
                reconciliation = await asyncio.to_thread(
                    reconcile_authenticated_state,
                    client, store, symbol=rules.symbol,
                )
            except BaseException:
                critical = await asyncio.to_thread(
                    execution.emergency_flatten,
                    "testnet_protocol_reconciliation_failure",
                )
                print(_json({"event": "CRITICAL", "flatten": critical}))
                raise
            print(_json({"event": "hft_authenticated_reconciled", **reconciliation}))
        started = time.monotonic()
        next_heartbeat = started
        while args.duration_seconds == 0 or time.monotonic() - started < args.duration_seconds:
            if consumer.done():
                error = consumer.exception()
                raise RuntimeError("user-data stream stopped") from error
            now = time.monotonic()
            if now >= next_heartbeat:
                print(_json(authenticated_heartbeat(
                    client, store, environment=environment,
                    user_stream=user_stream,
                    mainnet_armed=execution.mainnet_armed,
                )))
                next_heartbeat = now + 5
            await asyncio.sleep(.1)
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


def _authenticated_command(args: argparse.Namespace) -> int:
    from quantos.application.hft_authenticated import (
        authenticated_preflight,
        reconcile_authenticated_state,
        run_authenticated_hft_session,
    )
    from quantos.domain.execution.hft_authenticated import (
        HftAuthenticatedExecution,
    )
    config, environment, client, store = _authenticated_components(args)
    print(_json({
        "event": "hft_credential_health",
        **client.credential_health(),
    }))
    if args.futures_operation == "hft-rearm":
        store.rearm(args.confirm_rearm)
        print(_json({
            "event": "hft_authenticated_rearmed",
            "environment": environment.value,
        }))
        return 0
    if getattr(args, "confirm_new_auth_state", False):
        store.initialize()
    else:
        store.snapshot()
    if args.futures_operation == "hft-auth-preflight":
        payload, _, _ = authenticated_preflight(
            client, store,
            environment=environment,
            normalize_account=False,
            perform_test_order=True,
            allow_testnet_btcusdt=args.allow_testnet_btcusdt,
        )
        print(_json(payload))
        return 0
    state = store.snapshot()
    symbol = ((state.get("preflight") or {}).get("symbol") or "BTCUSDC")
    session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    execution = HftAuthenticatedExecution(
        environment=environment,
        gateway=client,
        store=store,
        session_id=session_id,
        enable_mainnet=getattr(args, "enable_mainnet", False),
        approval_token=os.environ.get("QUANTOS_MAINNET_APPROVAL"),
        minimum_size_only=getattr(args, "mainnet_minimum_size", False),
        symbol=symbol,
    )
    if args.futures_operation == "hft-kill":
        result = execution.kill(flatten=args.flatten)
        print(_json({
            "event": "hft_authenticated_killed",
            "environment": environment.value,
            **result,
        }))
        return 0
    if args.futures_operation != "hft-execute":
        raise ValueError("unknown authenticated HFT operation")
    if args.duration_seconds < 0:
        raise ValueError("duration-seconds must be non-negative")
    if args.confirm_rearm is not None:
        store.rearm(args.confirm_rearm)
    if environment.value == "MAINNET" and not execution.mainnet_armed:
        raise ValueError(
            "MAINNET requires --enable-mainnet and exact QUANTOS_MAINNET_APPROVAL"
        )
    if environment.value == "MAINNET" and not args.mainnet_minimum_size:
        raise ValueError("initial MAINNET execution requires --mainnet-minimum-size")
    payload, rules, actual_fees = authenticated_preflight(
        client, store,
        environment=environment,
        normalize_account=True,
        perform_test_order=True,
        allow_testnet_btcusdt=args.allow_testnet_btcusdt,
    )
    print(_json(payload))
    if execution.symbol != rules.symbol:
        execution = HftAuthenticatedExecution(
            environment=environment,
            gateway=client,
            store=store,
            session_id=session_id,
            enable_mainnet=getattr(args, "enable_mainnet", False),
            approval_token=os.environ.get("QUANTOS_MAINNET_APPROVAL"),
            minimum_size_only=getattr(args, "mainnet_minimum_size", False),
            symbol=rules.symbol,
        )
    reconciliation = reconcile_authenticated_state(
        client, store, symbol=rules.symbol
    )
    print(_json({"event": "hft_authenticated_reconciled", **reconciliation}))
    if args.protocol_smoke:
        asyncio.run(_authenticated_runtime(
            args, environment, client, store, execution, rules
        ))
        return 0
    from quantos.infrastructure.binance.hft import (
        BinanceHftPublicClient, BinanceHftStream,
    )
    from quantos.infrastructure.binance.hft_authenticated import (
        BinanceHftUserDataStream, REST_BASE_URLS, WS_BASE_URLS,
    )
    public_client = BinanceHftPublicClient(
        base_url=REST_BASE_URLS[environment],
        request_timeout=config.snapshot_timeout.total_seconds(),
    )
    user_stream = BinanceHftUserDataStream(
        client, lifecycle=lambda payload: print(_json(payload))
    )
    public_stream = BinanceHftStream(
        base_url=WS_BASE_URLS[environment],
        open_timeout=config.websocket_open_timeout.total_seconds(),
        read_timeout=config.websocket_read_timeout.total_seconds(),
        shutdown_timeout=config.shutdown_timeout.total_seconds(),
        lifecycle=lambda payload: print(_json(payload)),
    )
    asyncio.run(run_authenticated_hft_session(
        config=config,
        rules=rules,
        fees=actual_fees,
        authenticated_client=client,
        public_client=public_client,
        public_stream=public_stream,
        user_stream=user_stream,
        execution=execution,
        store=store,
        duration_seconds=args.duration_seconds,
        emit=lambda payload: print(_json(payload)),
    ))
    return 0
