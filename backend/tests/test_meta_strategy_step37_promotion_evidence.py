from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.app.algorithms.meta_strategy import (
    META_STRATEGY_PROMOTION_EVIDENCE_FIELDS,
    MetaStrategyPromotionPolicy,
    PromotionEvidenceSourceError,
    artifact_hash,
    build_meta_strategy_promotion_evidence,
    evaluate_meta_strategy_promotion_policy,
    evidence_matches_candidate_artifact,
)


ROOT = Path(__file__).resolve().parents[2]
PROMOTION_DIR = ROOT / "backend" / "app" / "algorithms" / "meta_strategy" / "promotion"
NOW = datetime(2026, 1, 12, 15, 30, tzinfo=UTC)


class MetaStrategyStep37PromotionEvidenceTest(unittest.TestCase):
    def test_promotion_package_files_exist(self) -> None:
        self.assertTrue((PROMOTION_DIR / "evidence.py").is_file())
        self.assertTrue((PROMOTION_DIR / "policy.py").is_file())

    def test_backend_generated_evidence_contains_all_required_fields_and_promotes_when_valid(self) -> None:
        artifact = candidate_artifact()
        evidence = valid_evidence(artifact)

        decision = evaluate_meta_strategy_promotion_policy(evidence, candidate_artifact=artifact, checked_at=NOW)

        self.assertEqual(evidence.algorithm_id, "meta_strategy")
        self.assertEqual(evidence.artifact_id, artifact["artifactId"])
        self.assertEqual(evidence.artifact_hash, artifact_hash(artifact))
        self.assertTrue(evidence_matches_candidate_artifact(evidence, artifact))
        self.assertTrue(decision.promoted)
        self.assertEqual(decision.action, "PROMOTE")
        for field_name in META_STRATEGY_PROMOTION_EVIDENCE_FIELDS:
            self.assertTrue(getattr(evidence, field_name), field_name)
            self.assertIn(field_name, evidence.evidence_timestamps)
        with self.assertRaises(FrozenInstanceError):
            evidence.artifact_id = "mutated"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            evidence.walk_forward_result["passed"] = False  # type: ignore[index]

    def test_missing_evidence_fails_closed(self) -> None:
        artifact = candidate_artifact()
        evidence = valid_evidence(artifact, paper_stability={})

        decision = evaluate_meta_strategy_promotion_policy(evidence, candidate_artifact=artifact, checked_at=NOW)

        self.assertFalse(decision.promoted)
        self.assertEqual(decision.action, "REJECT")
        self.assertIn("meta_strategy.promotion.fail_closed", decision.reason_codes)
        self.assertIn("meta_strategy.promotion.missing_paper_stability", decision.reason_codes)

    def test_stale_evidence_fails_closed(self) -> None:
        artifact = candidate_artifact()
        stale = NOW - timedelta(days=30)
        evidence = valid_evidence(
            artifact,
            evidence_timestamps={field_name: stale for field_name in META_STRATEGY_PROMOTION_EVIDENCE_FIELDS},
            generated_at=stale,
        )

        decision = evaluate_meta_strategy_promotion_policy(
            evidence,
            candidate_artifact=artifact,
            policy=MetaStrategyPromotionPolicy(max_evidence_age_days=7),
            checked_at=NOW,
        )

        self.assertFalse(decision.promoted)
        self.assertIn("meta_strategy.promotion.stale_walk_forward_result", decision.reason_codes)

    def test_evidence_must_match_exact_candidate_artifact(self) -> None:
        artifact = candidate_artifact()
        evidence = valid_evidence(artifact)
        mutated = {**artifact, "featureSchemaHash": "different-schema"}

        decision = evaluate_meta_strategy_promotion_policy(evidence, candidate_artifact=mutated, checked_at=NOW)

        self.assertFalse(evidence_matches_candidate_artifact(evidence, mutated))
        self.assertFalse(decision.promoted)
        self.assertIn("meta_strategy.promotion.candidate_artifact_mismatch", decision.reason_codes)

    def test_frontend_provided_evidence_is_not_trusted(self) -> None:
        with self.assertRaises(PromotionEvidenceSourceError):
            valid_evidence(candidate_artifact(), evidence_source="frontend")

    def test_operational_risk_and_reconciliation_failures_block_promotion(self) -> None:
        artifact = candidate_artifact()
        evidence = valid_evidence(
            artifact,
            operational_failures={"count": 1},
            risk_violations={"count": 1},
            reconciliation_failures={"count": 1},
        )

        decision = evaluate_meta_strategy_promotion_policy(evidence, candidate_artifact=artifact, checked_at=NOW)

        self.assertFalse(decision.promoted)
        self.assertIn("meta_strategy.promotion.operational_failures", decision.reason_codes)
        self.assertIn("meta_strategy.promotion.risk_violations", decision.reason_codes)
        self.assertIn("meta_strategy.promotion.reconciliation_failures", decision.reason_codes)


def candidate_artifact() -> dict:
    artifact = {
        "artifactId": "meta-strategy-candidate-artifact-37",
        "modelVersion": "meta_strategy_model_v1",
        "modelArtifactVersion": "meta_strategy_model_artifact_v1",
        "featureSchemaHash": "feature-schema-step37",
        "labelVersion": "candidate_triple_barrier_v1",
        "strategyCatalogVersion": "meta_strategy_strategy_catalog_v1",
        "promotionStatus": "candidate",
        "rollbackArtifact": {"artifactId": "meta-strategy-runtime-artifact-previous", "artifactHash": "previous-hash"},
    }
    return {**artifact, "artifactHash": artifact_hash(artifact)}


def valid_evidence(
    artifact: dict,
    *,
    evidence_timestamps: dict | None = None,
    generated_at: datetime = NOW,
    evidence_source: str = "backend",
    **overrides,
):
    payload = {
        "candidate_artifact": artifact,
        "walk_forward_result": {"passed": True, "netPnl": 100.0},
        "holdout_result": {"passed": True, "netPnl": 25.0},
        "calibration_result": {"approved": True, "brierScore": 0.12, "probabilitySizingApproved": True},
        "economic_baseline_comparison": {"outperformedBaseline": True, "netPnlDelta": 30.0},
        "drawdown_result": {"maxDrawdown": 2.0},
        "side_coverage": {"coverage": {"BUY": 0.25, "SELL": 0.25}},
        "regime_coverage": {"coverage": {"trend": 0.25, "range": 0.25}},
        "shadow_comparison": {"passed": True},
        "paper_stability": {"stable": True, "paperSessions": 5},
        "operational_failures": {"count": 0},
        "risk_violations": {"count": 0},
        "reconciliation_failures": {"count": 0},
        "rollback_artifact": artifact["rollbackArtifact"],
        "evidence_timestamps": evidence_timestamps or {field_name: generated_at for field_name in META_STRATEGY_PROMOTION_EVIDENCE_FIELDS},
        "generated_at": generated_at,
        "evidence_source": evidence_source,
    }
    payload.update(overrides)
    return build_meta_strategy_promotion_evidence(**payload)


if __name__ == "__main__":
    unittest.main()
