from __future__ import annotations

import json
import os
import signal
from pathlib import Path
from types import FrameType
from typing import Callable


def atomic_json(path: Path | str, payload: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


def install_shutdown_handlers(checkpoint: Callable[[str], None]) -> None:
    def handler(signum: int, _frame: FrameType | None) -> None:
        name = signal.Signals(signum).name
        checkpoint(name)
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
