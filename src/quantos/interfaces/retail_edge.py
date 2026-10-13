"""QOS04 public-only operational commands; offline replay/status/kill."""
import asyncio
from dataclasses import asdict, replace
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import tempfile

from quantos.application.hft_trader import select_hft_contract
from quantos.application.retail_edge_session import run_edge_session
from quantos.application.retail_replay import RetailCausalProcessor, replay_commands, stable_json
from quantos.application.retail_trader import RetailPaperEngine
from quantos.domain.evaluation.retail import RetailEvaluation
from quantos.domain.market_data.futures import FuturesSymbolRules
from quantos.domain.risk.hft import HftFeeSchedule
from quantos.domain.risk.retail import RetailRiskPolicy, policy_from_dict
from quantos.infrastructure.binance.hft import BinanceHftPublicClient, BinanceHftStream, BinanceHftStreamError, BinanceFuturesError
from quantos.infrastructure.public_data.retail import PublicIntelligenceStream
from quantos.infrastructure.configuration.hft import load_hft_config
from quantos.infrastructure.storage.retail_events import RetailEventJournal, read_journal
from quantos.infrastructure.storage.retail_paper import (
    RetailPaperStateStore, _decode, account_payload, restore_account, exclusive_account,
)


def add_edge_parsers(operations):
    for name in ("retail-edge-reset", "retail-edge-paper", "retail-edge-status", "retail-edge-kill", "retail-edge-replay"):
        parser = operations.add_parser(name, help="QOS04 public-only retail operation")
        if name == "retail-edge-replay":
            parser.add_argument("--capture", type=Path, required=True)
            parser.add_argument("--stress", action="store_true")
            parser.add_argument("--qualify-reclaim", action="store_true", help="Counterfactual Q05 qualification of a source capture")
            parser.add_argument("--output-capture", type=Path)
            continue
        parser.add_argument("--state-store", type=Path, required=True)
        if name in {"retail-edge-reset", "retail-edge-paper"}:
            parser.add_argument("--strategy", choices=("qos04", "qos05", "qos06", "qos07"), default="qos04")
            parser.add_argument("--capital-profile", type=Path)
            parser.add_argument("--market-config", type=Path, default=Path("configs/futures_hft_200_paper.toml"))
        if name == "retail-edge-reset":
            parser.add_argument("--confirm-new-paper-account", action="store_true")
        if name == "retail-edge-paper":
            parser.add_argument("--duration-seconds", type=int, required=True)
            parser.add_argument("--capture", type=Path, required=True)


def read_state(path):
    wrapper = json.loads(path.read_text(encoding="ascii"))
    if wrapper["schema_version"] != 2 or sha256(wrapper["payload"].encode("ascii")).hexdigest() != wrapper["digest"]:
        raise ValueError("state version or digest mismatch")
    payload = json.loads(wrapper["payload"], object_hook=_decode)
    account = restore_account(payload, HftFeeSchedule(**payload["fees"]), policy_from_dict(payload["policy"]))
    return account, payload["runtime"]


