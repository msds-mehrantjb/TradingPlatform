from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.common import DirectionalSnapshotStrategy, pct_distance


class VwapTrendContinuationStrategy(DirectionalSnapshotStrategy):
    strategy_id = "vwap_trend_continuation"
    family = "TREND"
    minimum_warmup = 30
    required_inputs = ("candles", "vwap", "moving_averages", "relative_volume")
    buy_threshold = 0.50
    sell_threshold = 0.50

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        ema20 = float((snapshot.moving_averages.get("1m") or {}).get("ema20") or 0.0)
        relvol = float(snapshot.relative_volume.get("1m") or 0.0)
        vwap_bias = pct_distance(snapshot.last_price, snapshot.vwap)
        ema_bias = pct_distance(snapshot.last_price, ema20)
        buy_score = ((0.35 if vwap_bias >= 0.0015 else 0.0) + (0.25 if ema_bias >= 0.0 else 0.0) + min(0.4, relvol / 3.0)) if vwap_bias >= 0.0015 else 0.0
        sell_score = ((0.35 if vwap_bias <= -0.0015 else 0.0) + (0.25 if ema_bias <= 0.0 else 0.0) + min(0.4, relvol / 3.0)) if vwap_bias <= -0.0015 else 0.0
        return {
            "vwapBias": vwap_bias,
            "ema20Bias": ema_bias,
            "relativeVolume": relvol,
            "buyScore": buy_score,
            "sellScore": sell_score,
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold, "minimumVwapBias": 0.0015},
        }
