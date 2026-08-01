from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.common import DirectionalSnapshotStrategy, candle_high, candle_low, latest_close, structural_invalidation


class VwapMeanReversionStrategy(DirectionalSnapshotStrategy):
    strategy_id = "vwap_mean_reversion"
    family = "MEAN_REVERSION"
    required_inputs = ("candles", "vwap", "atr", "adx", "rsi", "volume", "vwap_relationship", "reclaimDistanceAtr")

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        atr = float(snapshot.atr.get("1m") or 0.0)
        vwap = float(snapshot.vwap or latest_close(snapshot))
        distance_atr = (latest_close(snapshot) - vwap) / atr if atr else 0.0
        rsi = float(snapshot.rsi.get("1m") or 50.0)
        adx = float(snapshot.adx.get("1m") or 100.0)
        vwap_slope = float(snapshot.features.get("vwapSlope") or 0.0)
        low_trend = adx <= 25.0 and abs(vwap_slope) <= 0.0008
        exhaustion_buy = rsi <= 35.0 or bool(snapshot.features.get("vwapReclaimTriggerBuy"))
        exhaustion_sell = rsi >= 65.0 or bool(snapshot.features.get("vwapReclaimTriggerSell"))
        volume_ok = float(snapshot.relative_volume.get("1m") or 0.0) >= 0.6
        liquidity_ok = float(snapshot.liquidity.get("score") or 0.0) >= 0.45
        continuation_stronger = bool(snapshot.features.get("trendContinuationStronger"))
        buy_valid = distance_atr <= -0.75 and low_trend and exhaustion_buy and volume_ok and liquidity_ok and not continuation_stronger
        sell_valid = distance_atr >= 0.75 and low_trend and exhaustion_sell and volume_ok and liquidity_ok and not continuation_stronger
        score = 0.35 + (0.2 if low_trend else 0.0) + (0.15 if volume_ok and liquidity_ok else 0.0) + (0.15 if exhaustion_buy or exhaustion_sell else 0.0)
        return {
            "vwap": vwap,
            "distanceFromVwapAtr": distance_atr,
            "vwapSlope": vwap_slope,
            "lowTrendRegime": low_trend,
            "exhaustionOrReclaimTrigger": {"buy": exhaustion_buy, "sell": exhaustion_sell},
            "volumeAndLiquidityOk": volume_ok and liquidity_ok,
            "trendContinuationEvidenceStronger": continuation_stronger,
            "meanTarget": vwap,
            "entryReference": latest_close(snapshot),
            "invalidationReference": candle_low(snapshot) - max(0.01, atr * 0.1) if buy_valid else candle_high(snapshot) + max(0.01, atr * 0.1),
            "suggestedStopReference": structural_invalidation(snapshot, "BUY" if buy_valid else "SELL"),
            "rsi": rsi,
            "adx": adx,
            "buyScore": score if buy_valid else 0.0,
            "sellScore": score if sell_valid else 0.0,
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold, "minimumDistanceAtr": 0.75, "maxAdx": 25.0, "maxAbsVwapSlope": 0.0008},
        }

    def regime_allows(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> bool:
        return super().regime_allows(snapshot, evidence) and float(evidence["adx"]) <= 25.0
