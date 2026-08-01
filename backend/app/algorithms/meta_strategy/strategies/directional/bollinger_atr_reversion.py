from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.common import DirectionalSnapshotStrategy, latest_close, structural_invalidation


class BollingerAtrReversionStrategy(DirectionalSnapshotStrategy):
    strategy_id = "bollinger_atr_reversion"
    family = "MEAN_REVERSION"
    required_inputs = ("candles", "bollinger_bands", "atr", "adx", "rsi", "rejectionWickRatio")

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        bands = snapshot.bollinger_bands.get("1m") or {}
        lower = float(bands.get("lower") or 0.0)
        upper = float(bands.get("upper") or 0.0)
        atr = float(snapshot.atr.get("1m") or 0.0)
        adx = float(snapshot.adx.get("1m") or 100.0)
        rsi = float(snapshot.rsi.get("1m") or 50.0)
        middle = float(bands.get("middle") or latest_close(snapshot))
        lower_extension = (lower - latest_close(snapshot)) / atr if atr and lower else 0.0
        upper_extension = (latest_close(snapshot) - upper) / atr if atr and upper else 0.0
        distance_from_mean_atr = abs(latest_close(snapshot) - middle) / atr if atr else 0.0
        event_blackout = bool(snapshot.economic_event_state.get("active")) or str(snapshot.economic_event_state.get("state") or "").lower() in {"blocked", "halt"}
        volatility_shock = float(snapshot.relative_volume.get("1m") or 0.0) >= 4.0 or (atr / latest_close(snapshot) if latest_close(snapshot) else 0.0) >= 0.04
        non_trending = adx <= 28.0
        buy_valid = lower_extension >= 0.20 and distance_from_mean_atr >= 0.35 and rsi <= 35.0 and non_trending and not event_blackout and not volatility_shock
        sell_valid = upper_extension >= 0.20 and distance_from_mean_atr >= 0.35 and rsi >= 65.0 and non_trending and not event_blackout and not volatility_shock
        score = 0.35 + (0.15 if distance_from_mean_atr >= 0.35 else 0.0) + (0.2 if non_trending else 0.0) + (0.15 if rsi <= 35.0 or rsi >= 65.0 else 0.0)
        return {
            "meanReference": middle,
            "distanceFromMeanAtr": distance_from_mean_atr,
            "lowerBandExtensionAtr": lower_extension,
            "upperBandExtensionAtr": upper_extension,
            "rsi": rsi,
            "adx": adx,
            "nonTrendingRegime": non_trending,
            "economicEventBlackout": event_blackout,
            "volatilityShock": volatility_shock,
            "meanTarget": middle,
            "entryReference": latest_close(snapshot),
            "invalidationReference": structural_invalidation(snapshot, "BUY" if buy_valid else "SELL"),
            "suggestedStopReference": structural_invalidation(snapshot, "BUY" if buy_valid else "SELL"),
            "buyScore": score if buy_valid else 0.0,
            "sellScore": score if sell_valid else 0.0,
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold, "minimumExtensionAtr": 0.20, "minimumMeanDistanceAtr": 0.35, "maxAdx": 28.0},
        }

    def regime_allows(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> bool:
        return super().regime_allows(snapshot, evidence) and float(evidence["adx"]) <= 28.0
