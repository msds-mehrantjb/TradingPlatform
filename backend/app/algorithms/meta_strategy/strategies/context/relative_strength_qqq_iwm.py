"""Relative-strength context against QQQ and IWM."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.context.common import ContextSnapshotStrategy


class RelativeStrengthQqqIwmStrategy(ContextSnapshotStrategy):
    strategy_id = "relative_strength_qqq_iwm"
    required_inputs = ("qqq_iwm_context",)

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        spy_vs_qqq = float(snapshot.qqq_iwm_context.get("spyVsQqq") or 1.0) - 1.0
        spy_vs_iwm = float(snapshot.qqq_iwm_context.get("spyVsIwm") or 1.0) - 1.0
        edge = (spy_vs_qqq + spy_vs_iwm) / 2.0
        return {
            "spyVsQqq": round(spy_vs_qqq, 6),
            "spyVsIwm": round(spy_vs_iwm, 6),
            "relativeStrengthEdge": round(edge, 6),
        }

    def adjustments(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> dict[str, float]:
        edge = float(evidence["relativeStrengthEdge"])
        return {
            "eligibilityAdjustment": 0.0,
            "confidenceAdjustment": edge * 5.0,
            "familyWeightMultiplier": 1.0 + edge * 4.0,
            "candidateQualityAdjustment": edge * 3.0,
            "profileSelectionBias": edge * 4.0,
        }
