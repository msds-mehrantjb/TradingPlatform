from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.common import DirectionalSnapshotStrategy, candles, latest_close, ma, pct_distance, reward_to_risk, slope, structural_invalidation


class VwapTrendContinuationStrategy(DirectionalSnapshotStrategy):
    strategy_id = "vwap_trend_continuation"
    family = "TREND"
    required_inputs = ("candles", "vwap", "moving_averages", "market_structure", "relative_volume", "vwap_relationship", "vwap_slope")

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        ema20 = ma(snapshot, "1m", "ema20") or 0.0
        ema50 = ma(snapshot, "1m", "ema50") or ema20
        relvol = float(snapshot.relative_volume.get("1m") or 0.0)
        vwap_bias = pct_distance(latest_close(snapshot), snapshot.vwap)
        ema_bias = pct_distance(latest_close(snapshot), ema20)
        vwap_slope = float(snapshot.features.get("vwapSlope") or 0.0)
        rows = candles(snapshot, "1m")
        recent = rows[-5:]
        atr = float(snapshot.atr.get("1m") or 0.0)
        consolidation = bool(recent) and (max(float(row["high"]) for row in recent) - min(float(row["low"]) for row in recent)) <= max(atr * 1.2, 0.01)
        pullback_depth = float(snapshot.features.get("pullbackDepthAtr") or 0.0)
        continuation_buy = len(rows) >= 2 and float(rows[-1]["close"]) > max(float(rows[-2]["high"]), ema20)
        continuation_sell = len(rows) >= 2 and float(rows[-1]["close"]) < min(float(rows[-2]["low"]), ema20)
        trend_up = ema20 > ema50 and slope(snapshot, "1m") >= 0 and vwap_slope >= 0
        trend_down = ema20 < ema50 and slope(snapshot, "1m") <= 0 and vwap_slope <= 0
        extension_ok = abs(vwap_bias) <= 0.012
        volume_ok = relvol >= 0.85
        score = 0.35 + (0.2 if consolidation or 0.15 <= pullback_depth <= 1.25 else 0.0) + (0.2 if volume_ok else 0.0) + (0.15 if extension_ok else 0.0)
        buy_valid = trend_up and vwap_bias > 0 and ema_bias >= 0 and continuation_buy and extension_ok and volume_ok
        sell_valid = trend_down and vwap_bias < 0 and ema_bias <= 0 and continuation_sell and extension_ok and volume_ok
        return {
            "trendRegime": "TREND_UP" if trend_up else "TREND_DOWN" if trend_down else "NOT_TREND",
            "vwapBias": vwap_bias,
            "vwapSlope": vwap_slope,
            "ema20Bias": ema_bias,
            "movingAverageConfirmation": {"ema20": ema20, "ema50": ema50, "trendUp": trend_up, "trendDown": trend_down},
            "structureConfirmation": {"continuationBuy": continuation_buy, "continuationSell": continuation_sell},
            "pullbackOrConsolidation": {"pullbackDepthAtr": pullback_depth, "consolidation": consolidation},
            "relativeVolume": relvol,
            "extensionFromVwapOk": extension_ok,
            "rewardToRisk": {"buy": reward_to_risk(snapshot, "BUY"), "sell": reward_to_risk(snapshot, "SELL")},
            "entryReference": latest_close(snapshot),
            "invalidationReference": structural_invalidation(snapshot, "BUY" if trend_up else "SELL"),
            "suggestedStopReference": structural_invalidation(snapshot, "BUY" if trend_up else "SELL"),
            "buyScore": score if buy_valid else 0.0,
            "sellScore": score if sell_valid else 0.0,
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold, "minimumVwapBias": 0.0015, "maxVwapExtension": 0.012},
        }
