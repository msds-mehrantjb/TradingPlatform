from __future__ import annotations

from typing import Any


def condition_tags(history: list[dict[str, Any]], prior_close: float, *, timeframe: str = "") -> set[str]:
    from backend.app import main

    return main.historical_condition_tags(history, prior_close, timeframe=timeframe)


def regime_label(history: list[dict[str, Any]]) -> str:
    from backend.app import main

    return main.historical_regime_label(history)


def opening_range(history: list[dict[str, Any]], count: int) -> dict[str, float]:
    from backend.app import main

    return main.opening_range_values(history, count)


def average_true_range(history: list[dict[str, Any]], period: int) -> float | None:
    from backend.app import main

    return main.average_true_range(history, period)

