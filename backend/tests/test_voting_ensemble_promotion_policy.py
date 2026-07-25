from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.voting_ensemble.lifecycle import (
    VOTING_ENSEMBLE_PROTECTED_SHADOW_MODULE_IDS,
    CostStressEvidence,
    LatencyEvidence,
    OverlapEvidence,
    PromotionEvidenceRecord,
    StabilityEvidence,
    evaluate_lifecycle_promotion,
    promotion_policy_status,
    validate_inventory_lifecycle_change,
)
from backend.app.algorithms.voting_ensemble.strategies.registry import (
    VOTING_ENSEMBLE_MODULE_INVENTORY,
    resolve_strategy,
    validate_voting_ensemble_inventory_startup,
)


START = datetime(2026, 1, 5, tzinfo=UTC)


class VotingEnsemblePromotionPolicyTest(unittest.TestCase):
    def test_lifecycle_states_include_candidate_and_policy_status_is_auditable(self) -> None:
        status = promotion_policy_status(VOTING_ENSEMBLE_MODULE_INVENTORY)

        self.assertEqual(
            status["lifecycleStates"],
            ["unavailable", "not_data_ready", "shadow", "candidate", "active", "disabled", "deprecated_alias"],
        )
        self.assertEqual(status["protectedShadowModuleIds"], list(VOTING_ENSEMBLE_PROTECTED_SHADOW_MODULE_IDS))
        opening_range = next(module for module in status["modules"] if module["strategyId"] == "opening_range_breakout")
        self.assertTrue(opening_range["protectedShadowModule"])
        self.assertFalse(opening_range["requiresPromotionEvidence"])

    def test_protected_modules_are_not_automatically_activated(self) -> None:
        statuses = {
            module.strategyId: module.lifecycleStatus
            for module in VOTING_ENSEMBLE_MODULE_INVENTORY.modules
            if module.strategyId in VOTING_ENSEMBLE_PROTECTED_SHADOW_MODULE_IDS
        }

        self.assertEqual(
            statuses,
            {
                "opening_range_breakout": "shadow",
                "vwap_trend_continuation": "shadow",
                "gap_continuation_fade": "shadow",
                "economic_event_context": "shadow",
                "market_structure_context": "shadow",
                "volume_confirmation_context": "shadow",
                "vwap_position_context": "shadow",
            },
        )
        self.assertTrue(validate_voting_ensemble_inventory_startup()["valid"])

    def test_shadow_to_candidate_requires_complete_evidence_contract(self) -> None:
        evidence = valid_evidence("shadow", "candidate").model_copy(
            update={"sampleSize": 20, "minimumSampleSizeMet": False}
        )

        decision = evaluate_lifecycle_promotion(
            module_id="opening_range_breakout",
            from_lifecycle="shadow",
            requested_lifecycle="candidate",
            evidence=evidence,
        )

        self.assertFalse(decision.approved)
        self.assertIn("voting_ensemble.promotion.evidence_failed:minimum_sample_size", decision.reasonCodes)
        self.assertIn("voting_ensemble.promotion.evidence_failed:minimum_sample_size_flag", decision.reasonCodes)

    def test_shadow_to_candidate_is_approved_only_after_evidence_passes(self) -> None:
        evidence = valid_evidence("shadow", "candidate")

        decision = evaluate_lifecycle_promotion(
            module_id="opening_range_breakout",
            from_lifecycle="shadow",
            requested_lifecycle="candidate",
            evidence=evidence,
        )

        self.assertTrue(decision.approved)
        self.assertFalse(decision.requiresExplicitInventoryChange)
        self.assertEqual(decision.evidenceRecord, evidence)
        self.assertIn("voting_ensemble.promotion.approved_by_evidence_policy", decision.reasonCodes)

    def test_shadow_cannot_jump_directly_to_active(self) -> None:
        decision = evaluate_lifecycle_promotion(
            module_id="opening_range_breakout",
            from_lifecycle="shadow",
            requested_lifecycle="active",
            evidence=valid_evidence("shadow", "active"),
            explicit_inventory_change_id="ve-inventory-2026-02-01",
        )

        self.assertFalse(decision.approved)
        self.assertTrue(decision.requiresExplicitInventoryChange)
        self.assertIn("voting_ensemble.promotion.active_requires_candidate_state", decision.reasonCodes)

    def test_candidate_to_active_requires_explicit_versioned_inventory_change(self) -> None:
        evidence = valid_evidence("candidate", "active")
        without_change = evaluate_lifecycle_promotion(
            module_id="opening_range_breakout",
            from_lifecycle="candidate",
            requested_lifecycle="active",
            evidence=evidence,
        )
        with_change = evaluate_lifecycle_promotion(
            module_id="opening_range_breakout",
            from_lifecycle="candidate",
            requested_lifecycle="active",
            evidence=evidence,
            explicit_inventory_change_id="ve-inventory-promotion-2026-02-01",
        )

        self.assertFalse(without_change.approved)
        self.assertIn("voting_ensemble.promotion.active_requires_explicit_versioned_inventory_change", without_change.reasonCodes)
        self.assertTrue(with_change.approved)
        self.assertEqual(with_change.explicitInventoryChangeId, "ve-inventory-promotion-2026-02-01")

    def test_code_deployment_alone_cannot_silently_promote_a_module(self) -> None:
        previous = resolve_strategy("opening_range_breakout")
        proposed = previous.model_copy(update={"lifecycleStatus": "active", "enabled": True})

        decision = validate_inventory_lifecycle_change(
            previous_module=previous,
            proposed_module=proposed,
            evidence=valid_evidence("shadow", "active"),
        )

        self.assertFalse(decision.approved)
        self.assertIn("voting_ensemble.promotion.active_requires_candidate_state", decision.reasonCodes)

    def test_evidence_record_contains_required_promotion_audit_fields(self) -> None:
        payload = valid_evidence("shadow", "candidate").model_dump(mode="json")

        self.assertTrue(
            {
                "evidenceWindowStart",
                "evidenceWindowEnd",
                "sampleSize",
                "regimesTested",
                "netExpectancy",
                "maximumDrawdownPct",
                "costStress",
                "stability",
                "latency",
                "overlap",
                "approvalReason",
                "configurationHash",
            }.issubset(payload)
        )
        self.assertEqual(payload["algorithmId"], "voting_ensemble")
        self.assertEqual(payload["configurationHash"], "opening-range-breakout-config-v1")


