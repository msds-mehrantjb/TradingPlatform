from __future__ import annotations

from typing import Any


def strategy_catalog() -> list[str]:
    from backend.app import main

    return list(main.VOTING_STRATEGY_NAMES)


def strategy_fits(history: list[dict[str, Any]], prior_close: float, *, timeframe: str = "") -> list[dict[str, Any]]:
    from backend.app import main

    return main.historical_strategy_fits(history, prior_close, timeframe=timeframe)


def strategy_signal(name: str, history: list[dict[str, Any]], prior_close: float, *, timeframe: str = "") -> str:
    from backend.app import main

    return main.historical_strategy_signal(name, history, prior_close, timeframe=timeframe)


def strategy_votes(history: list[dict[str, Any]], prior_close: float, *, timeframe: str = "") -> list[str]:
    from backend.app import main

    return main.historical_strategy_votes(history, prior_close, timeframe=timeframe)

