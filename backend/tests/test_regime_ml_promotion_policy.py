from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime.ml.paper_stability import (
    RegimeMlPaperStabilityEvidence,
    evaluate_regime_ml_paper_stability,
)
from backend.app.algorithms.regime.ml.promotion_policy import (
    RegimeMlCandidateArtifact,
    RegimeMlPromotionEvidence,
    evaluate_regime_ml_promotion_policy,
)
from backend.app.algorithms.regime.repository import RegimeRepository


NOW = datetime(2026, 7, 18, 16, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


class MemoryPromotionEvidenceRepository:
    def __init__(self, evidence: dict | None = None) -> None:
        self.evidence = evidence

    def latest_regime_ml_promotion_evidence(self, artifact_id: str) -> dict | None:
        if self.evidence and self.evidence.get("artifact_id") == artifact_id:
            return self.evidence
        return None


def candidate() -> RegimeMlCandidateArtifact:
    return RegimeMlCandidateArtifact(
        artifact_id="artifact-1",
        artifact_hash="sha256:abc",
        model_version="regime-ml-v1",
        feature_schema_version="regime-features-v1",
        label_version="regime-labels-v1",
        deterministic_baseline_version="regime_algorithm_v3_backend_authoritative",
    )


def valid_evidence() -> dict:
    return RegimeMlPromotionEvidence(
        artifact_id="artifact-1",
        artifact_hash="sha256:abc",
        model_version="regime-ml-v1",
        feature_schema_version="regime-features-v1",
        label_version="regime-labels-v1",
        deterministic_baseline_version="regime_algorithm_v3_backend_authoritative",
        walk_forward_passed=True,
        untouched_holdout_passed=True,
        deterministic_baseline_comparison_passed=True,
        calibration_passed=True,
        leakage_tests_passed=True,
        paper_stability_passed=True,
        paper_shadow_decision_count=300,
        paper_trading_day_count=12,
        distinct_regimes_observed=6,
        minimum_regime_coverage_passed=True,
        global_risk_violations=0,
        unexpected_decision_mutations=0,
        broker_reconciliation_failures=0,
        operational_errors=0,
        performance_review_passed=True,
        rollback_artifact_retained=True,
        tests_passed=True,
        evidence_generated_at=(NOW - timedelta(days=1)).isoformat(),
        evidence_expiration_at=(NOW + timedelta(days=1)).isoformat(),
    ).as_dict()


class RegimeMlPromotionPolicyTest(unittest.TestCase):
    def assert_blocked(self, evidence_patch: dict | None, expected_reason: str) -> None:
        evidence = valid_evidence()
        if evidence_patch is not None:
            evidence.update(evidence_patch)
        decision = evaluate_regime_ml_promotion_policy(candidate(), MemoryPromotionEvidenceRepository(evidence), now=NOW)

        self.assertFalse(decision.promoted)
        self.assertEqual(decision.target_mode, "shadow")
        self.assertIn(expected_reason, decision.reason_codes)

    def test_complete_backend_evidence_allows_confirm_only_but_not_active(self) -> None:
        decision = evaluate_regime_ml_promotion_policy(candidate(), MemoryPromotionEvidenceRepository(valid_evidence()), now=NOW)

        self.assertTrue(decision.promoted)
        self.assertEqual(decision.target_mode, "confirm_only")
        self.assertEqual(decision.maximum_automatic_promotion_mode, "confirm_only")
        self.assertEqual(decision.reason_codes, ("regime.ml.promotion.confirm_only_allowed",))

    def test_missing_backend_evidence_fails_closed(self) -> None:
        decision = evaluate_regime_ml_promotion_policy(candidate(), MemoryPromotionEvidenceRepository(None), now=NOW)

        self.assertFalse(decision.promoted)
        self.assertEqual(decision.target_mode, "shadow")
        self.assertIn("regime.ml.promotion.missing_backend_evidence", decision.reason_codes)

    def test_frontend_supplied_evidence_is_rejected_and_cannot_promote(self) -> None:
        decision = evaluate_regime_ml_promotion_policy(
            candidate(),
            MemoryPromotionEvidenceRepository(None),
            now=NOW,
            frontend_supplied_evidence=valid_evidence(),
        )

        self.assertFalse(decision.promoted)
        self.assertTrue(decision.frontend_supplied_evidence_rejected)
        self.assertIn("regime.ml.promotion.frontend_supplied_evidence_rejected", decision.reason_codes)
        self.assertIn("regime.ml.promotion.missing_backend_evidence", decision.reason_codes)

    def test_negative_mandatory_conditions_fail_closed(self) -> None:
        cases = (
            ({"walk_forward_passed": False, "untouched_holdout_passed": True}, "regime.ml.promotion.walk_forward_passed_required"),
            ({"untouched_holdout_passed": False, "walk_forward_passed": True}, "regime.ml.promotion.untouched_holdout_passed_required"),
            ({"paper_stability_passed": False}, "regime.ml.promotion.paper_stability_passed_required"),
            ({"deterministic_baseline_comparison_passed": False}, "regime.ml.promotion.deterministic_baseline_comparison_passed_required"),
            ({"calibration_passed": False}, "regime.ml.promotion.calibration_passed_required"),
            ({"leakage_tests_passed": False}, "regime.ml.promotion.leakage_tests_passed_required"),
            ({"minimum_regime_coverage_passed": False}, "regime.ml.promotion.minimum_regime_coverage_passed_required"),
            ({"performance_review_passed": False}, "regime.ml.promotion.performance_review_passed_required"),
            ({"rollback_artifact_retained": False}, "regime.ml.promotion.rollback_artifact_retained_required"),
            ({"tests_passed": False}, "regime.ml.promotion.tests_passed_required"),
        )
        for patch, reason in cases:
            with self.subTest(reason=reason):
                self.assert_blocked(patch, reason)

    def test_paper_volume_and_regime_coverage_are_mandatory(self) -> None:
        cases = (
            ({"paper_trading_day_count": 0}, "regime.ml.promotion.paper_days_required"),
            ({"paper_shadow_decision_count": 0}, "regime.ml.promotion.paper_shadow_decisions_required"),
            ({"distinct_regimes_observed": 1}, "regime.ml.promotion.multi_regime_paper_evidence_required"),
        )
        for patch, reason in cases:
            with self.subTest(reason=reason):
                self.assert_blocked(patch, reason)

    def test_stale_and_mismatched_evidence_fail_closed(self) -> None:
        cases = (
            ({"evidence_expiration_at": (NOW - timedelta(seconds=1)).isoformat()}, "regime.ml.promotion.stale_evidence"),
            ({"artifact_hash": "sha256:other"}, "regime.ml.promotion.artifact_hash_mismatch"),
            ({"model_version": "other-model"}, "regime.ml.promotion.model_version_mismatch"),
            ({"feature_schema_version": "other-schema"}, "regime.ml.promotion.feature_schema_version_mismatch"),
            ({"label_version": "other-labels"}, "regime.ml.promotion.label_version_mismatch"),
        )
        for patch, reason in cases:
            with self.subTest(reason=reason):
                self.assert_blocked(patch, reason)

    def test_failures_during_paper_validation_block_promotion(self) -> None:
        cases = (
            ({"global_risk_violations": 1}, "regime.ml.promotion.global_risk_violation_present"),
            ({"broker_reconciliation_failures": 1}, "regime.ml.promotion.broker_reconciliation_failure_present"),
            ({"unexpected_decision_mutations": 1}, "regime.ml.promotion.unexpected_decision_mutation_present"),
            ({"operational_errors": 1}, "regime.ml.promotion.operational_error_present"),
        )
        for patch, reason in cases:
            with self.subTest(reason=reason):
                self.assert_blocked(patch, reason)

    def test_paper_stability_policy_requires_multiple_days_decisions_regimes_and_clean_operations(self) -> None:
        failed, reasons = evaluate_regime_ml_paper_stability(
            RegimeMlPaperStabilityEvidence(
                paper_trading_day_count=1,
                paper_shadow_decision_count=10,
                eligible_trade_opportunity_count=1,
                completed_paper_trade_count=0,
                distinct_regimes_observed=1,
                trend_condition_observed=True,
                range_condition_observed=False,
                volatility_condition_observed=False,
                event_risk_condition_observed=False,
                liquidity_condition_observed=False,
                classification_instability_rate=0.2,
                calibration_error=0.2,
                prediction_drift=0.2,
                decision_disagreement_rate=0.5,
                global_risk_rejections_without_reason=1,
                broker_reconciliation_failures=1,
                missing_data_failures=1,
                stale_data_failures=1,
                system_errors=1,
                restart_recovery_passed=False,
            )
        )

        self.assertFalse(failed)
        self.assertIn("regime.ml.paper_stability.insufficient_paper_days", reasons)
        self.assertIn("regime.ml.paper_stability.insufficient_distinct_regimes", reasons)
        self.assertIn("regime.ml.paper_stability.broker_reconciliation_failure", reasons)

    def test_promotion_uses_persisted_backend_evidence_tied_to_candidate_artifact(self) -> None:
        path = ROOT / "backend" / "tests" / "tmp" / "regime_ml_promotion" / f"{uuid4().hex}.sqlite"
        repository = RegimeRepository(f"sqlite:///{path}")
        evidence = deepcopy(valid_evidence())

        record = repository.record_regime_ml_promotion_evidence(evidence)
        decision = evaluate_regime_ml_promotion_policy(candidate(), repository, now=NOW)

        self.assertTrue(record["recorded"])
        self.assertTrue(decision.promoted)
        self.assertEqual(decision.target_mode, "confirm_only")


if __name__ == "__main__":
    unittest.main()
