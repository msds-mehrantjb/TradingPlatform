from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from backend.app.algorithms.meta_strategy.models import (
    LogisticRegressionChampion,
    artifact_hash,
    load_runtime_model_artifact_data,
    model_artifact_payload,
    runtime_model_artifact_payload,
)
from backend.tests.test_meta_strategy_step20_models import FEATURE_NAMES, training_rows


FEATURE_SCHEMA_HASH = "feature-schema-step21"


def valid_runtime_artifact() -> dict:
    model = LogisticRegressionChampion().fit(training_rows(), FEATURE_NAMES)
    model_payload = model_artifact_payload(
        model,
        feature_schema_hash=FEATURE_SCHEMA_HASH,
        label_version="candidate_triple_barrier_v1",
        training_window={"start": "2026-01-05T14:30:00+00:00", "end": "2026-01-05T15:30:00+00:00"},
    )
    return runtime_model_artifact_payload(
        artifact_id="meta-strategy-runtime-artifact-1",
        feature_schema_hash=FEATURE_SCHEMA_HASH,
        label_version="candidate_triple_barrier_v1",
        training_window={"start": "2026-01-05T14:30:00+00:00", "end": "2026-01-05T15:30:00+00:00"},
        validation_windows=[
            {"fold": 1, "start": "2026-01-05T15:40:00+00:00", "end": "2026-01-05T16:20:00+00:00"}
        ],
        holdout_window={"start": "2026-01-05T16:30:00+00:00", "end": "2026-01-05T17:00:00+00:00"},
        calibration_method="inner_out_of_fold_sigmoid_platt",
        economic_metrics={"netExpectancyAfterCosts": 0.12, "maximumDrawdown": 1.5, "tradeCoverage": 0.4},
        random_seed=17,
        promotion_status="approved",
        rollback_artifact={"artifactId": "meta-strategy-runtime-artifact-0", "artifactHash": "previous-hash"},
        models={"logistic_regression_champion": model_payload},
        library_versions={"python": "3.12.test", "pydantic": "test", "xgboost": "missing", "lightgbm": "missing"},
    )


class MetaStrategyStep21ArtifactManagementTest(unittest.TestCase):
    def test_runtime_artifact_contains_required_management_metadata_and_loads_immutable(self) -> None:
        artifact = valid_runtime_artifact()

        loaded = load_runtime_model_artifact_data(artifact, expected_feature_schema_hash=FEATURE_SCHEMA_HASH)

        self.assertEqual(loaded.artifactId, "meta-strategy-runtime-artifact-1")
        self.assertEqual(loaded.artifactHash, artifact_hash(artifact))
        self.assertEqual(loaded.featureSchemaHash, FEATURE_SCHEMA_HASH)
        self.assertEqual(loaded.labelVersion, "candidate_triple_barrier_v1")
        self.assertEqual(loaded.strategyCatalogVersion, "meta_strategy_strategy_catalog_v1")
        self.assertEqual(loaded.calibrationMethod, "inner_out_of_fold_sigmoid_platt")
        self.assertEqual(loaded.randomSeed, 17)
        self.assertEqual(loaded.promotionStatus, "approved")
        self.assertEqual(loaded.rollbackArtifact["artifactId"], "meta-strategy-runtime-artifact-0")
        self.assertIn("netExpectancyAfterCosts", loaded.economicMetrics)
        self.assertIn("python", loaded.libraryVersions)
        with self.assertRaises(FrozenInstanceError):
            loaded.artifactId = "mutated"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            loaded.payload["artifactId"] = "mutated"

    def test_invalid_top_level_artifact_hash_is_rejected(self) -> None:
        artifact = valid_runtime_artifact()
        artifact["artifactHash"] = "bad-hash"

        with self.assertRaisesRegex(ValueError, "artifact hash mismatch"):
            load_runtime_model_artifact_data(artifact, expected_feature_schema_hash=FEATURE_SCHEMA_HASH)

    def test_invalid_nested_model_hash_is_rejected(self) -> None:
        artifact = valid_runtime_artifact()
        artifact["models"]["logistic_regression_champion"]["kind"] = "mutated"
        artifact["artifactHash"] = artifact_hash(artifact)

        with self.assertRaisesRegex(ValueError, "model hash mismatch"):
            load_runtime_model_artifact_data(artifact, expected_feature_schema_hash=FEATURE_SCHEMA_HASH)

    def test_incompatible_feature_schema_is_rejected(self) -> None:
        artifact = valid_runtime_artifact()

        with self.assertRaisesRegex(ValueError, "feature schema mismatch"):
            load_runtime_model_artifact_data(artifact, expected_feature_schema_hash="wrong-schema")

    def test_unapproved_or_retired_artifacts_cannot_load(self) -> None:
        unapproved = valid_runtime_artifact()
        unapproved["approved"] = False
        unapproved["promotionStatus"] = "shadow"
        unapproved["artifactHash"] = artifact_hash(unapproved)

        retired = valid_runtime_artifact()
        retired["approved"] = True
        retired["retired"] = True
        retired["promotionStatus"] = "retired"
        retired["artifactHash"] = artifact_hash(retired)

        with self.assertRaisesRegex(ValueError, "not approved"):
            load_runtime_model_artifact_data(unapproved, expected_feature_schema_hash=FEATURE_SCHEMA_HASH)
        with self.assertRaisesRegex(ValueError, "not approved|retired"):
            load_runtime_model_artifact_data(retired, expected_feature_schema_hash=FEATURE_SCHEMA_HASH)

    def test_missing_required_runtime_metadata_is_rejected(self) -> None:
        artifact = valid_runtime_artifact()
        artifact.pop("rollbackArtifact")
        artifact["artifactHash"] = artifact_hash(artifact)

        with self.assertRaisesRegex(ValueError, "missing mandatory fields"):
            load_runtime_model_artifact_data(artifact, expected_feature_schema_hash=FEATURE_SCHEMA_HASH)


if __name__ == "__main__":
    unittest.main()
