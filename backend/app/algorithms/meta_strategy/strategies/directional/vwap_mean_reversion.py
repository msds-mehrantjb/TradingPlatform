from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.common import DirectionalSnapshotStrategy


class VwapMeanReversionStrategy(DirectionalSnapshotStrategy):
    strategy_id = "vwap_mean_reversion"
    family = "MEAN_REVERSION"
    minimum_warmup = 40
    required_inputs = ("candles", "vwap", "adx", "rsi", "volume")
    buy_threshold = 0.58
    sell_threshold = 0.58

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        atr = float(snapshot.atr.get("1m") or 0.0)
        distance_atr = (snapshot.last_price - float(snapshot.vwap or snapshot.last_price)) / atr if atr else 0.0
        rsi = float(snapshot.rsi.get("1m") or 50.0)
        adx = float(snapshot.adx.get("1m") or 100.0)
        buy_score = (0.35 if distance_atr <= -0.75 else 0.0) + (0.25 if rsi <= 35.0 else 0.0) + (0.15 if adx <= 25.0 else 0.0)
        sell_score = (0.35 if distance_atr >= 0.75 else 0.0) + (0.25 if rsi >= 65.0 else 0.0) + (0.15 if adx <= 25.0 else 0.0)
        return {
            "distanceFromVwapAtr": distance_atr,
            "rsi": rsi,
            "adx": adx,
            "buyScore": buy_score,
            "sellScore": sell_score,
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold, "minimumDistanceAtr": 0.75, "maxAdx": 25.0},
        }

    def regime_allows(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> bool:
        return super().regime_allows(snapshot, evidence) and float(evidence["adx"]) <= 25.0
