"""Controlled source audit adapter for the archived EMA 9/20 backtest.

This is intentionally separate from the current 12/26 EMA candidate. It loads
only an AST whitelist of source definitions, uses strict UTC-complete USD-M 2h
bars, and emits training-window signal paths. Outcome scoring is an explicit,
separate call and has not been run during wrapper validation.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from ..market import parse_exchange_info
from ..strategies.candidate_features import HOUR_MS, resample_hourly
from ..types import Instrument
from .data import Bar

D = Decimal
TRAIN_START_MS = int(datetime(2022, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
TRAIN_END_MS = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
ARCHIVE_ROOT = Path.home() / "Desktop"
DEFAULT_STRATEGY_PATH = ARCHIVE_ROOT / "strategy.py"
DEFAULT_OPTIMIZE_PATH = ARCHIVE_ROOT / "optimize.py"
DEFAULT_BACKTEST_PATH = ARCHIVE_ROOT / "backtest_edge.py"
STRATEGY_BINDINGS = (
    "Params", "ema", "atr", "pivots", "Indicators", "_clusters", "find_level",
    "context_ok", "signal",
)
_FORBIDDEN_CALLS = {"open", "exec", "eval", "__import__", "system", "popen"}
_EXPECTED_CFG = {"tf": "2h", "ema": (9, 20), "filt": "level", "stop": 0.03,
                 "exit": "cross", "slots": 4, "lev": 1}
_EXPECTED_COINS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
                   "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT")
_EXPECTED_LEVEL_PARAMS = {"fast": 9, "slow": 20, "near_atr": 3,
                          "retest_win": 12, "tol_atr": 1.0,
                          "zone_body": True, "lookback": 96}


@dataclass(frozen=True)
class ArchivedConfig:
    timeframe: str = "2h"
    fast_ema: int = 9
    slow_ema: int = 20
    filter: str = "level"
    stop_fraction: float = 0.03
    exit_rule: str = "cross"
    slots: int = 4
    leverage: int = 1
    fee_per_side: float = 0.0005
    maximum_path_bars: int = 60
    source_warmup_bars: int = 300
    starting_equity: float = 200.0


DEFAULT_CONFIG = ArchivedConfig()


@dataclass(frozen=True)
class ArchivedCandidate:
    symbol: str
    observed_ms: int
    side: int
    entry: float
    level_pass: bool
    source_row: tuple[Any, ...]


@dataclass(frozen=True)
class ArchivedSource:
    config: ArchivedConfig
    strategy_sha256: str
    optimize_sha256: str
    backtest_sha256: str
    selected_bindings: tuple[str, ...]
    level_parameters: tuple[tuple[str, Any], ...]
    universe: tuple[str, ...]
    data_hash: str = ""
    filter_hash: str = ""


@dataclass(frozen=True)
class ArchivedAuditResult:
    source: ArchivedSource
    trade_count: int
    ending_equity: float
    max_drawdown: float
    trades: tuple[tuple[int, str, int, float], ...]


def _calls(node: ast.AST) -> set[str]:
    return {item.func.id for item in ast.walk(node)
            if isinstance(item, ast.Call) and isinstance(item.func, ast.Name)}


def _definitions(tree: ast.Module) -> dict[str, ast.AST]:
    return {node.name: node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))}


def _cfg_from_tree(tree: ast.Module) -> dict[str, Any]:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "CFG" for target in node.targets):
            value = node.value
            if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name) or value.func.id != "dict":
                raise ValueError("archived CFG is not a literal dict call")
            result = {kw.arg: ast.literal_eval(kw.value) for kw in value.keywords if kw.arg}
            if result != _EXPECTED_CFG:
                raise ValueError("archived backtest configuration changed from the reviewed source")
            return result
    raise ValueError("archived CFG was not found")


def _level_params_from_tree(tree: ast.Module) -> dict[str, Any]:
    functions = _definitions(tree)
    build = functions.get("build")
    if build is None:
        raise ValueError("archived optimizer build() was not found")
    for node in ast.walk(build):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name) and node.value.func.id == "Params"):
            values = {}
            for kw in node.value.keywords:
                if not kw.arg:
                    continue
                if kw.arg in {"fast", "slow"}:
                    expected_name = "fa" if kw.arg == "fast" else "sl"
                    if not isinstance(kw.value, ast.Name) or kw.value.id != expected_name:
                        raise ValueError("archived EMA Params are no longer sourced from the selected pair")
                    values[kw.arg] = _EXPECTED_LEVEL_PARAMS[kw.arg]
                else:
                    values[kw.arg] = ast.literal_eval(kw.value)
            if all(values.get(name) == value for name, value in _EXPECTED_LEVEL_PARAMS.items()):
                return values
    raise ValueError("archived level Params no longer match the reviewed config")


def _constant_from_tree(tree: ast.Module, name: str) -> Any:
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)):
            return ast.literal_eval(node.value)
    raise ValueError(f"archived constant {name} was not found")


def _build_control_from_tree(tree: ast.Module) -> tuple[int, int]:
    build = _definitions(tree).get("build")
    if build is None:
        raise ValueError("archived optimizer build() was not found")
    warmup, hold = None, None
    for node in ast.walk(build):
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Call):
            if isinstance(node.iter.func, ast.Name) and node.iter.func.id == "range":
                args = node.iter.args
                if (len(args) == 2 and isinstance(args[0], ast.Constant)
                        and args[0].value == 300):
                    warmup = 300
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                and node.value.value == 60):
            if any(isinstance(target, ast.Name) and target.id == "hold" for target in node.targets):
                hold = 60
    if warmup is None or hold is None:
        raise ValueError("archived warmup or 60-bar path changed from the reviewed source")
    return warmup, hold


def load_archived_source(strategy_path: str | Path = DEFAULT_STRATEGY_PATH,
                         optimize_path: str | Path = DEFAULT_OPTIMIZE_PATH,
                         backtest_path: str | Path = DEFAULT_BACKTEST_PATH
                         ) -> tuple[dict[str, Any], ArchivedSource]:
    """Parse all sources, compile only reviewed strategy definitions, and hash provenance.

    No module import or original top-level code runs. The selected AST definitions
    are checked for obvious dynamic or filesystem/process calls before compilation.
    """
    strategy_bytes = Path(strategy_path).read_bytes()
    optimize_bytes = Path(optimize_path).read_bytes()
    backtest_bytes = Path(backtest_path).read_bytes()
    strategy_tree = ast.parse(strategy_bytes, filename=str(strategy_path))
    optimize_tree = ast.parse(optimize_bytes, filename=str(optimize_path))
    backtest_tree = ast.parse(backtest_bytes, filename=str(backtest_path))
    config_tree = _cfg_from_tree(backtest_tree)
    level_params = _level_params_from_tree(optimize_tree)
    fee = _constant_from_tree(optimize_tree, "FEE")
    coins = tuple(_constant_from_tree(optimize_tree, "COINS"))
    if coins != _EXPECTED_COINS:
        raise ValueError("archived optimizer universe changed from the reviewed source")
    warmup, hold = _build_control_from_tree(optimize_tree)
    definitions = _definitions(strategy_tree)
    if not set(STRATEGY_BINDINGS) <= set(definitions):
        raise ValueError("archived strategy source lacks a required pure definition")
    selected = [definitions[name] for name in STRATEGY_BINDINGS]
    if any(_calls(node) & _FORBIDDEN_CALLS for node in selected):
        raise ValueError("archived strategy definition contains a forbidden dynamic call")
    namespace: dict[str, Any] = {"np": np, "dataclass": dataclass}
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(module, str(strategy_path), "exec"), namespace, namespace)
    config = ArchivedConfig(fee_per_side=fee, source_warmup_bars=warmup,
                            maximum_path_bars=hold)
    source = ArchivedSource(
        config,
        hashlib.sha256(strategy_bytes).hexdigest(),
        hashlib.sha256(optimize_bytes).hexdigest(),
        hashlib.sha256(backtest_bytes).hexdigest(),
        STRATEGY_BINDINGS,
        tuple(sorted(level_params.items())),
        coins,
    )
    if (config_tree["ema"] != (source.config.fast_ema, source.config.slow_ema)
            or config_tree["stop"] != source.config.stop_fraction
            or config_tree["exit"] != source.config.exit_rule
            or config_tree["slots"] != source.config.slots
            or config_tree["lev"] != source.config.leverage):
        raise ValueError("audited configuration disagrees with the archived CFG")
    return namespace, source


def build_archived_candidates(index, *, filter_snapshot_path: str | Path =
                              "data/portfolio/filters/current_usdt_proxy_20261029.json",
                              strategy_path: str | Path = DEFAULT_STRATEGY_PATH,
                              optimize_path: str | Path = DEFAULT_OPTIMIZE_PATH,
                              backtest_path: str | Path = DEFAULT_BACKTEST_PATH
                              ) -> tuple[tuple[ArchivedCandidate, ...], ArchivedSource]:
    """Create the frozen archived 9/20 + level candidate paths on 2022–24 only.

    The source's loose grouping is intentionally replaced by complete UTC 2h
    groups. All source indicators seed from the earliest supplied training-index
    bar; results therefore remain a controlled source audit, not exact Spot
    reproduction or a candidate-selection run.
    """
    if tuple(index.window) != ("2021-10-01", "2024-12-31"):
        raise ValueError("archived audit requires the bounded 2021-10-01..2024-12-31 index")
    filter_bytes = Path(filter_snapshot_path).read_bytes()
    filter_payload = json.loads(filter_bytes)
    if "not point-in-time" not in filter_payload.get("note", ""):
        raise ValueError("archived audit requires the declared current historical filter proxy")
    instruments = parse_exchange_info(filter_payload, margin_asset="USDT")
    namespace, source = load_archived_source(strategy_path, optimize_path, backtest_path)
    source = replace(source, data_hash=index.content_hash,
                     filter_hash=hashlib.sha256(filter_bytes).hexdigest())
    p = namespace["Params"](fast=9, slow=20, near_atr=3, retest_win=12,
                            tol_atr=1.0, zone_body=True, lookback=96)
    output: list[ArchivedCandidate] = []
    for symbol in source.universe:
        if symbol not in instruments:
            continue
        instrument: Instrument = instruments[symbol]
        if (instrument.status != "TRADING" or instrument.margin_asset != "USDT"
                or symbol not in index.bars):
            continue
        hourly_rows = tuple(row for row in index.bars[symbol] if row.open_ms < TRAIN_END_MS)
        hourly = tuple(row.to_candle() for row in hourly_rows)
        if not hourly:
            continue
        bars_2h = resample_hourly(hourly, 2, as_of_ms=hourly[-1].close_ms)
        if not bars_2h:
            continue
        times = np.asarray([bar.open_ms for bar in bars_2h], dtype=np.int64)
        opens = np.asarray([float(bar.open) for bar in bars_2h])
        highs = np.asarray([float(bar.high) for bar in bars_2h])
        lows = np.asarray([float(bar.low) for bar in bars_2h])
        closes = np.asarray([float(bar.close) for bar in bars_2h])
        ind = namespace["Indicators"](opens, highs, lows, closes, p)
        fast, slow = ind.f, ind.s
        for t in range(source.config.source_warmup_bars, len(closes) - 1):
            if not TRAIN_START_MS <= int(times[t]) < TRAIN_END_MS:
                continue
            if fast[t - 1] <= slow[t - 1] and fast[t] > slow[t]:
                side = 1
            elif fast[t - 1] >= slow[t - 1] and fast[t] < slow[t]:
                side = -1
            else:
                continue
            level = namespace["find_level"](ind, t, side) is not None
            end = min(len(closes), t + 1 + source.config.maximum_path_bars)
            hi = (highs[t + 1:end] / closes[t] - 1) * side
            lo = (lows[t + 1:end] / closes[t] - 1) * side
            fav, adv = (hi, lo) if side > 0 else (lo, hi)
            cl = (closes[t + 1:end] / closes[t] - 1) * side
            xs = (fast[t + 1:end] < slow[t + 1:end]) if side > 0 else (fast[t + 1:end] > slow[t + 1:end])
            row = (int(times[t]), side, False, level,
                   fav.astype(np.float32), adv.astype(np.float32),
                   cl.astype(np.float32), xs,
                   times[t + 1:end].astype(np.int64))
            output.append(ArchivedCandidate(symbol, int(times[t]) + 2 * HOUR_MS,
                                             side, float(closes[t]), level, row))
    output.sort(key=lambda item: (item.observed_ms, item.symbol))
    return tuple(output), source


def score_archived_candidates(candidates: tuple[ArchivedCandidate, ...],
                              source: ArchivedSource, *,
                              optimize_path: str | Path = DEFAULT_OPTIMIZE_PATH
                              ) -> ArchivedAuditResult:
    """Apply only the original archived 3%-stop/cross scorer to level entries.

    This outcome-producing function is separate from candidate construction and
    was not invoked during wrapper tests. Main-agent review is required before
    using it for the controlled audit.
    """
    optimize_path = Path(optimize_path)
    if hashlib.sha256(optimize_path.read_bytes()).hexdigest() != source.optimize_sha256:
        raise ValueError("archived optimizer source changed after candidate generation")
    tree = ast.parse(optimize_path.read_bytes(), filename=str(optimize_path))
    definitions = _definitions(tree)
    if not {"outcome", "portfolio"} <= set(definitions):
        raise ValueError("archived scorer functions are missing")
    scorer_nodes = [definitions["outcome"], definitions["portfolio"]]
    if any(_calls(node) & _FORBIDDEN_CALLS for node in scorer_nodes):
        raise ValueError("archived scorer contains a forbidden dynamic call")
    namespace: dict[str, Any] = {"np": np, "FEE": source.config.fee_per_side,
                                 "datetime": datetime, "timezone": timezone,
                                 "timedelta": timedelta}
    exec(compile(ast.Module(body=scorer_nodes, type_ignores=[]), str(optimize_path), "exec"), namespace, namespace)
    trades = []
    for candidate in candidates:
        if not candidate.level_pass:
            continue
        exit_ms, return_fraction = namespace["outcome"](
            candidate.source_row, source.config.stop_fraction, source.config.exit_rule)
        trades.append((candidate.observed_ms - 2 * HOUR_MS, candidate.symbol,
                       exit_ms, return_fraction))
    trades.sort()
    equity, drawdown = namespace["portfolio"](
        trades, source.config.slots, source.config.leverage, source.config.starting_equity)
    return ArchivedAuditResult(source, len(trades), equity, drawdown, tuple(trades))
