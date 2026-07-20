from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.app.algorithms.meta_strategy import (
    MetaStrategyPaperStabilityConfig,
    artifact_hash,
    paper_stability_matches_candidate_artifact,
    validate_meta_strategy_paper_stability,
)
from backend.tests.test_meta_strategy_step37_promotion_evidence import candidate_artifact


ROOT = Path(__file__).resolve().parents[2]
PAPER_STABILITY_PATH = ROOT / "backend" / "app" / "algorithms" / "meta_strategy" / "promotion" / "paper_stability.py"
START = datetime(2026, 1, 14, 15, 30, tzinfo=UTC)


class MetaStrategyStep39PaperStabilityTest(unittest.TestCase):
    def test_paper_stability_file_exists(self) -> None:
        self.assertTrue(PAPER_STABILITY_PATH.is_file())

    def test_valid_paper_stability_evidence_is_backend_attributed_and_artifact_bound(self) -> None:
        artifact = candidate_artifact()
        evidence = validate_meta_strategy_paper_stability(
            candidate_artifact=artifact,
            observations=paper_observations(artifact),
            generated_at=START + timedelta(days=3),
        )

        self.assertEqual(evidence.algorithm_id, "meta_strategy")
        self.assertTrue(evidence.stable)
        self.assertEqual(evidence.paper_days, 3)
        self.assertEqual(evidence.shadow_decisions, 150)
        self.assertEqual(evidence.eligible_candidates, 30)
        self.assertEqual(evidence.completed_trades, 15)
        self.assertGreaterEqual(evidence.buy_count, 1)
        self.assertGreaterEqual(evidence.sell_count, 1)
        self.assertEqual(evidence.distinct_regimes, 2)
        self.assertEqual(evidence.reason_codes, ("meta_strategy.paper_stability.stable",))
        self.assertTrue(paper_stability_matches_candidate_artifact(evidence, artifact))
        with self.assertRaises(FrozenInstanceError):
            evidence.stable = False  # type: ignore[misc]
        with self.assertRaises(TypeError):
            evidence.metrics["paperDays"] = 1  # type: ignore[index]

    def test_one_favorable_day_cannot_satisfy_paper_stability(self) -> None:
        artifact = candidate_artifact()
        one_day = paper_observations(artifact)[:1]

        evidence = validate_meta_strategy_paper_stability(candidate_artifact=artifact, observations=one_day, generated_at=START)

        self.assertFalse(evidence.stable)
        self.assertIn("meta_strategy.paper_stability.insufficient_paper_days", evidence.reason_codes)

    def test_missing_regime_coverage_blocks_promotion(self) -> None:
        artifact = candidate_artifact()
        observations = tuple({**row, "regimes": ("trend",)} for row in paper_observations(artifact))

        evidence = validate_meta_strategy_paper_stability(candidate_artifact=artifact, observations=observations, generated_at=START + timedelta(days=3))

        self.assertFalse(evidence.stable)
        self.assertIn("meta_strategy.paper_stability.regime_coverage_missing", evidence.reason_codes)

    def test_missing_side_coverage_blocks_promotion(self) -> None:
        artifact = candidate_artifact()
        observations = tuple({**row, "sellCount": 0} for row in paper_observations(artifact))

        evidence = validate_meta_strategy_paper_stability(candidate_artifact=artifact, observations=observations, generated_at=START + timedelta(days=3))

        self.assertFalse(evidence.stable)
        self.assertIn("meta_strategy.paper_stability.sell_coverage_missing", evidence.reason_codes)

    def test_drift_ood_risk_reconciliation_and_operational_limits_fail_closed(self) -> None:
        artifact = candidate_artifact()
        observations = list(paper_observations(artifact))
        observations[0] = {
            **observations[0],
            "calibrationDrift": 0.20,
            "featureDrift": 0.30,
            "oodRate": 0.40,
            "riskViolations": 1,
            "reconciliationFailures": 1,
            "operationalErrors": 1,
        }

        evidence = validate_meta_strategy_paper_stability(candidate_artifact=artifact, observations=tuple(observations), generated_at=START + timedelta(days=3))

        self.assertFalse(evidence.stable)
        self.assertIn("meta_strategy.paper_stability.calibration_unstable", evidence.reason_codes)
        self.assertIn("meta_strategy.paper_stability.feature_drift_too_high", evidence.reason_codes)
        self.assertIn("meta_strategy.paper_stability.ood_rate_too_high", evidence.reason_codes)
        self.assertIn("meta_strategy.paper_stability.risk_violations", evidence.reason_codes)
        self.assertIn("meta_strategy.paper_stability.reconciliation_failures", evidence.reason_codes)
        self.assertIn("meta_strategy.paper_stability.operational_errors", evidence.reason_codes)

    def test_evidence_is_tied_to_exact_model_artifact(self) -> None:
        artifact = candidate_artifact()
        other_base = {key: value for key, value in candidate_artifact().items() if key != "artifactHash"}
        other_base["artifactId"] = "other-artifact"
        other = {**other_base, "artifactHash": artifact_hash(other_base)}

        evidence = validate_meta_strategy_paper_stability(candidate_artifact=artifact, observations=paper_observations(artifact), generated_at=START + timedelta(days=3))
        mismatched = validate_meta_strategy_paper_stability(candidate_artifact=other, observations=paper_observations(artifact), generated_at=START + timedelta(days=3))

        self.assertFalse(paper_stability_matches_candidate_artifact(evidence, other))
        self.assertFalse(mismatched.stable)
        self.assertIn("meta_strategy.paper_stability.no_matching_artifact_observations", mismatched.reason_codes)

    def test_configurable_minimums_are_enforced(self) -> None:
        artifact = candidate_artifact()
        config = MetaStrategyPaperStabilityConfig(minimum_shadow_decisions=151)

        evidence = validate_meta_strategy_paper_stability(
            candidate_artifact=artifact,
            observations=paper_observations(artifact),
            config=config,
            generated_at=START + timedelta(days=3),
        )

        self.assertFalse(evidence.stable)
        self.assertIn("meta_strategy.paper_stability.insufficient_shadow_decisions", evidence.reason_codes)


def paper_observations(artifact: dict) -> tuple[dict, ...]:
    rows = []
    for index, regime in enumerate(("trend", "range", "trend")):
        rows.append(
            {
                "artifactId": artifact["artifactId"],
                "artifactHash": artifact["artifactHash"],
                "timestamp": START + timedelta(days=index),
                "sessionDate": (START + timedelta(days=index)).date().isoformat(),
                "shadowDecisions": 50,
                "eligibleCandidates": 10,
                "completedTrades": 5,
                "buyCount": 3 if index != 1 else 1,
                "sellCount": 1 if index != 0 else 2,
                "regimes": (regime,),
                "calibrationDrift": 0.01,
                "featureDrift": 0.02,
                "oodRate": 0.03,
                "riskViolations": 0,
                "reconciliationFailures": 0,
                "operationalErrors": 0,
            }
        )
    return tuple(rows)


if __name__ == "__main__":
    unittest.main()
