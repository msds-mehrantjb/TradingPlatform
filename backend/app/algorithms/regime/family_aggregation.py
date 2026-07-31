"""Backend-owned family aggregation."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from backend.app.algorithms.regime.contracts import RegimeClassification, RegimeStrategyEvaluation


DEFAULT_MAX_FAMILY_CONTRIBUTION = 0.40
DEFAULT_MAX_CONFIRMATION_ADJUSTMENT = 0.08


def aggregate_directional_strategies(
    outputs: tuple[RegimeStrategyEvaluation, ...],
    settings: dict[str, Any] | None = None,
    classification: RegimeClassification | None = None,
) -> dict[str, object]:
    max_family_contribution = max(
        0.01,
        min(1.0, float((settings or {}).get("maximumContributionPerFamily", DEFAULT_MAX_FAMILY_CONTRIBUTION))),
    )
    directional = tuple(output for output in outputs if output.role == "directional")
    health_restricted: list[dict[str, object]] = []
    eligible_candidates: list[RegimeStrategyEvaluation] = []
    for output in directional:
        health = str(output.evidence.get("strategyHealth") or output.evidence.get("health") or "healthy")
        if health not in {"healthy", "ok", "nominal"}:
            health_restricted.append({"strategyId": output.strategy_id, "health": health, "reasonCode": "regime.directional.strategy_health_restricted"})
            continue
        if output.eligible and output.lifecycle_status == "active":
            eligible_candidates.append(output)
    eligible = tuple(eligible_candidates)
    directional_votes = tuple(output for output in eligible if output.signal in {"Buy", "Sell"})
    by_family: dict[str, list[RegimeStrategyEvaluation]] = defaultdict(list)
    for output in directional_votes:
        by_family[output.family].append(output)

    family_scores: dict[str, float] = {}
    family_confidence: dict[str, float] = {}
    selected_by_family: dict[str, str] = {}
    expected_edge_by_family: dict[str, float] = {}
    opposing_evidence: list[dict[str, object]] = []
    collision_reasons: list[str] = []
    calibration_reasons: list[str] = []
    buy = 0.0
    sell = 0.0
    gross_edge_numerator = 0.0
    gross_edge_denominator = 0.0
    for family, family_outputs in sorted(by_family.items()):
        calibrated = tuple((_calibrated_confidence(item, classification), item) for item in family_outputs)
        selected_confidence, selected = max(
            calibrated,
            key=lambda item: (item[1].weight * item[0], item[0], item[1].expected_gross_edge_bps, item[1].strategy_id),
        )
        if selected_confidence != selected.confidence:
            calibration_reasons.append(f"regime.directional.confidence_calibrated:{selected.strategy_id}")
        if len(family_outputs) > 1:
            collision_reasons.append(f"regime.family_aggregation.correlated_family_collapsed:{family}")
        raw_contribution = selected.weight * selected_confidence
        contribution = min(max_family_contribution, raw_contribution)
        signed = contribution if selected.signal == "Buy" else -contribution
        family_scores[family] = round(signed, 6)
        family_confidence[family] = round(selected_confidence, 4)
        selected_by_family[family] = selected.strategy_id
        expected_edge = max(0.0, float(selected.expected_gross_edge_bps or 0.0))
        expected_edge_by_family[family] = round(expected_edge, 4)
        if expected_edge > 0:
            gross_edge_numerator += expected_edge * contribution
            gross_edge_denominator += contribution
        if selected.signal == "Buy":
            buy += contribution
        else:
            sell += contribution
        for item in family_outputs:
            if item.strategy_id != selected.strategy_id or item.signal != selected.signal:
                opposing_evidence.append(
                    {
                        "family": family,
                        "strategyId": item.strategy_id,
                        "signal": item.signal,
                        "confidence": item.confidence,
                        "reason": item.reason,
                        "collision": item.strategy_id != selected.strategy_id,
                    }
                )

    signal_total = buy + sell
    if signal_total <= 0:
        scores = {"buy": 0.0, "sell": 0.0, "hold": 1.0}
    else:
        scores = {"buy": buy / signal_total, "sell": sell / signal_total, "hold": 0.0}
    minimum_families = max(1, int((settings or {}).get("minimumIndependentFamilies", 1)))
    threshold_reasons: list[str] = []
    if len(by_family) < minimum_families:
        threshold_reasons.extend(
            (
                "regime.directional.minimum_independent_strategies_not_met",
                "regime.family_aggregation.minimum_independent_strategies_not_met",
            )
        )
        scores = {"buy": 0.0, "sell": 0.0, "hold": 1.0}
        signal = "Hold"
        edge = 0.0
        score = 0.0
    elif scores["buy"] > scores["sell"]:
        signal = "Buy"
        edge = scores["buy"] - scores["sell"]
        score = scores["buy"]
    elif scores["sell"] > scores["buy"]:
        signal = "Sell"
        edge = scores["sell"] - scores["buy"]
        score = scores["sell"]
    else:
        signal = "Hold"
        edge = 0.0
        score = 0.0
    conflict_reasons: list[str] = []
    if buy > 0 and sell > 0:
        conflict_reasons.append("regime.directional.conflicting_buy_sell_families")
    active_family_count = len(by_family)
    abstention_rate = 1 - (len(directional_votes) / max(1, len(directional)))
    expected_gross_edge_bps = gross_edge_numerator / gross_edge_denominator if gross_edge_denominator > 0 else 0.0
    return {
        "aggregationLayer": "directional",
        "scores": scores,
        "familyScores": family_scores,
        "familyConfidence": family_confidence,
        "expectedGrossEdgeBpsByFamily": expected_edge_by_family,
        "expectedGrossEdgeBps": round(expected_gross_edge_bps, 4),
        "selectedStrategyByFamily": selected_by_family,
        "aggregateSignal": signal.lower(),
        "signal": signal,
        "winningScore": round(score, 4),
        "winningEdge": round(edge, 4),
        "votingScoreMargin": round(edge, 4),
        "economicEdgeSource": "selected_strategy_expected_gross_edge_bps",
        "eligibleStrategyCount": len(eligible),
        "activeStrategyCount": len(directional_votes),
        "activeFamilyCount": active_family_count,
        "abstentionRate": round(abstention_rate, 4),
        "abstentionCount": len(directional) - len(directional_votes),
        "directionalStrategyCount": len(directional),
        "opposingEvidence": tuple(opposing_evidence),
        "correlationCollisionReasonCodes": tuple(collision_reasons),
        "confidenceCalibrationReasonCodes": tuple(dict.fromkeys(calibration_reasons)),
        "conflictReasonCodes": tuple(conflict_reasons),
        "strategyHealthRestrictions": tuple(health_restricted),
        "thresholdReasonCodes": tuple(threshold_reasons),
        "minimumIndependentFamiliesRequired": minimum_families,
        "maximumContributionPerFamily": max_family_contribution,
        "aggregationPolicy": "directional_only_strongest_active_strategy_per_family",
    }


def apply_confirmation_layer(
    directional_aggregation: dict[str, object],
    confirmation_outputs: tuple[RegimeStrategyEvaluation, ...],
    context_outputs: tuple[RegimeStrategyEvaluation, ...] = (),
    settings: dict[str, Any] | None = None,
) -> dict[str, object]:
    max_adjustment = max(0.0, min(0.20, float((settings or {}).get("maximumConfirmationAdjustment", DEFAULT_MAX_CONFIRMATION_ADJUSTMENT))))
    all_outputs = (*confirmation_outputs, *context_outputs)
    if not all_outputs:
        return {
            **directional_aggregation,
            "aggregationLayer": "directional_confirmation",
            "confirmationAdjustment": 0.0,
            "confirmationReasonCodes": ("regime.confirmation.no_modules",),
            "confirmationBlockers": (),
        }
    reasons: list[str] = []
    blockers: list[str] = []
    adjustments: list[float] = []
    for output in all_outputs:
        if output.signal != "Hold":
            blockers.append(f"regime.confirmation.direction_creation_rejected:{output.strategy_id}")
        if not output.eligible or output.lifecycle_status in {"disabled", "unavailable", "not_data_ready"}:
            reasons.append(f"regime.confirmation.module_not_eligible:{output.strategy_id}")
            continue
        module_adjustment = (max(0.0, min(1.0, output.confidence)) - 0.5) * 0.20
        adjustments.append(max(-max_adjustment, min(max_adjustment, module_adjustment)))
        reasons.append(f"regime.confirmation.module_applied:{output.strategy_id}")
    average_adjustment = sum(adjustments) / len(adjustments) if adjustments else 0.0
    adjustment = max(-max_adjustment, min(max_adjustment, average_adjustment))
    score = max(0.0, min(1.0, float(directional_aggregation.get("winningScore") or 0.0) + adjustment))
    signal = str(directional_aggregation.get("signal") or "Hold")
    if signal == "Hold":
        score = 0.0
    minimum_confirmation = float((settings or {}).get("minimumConfirmationScore", 0.0))
    if score < minimum_confirmation and signal != "Hold":
        blockers.append("regime.confirmation.minimum_confirmation_score")
        signal = "Hold"
    return {
        **directional_aggregation,
        "aggregationLayer": "directional_confirmation",
        "signal": signal,
        "aggregateSignal": signal.lower(),
        "winningScore": round(score, 4),
        "confirmationAdjustment": round(adjustment, 6),
        "confirmationReasonCodes": tuple(dict.fromkeys(reasons or ("regime.confirmation.neutral",))),
        "confirmationBlockers": tuple(dict.fromkeys(blockers)),
        "confirmationOutputs": tuple(
            {"strategyId": output.strategy_id, "role": output.role, "confidence": output.confidence, "reason": output.reason}
            for output in all_outputs
        ),
    }


def apply_safety_layer(
    aggregation: dict[str, object],
    *,
    safety_outputs: tuple[RegimeStrategyEvaluation, ...] = (),
    blockers: tuple[str, ...] = (),
    reductions: tuple[dict[str, Any], ...] = (),
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, object]:
    context = runtime_context or {}
    reason_codes: list[str] = list(blockers)
    for output in safety_outputs:
        if output.signal != "Hold":
            reason_codes.append(f"regime.safety.direction_creation_rejected:{output.strategy_id}")
        if output.reason != "regime.safety.clear" and output.confidence >= 0.5:
            reason_codes.append(output.reason)
    if context.get("runtimePaused") or context.get("killSwitchActive") or context.get("operationalKillSwitch"):
        reason_codes.append("regime.safety.operational_kill_switch")
    if context.get("duplicateProposal") or context.get("duplicateOrderIntent"):
        reason_codes.append("regime.safety.duplicate_order")
    if context.get("inventoryReconciled") is False or context.get("reconciliationRequired"):
        reason_codes.append("regime.safety.inventory_reconciliation_required")
    if context.get("dailyRiskLocked") or context.get("dailyLossLimitBreached"):
        reason_codes.append("regime.safety.daily_risk_limit")
    unique_reasons = tuple(dict.fromkeys(reason_codes))
    signal = "Hold" if unique_reasons else str(aggregation.get("signal") or "Hold")
    return {
        **aggregation,
        "aggregationLayer": "directional_confirmation_safety",
        "signal": signal,
        "aggregateSignal": signal.lower(),
        "safetyBlockers": unique_reasons,
        "safetyReductions": reductions,
        "safetyReasonCodes": unique_reasons or ("regime.safety.layer_clear",),
    }


def aggregate_family_scores(outputs: tuple[RegimeStrategyEvaluation, ...], settings: dict[str, Any] | None = None) -> dict[str, object]:
    return aggregate_directional_strategies(outputs, settings=settings)


def _calibrated_confidence(output: RegimeStrategyEvaluation, classification: RegimeClassification | None) -> float:
    confidence = max(0.0, min(1.0, output.confidence))
    if output.data_ready is False:
        return 0.0
    if classification is not None and getattr(classification, "confidence", None) is not None:
        confidence *= max(0.5, min(1.0, float(classification.confidence)))
    if output.expected_gross_edge_bps <= 0 and output.signal in {"Buy", "Sell"}:
        confidence *= 0.85
    return round(max(0.0, min(1.0, confidence)), 6)


__all__ = [
    "aggregate_directional_strategies",
    "aggregate_family_scores",
    "apply_confirmation_layer",
    "apply_safety_layer",
]
