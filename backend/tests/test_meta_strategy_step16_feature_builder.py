from __future__ import annotations

import json
import unittest
from pathlib import Path

from backend.app.algorithms.meta_strategy import (
    ForbiddenMetaStrategyFeatureFieldError,
    ForeignAlgorithmFeatureRowError,
    build_meta_strategy_features,
    build_meta_strategy_features_from_characterization_fixture,
    meta_strategy_feature_schema,
    meta_strategy_feature_schema_hash,
)


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "meta_strategy_current_behavior.json"


class MetaStrategyStep16FeatureBuilderTest(unittest.TestCase):
    maxDiff = None

    def test_feature_schema_hash_is_deterministic_and_matches_characterization(self) -> None:
        payload = fixture_payload()

        self.assertEqual(meta_strategy_feature_schema_hash(), "988c0cfe6ab86361")
        self.assertEqual(meta_strategy_feature_schema_hash(), meta_strategy_feature_schema_hash())
        self.assertEqual(meta_strategy_feature_schema_hash(), payload["featureSchemaHash"])
        self.assertEqual(len(meta_strategy_feature_schema()), 133)

    def test_feature_vectors_match_characterization_fixtures(self) -> None:
        for fixture in fixture_payload()["fixtures"]:
            with self.subTest(fixture=fixture["id"]):
                feature_set = build_meta_strategy_features_from_characterization_fixture(fixture)
                expected = fixture["featureVector"]

                self.assertEqual(feature_set.schemaVersion, expected["schemaVersion"])
                self.assertEqual(feature_set.schemaHash, expected["schemaHash"])
                self.assertEqual(len(feature_set.featureValues), expected["featureCount"])
                self.assertEqual(feature_set.missing_feature_count(), expected["missingFeatureCount"])
                self.assertEqual(feature_set.complete_feature_vector_hash(), expected["completeFeatureVectorHash"])
                for key, value in expected["selectedValues"].items():
                    self.assertEqual(feature_set.featureValues[key], value)

    def test_features_include_required_step16_groups(self) -> None:
        feature_set = build_meta_strategy_features_from_characterization_fixture(fixture_payload()["fixtures"][0])
        required = {
            "strategy_multi_timeframe_trend_alignment_confidence",
            "strategy_multi_timeframe_trend_alignment_eligible",
            "family_trend_score",
            "family_agreement",
            "economic_event_state",
            "regime_category",
            "candidate_side",
            "signal_margin",
            "entry_distance",
            "stop_distance",
            "target_distance",
            "spread_dollars",
            "relative_volume",
            "time_of_day_minutes",
            "expected_transaction_cost",
            "forecast_status",
            "forecast_probability_buy_success",
            "candidate_side__missing",
            "forecast_probability_buy_success__missing",
        }

        self.assertTrue(required.issubset(feature_set.featureValues))

    def test_forbidden_future_fields_are_rejected(self) -> None:
        row = {"algorithmId": "meta_strategy", "id": "bad-row", "futureHigh": 104.0}

        with self.assertRaisesRegex(ForbiddenMetaStrategyFeatureFieldError, "futureHigh"):
            build_meta_strategy_features(row)

    def test_rows_from_other_algorithms_are_rejected(self) -> None:
        row = {"algorithmId": "weighted_voting", "id": "foreign-row"}

        with self.assertRaisesRegex(ForeignAlgorithmFeatureRowError, "weighted_voting"):
            build_meta_strategy_features(row)

    def test_oos_forecast_values_and_missingness_indicators_are_built(self) -> None:
        fixture = dict(fixture_payload()["fixtures"][0])
        fixture["oosForecast"] = {
            "status": "ok",
            "probabilityBuySuccess": 0.61,
            "probabilitySellSuccess": 0.12,
            "probabilityTimeout": 0.27,
            "trainingEndAgeMinutes": 120.0,
            "artifactId": "forecast-artifact-1",
        }
        feature_set = build_meta_strategy_features_from_characterization_fixture(fixture)

        self.assertEqual(feature_set.featureValues["forecast_status"], "ok")
        self.assertEqual(feature_set.featureValues["forecast_probability_buy_success"], 0.61)
        self.assertEqual(feature_set.featureValues["forecast_artifact_id"], "forecast-artifact-1")
        self.assertFalse(feature_set.missingIndicators["forecast_probability_buy_success"])


def fixture_payload() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
