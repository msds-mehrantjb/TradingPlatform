"""Market breadth momentum context."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.context.common import ContextSnapshotStrategy


class MarketBreadthMomentumStrategy(ContextSnapshotStrategy):
    strategy_id = "market_breadth_momentum"
    required_inputs = ("breadth",)

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        average_return = float(snapshot.breadth.get("averageReturn") or 0.0)
        positive_share = float(snapshot.breadth.get("positiveShare") or 0.5)
        component_count = int(snapshot.breadth.get("componentCount") or 0)
        breadth_edge = average_return * 50.0 + (positive_share - 0.5)
        return {
            "averageReturn": round(average_return, 6),
            "positiveShare": round(positive_share, 6),
            "componentCount": component_count,
            "breadthEdge": round(breadth_edge, 6),
        }

    def adjustments(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> dict[str, float]:
        edge = float(evidence["breadthEdge"])
        participation = min(1.0, max(0.0, float(evidence["componentCount"]) / 500.0))
        return {
            "eligibilityAdjustment": -0.2 if participation < 0.05 else 0.0,
            "confidenceAdjustment": edge * 0.25,
            "familyWeightMultiplier": 1.0 + edge * 0.3,
            "candidateQualityAdjustment": edge * 0.2,
            "profileSelectionBias": edge * 0.5,
        }
