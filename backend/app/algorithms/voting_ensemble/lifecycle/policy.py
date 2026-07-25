from __future__ import annotations

from typing import Any

from backend.app.algorithms.voting_ensemble.lifecycle.models import (
    PROMOTION_APPROVAL_MARKER,
    PROMOTION_CANDIDATE_EVIDENCE_MARKER,
    VOTING_ENSEMBLE_PROMOTION_POLICY_VERSION,
    VOTING_ENSEMBLE_PROTECTED_SHADOW_MODULE_IDS,
    PromotionDecision,
    PromotionEvidenceRecord,
    PromotionPolicyConfig,
    VotingEnsembleLifecycleState,
)


def evaluate_lifecycle_promotion(
    *,
    module_id: str,
    from_lifecycle: VotingEnsembleLifecycleState,
    requested_lifecycle: VotingEnsembleLifecycleState,
    evidence: PromotionEvidenceRecord | None,
    explicit_inventory_change_id: str | None = None,
    config: PromotionPolicyConfig | None = None,
) -> PromotionDecision:
    policy = config or PromotionPolicyConfig()
    reason_codes: list[str] = [VOTING_ENSEMBLE_PROMOTION_POLICY_VERSION]
    requires_explicit = requested_lifecycle == "active"

    if from_lifecycle == requested_lifecycle:
        reason_codes.append("voting_ensemble.promotion.no_lifecycle_change")
        return _decision(module_id, from_lifecycle, requested_lifecycle, True, requires_explicit, explicit_inventory_change_id, reason_codes, evidence)

    if requested_lifecycle == "active" and from_lifecycle != "candidate":
        reason_codes.append("voting_ensemble.promotion.active_requires_candidate_state")
        return _decision(module_id, from_lifecycle, requested_lifecycle, False, True, explicit_inventory_change_id, reason_codes, evidence)

    if from_lifecycle == "shadow" and requested_lifecycle != "candidate":
        reason_codes.append("voting_ensemble.promotion.shadow_can_only_promote_to_candidate")
        return _decision(module_id, from_lifecycle, requested_lifecycle, False, requires_explicit, explicit_inventory_change_id, reason_codes, evidence)

    if requested_lifecycle == "candidate" and from_lifecycle != "shadow":
        reason_codes.append("voting_ensemble.promotion.candidate_requires_shadow_source")
        return _decision(module_id, from_lifecycle, requested_lifecycle, False, requires_explicit, explicit_inventory_change_id, reason_codes, evidence)

    if requested_lifecycle == "active" and not explicit_inventory_change_id:
        reason_codes.append("voting_ensemble.promotion.active_requires_explicit_versioned_inventory_change")
        return _decision(module_id, from_lifecycle, requested_lifecycle, False, True, explicit_inventory_change_id, reason_codes, evidence)

    if evidence is None:
        reason_codes.append("voting_ensemble.promotion.evidence_record_required")
        return _decision(module_id, from_lifecycle, requested_lifecycle, False, requires_explicit, explicit_inventory_change_id, reason_codes, None)

    reason_codes.extend(_evidence_failures(evidence, policy))
    approved = not any(code.startswith("voting_ensemble.promotion.evidence_failed") for code in reason_codes)
    if approved:
        reason_codes.append("voting_ensemble.promotion.approved_by_evidence_policy")
    return _decision(module_id, from_lifecycle, requested_lifecycle, approved, requires_explicit, explicit_inventory_change_id, reason_codes, evidence)


def validate_inventory_lifecycle_change(
    *,
    previous_module: Any,
    proposed_module: Any,
    evidence: PromotionEvidenceRecord | None = None,
    explicit_inventory_change_id: str | None = None,
    config: PromotionPolicyConfig | None = None,
) -> PromotionDecision:
    module_id = str(getattr(proposed_module, "strategyId", getattr(proposed_module, "id", "")))
    from_lifecycle = str(getattr(previous_module, "lifecycleStatus", getattr(previous_module, "status", "")))
    requested_lifecycle = str(getattr(proposed_module, "lifecycleStatus", getattr(proposed_module, "status", "")))
    return evaluate_lifecycle_promotion(
        module_id=module_id,
        from_lifecycle=from_lifecycle,  # type: ignore[arg-type]
        requested_lifecycle=requested_lifecycle,  # type: ignore[arg-type]
        evidence=evidence,
        explicit_inventory_change_id=explicit_inventory_change_id,
        config=config,
    )


