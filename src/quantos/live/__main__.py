"""python -m quantos.live kill --state DIR [--flatten --mode testnet|dry-run|mainnet]"""
from __future__ import annotations

import argparse
import sys

from .client import SignedClient, check_mainnet
from .keys import load_keys
from .kill import KillSwitch, flatten


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="quantos.live")
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("kill")
    k.add_argument("--state", required=True)
    k.add_argument("--reason", default="manual kill")
    k.add_argument("--flatten", action="store_true")
    k.add_argument("--mode", choices=("testnet", "dry-run", "mainnet"), default="dry-run")
    k.add_argument("--symbols", default="")
    k.add_argument("--key-file")
    k.add_argument("--allow-mainnet", action="store_true")
    k.add_argument("--confirm-file")
    k.add_argument("--log")
    a = ap.parse_args(argv)
    KillSwitch(a.state).engage(a.reason)
    print("KILL engaged")
    if not a.flatten: return 0
    if a.mode == "mainnet": check_mainnet(a.allow_mainnet, a.confirm_file)
    keys = None if a.mode == "dry-run" else load_keys(a.key_file)
    client = SignedClient(a.mode, keys, allow_mainnet=a.allow_mainnet, confirm_file=a.confirm_file, log_path=a.log)
    res = flatten(client, [s for s in a.symbols.split(",") if s] or None)
    print(f"flattened={res.closed} errors={res.errors}")
    return 1 if res.errors else 0


if __name__ == "__main__":
    sys.exit(main())
