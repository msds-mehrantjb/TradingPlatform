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
    strategy_version: str = "v1"
    lifecycle_status: str = "active"


def evaluate_definition(
    definition: RegimeStrategyDefinition,
    snapshot: RegimeMarketSnapshot,
    classification: RegimeClassification,
    strategy_settings: dict[str, Any] | None = None,
) -> RegimeStrategyEvaluation:
    lifecycle = _strategy_lifecycle(strategy_settings, default=definition.lifecycle_status)
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
            strategy_version=definition.strategy_version,
            lifecycle_status=lifecycle,
            data_ready=False,
            reason_codes=("regime.strategy.lifecycle_disabled",),
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
            strategy_version=definition.strategy_version,
            lifecycle_status="not_data_ready",
            data_ready=False,
            reason_codes=("regime.strategy.minimum_bars_not_met",),
        )
    signal, confidence, reason, evidence = definition.evaluator(snapshot, classification)
    if definition.role != "directional":
        signal = "Hold"
    signal = signal if signal in {"Buy", "Sell", "Hold"} else "Hold"
    confidence = max(0.0, min(1.0, float(confidence)))
    strategy_version = str(evidence.get("strategy_version") or evidence.get("strategyVersion") or definition.strategy_version)
    lifecycle_status = str(evidence.get("lifecycle_status") or evidence.get("lifecycleStatus") or lifecycle)
    data_ready = bool(evidence.get("data_ready", evidence.get("dataReady", True)))
    reason_codes = tuple(str(item) for item in (evidence.get("reason_codes") or evidence.get("reasonCodes") or (reason,)))
    eligible = data_ready
    if lifecycle == "shadow":
        evaluator_evidence = dict(evidence)
        evidence = {
            **evaluator_evidence,
            "lifecycle": lifecycle,
            "shadowSignal": signal,
            "shadowConfidence": confidence,
            "shadowReason": reason,
            "evaluatorEvidence": evaluator_evidence,
        }
        signal = "Hold"
        eligible = False
        reason = "regime.strategy.lifecycle_shadow_only"
        lifecycle_status = "shadow"
        reason_codes = tuple(dict.fromkeys(("regime.strategy.lifecycle_shadow_only", *reason_codes)))
    else:
        evidence = {**evidence, "lifecycle": lifecycle}
    if lifecycle_status in {"shadow", "disabled", "not_data_ready"}:
        signal = "Hold"
        eligible = False
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
        strategy_version=strategy_version,
        lifecycle_status=lifecycle_status,
        expected_gross_edge_bps=float(evidence.get("expected_gross_edge_bps") or evidence.get("expectedGrossEdgeBps") or 0.0),
        entry_reference=evidence.get("entry_reference") or evidence.get("entryReference"),
        stop_reference=evidence.get("stop_reference") or evidence.get("stopReference"),
        target_reference=evidence.get("target_reference") or evidence.get("targetReference"),
        valid_until=evidence.get("valid_until") or evidence.get("validUntil"),
        setup_id=evidence.get("setup_id") or evidence.get("setupId"),
        reason_codes=reason_codes,
        data_ready=data_ready,
    )


def _strategy_lifecycle(settings: dict[str, Any] | None, *, default: str = "active") -> str:
    if not settings:
        return default if default in {"active", "shadow", "disabled", "not_data_ready"} else "active"
    lifecycle = str(settings.get("lifecycle") or settings.get("status") or default).lower()
    if settings.get("enabled") is False:
        lifecycle = "disabled"
    if lifecycle not in {"active", "shadow", "disabled", "not_data_ready"}:
        lifecycle = "active"
    return lifecycle
