"""ATR volatility regime description."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.regime.common import RegimeSnapshotStrategy, bounded_strategy_fit, clamp


class AtrVolatilityRegimeStrategy(RegimeSnapshotStrategy):
    strategy_id = "atr_volatility_regime"
    required_inputs = ("atr", "relative_volume", "economic_event_state")

    def regime_evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        atr = float(snapshot.atr["1m"] or 0.0)
        atr_percent = atr / snapshot.last_price
        relative_volume = float(snapshot.relative_volume["1m"] or 0.0)
        event_state = str(snapshot.economic_event_state.get("state") or "none").lower()
        event_active = bool(snapshot.economic_event_state.get("active") or event_state in {"active", "blocked", "halt"})
        if event_state in {"blocked", "halt"} or atr_percent >= 0.045:
            volatility = "EXTREME"
            label = "event_or_extreme_volatility"
            confidence = 0.92
        elif atr_percent >= 0.025 or relative_volume >= 2.0 or event_active:
            volatility = "HIGH"
            label = "high_volatility"
            confidence = 0.78
        elif atr_percent <= 0.006 and relative_volume <= 0.75:
            volatility = "LOW"
            label = "low_volatility"
            confidence = 0.74
        else:
            volatility = "NORMAL"
            label = "normal_volatility"
            confidence = 0.65
        expansion = clamp((atr_percent / 0.02 + relative_volume / 1.5) / 2.0, 0.0, 2.0)
        high_risk_penalty = 0.4 if volatility == "EXTREME" else 0.0
        return {
            "regimeLabel": label,
            "direction": 0,
            "volatility": volatility,
            "regimeConfidence": confidence,
            "atr": atr,
            "atrPercent": round(atr_percent, 6),
            "relativeVolume": relative_volume,
            "eventState": event_state,
            "eventActive": event_active,
            "strategyFit": bounded_strategy_fit(
                {
                    "TREND": 1.0 - high_risk_penalty,
                    "BREAKOUT": clamp(0.75 + expansion * 0.35 - high_risk_penalty, 0.0, 1.7),
                    "REVERSAL": clamp(1.0 + expansion * 0.1 - high_risk_penalty, 0.0, 1.4),
                    "MEAN_REVERSION": clamp(1.25 - expansion * 0.25 - high_risk_penalty, 0.0, 1.5),
                    "GAP_SESSION": clamp(1.0 - high_risk_penalty, 0.0, 1.2),
                }
            ),
            "reasonCodes": (f"meta_strategy.regime.atr.{label}",),
        }
