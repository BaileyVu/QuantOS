"""Attributed trade economics; insufficient samples never become return claims."""
from decimal import Decimal as D
from random import Random
from quantos.domain.evaluation.monte_carlo import stationary_indices


def trade_statistics(account, coverage_seconds):
    ids = list(dict.fromkeys(f.signal_id for f in account.fills if f.kind == "ENTRY"))
    trades = []
    for signal_id in ids:
        fills = [f for f in account.fills if f.signal_id == signal_id]
        entries = [f for f in fills if f.kind == "ENTRY"]
        exits = [f for f in fills if f.kind == "EXIT"]
        closed = sum((f.quantity for f in entries), D(0)) == sum((f.quantity for f in exits), D(0))
        contexts = [r for r in account.lifecycle if r["event"] == "alpha_context" and r["signal_id"] == signal_id]
        trades.append({"signal_id": signal_id, "closed": closed,
            "order_ids": list(dict.fromkeys(f.order_id for f in fills)),
            "fill_ids": [f.fill_id for f in fills], "source_context": contexts,
            "gross_pnl": sum((f.gross_pnl for f in exits), D(0)),
            "net_pnl": sum((f.net_pnl for f in exits), D(0)),
            "fees": sum((f.fee for f in fills), D(0)),
            "funding": sum((r["cost"] for r in account.funding_ledger if r["signal_id"] == signal_id), D(0)),
            "slippage_cost": sum((r.get("slippage_cost", D(0)) for r in account.lifecycle if r.get("signal_id") == signal_id), D(0))})
    pnls = [r["net_pnl"] for r in trades if r["closed"]]
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
    return {"trades": trades, "closed_trades": len(pnls), "censored_trades": len(trades)-len(pnls),
        "coverage_seconds": coverage_seconds,
        "closed_trades_per_hour": D(len(pnls))*3600/D(str(coverage_seconds)) if coverage_seconds else None,
        "net_expectancy": sum(pnls, D(0))/len(pnls) if pnls else None,
        "profit_factor": sum(wins, D(0))/-sum(losses, D(0)) if losses else None,
        "profit_factor_status": "defined" if losses else "undefined_no_losing_trades",
        "average_win": sum(wins, D(0))/len(wins) if wins else None,
        "average_loss": sum(losses, D(0))/len(losses) if losses else None,
        "slippage_cost": sum((r["slippage_cost"] for r in trades), D(0)),
        "uncertainty": trade_monte_carlo(pnls)}


def trade_monte_carlo(pnls):
    if len(pnls) < 30:
        return {"status": "INSUFFICIENT_EVIDENCE", "sample_size": len(pnls),
                "minimum_samples": 30, "confidence_interval": None, "monte_carlo": None}
    rng = Random(505)
    terminal, means, drawdowns = [], [], []
    returns = []
    equity = D(200)
    for pnl in pnls:
        if equity <= 0:
            raise ValueError("nonpositive trade-sequence equity")
        returns.append(pnl/equity)
        equity += pnl
    for _ in range(1000):
        equity = peak = D(200)
        dd, sampled = D(0), []
        for index in stationary_indices(len(pnls), rng, 5):
            equity *= 1 + returns[index]
            peak = max(peak, equity)
            dd = max(dd, (peak-equity)/peak)
            sampled.append(pnls[index])
        terminal.append(equity-200)
        means.append(sum(sampled, D(0))/len(sampled))
        drawdowns.append(dd)
    def interval(values):
        values = sorted(values)
        return [values[24], values[974]]
    return {"status": "DESCRIPTIVE_BOOTSTRAP", "sample_size": len(pnls), "seed": 505,
        "simulations": 1000, "mean_block_length": 5, "empirical_level": ".95",
        "expectancy_interval": interval(means), "terminal_pnl_interval": interval(terminal),
        "drawdown_interval": interval(drawdowns),
        "assumptions": "stationary observed trade returns; circular blocks; no regime prediction, intratrade path or exchange resizing; not approval"}