def replay_capture(path, stress=False, qualify_reclaim=False, output_capture=None):
    records = read_journal(path)
    manifest = next(records)
    if manifest["kind"] != "manifest" or manifest["payload"]["version"] not in {"qos04-v1", "qos04-v2", "qos05-v1", "qos06-v1", "qos07-v1"}:
        raise ValueError("unsupported capture manifest")
    header = manifest["payload"]
    reclaim = qualify_reclaim or header["version"] in {"qos05-v1", "qos06-v1", "qos07-v1"}
    reliability = header["version"] in {"qos06-v1", "qos07-v1"}
    if qualify_reclaim and (output_capture is None or header["account"]["fills"] or header["account"]["position"]):
        raise ValueError("Q05 qualification requires a fresh source account and exclusive output capture")
    if output_capture is not None and not qualify_reclaim:
        raise ValueError("output capture is only for counterfactual qualification")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml") as temp:
        temp.write(header["config_text"])
        temp.flush()
        config = load_hft_config(Path(temp.name))
    policy = policy_from_dict(header["policy"])
    rules = FuturesSymbolRules(**header["rules"])
    fees = config.fees[rules.symbol]
    if header["version"] in {"qos05-v1", "qos06-v1", "qos07-v1"}:
        fees = HftFeeSchedule(**header["account"]["fees"])
        config = replace(config, fees={**config.fees, rules.symbol: fees})
    if stress:
        if header["account"]["fills"] or header["account"]["position"]:
            raise ValueError("stress requires a fresh flat development account")
        policy = replace(policy, slippage_bps_per_leg=Decimal("3"), acknowledgement_ms=300)
        fees = replace(fees, taker_rate=fees.taker_rate * Decimal("1.5"))
        config = replace(config, fees={**config.fees, rules.symbol: fees})
    if qualify_reclaim:
        from quantos.domain.execution.retail_paper import RetailPaperAccount
        account = RetailPaperAccount(fees, policy)
    else:
        account = restore_account(header["account"], fees, policy)
    processor = RetailCausalProcessor(RetailPaperEngine(config, rules, account, RetailEvaluation(reclaim=reclaim, reliability=reliability), policy, unified=True, reclaim=reclaim, reliability=reliability), counterfactual_clock=stress)
    if qualify_reclaim:
        from quantos.application.reclaim_qualification import qualify_commands
        source_digest = sha256(path.read_bytes()).hexdigest()
        journal = RetailEventJournal(output_capture)
        try:
            journal.append("manifest", version="qos05-v1", source_sha256=source_digest,
                source_capture=str(path), extra_processing_ms=100,
                config_text=header["config_text"],
                rules=header["rules"], policy=asdict(policy), account=account_payload(account))
            return qualify_commands(processor, records, journal, source_digest, stress)
        finally:
            journal.close()
    report, expected = replay_commands(processor, records)
    matches = stable_json(report) == stable_json(expected)
    if not stress and not matches:
        raise ValueError("replay differs from captured final decisions/account")
    return {"stress": stress, "matches_capture": matches, "metrics": report}


