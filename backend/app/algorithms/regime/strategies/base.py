"""Shared Regime strategy primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from backend.app.algorithms.regime.contracts import RegimeClassification, RegimeMarketSnapshot, RegimeStrategyEvaluation, StrategyRole


StrategyEvaluator = Callable[[RegimeMarketSnapshot, RegimeClassification], tuple[str, float, str, dict]]


@dataclass(frozen=True)
class RegimeStrategyDefinition:
    strategy_id: str
    name: str
    family: str
    role: StrategyRole
    base_weight: float
    minimum_bars: int
    evaluator: StrategyEvaluator


def evaluate_definition(
    definition: RegimeStrategyDefinition,
    snapshot: RegimeMarketSnapshot,
    classification: RegimeClassification,
    strategy_settings: dict[str, Any] | None = None,
) -> RegimeStrategyEvaluation:
    lifecycle = _strategy_lifecycle(strategy_settings)
    if lifecycle == "disabled":
        return RegimeStrategyEvaluation(
            strategy_id=definition.strategy_id,
            name=definition.name,
            family=definition.family,
            role=definition.role,
            signal="Hold",
            confidence=0.0,
            weight=definition.base_weight,
            eligible=False,
            reason="regime.strategy.lifecycle_disabled",
            evidence={"lifecycle": lifecycle, "settingsType": (strategy_settings or {}).get("settingsType")},
        )
    if len(snapshot.candles) < definition.minimum_bars:
        return RegimeStrategyEvaluation(
            strategy_id=definition.strategy_id,
            name=definition.name,
            family=definition.family,
            role=definition.role,
            signal="Hold",
            confidence=0.0,
            weight=definition.base_weight,
            eligible=False,
            reason="regime.strategy.minimum_bars_not_met",
            evidence={"minimumBars": definition.minimum_bars, "actualBars": len(snapshot.candles), "lifecycle": lifecycle},
        )
    signal, confidence, reason, evidence = definition.evaluator(snapshot, classification)
    if definition.role != "directional":
        signal = "Hold"
    signal = signal if signal in {"Buy", "Sell", "Hold"} else "Hold"
    confidence = max(0.0, min(1.0, float(confidence)))
    eligible = True
    if lifecycle == "shadow":
        evidence = {
            "lifecycle": lifecycle,
            "shadowSignal": signal,
            "shadowConfidence": confidence,
            "shadowReason": reason,
            "evaluatorEvidence": evidence,
        }
        signal = "Hold"
        eligible = False
        reason = "regime.strategy.lifecycle_shadow_only"
    else:
        evidence = {**evidence, "lifecycle": lifecycle}
    return RegimeStrategyEvaluation(
        strategy_id=definition.strategy_id,
        name=definition.name,
        family=definition.family,
        role=definition.role,
        signal=signal,
        confidence=confidence,
        weight=definition.base_weight,
        eligible=eligible,
        reason=reason,
        evidence=evidence,
    )


def _strategy_lifecycle(settings: dict[str, Any] | None) -> str:
    if not settings:
        return "active"
    lifecycle = str(settings.get("lifecycle") or settings.get("status") or "").lower()
    if settings.get("enabled") is False:
        lifecycle = "disabled"
    if lifecycle not in {"active", "shadow", "disabled"}:
        lifecycle = "active"
    return lifecycle
