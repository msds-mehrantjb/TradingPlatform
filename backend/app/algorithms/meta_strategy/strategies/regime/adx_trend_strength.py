"""ADX trend-strength regime description."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.regime.common import RegimeSnapshotStrategy, bounded_strategy_fit, clamp


class AdxTrendStrengthRegimeStrategy(RegimeSnapshotStrategy):
    strategy_id = "adx_trend_strength_regime"
    required_inputs = ("adx", "atr", "moving_averages")

    def regime_evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        adx = float(snapshot.adx["1m"])
        atr = float(snapshot.atr["1m"] or 0.0)
        ema20 = float(snapshot.moving_averages["1m"].get("ema20") or snapshot.last_price)
        ema50 = float(snapshot.moving_averages["1m"].get("ema50") or ema20)
        direction = 1 if ema20 > ema50 else -1 if ema20 < ema50 else 0
        if adx >= 35.0:
            label = "strong_trend"
            confidence = 0.9
        elif adx >= 22.0:
            label = "trend"
            confidence = 0.72
        elif adx <= 15.0:
            label = "range"
            confidence = 0.75
            direction = 0
        else:
            label = "transition"
            confidence = 0.55
        volatility = "HIGH" if atr / snapshot.last_price >= 0.025 else "NORMAL"
        trend_fit = clamp(adx / 25.0, 0.5, 1.6)
        range_fit = clamp((25.0 - adx) / 15.0, 0.4, 1.4)
        return {
            "regimeLabel": label,
            "direction": direction,
            "volatility": volatility,
            "regimeConfidence": confidence,
            "adx": adx,
            "atrPercent": round(atr / snapshot.last_price, 6),
            "ema20": ema20,
            "ema50": ema50,
            "strategyFit": bounded_strategy_fit(
                {
                    "TREND": trend_fit,
                    "BREAKOUT": clamp(trend_fit * 0.9, 0.5, 1.5),
                    "REVERSAL": clamp(range_fit * 0.8, 0.4, 1.2),
                    "MEAN_REVERSION": range_fit,
                    "GAP_SESSION": 1.0,
                }
            ),
            "reasonCodes": (f"meta_strategy.regime.adx.{label}",),
        }
