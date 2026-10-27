"""Thin public-feed composition; production Alpha must be explicitly supplied."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from quantos.application.paper_runtime import PaperRuntime, PaperRuntimeError
from quantos.domain.alpha import AlphaAction, AlphaDecision
from quantos.domain.evaluation import AlphaEvaluation
from quantos.infrastructure.binance.live_stream import BinanceSpotLiveMarketDataAdapter
from quantos.infrastructure.configuration.paper_runtime import load_paper_runtime_config
from quantos.infrastructure.storage.execution_ledger import JsonlExecutionLedger
from quantos.infrastructure.storage.paper_runtime import LocalPaperRuntimeStore


SMOKE_ALPHA_ID = "non-trading-hold-smoke-v1"


def non_trading_hold(feature):
    """Operational feed smoke only; never a selected or profitable-looking Alpha."""
    return AlphaEvaluation(AlphaDecision(feature.timestamp, feature.symbol,
        SMOKE_ALPHA_ID, "no-model-non-trading", feature.feature_version,
        AlphaAction.HOLD, "Non-trading operational smoke: no selected production Alpha",
        "non-trading", Decimal("0")), Decimal("0"))


async def run_paper(config_path, *, alpha, alpha_implementation_id, logger, stop=None,
                    clock=None, connector=None):
    """Programmatic composition for a selected Alpha; no dynamic plugin loading."""
    config = load_paper_runtime_config(config_path, alpha_implementation_id=alpha_implementation_id)
    feed = BinanceSpotLiveMarketDataAdapter(symbols=config.policy.symbols, connector=connector)
    runtime = PaperRuntime(policy=config.policy, alpha=alpha,
        ledger=JsonlExecutionLedger(config.ledger_path),
        store=LocalPaperRuntimeStore(config.state_path, config.evidence_path, config.ledger_path),
        clock=clock or (lambda: datetime.now(timezone.utc)), stop=stop, logger=logger)
    return await runtime.run(feed)


def paper_command(args, logger):
    if not args.non_trading_smoke:
        raise PaperRuntimeError("production Alpha is not selected; supply it through run_paper, or explicitly request --non-trading-smoke")
    logger.warning("paper_non_trading_smoke", extra={"event": "paper_non_trading_smoke",
        "context": {"alpha_implementation_id": SMOKE_ALPHA_ID, "trading": False}})
    try:
        asyncio.run(run_paper(args.runtime_config, alpha=non_trading_hold,
                             alpha_implementation_id=SMOKE_ALPHA_ID, logger=logger))
    except KeyboardInterrupt:
        return 130
    return 0