def valid_evidence(from_lifecycle: str, requested_lifecycle: str) -> PromotionEvidenceRecord:
    return PromotionEvidenceRecord(
        moduleId="opening_range_breakout",
        fromLifecycle=from_lifecycle,  # type: ignore[arg-type]
        requestedLifecycle=requested_lifecycle,  # type: ignore[arg-type]
        evidenceWindowStart=START,
        evidenceWindowEnd=START + timedelta(days=45),
        sampleSize=420,
        regimesTested=("trend", "range", "high_volatility"),
        netExpectancy=0.18,
        maximumDrawdownPct=5.5,
        costStress=CostStressEvidence(
            baselineNetExpectancy=0.18,
            twoTimesCostNetExpectancy=0.11,
            threeTimesCostNetExpectancy=0.06,
            maximumStressDrawdownPct=8.0,
        ),
        stability=StabilityEvidence(
            walkForwardStabilityScore=0.74,
            untouchedHoldoutNetExpectancy=0.09,
            paperShadowDays=12,
            paperShadowDecisionCount=84,
            paperShadowStabilityScore=0.79,
        ),
        latency=LatencyEvidence(
            p50EvaluationLatencyMs=12.0,
            p95EvaluationLatencyMs=44.0,
            p99EvaluationLatencyMs=90.0,
            maximumObservedLatencyMs=120.0,
            assumptionsValid=True,
        ),
        overlap=OverlapEvidence(
            maximumFamilyContributionShare=0.42,
            maximumSameEventOverlapShare=0.28,
        ),
        focusedUnitTestsPassed=True,
        pointInTimeReplayPassed=True,
        minimumSampleSizeMet=True,
        walkForwardResultsStable=True,
        untouchedHoldoutAcceptable=True,
        netResultsAcceptableUnderCostStress=True,
        latencyAssumptionsValid=True,
        noUnacceptableOverlapOrConcentration=True,
        paperShadowStabilityDemonstrated=True,
        approvalReason="Synthetic Step 23 evidence fixture passes every promotion gate.",
        configurationHash="opening-range-breakout-config-v1",
    )


if __name__ == "__main__":
    unittest.main()
