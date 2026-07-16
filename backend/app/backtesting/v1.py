from __future__ import annotations

from pathlib import Path
from typing import Any


def run_voting_ensemble_backtest(candles: list[dict[str, Any]], *, timeframe: str, ml_filter: dict[str, Any] | None = None, risk_config_override: dict[str, Any] | None = None) -> dict[str, Any]:
    from backend.app import main

    return main.run_voting_ensemble_backtest(
        candles,
        timeframe=timeframe,
        ml_filter=ml_filter,
        risk_config_override=risk_config_override,
    )


def cached_voting_ensemble_backtest(*, data_path: Path, manifest: dict[str, Any], timeframe: str, start_date: str, end_date: str) -> dict[str, Any]:
    from backend.app import main

    return main.cached_voting_ensemble_backtest(
        data_path=data_path,
        manifest=manifest,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
    )

