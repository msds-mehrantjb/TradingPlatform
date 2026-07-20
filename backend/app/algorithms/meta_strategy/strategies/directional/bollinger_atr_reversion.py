from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.common import DirectionalSnapshotStrategy


class BollingerAtrReversionStrategy(DirectionalSnapshotStrategy):
    strategy_id = "bollinger_atr_reversion"
    family = "MEAN_REVERSION"
    minimum_warmup = 50
    required_inputs = ("candles", "bollinger_bands", "atr", "adx", "rsi")
    buy_threshold = 0.60
    sell_threshold = 0.60

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        bands = snapshot.bollinger_bands.get("1m") or {}
        lower = float(bands.get("lower") or 0.0)
        upper = float(bands.get("upper") or 0.0)
        atr = float(snapshot.atr.get("1m") or 0.0)
        adx = float(snapshot.adx.get("1m") or 100.0)
        rsi = float(snapshot.rsi.get("1m") or 50.0)
        lower_extension = (lower - snapshot.last_price) / atr if atr and lower else 0.0
        upper_extension = (snapshot.last_price - upper) / atr if atr and upper else 0.0
        buy_score = (0.35 if lower_extension >= 0.20 else 0.0) + (0.25 if rsi <= 35.0 else 0.0) + (0.15 if adx <= 28.0 else 0.0)
        sell_score = (0.35 if upper_extension >= 0.20 else 0.0) + (0.25 if rsi >= 65.0 else 0.0) + (0.15 if adx <= 28.0 else 0.0)
        return {
            "lowerBandExtensionAtr": lower_extension,
            "upperBandExtensionAtr": upper_extension,
            "rsi": rsi,
            "adx": adx,
            "buyScore": buy_score,
            "sellScore": sell_score,
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold, "minimumExtensionAtr": 0.20, "maxAdx": 28.0},
        }

    def regime_allows(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> bool:
        return super().regime_allows(snapshot, evidence) and float(evidence["adx"]) <= 28.0
