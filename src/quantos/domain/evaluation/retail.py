"""Compact auditable funnel and account evaluation for retail paper mode."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal

from quantos.domain.execution.retail_paper import RetailPaperAccount


class RetailEvaluation:
    def __init__(self, reclaim=False, reliability=False) -> None:
        from quantos.domain.evaluation.reclaim_diagnostics import ReclaimDiagnostics
        self.reliability = ReclaimDiagnostics() if reliability else None
        self.strategies = ("Q05-LR-v1",) if reclaim else ("Q04-LL-v1", "Q04-LD-v1", "Q03-directional-v1")
        self.candidate_reasons: Counter[str] = Counter()
        self.candidate_funnel: Counter[str] = Counter()
        self.funnel: Counter[str] = Counter()
        self.alpha_reasons: Counter[str] = Counter()
        self.signal_candidates = 0
        self.risk_approved = 0
        self.rejections: Counter[str] = Counter()
        self.events_received: Counter[str] = Counter()
        self.disarms: Counter[str] = Counter()
        self.maximum_drawdown = Decimal("0")

    def observe_equity(self, account: RetailPaperAccount, equity: Decimal) -> None:
        account.peak_equity = max(account.peak_equity, equity)
        if account.peak_equity > 0:
            drawdown = (account.peak_equity - equity) / account.peak_equity
            self.maximum_drawdown = max(self.maximum_drawdown, drawdown)

    def report(self, account: RetailPaperAccount) -> dict:
        entries = sum(fill.kind == "ENTRY" for fill in account.fills)
        exits = sum(fill.kind == "EXIT" for fill in account.fills)
        return {
            **({"market_reliability": self.reliability.report()} if self.reliability else {}),
            "candidate_economics": self.candidate_economics(account, self.strategies),
            "turnover_notional": sum((f.quantity * f.price for f in account.fills), Decimal("0")),
            "candidate_reasons": dict(self.candidate_reasons),
            "candidate_funnel": dict(self.candidate_funnel),
            "funnel": {**dict(self.funnel), "orders": account.orders_submitted,
                       "fills": len(account.fills), "realized_exits": exits},
            "alpha_reasons": dict(self.alpha_reasons),
            "starting_equity": account.starting_equity,
            "balance": account.balance,
            "peak_equity": account.peak_equity,
            "signal_candidates": self.signal_candidates,
            "risk_approved": self.risk_approved,
            "rejections": dict(self.rejections),
            "disarms": dict(self.disarms),
            "events_received": dict(self.events_received),
            "orders_submitted": account.orders_submitted,
            "orders_cancelled": account.orders_cancelled,
            "entry_fills": entries,
            "exit_fills": exits,
            "fees": account.total_fees,
            "funding": account.total_funding,
            "risk_off": account.risk_off,
            "daily_start_equity": account.daily_start_equity,
            "realized_gross_pnl": account.realized_gross_pnl,
            "realized_net_pnl": account.realized_net_pnl,
            "maximum_drawdown_fraction": self.maximum_drawdown,
            "open_position": account.position is not None,
            "pending_order": account.pending is not None,
        }

    @staticmethod
    def candidate_economics(account, strategies=("Q04-LL-v1", "Q04-LD-v1", "Q03-directional-v1")):
        result = {}
        for strategy in strategies:
            fills = [f for f in account.fills if f.strategy_id == strategy]
            signals = {f.signal_id for f in fills if f.kind == "ENTRY"}
            closed = signals - ({account.position.signal_id} if account.position else set())
            exits = [f for f in fills if f.kind == "EXIT" and f.signal_id in closed]
            net = sum((f.net_pnl for f in exits), Decimal("0"))
            cumulative, peak, drawdown = Decimal("200"), Decimal("200"), Decimal("0")
            for fill in exits:
                cumulative += fill.net_pnl
                peak = max(peak, cumulative)
                drawdown = max(drawdown, (peak - cumulative) / peak)
            result[strategy] = {"closed_trades": len(closed), "net_pnl": net,
                "net_expectancy_per_trade": net / len(closed) if closed else None,
                "fees": sum((f.fee for f in fills), Decimal("0")),
                "funding": sum((r["cost"] for r in account.funding_ledger if r["strategy_id"] == strategy), Decimal("0")),
                "turnover_notional": sum((f.price * f.quantity for f in fills), Decimal("0")),
                "realized_drawdown_fraction": drawdown}
        return result