def edge_command(args):
    operation = args.futures_operation
    if operation == "retail-edge-replay":
        print(stable_json(replay_capture(args.capture, args.stress, getattr(args, "qualify_reclaim", False), getattr(args, "output_capture", None))))
        return 0
    marker = args.state_store.with_suffix(args.state_store.suffix + ".kill")
    if operation == "retail-edge-kill":
        if not args.state_store.exists():
            raise ValueError("no paper account to halt")
        marker.touch(exist_ok=True)
        print(stable_json({"event": "kill_requested", "marker": str(marker), "effect": "cancel entries; preserve unfilled exposure for reconciled recovery"}))
        return 0
    if operation == "retail-edge-status":
        account, runtime = read_state(args.state_store)
        print(stable_json({"event": "status", "metrics": RetailEvaluation(reclaim=runtime.get("strategy") == "Q05-LR-v1" or "Q05-LR-v1" in runtime.get("metrics", {}).get("candidate_economics", {}), reliability=runtime.get("strategy") == "QOS06" or "market_reliability" in runtime.get("metrics", {})).report(account), "last_runtime": runtime, "kill_requested": marker.exists(), "position": account.position, "pending": account.pending, "policy": account.policy}))
        return 0
    if marker.exists():
        raise ValueError("operator kill marker is present; no automatic rearm")
    if operation == "retail-edge-reset" and not args.confirm_new_paper_account:
        raise ValueError("explicit new-paper-account confirmation required")
    if operation == "retail-edge-paper" and not 1 <= args.duration_seconds <= (43200 if getattr(args, "strategy", "qos04") in {"qos06", "qos07"} else 1800):
        raise ValueError("public session duration exceeds selected mode limit")
    strategy = getattr(args, "strategy", "qos04")
    reclaim = strategy in {"qos05", "qos06", "qos07"}
    reliability = strategy in {"qos06", "qos07"}
    version = "qos07-v1" if strategy == "qos07" else "qos06-v1" if reliability else "qos05-v1" if reclaim else "qos04-v2"
    config = load_hft_config(args.market_config)
    policy = RetailRiskPolicy()
    if strategy == "qos07":
        from quantos.infrastructure.configuration.retail import load_retail_profile
        if getattr(args, "capital_profile", None) is None:
            raise ValueError("QOS07 requires explicit --capital-profile")
        policy = load_retail_profile(args.capital_profile)
        if operation == "retail-edge-paper":
            import os
            active = args.state_store.with_suffix(args.state_store.suffix + ".session.json")
            if not getattr(args, "managed_session", None) or not active.exists():
                raise ValueError("QOS07 paper requires the managed foreground launcher")
            claim = json.loads(active.read_text())
            if claim != {"session": args.managed_session, "pid": os.getpid()}:
                raise ValueError("managed account session identity differs")
    elif getattr(args, "capital_profile", None) is not None:
        raise ValueError("capital profile is isolated to QOS07")
    if config.starting_equity != 200 or config.research_only:
        raise ValueError("QOS04 requires isolated operational $200 paper configuration")
    if config.fees["BTCUSDC"].taker_rate < Decimal(".0004") or config.fees["BTCUSDT"].taker_rate < Decimal(".0005"):
        raise ValueError("configured fee below approved floor")
    with exclusive_account(args.state_store):
        if operation == "retail-edge-reset" and args.state_store.exists():
            raise ValueError("refusing to overwrite existing account")
        client = BinanceHftPublicClient(request_timeout=config.snapshot_timeout.total_seconds())
        rules = select_hft_contract(client.exchange_info(), config)
        identity = sha256(repr((version, config, policy, rules)).encode()).hexdigest()
        store = RetailPaperStateStore(args.state_store)
        if operation == "retail-edge-reset":
            account = store.reset(identity, config.fees[rules.symbol], policy)
            if reclaim:
                store.save(identity, account, {"strategy": "QOS07" if strategy == "qos07" else "QOS06" if reliability else "Q05-LR-v1"})
            print(stable_json({"event": "qos07_account_created" if strategy == "qos07" else "qos06_account_created" if reliability else "qos05_account_created" if reclaim else "qos04_account_created", "starting_equity": account.starting_equity, "identity": identity, "symbol": rules.symbol, "authenticated_requests": 0, "economic_orders": 0}))
            return 0
        account, _ = store.load(identity, config.fees[rules.symbol], policy)
        journal = RetailEventJournal(args.capture, maximum_bytes=8 * 1024 ** 3 if reliability else 256 * 1024 ** 2)
        try:
            journal.append("manifest", version=version, identity=identity,
                config_text=args.market_config.read_text(), policy=asdict(policy),
                rules=asdict(rules), account=account_payload(account))
            def emit(payload):
                if payload.get("event") not in {"mark_price", "websocket_timeout"}:
                    try:
                        print(stable_json(payload), flush=True)
                    except OSError as error:
                        # Do not let WebSocket adapters reinterpret log I/O as network I/O.
                        raise RuntimeError("critical runtime output failure") from error
            emit({"event": "qos07_paper_started" if strategy == "qos07" else "qos06_paper_started" if reliability else "qos05_paper_started" if reclaim else "qos04_paper_started", "starting_equity": account.starting_equity, "balance": account.balance,
                  "fees": asdict(account.fees), "strategy": "Q05-LR-v1" if reclaim else "QOS04", "mode": "PUBLIC PAPER ONLY", "authenticated_requests": 0, "economic_orders": 0})
            result = asyncio.run(run_edge_session(
                config=config, rules=rules, account=account, evaluation=RetailEvaluation(reclaim=reclaim, reliability=reliability), policy=policy,
                store=store, identity=identity, duration_seconds=args.duration_seconds, client=client,
                stream_factory=lambda: BinanceHftStream(lifecycle=emit),
                external_factories=[] if reclaim else [("coinbase", "leader_quote", lambda: PublicIntelligenceStream("coinbase", rules.symbol, emit)),
                                    ("binance", "liquidation", lambda: PublicIntelligenceStream("liquidation", rules.symbol, emit))],
                journal=journal, emit=emit, kill_requested=marker.exists, reclaim=reclaim, reliability=reliability,
                feed_errors=(TimeoutError, ConnectionError, BinanceHftStreamError, BinanceFuturesError),
            ))
            emit({"event": "qos07_paper_stopped" if strategy == "qos07" else "qos06_paper_stopped" if reliability else "qos05_paper_stopped" if reclaim else "qos04_paper_stopped", "metrics": result, "authenticated_requests": 0, "economic_orders": 0})
        finally:
            journal.close()
    return 0
