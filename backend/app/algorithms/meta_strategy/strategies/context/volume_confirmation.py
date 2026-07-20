"""Volume confirmation context."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.context.common import ContextSnapshotStrategy


class VolumeConfirmationStrategy(ContextSnapshotStrategy):
    strategy_id = "volume_confirmation"
    required_inputs = ("volume", "relative_volume")

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        relative_volume = float(snapshot.relative_volume.get("1m") or 1.0)
        volume_edge = relative_volume - 1.0
        return {
            "volume": snapshot.volume,
            "relativeVolume": round(relative_volume, 6),
            "volumeEdge": round(volume_edge, 6),
        }

    def adjustments(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> dict[str, float]:
        edge = float(evidence["volumeEdge"])
        thin_penalty = -0.4 if float(evidence["volume"]) <= 0.0 or float(evidence["relativeVolume"]) < 0.35 else 0.0
        return {
            "eligibilityAdjustment": thin_penalty,
            "confidenceAdjustment": edge * 0.12 + thin_penalty * 0.2,
            "familyWeightMultiplier": 1.0 + edge * 0.15 + thin_penalty * 0.25,
            "candidateQualityAdjustment": edge * 0.1 + thin_penalty * 0.2,
            "profileSelectionBias": edge * 0.25 + thin_penalty,
        }
