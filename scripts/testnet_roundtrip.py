"""Binance USD-M TESTNET round trip: tiny entry + exchange stop + reconcile + close + reconcile.

Usage: QUANTOS_KEY_FILE=/path/to/testnet-keys PYTHONPATH=src python scripts/testnet_roundtrip.py [SYMBOL]
Testnet only (hard-coded). Prints statuses and client ids only; never key material.
"""
from decimal import ROUND_CEILING
import json
import os
import sys

from quantos.live.client import SignedClient
from quantos.live.keys import load_keys
from quantos.live.orders import client_order_id, place_with_protection, reduce_only_close, submit
from quantos.live.reconcile import reconcile
from quantos.paper.market import parse_exchange_info
from quantos.paper.types import D, floor_step


def main(symbol="BTCUSDC"):
    client = SignedClient("testnet", load_keys(os.environ["QUANTOS_KEY_FILE"]))
    info = client.exchange_info()
    margin = "USDC" if symbol.endswith("USDC") else "USDT"
    ins = parse_exchange_info(info, margin).get(symbol)
    if ins is None:
        raise SystemExit(f"{symbol} not tradable on testnet")
    mark = D(str(client.position_risk(symbol)[0]["markPrice"]))
    step = max(ins.step, ins.market_step)
    qty = max(ins.min_qty, -floor_step(-(ins.min_notional * D("1.1") / mark), step))
    run, sig = "tn", f"rt{abs(hash(mark)) % 10**8}"
    client.set_margin_type(symbol, "ISOLATED")
    client.set_leverage(symbol, 1)
    placed = place_with_protection(client, ins, "BUY", qty, mark * D("0.95"), run, sig)
    stop_cid = placed.sl.get("clientAlgoId") or placed.sl.get("clientOrderId")
    filled = D(str(placed.entry.get("executedQty", qty)))
    first = reconcile(client, {symbol: filled}, {stop_cid})
    close = submit(client, reduce_only_close(symbol, "SELL", filled, client_order_id(run, sig, "close"), ins))
    client.cancel_algo_order(stop_cid) if client.conditional_api == "algo" else client.cancel_order(symbol, stop_cid)
    final = reconcile(client, {}, set())
    print(json.dumps({"symbol": symbol, "qty": str(qty), "entry_status": placed.entry.get("status"),
                      "stop_client_id": stop_cid, "reconcile_open_ok": first.ok,
                      "close_status": close.get("status"), "reconcile_flat_ok": final.ok,
                      "flat_report": {k: v for k, v in final.__dict__.items() if v and k != "ok"}}, default=str))


if __name__ == "__main__":
    main(*sys.argv[1:])
