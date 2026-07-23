"""Composite ADX/ATR regime classifier for production Meta-Strategy inventory."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.regime.common import RegimeSnapshotStrategy, bounded_strategy_fit, clamp


class AdxAtrRegimeClassifierStrategy(RegimeSnapshotStrategy):
    strategy_id = "adx_atr_regime_classifier"
    required_inputs = ("adx", "atr", "moving_averages", "relative_volume", "economic_event_state")

    def regime_evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        adx = float(snapshot.adx["1m"])
        atr = float(snapshot.atr["1m"] or 0.0)
        atr_percent = atr / snapshot.last_price if snapshot.last_price else 0.0
        relative_volume = float(snapshot.relative_volume["1m"] or 0.0)
        ema20 = float(snapshot.moving_averages["1m"].get("ema20") or snapshot.last_price)
        ema50 = float(snapshot.moving_averages["1m"].get("ema50") or ema20)
        event_state = str(snapshot.economic_event_state.get("state") or "none").lower()
        event_active = bool(snapshot.economic_event_state.get("active") or event_state in {"active", "blocked", "halt"})

        direction = 1 if ema20 > ema50 else -1 if ema20 < ema50 else 0
        if adx >= 35.0:
            trend_label = "strong_trend"
            trend_confidence = 0.9
        elif adx >= 22.0:
            trend_label = "trend"
            trend_confidence = 0.72
        elif adx <= 15.0:
            trend_label = "range"
            trend_confidence = 0.75
            direction = 0
        else:
            trend_label = "transition"
            trend_confidence = 0.55

        if event_state in {"blocked", "halt"} or atr_percent >= 0.045:
            volatility = "EXTREME"
            volatility_label = "event_or_extreme_volatility"
            volatility_confidence = 0.92
        elif atr_percent >= 0.025 or relative_volume >= 2.0 or event_active:
            volatility = "HIGH"
            volatility_label = "high_volatility"
            volatility_confidence = 0.78
        elif atr_percent <= 0.006 and relative_volume <= 0.75:
            volatility = "LOW"
            volatility_label = "low_volatility"
            volatility_confidence = 0.74
        else:
            volatility = "NORMAL"
            volatility_label = "normal_volatility"
            volatility_confidence = 0.65

        trend_fit = clamp(adx / 25.0, 0.5, 1.6)
        range_fit = clamp((25.0 - adx) / 15.0, 0.4, 1.4)
        expansion = clamp((atr_percent / 0.02 + relative_volume / 1.5) / 2.0, 0.0, 2.0)
        high_risk_penalty = 0.4 if volatility == "EXTREME" else 0.0

        return {
            "regimeLabel": f"{trend_label}.{volatility_label}",
            "direction": direction,
            "volatility": volatility,
            "regimeConfidence": round(max(trend_confidence, volatility_confidence), 6),
            "adx": adx,
            "atr": atr,
            "atrPercent": round(atr_percent, 6),
            "relativeVolume": relative_volume,
            "ema20": ema20,
            "ema50": ema50,
            "eventState": event_state,
            "eventActive": event_active,
            "evidenceAxes": ("Trend strength", "Volatility level", "Structure", "Liquidity", "Session", "Event risk"),
            "strategyFit": bounded_strategy_fit(
                {
                    "TREND": clamp(trend_fit - high_risk_penalty, 0.0, 1.6),
                    "BREAKOUT": clamp((trend_fit * 0.9) + expansion * 0.2 - high_risk_penalty, 0.0, 1.7),
                    "REVERSAL": clamp((range_fit * 0.8) + expansion * 0.1 - high_risk_penalty, 0.0, 1.2),
                    "MEAN_REVERSION": clamp(range_fit + 0.15 - expansion * 0.2 - high_risk_penalty, 0.0, 1.5),
                    "GAP_SESSION": clamp(1.0 - high_risk_penalty, 0.0, 1.2),
                }
            ),
            "reasonCodes": (
                f"meta_strategy.regime.adx.{trend_label}",
                f"meta_strategy.regime.atr.{volatility_label}",
            ),
        }
