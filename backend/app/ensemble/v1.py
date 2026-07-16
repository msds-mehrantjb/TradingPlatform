from __future__ import annotations

from typing import Any


def vote_summary(history: list[dict[str, Any]], prior_close: float, *, timeframe: str = "") -> dict[str, Any]:
    from backend.app import main

    return main.historical_vote_summary(history, prior_close, timeframe=timeframe)


def winner_signal(history: list[dict[str, Any]], prior_close: float) -> str:
    from backend.app import main

    return main.historical_winner_signal(history, prior_close)

