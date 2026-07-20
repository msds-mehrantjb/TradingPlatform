"""Shared base for Meta-Strategy context modules."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.base import SnapshotEvaluationResult, hold_result


ADJUSTMENT_LIMITS = {
    "eligibilityAdjustment": (-1.0, 1.0),
    "confidenceAdjustment": (-0.25, 0.25),
    "familyWeightMultiplier": (0.5, 1.5),
    "candidateQualityAdjustment": (-0.25, 0.25),
    "profileSelectionBias": (-1.0, 1.0),
}


class ContextSnapshotStrategy:
    strategy_id = "context_snapshot_strategy"
    family = "MARKET_CONTEXT"
    required_inputs: tuple[str, ...] = ()

    def evaluate(self, snapshot: MetaStrategyMarketSnapshot) -> SnapshotEvaluationResult:
        required_status = self.required_input_status(snapshot)
        if not snapshot.point_in_time:
            return hold_result(
                self.strategy_id,
                "meta_strategy.context.snapshot_not_point_in_time",
                family=self.family,
                evidence=clamp_adjustments(neutral_adjustments()),
                required_input_status=required_status,
            )
        if not all(required_status.values()):
            return hold_result(
                self.strategy_id,
                "meta_strategy.context.missing_required_inputs",
                family=self.family,
                evidence=_safe_missing_evidence({}),
                required_input_status=required_status,
            )
        evidence = self.evidence(snapshot)
        bounded = clamp_adjustments({**evidence, **self.adjustments(snapshot, evidence)})
        confidence = abs(float(bounded.get("confidenceAdjustment") or 0.0)) * 4.0
        return SnapshotEvaluationResult(
            strategy_id=self.strategy_id,
            signal="HOLD",
            confidence=round(min(1.0, confidence), 6),
            eligible=True,
            family=self.family,
            evidence=bounded,
            required_input_status=required_status,
            reason_codes=(f"meta_strategy.context.{self.strategy_id}.adjusted",),
        )

    def required_input_status(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, bool]:
        return {name: self.has_input(snapshot, name) for name in self.required_inputs}

    def has_input(self, snapshot: MetaStrategyMarketSnapshot, name: str) -> bool:
        if name == "qqq_iwm_context":
            return bool(snapshot.qqq_iwm_context)
        if name == "breadth":
            return bool(snapshot.breadth)
        if name == "economic_event_state":
            return bool(snapshot.economic_event_state)
        if name == "session_phase":
            return bool(snapshot.session_phase)
        if name == "spread":
            return bool(snapshot.spread)
        if name == "candles":
            return bool(snapshot.candles.get("1m"))
        if name == "moving_averages":
            return bool(snapshot.moving_averages.get("1m"))
        if name == "atr":
            return snapshot.atr.get("1m") is not None
        if name == "volume":
            return snapshot.volume > 0
        if name == "relative_volume":
            return snapshot.relative_volume.get("1m") is not None
        if name == "vwap":
            return snapshot.vwap is not None
        return snapshot.features.get(name) is not None

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        return {"limits": ADJUSTMENT_LIMITS}

    def adjustments(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> dict[str, float]:
        return neutral_adjustments()


def neutral_adjustments() -> dict[str, float]:
    return {
        "eligibilityAdjustment": 0.0,
        "confidenceAdjustment": 0.0,
        "familyWeightMultiplier": 1.0,
        "candidateQualityAdjustment": 0.0,
        "profileSelectionBias": 0.0,
    }


def clamp_adjustments(evidence: dict[str, Any]) -> dict[str, Any]:
    clamped = dict(evidence)
    for key, (minimum, maximum) in ADJUSTMENT_LIMITS.items():
        raw = float(clamped.get(key, neutral_adjustments()[key]))
        clamped[key] = max(minimum, min(maximum, raw))
    clamped["canGenerateTrade"] = False
    return clamped


def _safe_missing_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    safe = clamp_adjustments({**evidence, **neutral_adjustments()})
    safe["missingContextSafe"] = True
    return safe
