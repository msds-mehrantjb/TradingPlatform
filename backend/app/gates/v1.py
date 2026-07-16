from __future__ import annotations

from typing import Any


def signal_confirmed(candles: list[dict[str, Any]], index: int, prior_close: float, signal: str, warmup: int, bars: int, timeframe: str) -> bool:
    from backend.app import main

    return main.signal_confirmed(candles, index, prior_close, signal, warmup, bars, timeframe)