def promotion_policy_status(inventory: Any) -> dict[str, Any]:
    modules = []
    for module in getattr(inventory, "modules", ()):
        lifecycle = str(getattr(module, "lifecycleStatus", ""))
        module_id = str(getattr(module, "strategyId", ""))
        requires_approval = module_id in VOTING_ENSEMBLE_PROTECTED_SHADOW_MODULE_IDS and lifecycle in {"candidate", "active"}
        modules.append(
            {
                "strategyId": module_id,
                "lifecycleStatus": lifecycle,
                "protectedShadowModule": module_id in VOTING_ENSEMBLE_PROTECTED_SHADOW_MODULE_IDS,
                "requiresPromotionEvidence": requires_approval,
                "candidateEvidenceRecorded": _has_marker(getattr(module, "promotionEvidence", ()), PROMOTION_CANDIDATE_EVIDENCE_MARKER),
                "activationApprovalRecorded": _has_marker(getattr(module, "promotionEvidence", ()), PROMOTION_APPROVAL_MARKER),
            }
        )
    return {
        "algorithmId": "voting_ensemble",
        "policyVersion": VOTING_ENSEMBLE_PROMOTION_POLICY_VERSION,
        "protectedShadowModuleIds": list(VOTING_ENSEMBLE_PROTECTED_SHADOW_MODULE_IDS),
        "lifecycleStates": ["unavailable", "not_data_ready", "shadow", "candidate", "active", "disabled", "deprecated_alias"],
        "modules": modules,
        "reasonCodes": ["voting_ensemble.promotion_policy.status_ready"],
    }


def _evidence_failures(evidence: PromotionEvidenceRecord, config: PromotionPolicyConfig) -> list[str]:
    failures: list[str] = []
    checks = {
        "focused_unit_tests": evidence.focusedUnitTestsPassed,
        "point_in_time_replay": evidence.pointInTimeReplayPassed,
        "minimum_sample_size_flag": evidence.minimumSampleSizeMet,
        "walk_forward_stability_flag": evidence.walkForwardResultsStable,
        "untouched_holdout": evidence.untouchedHoldoutAcceptable,
        "cost_stress_flag": evidence.netResultsAcceptableUnderCostStress,
        "latency_flag": evidence.latencyAssumptionsValid and evidence.latency.assumptionsValid,
        "overlap_flag": evidence.noUnacceptableOverlapOrConcentration,
        "paper_shadow_stability_flag": evidence.paperShadowStabilityDemonstrated,
    }
    failures.extend(f"voting_ensemble.promotion.evidence_failed:{name}" for name, passed in checks.items() if not passed)
    if evidence.sampleSize < config.minimumSampleSize:
        failures.append("voting_ensemble.promotion.evidence_failed:minimum_sample_size")
    if evidence.netExpectancy <= config.minimumNetExpectancy:
        failures.append("voting_ensemble.promotion.evidence_failed:net_expectancy")
    if evidence.maximumDrawdownPct > config.maximumDrawdownPct:
        failures.append("voting_ensemble.promotion.evidence_failed:drawdown")
    if evidence.costStress.twoTimesCostNetExpectancy <= config.minimumNetExpectancy or evidence.costStress.threeTimesCostNetExpectancy <= config.minimumNetExpectancy:
        failures.append("voting_ensemble.promotion.evidence_failed:cost_stress_net_expectancy")
    if evidence.costStress.maximumStressDrawdownPct > config.maximumStressDrawdownPct or evidence.costStress.promotionBlockedByStress:
        failures.append("voting_ensemble.promotion.evidence_failed:cost_stress_drawdown")
    if evidence.stability.walkForwardStabilityScore < config.minimumStabilityScore or evidence.stability.paperShadowStabilityScore < config.minimumStabilityScore:
        failures.append("voting_ensemble.promotion.evidence_failed:stability_score")
    if evidence.stability.paperShadowDays < config.minimumPaperShadowDays or evidence.stability.paperShadowDecisionCount < config.minimumPaperShadowDecisions:
        failures.append("voting_ensemble.promotion.evidence_failed:paper_shadow_sample")
    if evidence.latency.p95EvaluationLatencyMs > config.maximumP95EvaluationLatencyMs:
        failures.append("voting_ensemble.promotion.evidence_failed:latency_p95")
    if (
        evidence.overlap.maximumFamilyContributionShare > config.maximumFamilyContributionShare
        or evidence.overlap.maximumSameEventOverlapShare > config.maximumSameEventOverlapShare
        or evidence.overlap.unacceptableOverlapDetected
        or evidence.overlap.concentrationDetected
    ):
        failures.append("voting_ensemble.promotion.evidence_failed:overlap_or_concentration")
    return failures


def _decision(
    module_id: str,
    from_lifecycle: VotingEnsembleLifecycleState,
    requested_lifecycle: VotingEnsembleLifecycleState,
    approved: bool,
    requires_explicit: bool,
    explicit_inventory_change_id: str | None,
    reason_codes: list[str],
    evidence: PromotionEvidenceRecord | None,
) -> PromotionDecision:
    return PromotionDecision(
        moduleId=module_id,
        fromLifecycle=from_lifecycle,
        requestedLifecycle=requested_lifecycle,
        approved=approved,
        requiresExplicitInventoryChange=requires_explicit,
        explicitInventoryChangeId=explicit_inventory_change_id,
        reasonCodes=tuple(reason_codes),
        evidenceRecord=evidence,
    )


def _has_marker(values: tuple[str, ...] | list[str] | Any, marker: str) -> bool:
    if not isinstance(values, (tuple, list)):
        return False
    return any(isinstance(value, str) and value.startswith(marker) for value in values)
