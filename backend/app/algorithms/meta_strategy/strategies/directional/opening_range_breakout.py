from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.common import DirectionalSnapshotStrategy


class OpeningRangeBreakoutStrategy(DirectionalSnapshotStrategy):
    strategy_id = "opening_range_breakout"
    family = "BREAKOUT"
    minimum_warmup = 30
    required_inputs = ("candles", "atr", "relative_volume", "spread", "liquidity", "openingRangeHigh", "openingRangeLow")
    buy_threshold = 0.65
    sell_threshold = 0.65

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        high = float(snapshot.features.get("openingRangeHigh") or 0.0)
        low = float(snapshot.features.get("openingRangeLow") or 0.0)
        atr = float(snapshot.atr.get("1m") or 0.0)
        relvol = float(snapshot.relative_volume.get("1m") or 0.0)
        spread = float(snapshot.spread.get("basisPoints") or 0.0)
        breakout_up_atr = (snapshot.last_price - high) / atr if atr and high else 0.0
        breakout_down_atr = (low - snapshot.last_price) / atr if atr and low else 0.0
        quality = (0.25 if relvol >= 1.25 else 0.0) + (0.15 if spread <= 8.0 else 0.0)
        return {
            "openingRangeHigh": high,
            "openingRangeLow": low,
            "breakoutUpAtr": breakout_up_atr,
            "breakoutDownAtr": breakout_down_atr,
            "relativeVolume": relvol,
            "spreadBps": spread,
            "buyScore": (0.5 if breakout_up_atr >= 0.10 - 1e-9 else 0.0) + quality,
            "sellScore": (0.5 if breakout_down_atr >= 0.10 - 1e-9 else 0.0) + quality,
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold, "minimumBreakoutAtr": 0.10, "maxSpreadBps": 8.0},
        }

    def regime_allows(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> bool:
        return super().regime_allows(snapshot, evidence) and float(evidence["spreadBps"]) <= 8.0
