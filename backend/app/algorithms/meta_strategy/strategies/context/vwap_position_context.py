"""VWAP position context."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.context.common import ContextSnapshotStrategy


class VwapPositionContextStrategy(ContextSnapshotStrategy):
    strategy_id = "vwap_position_context"
    required_inputs = ("vwap", "moving_averages")

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        ema20 = float(snapshot.moving_averages["1m"].get("ema20") or snapshot.last_price)
        vwap = float(snapshot.vwap or snapshot.last_price)
        vwap_distance = (snapshot.last_price - vwap) / vwap
        ema_distance = (snapshot.last_price - ema20) / ema20
        position_edge = (vwap_distance + ema_distance) / 2.0
        return {
            "vwap": vwap,
            "ema20": ema20,
            "vwapDistance": round(vwap_distance, 6),
            "ema20Distance": round(ema_distance, 6),
            "positionEdge": round(position_edge, 6),
        }

    def adjustments(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> dict[str, float]:
        edge = float(evidence["positionEdge"])
        return {
            "eligibilityAdjustment": 0.0,
            "confidenceAdjustment": edge * 8.0,
            "familyWeightMultiplier": 1.0 + edge * 5.0,
            "candidateQualityAdjustment": edge * 4.0,
            "profileSelectionBias": edge * 6.0,
        }
