from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from backend.app.algorithms.meta_strategy import (
    FORECAST_FEATURE_VERSION,
    ForecastFallbackFeature,
    ForecastFeatureLeakageError,
    OutOfSampleForecastFeature,
    generate_oos_forecast_features,
    missing_forecast_feature,
    reject_full_history_forecast_artifact_for_historical_features,
    reject_in_sample_forecast_feature,
    select_live_forecast_feature,
    validate_oos_fold,
)


START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)


def rows(count: int = 180) -> list[dict]:
    generated: list[dict] = []
    for index in range(count):
        timestamp = START + timedelta(minutes=index)
        generated.append(
            {
                "rowId": f"row-{index}",
                "timestamp": timestamp.isoformat(),
                "labelStart": timestamp.isoformat(),
                "labelEnd": (timestamp + timedelta(minutes=5)).isoformat(),
                "features": {
                    "bias": 1.0,
                    "trend": (index % 11) / 10,
                    "momentum": ((index * 3) % 17) / 16,
                },
                "target": 1 if index % 5 in {0, 1} else -1 if index % 5 == 2 else 0,
                "targetProfit": 1.0,
                "stopLoss": 0.6,
                "tradingCost": 0.03,
            }
        )
    return generated


class MetaStrategyStep18ForecastTest(unittest.TestCase):
    def test_walk_forward_predictions_persist_artifact_id_and_oos_windows(self) -> None:
        features = generate_oos_forecast_features(
            rows(),
            symbol="SPY",
            requested_folds=2,
            embargo_minutes=5,
            min_train_rows=20,
            min_validation_rows=5,
        )

        self.assertGreater(len(features), 0)
        for feature in features:
            with self.subTest(row=feature.rowId):
                self.assertEqual(feature.featureVersion, FORECAST_FEATURE_VERSION)
                self.assertEqual(feature.status, "out_of_sample")
                self.assertLess(feature.trainingWindowEndUtc, feature.decisionTimestampUtc)
                self.assertLessEqual(feature.trainingWindowStartUtc, feature.trainingWindowEndUtc)
                self.assertIsNotNone(feature.validationWindowStartUtc)
                self.assertIsNotNone(feature.validationWindowEndUtc)
                self.assertIn("train-end-", feature.artifactId)

    def test_in_sample_probabilities_are_rejected_by_contract_and_validator(self) -> None:
        with self.assertRaisesRegex(ValidationError, "training window must end before prediction time"):
            OutOfSampleForecastFeature(
                status="out_of_sample",
                rowId="row-leaky",
                symbol="SPY",
                decisionTimestampUtc=NOW,
                trainingWindowStartUtc=NOW - timedelta(days=1),
                trainingWindowEndUtc=NOW,
                artifactId="leaky-artifact",
                probabilityBuySuccess=0.6,
                probabilitySellSuccess=0.2,
                probabilityTimeout=0.2,
                explanation="Invalid in-sample feature.",
            )

        valid = OutOfSampleForecastFeature(
            status="out_of_sample",
            rowId="row-valid",
            symbol="SPY",
            decisionTimestampUtc=NOW,
            trainingWindowStartUtc=NOW - timedelta(days=1),
            trainingWindowEndUtc=NOW - timedelta(minutes=1),
            artifactId="valid-artifact",
            probabilityBuySuccess=0.6,
            probabilitySellSuccess=0.2,
            probabilityTimeout=0.2,
            explanation="Valid OOS feature.",
        )
        reject_in_sample_forecast_feature(valid)

    def test_fold_validation_rejects_training_validation_overlap(self) -> None:
        overlap = {
            "fold": 1,
            "trainRows": rows(12)[:10],
            "validationRows": rows(12)[9:],
        }

        with self.assertRaisesRegex(ForecastFeatureLeakageError, "overlaps validation"):
            validate_oos_fold(overlap)

    def test_missing_forecast_remains_missing_instead_of_fabricated(self) -> None:
        fallback = select_live_forecast_feature(
            decision_timestamp_utc=NOW,
            symbol="SPY",
            approved_artifacts=[
                {
                    "artifactId": "too-new",
                    "symbol": "SPY",
                    "approved": True,
                    "trainingWindowEndUtc": (NOW + timedelta(minutes=1)).isoformat(),
                },
                {
                    "artifactId": "unapproved-old",
                    "symbol": "SPY",
                    "approved": False,
                    "trainingWindowEndUtc": (NOW - timedelta(minutes=30)).isoformat(),
                },
            ],
        )

        self.assertIsInstance(fallback, ForecastFallbackFeature)
        self.assertEqual(fallback, missing_forecast_feature())
        self.assertEqual(fallback.status, "missing_approved_forecast_model")
        self.assertIsNone(fallback.probabilityBuySuccess)
        self.assertIsNone(fallback.probabilitySellSuccess)
        self.assertIsNone(fallback.artifactId)

    def test_live_approved_artifact_requires_training_end_before_decision_and_persists_window(self) -> None:
        selected = select_live_forecast_feature(
            decision_timestamp_utc=NOW,
            symbol="SPY",
            approved_artifacts=[
                {
                    "artifactId": "approved-old",
                    "symbol": "SPY",
                    "approved": True,
                    "trainingWindowStartUtc": (NOW - timedelta(days=5)).isoformat(),
                    "trainingWindowEndUtc": (NOW - timedelta(minutes=15)).isoformat(),
                    "modelKind": "logistic_oos_forecast",
                }
            ],
        )

        self.assertIsInstance(selected, OutOfSampleForecastFeature)
        self.assertEqual(selected.status, "live_approved_artifact")
        self.assertEqual(selected.artifactId, "approved-old")
        self.assertEqual(selected.trainingWindowStartUtc, NOW - timedelta(days=5))
        self.assertEqual(selected.trainingWindowEndUtc, NOW - timedelta(minutes=15))
        self.assertLess(selected.trainingWindowEndUtc, selected.decisionTimestampUtc)

    def test_full_history_artifact_cannot_backfill_historical_forecast_features(self) -> None:
        with self.assertRaisesRegex(ForecastFeatureLeakageError, "full-history"):
            reject_full_history_forecast_artifact_for_historical_features(
                {
                    "artifactId": "forecast-full-history",
                    "trainedOnFullHistory": True,
                    "trainingWindowEndUtc": "2026-01-05T20:00:00+00:00",
                }
            )


if __name__ == "__main__":
    unittest.main()
