#!/usr/bin/env python3
"""Fail-closed policy wiring validation; no credentials or trading access."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

PIN = "c76e77af00ef53472a9da8f66dae7fdd2d3e5928"
MODEL_RELATIVE = "live/models/btc_snapshot_model_s199_cost101_daily_stop120_profile.json"
PROFILE = "s199_cost101_daily_stop120"
# Upstream S199 execution_policy key -> environment key.
ENV_MAPPING = {
    "trade_size": "LIVE_BTC_SNAPSHOT_TRADE_SIZE",
    "edge": "LIVE_BTC_SNAPSHOT_EDGE",
    "min_ask_price": "LIVE_BTC_MIN_ASK_PRICE",
    "max_ask_price": "LIVE_BTC_MAX_ASK_PRICE",
    "max_spread": "LIVE_BTC_MAX_SPREAD",
    "min_visible_size": "LIVE_BTC_MIN_VISIBLE_SIZE",
    "min_selected_probability": "LIVE_BTC_MIN_SELECTED_PROBABILITY",
    "expensive_ask_floor": "LIVE_BTC_EXPENSIVE_ASK_FLOOR",
    "expensive_min_selected_probability": "LIVE_BTC_EXPENSIVE_MIN_SELECTED_PROBABILITY",
    "expensive_min_signed_momentum_30s": "LIVE_BTC_EXPENSIVE_MIN_SIGNED_MOMENTUM_30S",
    "adverse_price_diff_floor": "LIVE_BTC_ADVERSE_PRICE_DIFF_FLOOR",
    "adverse_min_signed_momentum_30s": "LIVE_BTC_ADVERSE_MIN_SIGNED_MOMENTUM_30S",
    "exhausted_price_diff_floor": "LIVE_BTC_EXHAUSTED_PRICE_DIFF_FLOOR",
    "exhausted_min_selected_probability": "LIVE_BTC_EXHAUSTED_MIN_SELECTED_PROBABILITY",
    "volatile_price_diff_floor": "LIVE_BTC_VOLATILE_PRICE_DIFF_FLOOR",
    "volatile_min_selected_probability": "LIVE_BTC_VOLATILE_MIN_SELECTED_PROBABILITY",
    "max_yes_no_ask_cost": "LIVE_BTC_MAX_YES_NO_ASK_COST",
    "momentum_alignment": "LIVE_BTC_SNAPSHOT_MOMENTUM_ALIGNMENT",
    "daily_stop_loss": "LIVE_BTC_DAILY_STOP_LOSS",
}


def load_key_values(path: Path) -> dict[str, str]:
    d: dict[str,str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid config line: {line[:80]}")
        name,value = line.split("=",1)
        if not name.isidentifier() or any(c in value for c in "$`\"' ;(){}"): # prevent shell expansion/code
            raise ValueError(f"Unsafe config line: {name}")
        if name in d:
            raise ValueError(f"Repeated setting: {name}")
        d[name] = value
    return d


def verify_policy(upstream: Path, config_file: Path) -> tuple[int,int]:
    model = upstream/MODEL_RELATIVE
    if not model.is_file():
        raise ValueError(f"Pinned S199 profile missing: {model}")
    j = json.loads(model.read_text())
    dp = j["deployment_profile"]
    if dp["profile_name"] != PROFILE or dp["model_source"] != "live/models/btc_snapshot_model_s150_ev_guarded_cached_432_summary.json":
        raise ValueError("Unexpected S199 profile identity/model source")
    cfg = load_key_values(config_file)
    if cfg.get("LIVE_BTC_SNAPSHOT_MODEL_PATH") != MODEL_RELATIVE:
        raise ValueError("Wrong model artifact path")
    if cfg.get("LIVE_SANDBOX_STARTING_BALANCE") != "200":
        raise ValueError("Virtual starting balance must equal 200")
    matched=0
    for key,env in ENV_MAPPING.items():
        source = dp["execution_policy"][key]
        raw = cfg[env]
        if isinstance(source,str):
            good=source == raw
        else:
            good=abs(float(source)-float(raw)) < 1e-10
        if not good:
            raise ValueError(f"S199 policy mismatch: {key} upstream={source!r} config={raw!r}")
        matched+=1
    if dp["execution_policy"]["snapshot_seconds"] != [60]:
        raise ValueError("Expected S199 snapshot_seconds [60]")
    # Upstream runner has 60 as a hard-coded value (not environment-configurable).
    if cfg.get("LIVE_BTC_DATA_SOURCE") != "binance-global":
        raise ValueError("The archived S199 context uses Binance global, not US")
    if cfg.get("LIVE_BTC_MAX_FEATURE_AGE_SECONDS") != "30":
        raise ValueError("Unexpected Binance input freshness parameter")
    return matched,len(dp["execution_policy"])


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--upstream",type=Path,required=True)
    p.add_argument("--config",type=Path,default=Path(__file__).with_name("config.env"))
    p.add_argument("--offline",action="store_true")
    a=p.parse_args()
    if not (a.upstream/".git").exists():
        p.error("Upstream git repository is not installed")
    got=subprocess.check_output(["git","-C",str(a.upstream),"rev-parse","HEAD"],text=True).strip()
    if got != PIN:
        p.error(f"Unexpected upstream revision {got}. Expected SHA {PIN}")
    if (a.upstream/".env").exists():
        p.error("Unsafe unreviewed upstream .env")
    try:
        matched,policy_count=verify_policy(a.upstream,a.config)
    except (ValueError,KeyError,TypeError,FileNotFoundError) as e:
        p.error(str(e))
    print(f"PASS: source SHA {got}")
    print(f"PASS: verified {matched} config policy parameters against S199 ({policy_count} source policy keys; other engine parameters are upstream defaults)")
    print("PASS: sandbox-only launch with no wallet credentials; new $200 virtual account per process")
    print("WARNING: model was trained under older settlement rules; NO LIVE TRADING; account not restart-safe")
    print("WARNING: market API reachability and package installation remain untested in this offline doctor")

if __name__ == "__main__":
    main()
