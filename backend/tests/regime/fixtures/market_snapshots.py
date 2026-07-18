from __future__ import annotations

from copy import deepcopy

from backend.app.algorithms.regime.classifier import classify_market_regime
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot

from backend.tests.regime.fixtures.candles import candles


def snapshot(trend: str = "up", *, count: int = 70, context: dict | None = None, hour: int = 15):
    payload = {"symbol": "SPY", "primaryCandles": candles(count, trend=trend, hour=hour)}
    if context:
        payload["contextFeeds"] = context
    return build_regime_market_snapshot(payload)


def classified_snapshot(trend: str = "up", *, context: dict | None = None, hour: int = 15):
    market = snapshot(trend, context=context, hour=hour)
    return market, classify_market_regime(market)


def frozen_repr(value) -> str:
    return repr(deepcopy(value))

