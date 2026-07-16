from __future__ import annotations

from typing import Any


def position_size_for_config(config: dict[str, Any], *, equity: float, entry_price: float, stop_distance: float) -> tuple[int, float, str]:
    from backend.app import main

    return main.position_size_for_config(config, equity=equity, entry_price=entry_price, stop_distance=stop_distance)


def open_risk_managed_trade(
    *,
    side: str,
    candle: dict[str, Any],
    opening_range: dict[str, Any],
    equity: float,
    session_date: str,
    vote_summary: dict[str, Any],
    risk_config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    from backend.app import main

    return main.open_risk_managed_trade(
        side=side,
        candle=candle,
        opening_range=opening_range,
        equity=equity,
        session_date=session_date,
        vote_summary=vote_summary,
        risk_config=risk_config,
    )

