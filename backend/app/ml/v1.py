from __future__ import annotations

from typing import Any


def forecast_prediction(candles: list[dict[str, Any]], microstructure_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    from backend.app.market_forecast import market_forecast_prediction

    return market_forecast_prediction(candles, microstructure_rows=microstructure_rows or [])


def dynamic_ml_comparison(replay_data: dict[str, list[dict[str, Any]]], backtests: dict[str, dict[str, Any]], risk_config: dict[str, Any], *, symbol: str) -> dict[str, Any]:
    from backend.app import main

    return main.dynamic_ml_comparison(replay_data, backtests, risk_config, symbol=symbol)

