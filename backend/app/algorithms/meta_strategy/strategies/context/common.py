"""Shared base for Meta-Strategy context modules."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.evaluation_context import MetaStrategyEvaluationContext, context_market_snapshot
from backend.app.algorithms.meta_strategy.feature_contracts import has_required_input, required_input_status
from backend.app.algorithms.meta_strategy.settings import MetaStrategyContextSettings
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

    def __init__(
        self,
        settings: MetaStrategyContextSettings | None = None,
        *,
        settings_version: str = "meta_strategy_settings_v1",
        effective_settings_hash: str = "meta_strategy_settings_unresolved",
    ) -> None:
        self.context_settings = settings or MetaStrategyContextSettings()
        self.settings_version = settings_version
        self.effective_settings_hash = effective_settings_hash

    def evaluate(self, value: MetaStrategyMarketSnapshot | MetaStrategyEvaluationContext) -> SnapshotEvaluationResult:
        snapshot = context_market_snapshot(value)
        required_status = self.required_input_status(snapshot)
        if not snapshot.point_in_time:
            return hold_result(
                self.strategy_id,
                "meta_strategy.context.snapshot_not_point_in_time",
                family=self.family,
                settings_version=snapshot.settings_version,
                effective_settings_hash=snapshot.effective_settings_hash,
                evidence=clamp_adjustments(neutral_adjustments()),
                required_input_status=required_status,
            )
        if not all(required_status.values()):
            return hold_result(
                self.strategy_id,
                "meta_strategy.context.missing_required_inputs",
                family=self.family,
                settings_version=snapshot.settings_version,
                effective_settings_hash=snapshot.effective_settings_hash,
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
            settings_version=snapshot.settings_version,
            effective_settings_hash=snapshot.effective_settings_hash,
            family=self.family,
            evidence=bounded,
            required_input_status=required_status,
            reason_codes=(f"meta_strategy.context.{self.strategy_id}.adjusted",),
        )

    def required_input_status(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, bool]:
        return required_input_status(snapshot, self.required_inputs)

    def has_input(self, snapshot: MetaStrategyMarketSnapshot, name: str) -> bool:
        return has_required_input(snapshot, name)

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
