"""Backend-owned family aggregation."""

from __future__ import annotations

from collections import defaultdict
from math import isfinite
from typing import Any

from backend.app.algorithms.regime.contracts import RegimeClassification, RegimeStrategyEvaluation


DEFAULT_MAX_FAMILY_CONTRIBUTION = 0.40
DEFAULT_MAX_CONFIRMATION_ADJUSTMENT = 0.08
_DIRECTIONAL_SIGNALS = {"Buy", "Sell"}
_ACTIVE_LIFECYCLES = {"active"}
_HEALTHY_STATES = {"healthy", "ok", "nominal"}


def aggregate_directional_strategies(
    outputs: tuple[RegimeStrategyEvaluation, ...],
    settings: dict[str, Any] | None = None,
    classification: RegimeClassification | None = None,
) -> dict[str, object]:
    max_family_contribution = max(
        0.01,
        min(1.0, float((settings or {}).get("maximumContributionPerFamily", DEFAULT_MAX_FAMILY_CONTRIBUTION))),
    )
    minimum_strategies = max(1, int((settings or {}).get("minimumActiveStrategies", 1)))
    minimum_families = max(1, int((settings or {}).get("minimumIndependentFamilies", 1)))
    minimum_winning_score = max(0.0, min(1.0, _finite_float((settings or {}).get("minimumWinningScore"), 0.0)))
    minimum_margin = max(0.0, min(1.0, _finite_float((settings or {}).get("minimumSignalEdge"), 0.0)))
    minimum_net_edge_bps = max(0.0, _finite_float((settings or {}).get("minimumNetExpectedEdgeBps"), _finite_float((settings or {}).get("minimumNetExpectedEdge"), 0.0) * 100.0))
    max_abstention = max(0.0, min(1.0, _finite_float((settings or {}).get("maximumAbstentionRate"), 1.0)))
    estimated_cost_bps = _estimated_cost_bps(settings or {})

    initial_records = [_strategy_audit_record(output, classification) for output in outputs]
    duplicate_selected = _selected_duplicate_records(initial_records)
    deduped_records: list[dict[str, Any]] = []
    for record in initial_records:
        if record["eligibility"] and duplicate_selected.get(str(record["strategyId"])) is not record:
            record = _exclude_record(record, "regime.family_aggregation.duplicate_strategy_suppressed")
        deduped_records.append(record)

    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in deduped_records:
        if record["eligibility"]:
            by_family[str(record["family"])].append(record)

    family_scores: dict[str, float] = {}
    family_confidence: dict[str, float] = {}
    selected_by_family: dict[str, str] = {}
    expected_edge_by_family: dict[str, float] = {}
    opposing_evidence: list[dict[str, object]] = []
    collision_reasons: list[str] = []
    calibration_reasons: list[str] = []
    threshold_reasons: list[str] = []
    contribution_records: list[dict[str, Any]] = []
    selected_record_ids: set[int] = set()
    buy = 0.0
    sell = 0.0
    gross_edge_numerator = 0.0
    gross_edge_denominator = 0.0
    for family, family_outputs in sorted(by_family.items()):
        selected = max(
            family_outputs,
            key=lambda item: (item["configuredBaseWeight"] * item["confidence"], item["confidence"], item["expectedGrossEdgeBps"], str(item["strategyId"])),
        )
        selected_record_ids.add(id(selected))
        if selected["confidenceCalibrated"]:
            calibration_reasons.append(f"regime.directional.confidence_calibrated:{selected['strategyId']}")
        if len(family_outputs) > 1:
            collision_reasons.append(f"regime.family_aggregation.correlated_family_collapsed:{family}")
        raw_contribution = selected["configuredBaseWeight"] * selected["regimeCompatibilityMultiplier"] * selected["healthMultiplier"] * selected["confidence"]
        contribution = min(max_family_contribution, raw_contribution)
        correlation_adjustment = (contribution / raw_contribution) if raw_contribution > 0 else 0.0
        selected["correlationAdjustment"] = round(correlation_adjustment, 6)
        selected["effectiveWeight"] = round(selected["configuredBaseWeight"] * selected["regimeCompatibilityMultiplier"] * selected["healthMultiplier"] * correlation_adjustment, 8)
        selected["weightedContribution"] = round(contribution if selected["signal"] == "Buy" else -contribution, 8)
        signed = selected["weightedContribution"]
        family_scores[family] = round(signed, 6)
        family_confidence[family] = round(selected["confidence"], 4)
        selected_by_family[family] = str(selected["strategyId"])
        expected_edge = max(0.0, float(selected["expectedGrossEdgeBps"] or 0.0))
        expected_edge_by_family[family] = round(expected_edge, 4)
        if expected_edge > 0:
            gross_edge_numerator += expected_edge * abs(signed)
            gross_edge_denominator += abs(signed)
        if selected["signal"] == "Buy":
            buy += abs(signed)
        else:
            sell += abs(signed)
        for item in family_outputs:
            if item is not selected:
                item = _exclude_record(item, "regime.family_aggregation.correlated_family_collapsed")
                opposing_evidence.append(
                    {
                        "family": family,
                        "strategyId": item["strategyId"],
                        "signal": item["signal"],
                        "confidence": item["confidence"],
                        "reason": item["reason"],
                        "collision": True,
                    }
                )

    for record in deduped_records:
        if record["eligibility"] and id(record) not in selected_record_ids:
            record = _exclude_record(record, "regime.family_aggregation.correlated_family_collapsed")
        contribution_records.append(_public_record(record))

    signal_total = buy + sell
    if signal_total <= 0:
        scores = {"buy": 0.0, "sell": 0.0, "hold": 1.0}
    else:
        scores = {"buy": buy / signal_total, "sell": sell / signal_total, "hold": 0.0}
    directional_count = sum(1 for record in contribution_records if record["role"] == "directional")
    vote_count = sum(1 for record in contribution_records if record["eligibility"] and abs(float(record["weightedContribution"])) > 0)
    active_family_count = len(by_family)
    abstention_rate = 1 - (vote_count / max(1, directional_count))
    expected_gross_edge_bps = gross_edge_numerator / gross_edge_denominator if gross_edge_denominator > 0 else 0.0
    expected_net_edge_bps = expected_gross_edge_bps - estimated_cost_bps

    if vote_count < minimum_strategies:
        threshold_reasons.append("regime.family_aggregation.minimum_active_strategies_not_met")
    if active_family_count < minimum_families:
        threshold_reasons.extend(
            (
                "regime.directional.minimum_independent_strategies_not_met",
                "regime.family_aggregation.minimum_independent_strategies_not_met",
            )
        )
    if abstention_rate > max_abstention:
        threshold_reasons.append("regime.family_aggregation.maximum_abstention_rate_exceeded")
    if expected_gross_edge_bps <= 0 or expected_net_edge_bps <= 0 or expected_net_edge_bps < minimum_net_edge_bps:
        threshold_reasons.append("regime.family_aggregation.positive_net_expected_edge_required")

    if threshold_reasons:
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
    if signal != "Hold" and score < minimum_winning_score:
        threshold_reasons.append("regime.family_aggregation.minimum_winning_score_not_met")
        signal = "Hold"
        score = 0.0
        edge = 0.0
        scores = {"buy": 0.0, "sell": 0.0, "hold": 1.0}
    if signal != "Hold" and edge < minimum_margin:
        threshold_reasons.append("regime.family_aggregation.minimum_winning_margin_not_met")
        signal = "Hold"
        score = 0.0
        edge = 0.0
        scores = {"buy": 0.0, "sell": 0.0, "hold": 1.0}
    conflict_reasons: list[str] = []
    if buy > 0 and sell > 0:
        conflict_reasons.append("regime.directional.conflicting_buy_sell_families")
    return {
        "aggregationLayer": "directional",
        "scores": scores,
        "familyScores": family_scores,
        "familyConfidence": family_confidence,
        "expectedGrossEdgeBpsByFamily": expected_edge_by_family,
        "expectedGrossEdgeBps": round(expected_gross_edge_bps, 4),
        "expectedNetEdgeBps": round(expected_net_edge_bps, 4),
        "estimatedAggregationCostBps": round(estimated_cost_bps, 4),
        "selectedStrategyByFamily": selected_by_family,
        "aggregateSignal": signal.lower(),
        "signal": signal,
        "winningScore": round(score, 4),
        "winningEdge": round(edge, 4),
        "votingScoreMargin": round(edge, 4),
        "economicEdgeSource": "selected_strategy_expected_gross_edge_bps",
        "eligibleStrategyCount": vote_count,
        "activeStrategyCount": vote_count,
        "activeFamilyCount": active_family_count,
        "abstentionRate": round(abstention_rate, 4),
        "abstentionCount": max(0, directional_count - vote_count),
        "directionalStrategyCount": directional_count,
        "strategyContributions": tuple(sorted(contribution_records, key=lambda item: (str(item["family"]), str(item["strategyId"]), str(item["role"])))),
        "opposingEvidence": tuple(opposing_evidence),
        "correlationCollisionReasonCodes": tuple(collision_reasons),
        "confidenceCalibrationReasonCodes": tuple(dict.fromkeys(calibration_reasons)),
        "conflictReasonCodes": tuple(conflict_reasons),
        "strategyHealthRestrictions": tuple(
            {"strategyId": record["strategyId"], "health": record["health"], "reasonCode": "regime.directional.strategy_health_restricted"}
            for record in contribution_records
            if "regime.directional.strategy_health_restricted" in record["exclusionReasonCodes"]
        ),
        "thresholdReasonCodes": tuple(dict.fromkeys(threshold_reasons)),
        "minimumActiveStrategiesRequired": minimum_strategies,
        "minimumIndependentFamiliesRequired": minimum_families,
        "minimumWinningScoreRequired": minimum_winning_score,
        "minimumWinningMarginRequired": minimum_margin,
        "minimumNetExpectedEdgeBpsRequired": minimum_net_edge_bps,
        "maximumContributionPerFamily": max_family_contribution,
        "maximumAbstentionRate": max_abstention,
        "aggregationPolicy": "audited_directional_only_one_vote_per_strategy_one_representative_per_family",
        "weightAuthority": "active_immutable_regime_settings_and_backend_strategy_registry",
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


def _strategy_audit_record(output: RegimeStrategyEvaluation, classification: RegimeClassification | None) -> dict[str, Any]:
    configured_base_weight = max(0.0, _finite_float(output.weight, 0.0))
    raw_confidence = _finite_float(output.confidence, 0.0)
    confidence = _calibrated_confidence(output, classification)
    health = str(output.evidence.get("strategyHealth") or output.evidence.get("health") or "healthy").lower()
    health_multiplier = 1.0 if health in _HEALTHY_STATES else 0.0
    regime_multiplier = max(0.0, _finite_float(output.evidence.get("regimeCompatibilityMultiplier"), 1.0))
    reason_codes = [str(code) for code in output.reason_codes if code]
    exclusion: list[str] = []
    if output.role != "directional":
        exclusion.append(f"regime.family_aggregation.role_not_directional:{output.role}")
    if output.signal not in _DIRECTIONAL_SIGNALS:
        exclusion.append("regime.family_aggregation.hold_signal_not_directional_vote")
    if configured_base_weight <= 0:
        exclusion.append("regime.family_aggregation.nonpositive_weight")
    if not output.eligible:
        exclusion.append("regime.family_aggregation.strategy_not_eligible")
    if str(output.lifecycle_status or "").lower() not in _ACTIVE_LIFECYCLES:
        exclusion.append(f"regime.family_aggregation.lifecycle_not_active:{output.lifecycle_status}")
    if health_multiplier <= 0:
        exclusion.append("regime.directional.strategy_health_restricted")
    if output.data_ready is False:
        exclusion.append("regime.family_aggregation.missing_inputs")
    if _has_missing_input_reason(output):
        exclusion.append("regime.family_aggregation.missing_inputs")
    if raw_confidence != output.confidence:
        exclusion.append("regime.family_aggregation.confidence_not_finite")
    try:
        weight_finite = isfinite(float(output.weight))
    except (TypeError, ValueError):
        weight_finite = False
    if not weight_finite:
        exclusion.append("regime.family_aggregation.weight_not_finite")
    eligibility = not exclusion
    return {
        "strategyId": output.strategy_id,
        "family": output.family,
        "role": output.role,
        "signal": output.signal,
        "confidence": confidence,
        "configuredBaseWeight": configured_base_weight,
        "regimeCompatibilityMultiplier": regime_multiplier,
        "healthMultiplier": health_multiplier,
        "correlationAdjustment": 0.0,
        "effectiveWeight": 0.0,
        "weightedContribution": 0.0,
        "eligibility": eligibility,
        "exclusionReasonCodes": tuple(dict.fromkeys((*reason_codes, *exclusion))),
        "reason": output.reason,
        "health": health,
        "expectedGrossEdgeBps": max(0.0, _finite_float(output.expected_gross_edge_bps, 0.0)),
        "confidenceCalibrated": confidence != max(0.0, min(1.0, raw_confidence)),
    }


def _selected_duplicate_records(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record["eligibility"]:
            by_strategy[str(record["strategyId"])].append(record)
    return {
        strategy_id: max(
            candidates,
            key=lambda item: (item["configuredBaseWeight"] * item["confidence"], item["expectedGrossEdgeBps"], str(item["family"]), str(item["signal"])),
        )
        for strategy_id, candidates in by_strategy.items()
    }


def _exclude_record(record: dict[str, Any], reason_code: str) -> dict[str, Any]:
    return {
        **record,
        "eligibility": False,
        "correlationAdjustment": 0.0,
        "effectiveWeight": 0.0,
        "weightedContribution": 0.0,
        "exclusionReasonCodes": tuple(dict.fromkeys((*record.get("exclusionReasonCodes", ()), reason_code))),
    }


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategyId": str(record["strategyId"]),
        "family": str(record["family"]),
        "signal": str(record["signal"]),
        "confidence": round(float(record["confidence"]), 6),
        "configuredBaseWeight": round(float(record["configuredBaseWeight"]), 8),
        "regimeCompatibilityMultiplier": round(float(record["regimeCompatibilityMultiplier"]), 8),
        "healthMultiplier": round(float(record["healthMultiplier"]), 8),
        "correlationAdjustment": round(float(record["correlationAdjustment"]), 8),
        "effectiveWeight": round(float(record["effectiveWeight"]), 8),
        "weightedContribution": round(float(record["weightedContribution"]), 8),
        "eligibility": bool(record["eligibility"]),
        "exclusionReasonCodes": tuple(record.get("exclusionReasonCodes") or ()),
        "role": str(record.get("role") or ""),
        "health": str(record.get("health") or ""),
        "expectedGrossEdgeBps": round(float(record.get("expectedGrossEdgeBps") or 0.0), 6),
    }


def _calibrated_confidence(output: RegimeStrategyEvaluation, classification: RegimeClassification | None) -> float:
    confidence = max(0.0, min(1.0, _finite_float(output.confidence, 0.0)))
    if output.data_ready is False:
        return 0.0
    if classification is not None and getattr(classification, "confidence", None) is not None:
        confidence *= max(0.5, min(1.0, _finite_float(classification.confidence, 1.0)))
    if _finite_float(output.expected_gross_edge_bps, 0.0) <= 0 and output.signal in {"Buy", "Sell"}:
        confidence *= 0.85
    return round(max(0.0, min(1.0, confidence)), 6)


def _has_missing_input_reason(output: RegimeStrategyEvaluation) -> bool:
    values = [output.reason, *output.reason_codes]
    missing = output.evidence.get("missingInputReasons") or output.evidence.get("missingInputs") or ()
    if isinstance(missing, (list, tuple)) and missing:
        return True
    return any("missing" in str(value).lower() or "not_data_ready" in str(value).lower() for value in values if value)


def _estimated_cost_bps(settings: dict[str, Any]) -> float:
    execution = settings.get("execution") if isinstance(settings.get("execution"), dict) else {}
    values = (
        settings.get("estimatedFeesBps"),
        settings.get("estimatedRegulatoryFeesBps"),
        settings.get("estimatedSpreadBps"),
        settings.get("estimatedSlippageBps"),
        settings.get("marketImpactBps"),
        execution.get("estimatedFeesBps"),
        execution.get("estimatedRegulatoryFeesBps"),
        execution.get("estimatedSpreadBps"),
        execution.get("estimatedSlippageBps"),
        execution.get("marketImpactBps"),
    )
    return sum(max(0.0, _finite_float(value, 0.0)) for value in values)


def _finite_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not isfinite(parsed):
        return default
    return parsed


__all__ = [
    "aggregate_directional_strategies",
    "aggregate_family_scores",
    "apply_confirmation_layer",
    "apply_safety_layer",
]
