from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.common import DirectionalSnapshotStrategy, pct_distance


class MultiTimeframeTrendAlignmentStrategy(DirectionalSnapshotStrategy):
    strategy_id = "multi_timeframe_trend_alignment"
    family = "TREND"
    minimum_warmup = 50
    required_inputs = ("candles", "moving_averages", "vwap", "adx")
    buy_threshold = 0.60
    sell_threshold = 0.60

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        ema20_1m = _ma(snapshot, "1m", "ema20")
        ema50_1m = _ma(snapshot, "1m", "ema50")
        ema20_5m = _ma(snapshot, "5m", "ema20")
        ema50_5m = _ma(snapshot, "5m", "ema50")
        ema20_15m = _ma(snapshot, "15m", "ema20")
        ema50_15m = _ma(snapshot, "15m", "ema50")
        aligned_up = sum(1 for fast, slow in ((ema20_1m, ema50_1m), (ema20_5m, ema50_5m), (ema20_15m, ema50_15m)) if fast and slow and fast > slow)
        aligned_down = sum(1 for fast, slow in ((ema20_1m, ema50_1m), (ema20_5m, ema50_5m), (ema20_15m, ema50_15m)) if fast and slow and fast < slow)
        adx = float(snapshot.adx.get("1m") or 0.0)
        vwap_bias = pct_distance(snapshot.last_price, snapshot.vwap)
        trend_strength = min(1.0, adx / 40.0)
        return {
            "alignedUpTimeframes": aligned_up,
            "alignedDownTimeframes": aligned_down,
            "adx": adx,
            "vwapBias": vwap_bias,
            "buyScore": min(1.0, (aligned_up / 3.0) * 0.75 + trend_strength * 0.25) if vwap_bias >= 0 else 0.0,
            "sellScore": min(1.0, (aligned_down / 3.0) * 0.75 + trend_strength * 0.25) if vwap_bias <= 0 else 0.0,
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold, "minimumAdx": 18.0},
        }

    def regime_allows(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> bool:
        return super().regime_allows(snapshot, evidence) and float(evidence["adx"]) >= 18.0


def _ma(snapshot: MetaStrategyMarketSnapshot, timeframe: str, name: str) -> float | None:
    value = (snapshot.moving_averages.get(timeframe) or {}).get(name)
    return float(value) if value is not None else None
