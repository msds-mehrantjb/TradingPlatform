from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.common import DirectionalSnapshotStrategy


class VolatilityBreakoutStrategy(DirectionalSnapshotStrategy):
    strategy_id = "volatility_breakout"
    family = "BREAKOUT"
    minimum_warmup = 50
    required_inputs = ("candles", "atr", "bollinger_bands", "relative_volume", "spread")
    buy_threshold = 0.70
    sell_threshold = 0.70

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        bands = snapshot.bollinger_bands.get("1m") or {}
        upper = float(bands.get("upper") or 0.0)
        lower = float(bands.get("lower") or 0.0)
        atr = float(snapshot.atr.get("1m") or 0.0)
        relvol = float(snapshot.relative_volume.get("1m") or 0.0)
        spread = float(snapshot.spread.get("basisPoints") or 0.0)
        width_percentile = float(snapshot.features.get("bollingerWidthPercentile") or 0.0)
        buy_extension = (snapshot.last_price - upper) / atr if atr and upper else 0.0
        sell_extension = (lower - snapshot.last_price) / atr if atr and lower else 0.0
        quality = (0.2 if width_percentile >= 0.80 else 0.0) + (0.2 if relvol >= 1.50 else 0.0) + (0.1 if spread <= 10 else 0.0)
        return {
            "buyExtensionAtr": buy_extension,
            "sellExtensionAtr": sell_extension,
            "bollingerWidthPercentile": width_percentile,
            "relativeVolume": relvol,
            "spreadBps": spread,
            "buyScore": (0.4 if buy_extension >= 0.20 else 0.0) + quality,
            "sellScore": (0.4 if sell_extension >= 0.20 else 0.0) + quality,
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold, "minimumExtensionAtr": 0.20, "minimumWidthPercentile": 0.80},
        }

    def regime_allows(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> bool:
        return super().regime_allows(snapshot, evidence) and float(evidence["bollingerWidthPercentile"]) >= 0.80
