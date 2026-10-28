"""Conservative hourly research execution, sharing the paper risk authority.

This is an explicitly declared OHLC scenario, not reconstructed exchange fills.
Only MARKET strategies are supported: hourly bars cannot prove a 90-second
LIMIT/STOP entry or order-book liquidity. No performance selection occurs here.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Iterable, Mapping

from ..risk import Account, Decision, ResearchPolicy, evaluate, portfolio_risk_caps
from ..types import D, Instrument, Quote, Signal
from .data import Bar, FundingRate

HOUR = 3_600_000
DAY = 24 * HOUR
# Source funding timestamps have 1–31 ms jitter; allow at most one second.
# This tolerance validates cadence, never rewrites the event timestamp.
FUNDING_JITTER_MS = 1000


class IncompleteHistory(ValueError):
    """An occupied position cannot be valued/exited from missing observations."""


@dataclass
class Position:
    signal: Signal
    decision: Decision
    opened_ms: int
    entry: Decimal
    entry_fee: Decimal
    entry_equity: Decimal
    funding: Decimal = D(0)

    @property
    def direction(self): return D(1) if self.signal.side == "LONG" else D(-1)

    @property
    def quantity(self): return self.decision.quantity


@dataclass
class SimulationResult:
    points: list[tuple[int, Decimal]] = field(default_factory=list)
    # Simultaneous adverse extremes are a stress envelope, not an observed path.
    adverse_points: list[tuple[int, Decimal]] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    rejections: Counter = field(default_factory=Counter)
    approvals: Counter = field(default_factory=Counter)
    funding_events: list[dict] = field(default_factory=list)
    paused_at_ms: int | None = None
    final_cash: Decimal = D(200)
    assumptions: tuple[str, ...] = (
        "MARKET entries at next hour open, full adverse slippage; no measured book/spread",
        "entry quantity <=1% of previous completed hour volume; this is a liquidity proxy",
        "stop/liquidation triggers use mark OHLC; exit at adverse trade-bar extreme plus slippage",
        "stop beats target on ambiguous bars; targets fill at target plus adverse slippage",
        "funding uses actual event rate and causal mark-hour open proxy, not future close",
        "settlement within first second uses positions held before boundary exits/new entries",
        "later funding precedes any intrabar exit in this scenario",
        "daily/drawdown breakers use hourly opening/closing MTM, permanently pause new entries",
        "adverse_points are simultaneous mark extremes, a stress bound separate from observed MTM",
        "historical instrument filters require separately declared provenance",
    )


class Simulator:
    """A single cash account, at most three occupied symbols, no order bypass.

    bars/marks are symbol -> {hour_open_ms: Bar}; funding is symbol -> events.
    Missing occupied bars/marks or missing/irregular funding fail the entire cell.
    The caller provides complete funding coverage (including surrounding events)
    and declares any unavailable strategy as not evaluable instead of inventing it.
    """

    def __init__(self, bars: Mapping[str, Mapping[int, Bar]],
                 marks: Mapping[str, Mapping[int, Bar]],
                 funding: Mapping[str, Iterable[FundingRate]],
                 instruments: Mapping[str, Instrument], policy: ResearchPolicy,
                 *, initial_cash: Decimal = D(200), participation: Decimal = D(".01"),
                 risk_by_strategy: Mapping[str, Decimal] | None = None):
        if not isinstance(policy, ResearchPolicy): raise ValueError("explicit research policy required")
        if initial_cash <= 0 or not initial_cash.is_finite(): raise ValueError("invalid cash")
        if not 0 < participation <= D(".01"): raise ValueError("invalid liquidity proxy")
        self.bars, self.marks, self.instruments = bars, marks, instruments
        self.risk_by_strategy = dict(risk_by_strategy or {})
        for name, value in self.risk_by_strategy.items():
            if name not in {"crash_flush_e2", "alt_dip_reversal", "ema_level_2h"}:
                raise ValueError("unknown risk-map strategy")
            ceiling = D(".03") if name == "ema_level_2h" else D(".08")
            if not isinstance(value, Decimal) or not value.is_finite() or not 0 < value <= ceiling:
                raise ValueError("invalid per-strategy risk")
        cap = max([policy.portfolio_risk_fraction, policy.risk_fraction, *self.risk_by_strategy.values()])
        self.policy = replace(policy, portfolio_risk_fraction=cap) if self.risk_by_strategy else policy
        self.initial_cash, self.participation = initial_cash, participation
        self.funding = {s: tuple(sorted(rows, key=lambda r: r.event_ms)) for s, rows in funding.items()}
        self.cash = initial_cash
        self.positions: dict[str, Position] = {}
        self.cooldowns: dict[tuple[str, str], int] = {}
        self.high_water = self.day_start = initial_cash
        self.day = None
        self.result = SimulationResult()
        self._used = False

    def _bar(self, symbol, time, *, mark=False):
        row = (self.marks if mark else self.bars).get(symbol, {}).get(time)
        if row is None:
            raise IncompleteHistory(f"missing {'mark' if mark else 'trade'} bar {symbol}@{time}")
        if (row.symbol != symbol or row.open_ms != time or row.close_ms + 1 != time + HOUR
                or row.interval != "1h" or any(not x.is_finite() for x in
                    (row.open, row.high, row.low, row.close, row.volume))
                or min(row.open, row.high, row.low, row.close) <= 0 or row.volume < 0
                or row.low > min(row.open, row.close) or row.high < max(row.open, row.close)):
            raise IncompleteHistory(f"invalid hourly bar {symbol}@{time}")
        return row

    def _equity(self, prices):
        return self.cash + sum((p.quantity * (prices[s] - p.entry) * p.direction
                                for s, p in self.positions.items()), D(0))

    def _account(self, prices):
        equity = self._equity(prices)
        self.high_water = max(self.high_water, equity)
        gross = margin = long_risk = short_risk = D(0)
        for symbol, p in self.positions.items():
            notional = p.quantity * max(prices[symbol], p.signal.entry_price)
            gross += notional
            margin += notional / p.decision.leverage
            if p.direction > 0: long_risk += p.decision.achievable_risk
            else: short_risk += p.decision.achievable_risk
        return Account(equity, self.day_start, self.high_water, len(self.positions),
                       paused=self.result.paused_at_ms is not None,
                       gross_notional=gross, margin_used=margin,
                       long_risk=long_risk, short_risk=short_risk,
                       open_symbols=tuple(sorted(self.positions)))

    def _close(self, symbol, raw_price, when, reason):
        p = self.positions.pop(symbol)
        price = raw_price * (1 - p.direction * self.policy.slippage)
        if price <= 0: raise ValueError("invalid exit price")
        fee = price * p.quantity * self.policy.taker_fee
        gross = p.quantity * (price - p.entry) * p.direction
        self.cash += gross - fee
        net = gross - p.entry_fee - fee + p.funding
        self.result.trades.append({
            "strategy_id": p.signal.strategy_id, "signal_id": p.signal.signal_id,
            "symbol": symbol, "side": p.signal.side, "opened_ms": p.opened_ms,
            "closed_ms": when, "entry_price": p.entry, "exit_price": price,
            "quantity": p.quantity, "leverage": p.decision.leverage,
            "gross_pnl": gross, "fees": p.entry_fee + fee, "funding": p.funding,
            "net_pnl": net, "initial_risk": p.decision.achievable_risk,
            "net_r": net / p.decision.achievable_risk,
            "entry_equity": p.entry_equity, "account_return": net / p.entry_equity,
            "exit_reason": reason,
        })

    def _breaker(self, prices, trade_prices, when):
        a = self._account(prices)
        if (a.equity <= a.day_start_equity * (1 - self.policy.daily_loss)
                or a.equity <= a.high_water * (1 - self.policy.drawdown)):
            if self.result.paused_at_ms is None: self.result.paused_at_ms = when
            for symbol in sorted(tuple(self.positions)):
                self._close(symbol, trade_prices[symbol], when, "LOSS_BREAKER")

    def _fund(self, rows, time, mark_opens):
        for event in rows:
            p = self.positions.get(event.symbol)
            if p is None: continue
            # New entries at a funding boundary occur AFTER the funding event.
            amount = -p.direction * p.quantity * mark_opens[event.symbol] * event.rate
            p.funding += amount
            self.cash += amount
            self.result.funding_events.append({"symbol": event.symbol,
                "signal_id": p.signal.signal_id, "time_ms": event.event_ms,
                "mark_proxy_ms": time, "mark_proxy": mark_opens[event.symbol],
                "rate": event.rate, "amount": amount})

    def _enter(self, signal, time, prices):
        reject = self.result.rejections
        if signal.entry_type != "MARKET":
            reject["OHLC_CANNOT_ESTABLISH_ENTRY_TTL"] += 1; return
        if signal.strategy_id not in {"crash_flush_e2", "alt_dip_reversal", "ema_level_2h"}:
            reject["UNSUPPORTED_EXIT_MODEL"] += 1; return
        if time < self.cooldowns.get((signal.strategy_id, signal.symbol), 0):
            reject["STRATEGY_COOLDOWN"] += 1; return
        ins = self.instruments.get(signal.symbol)
        if ins is None:
            reject["MISSING_FILTERS"] += 1; return
        try:
            bar, mark = self._bar(signal.symbol, time), self._bar(signal.symbol, time, mark=True)
            previous = self._bar(signal.symbol, time - HOUR)
        except IncompleteHistory:
            reject["MISSING_ENTRY_DATA"] += 1; return
        # Explicit synthetic zero-spread quote; no actual bid/ask/depth claim.
        q = Quote(signal.symbol, time, time, bar.open, bar.open,
                  max(previous.volume, D(".00000001")), max(previous.volume, D(".00000001")), mark.open)
        prices[signal.symbol] = mark.open
        account = self._account(prices)
        entry_policy = replace(self.policy, risk_fraction=self.risk_by_strategy[signal.strategy_id]) if signal.strategy_id in self.risk_by_strategy else self.policy
        decision = evaluate(signal, ins, q, account, entry_policy, time)
        if not decision.approved:
            reject[decision.reason] += 1; return
        direction = D(1) if signal.side == "LONG" else D(-1)
        quantity = decision.quantity
        price = bar.open * (1 + direction * self.policy.slippage)
        distance = (price - signal.stop_loss) * direction
        costs = price * (2 * self.policy.taker_fee + 2 * self.policy.slippage)
        fee = quantity * price * self.policy.taker_fee
        fill_risk = quantity * (abs(price - signal.stop_loss) + price * self.policy.taker_fee
                              + signal.stop_loss * (self.policy.taker_fee + self.policy.slippage))
        equity_after = account.equity - fee + quantity * (mark.open - price) * direction
        reserve_notional = quantity * max(mark.open, signal.entry_price, price)
        direction_cap, total_cap = portfolio_risk_caps(self.policy)
        used_direction = account.long_risk if direction > 0 else account.short_risk
        if quantity > previous.volume * self.participation:
            reason = "INSUFFICIENT_PROXY_LIQUIDITY"
        elif abs(price - signal.entry_price) > signal.entry_price * self.policy.slippage * 2:
            reason = "ENTRY_GAP_EXCEEDS_ALLOWANCE"
        elif distance <= 0 or (signal.take_profit is not None and (signal.take_profit - price) * direction <= 0):
            reason = "INVALID_BRACKET"
        elif signal.take_profit is None and costs > D(".25") * distance:
            reason = "COSTS_TOO_HIGH_FOR_STOP"
        elif signal.take_profit is not None and ((signal.take_profit-price)*direction-costs)/(distance+costs) < self.policy.min_net_rr:
            reason = "NET_REWARD_RISK"
        elif fill_risk > decision.achievable_risk:
            reason = "FILL_EXCEEDS_APPROVED_RISK"
        elif (equity_after <= 0 or account.gross_notional + reserve_notional > equity_after*self.policy.gross_fraction
              or account.margin_used + reserve_notional/decision.leverage > equity_after):
            reason = "FILL_EXCEEDS_PORTFOLIO_CAP"
        elif (used_direction + decision.achievable_risk > equity_after * direction_cap
              or account.long_risk + account.short_risk + decision.achievable_risk > equity_after * total_cap):
            reason = "FILL_EXCEEDS_PORTFOLIO_RISK_CAP"
        elif (decision.leverage * (2 * (distance/price + self.policy.slippage) + self.policy.maintenance_buffer) > 1):
            reason = "LIQUIDATION_BUFFER"
        else:
            reason = None
        if reason:
            reject[reason] += 1; return
        self.cash -= fee
        self.positions[signal.symbol] = Position(signal, decision, time, price, fee, account.equity)
        if signal.strategy_id in {"crash_flush_e2", "alt_dip_reversal"}:
            seconds = 86400 if signal.strategy_id == "crash_flush_e2" else signal.maximum_hold_seconds
            self.cooldowns[(signal.strategy_id, signal.symbol)] = time + seconds * 1000
        self.result.approvals[decision.reason] += 1

    def _validate_funding(self, symbols, start, end):
        events = defaultdict(list)
        for symbol in symbols:
            rows = self.funding.get(symbol, ())
            if not rows: raise IncompleteHistory(f"funding absent for {symbol}")
            if any(a.event_ms >= b.event_ms for a, b in zip(rows, rows[1:])):
                raise IncompleteHistory(f"duplicate funding for {symbol}")
            for row in rows:
                if (row.symbol != symbol or not row.rate.is_finite() or not 0 < row.interval_hours <= 8):
                    raise IncompleteHistory(f"invalid funding for {symbol}")
                if start <= row.event_ms < end: events[row.event_ms // HOUR * HOUR].append(row)
            relevant = [r for r in rows if start - 8*HOUR <= r.event_ms <= end + 8*HOUR]
            if (not relevant or relevant[0].event_ms > start + FUNDING_JITTER_MS
                    or relevant[-1].event_ms < end - 8*HOUR - FUNDING_JITTER_MS):
                raise IncompleteHistory(f"funding boundary coverage absent for {symbol}")
            # At a reported schedule switch, either adjacent declared interval
            # can describe this boundary. Bounded jitter preserves actual times;
            # missing settlements cannot be replaced with zero rates.
            for a, b in zip(relevant, relevant[1:]):
                delta = b.event_ms - a.event_ms
                expected = {a.interval_hours * HOUR, b.interval_hours * HOUR}
                if min(abs(delta - interval) for interval in expected) > FUNDING_JITTER_MS:
                    raise IncompleteHistory(f"funding gap/irregular interval for {symbol}@{b.event_ms}")
        return events

    def run(self, signals: Iterable[Signal], *, start_ms: int, end_ms: int) -> SimulationResult:
        if self._used: raise ValueError("simulator is single-use")
        self._used = True
        if start_ms % HOUR or end_ms % HOUR or end_ms <= start_ms:
            raise ValueError("window must have UTC hourly boundaries")
        grouped = defaultdict(list)
        identities = set()
        for signal in signals:
            if not start_ms <= signal.observed_ms < end_ms: continue
            if signal.observed_ms % HOUR: raise ValueError("signal not at closed-bar boundary")
            identity = (signal.strategy_id, signal.signal_id)
            if identity in identities: raise ValueError("duplicate signal")
            identities.add(identity)
            grouped[signal.observed_ms].append(signal)
        symbols = {s.symbol for rows in grouped.values() for s in rows}
        events = self._validate_funding(symbols, start_ms, end_ms)
        self.result.points.append((start_ms, self.cash))
        for time in range(start_ms, end_ms, HOUR):
            opened_marks = {s: self._bar(s, time, mark=True).open for s in self.positions}
            opened_trade = {s: self._bar(s, time).open for s in self.positions}
            if self.day != time // DAY:
                self.day = time // DAY; self.day_start = self._equity(opened_marks)
            hour_events = sorted(events.get(time, ()), key=lambda r: (r.event_ms, r.symbol))
            boundary = [r for r in hour_events if r.event_ms - time <= FUNDING_JITTER_MS]
            self._fund(boundary, time, opened_marks)
            self._breaker(opened_marks, opened_trade, time)
            # Gaps and deadlines are handled before new entries at the same time.
            for symbol in sorted(tuple(self.positions)):
                p = self.positions[symbol]
                mark, trade = opened_marks[symbol], opened_trade[symbol]
                liquidation = p.entry * (1 - p.direction * (1/p.decision.leverage - self.policy.maintenance_buffer))
                if (mark-liquidation)*p.direction <= 0: reason = "LIQUIDATION_STRESS"
                elif (mark-p.signal.stop_loss)*p.direction <= 0:
                    reason = "CATASTROPHE_STOP" if p.signal.take_profit is None else "SL"
                elif p.signal.take_profit is not None and (trade-p.signal.take_profit)*p.direction >= 0: reason = "TP"
                elif time >= p.opened_ms + p.signal.maximum_hold_seconds*1000: reason = "TIME_EXIT"
                else: continue
                self._close(symbol, trade, time, reason)
            self._breaker(opened_marks, opened_trade, time)
            for signal in sorted(grouped.get(time, ()), key=lambda s: (s.strategy_id, s.symbol, s.signal_id)):
                self._enter(signal, time, opened_marks)
            bars = {s: self._bar(s, time) for s in self.positions}
            marks = {s: self._bar(s, time, mark=True) for s in self.positions}
            mark_opens = {s: b.open for s, b in marks.items()}
            self._fund([r for r in hour_events if r.event_ms - time > FUNDING_JITTER_MS], time, mark_opens)
            worst_marks = {s: (b.low if self.positions[s].direction > 0 else b.high) for s, b in marks.items()}
            self.result.adverse_points.append((time+HOUR-1, self._equity(worst_marks)))
            for symbol in sorted(tuple(self.positions)):
                p, bar, mark = self.positions[symbol], bars[symbol], marks[symbol]
                adverse = mark.low if p.direction > 0 else mark.high
                liquidation = p.entry * (1-p.direction*(1/p.decision.leverage-self.policy.maintenance_buffer))
                if (adverse-liquidation)*p.direction <= 0: reason = "LIQUIDATION_STRESS"
                elif (adverse-p.signal.stop_loss)*p.direction <= 0:
                    reason = "CATASTROPHE_STOP" if p.signal.take_profit is None else "SL"
                else: reason = None
                if reason:
                    self._close(symbol, bar.low if p.direction > 0 else bar.high, time+HOUR-1, reason)
                elif p.signal.take_profit is not None and ((bar.high if p.direction > 0 else bar.low)-p.signal.take_profit)*p.direction >= 0:
                    self._close(symbol, p.signal.take_profit, time+HOUR-1, "TP")
            close_marks = {s: b.close for s, b in marks.items()}
            close_trade = {s: b.close for s, b in bars.items()}
            self._breaker(close_marks, close_trade, time+HOUR-1)
            if time+HOUR == end_ms:
                for symbol in sorted(tuple(self.positions)):
                    self._close(symbol, close_trade[symbol], end_ms-1, "WINDOW_END")
            self.result.points.append((time+HOUR, self._equity(close_marks)))
        self.result.final_cash = self.cash
        net = sum((t["net_pnl"] for t in self.result.trades), D(0))
        if abs(self.cash-self.initial_cash-net) > D("1e-20"):
            raise AssertionError("cash/fees/funding reconciliation failed")
        return self.result
