from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.meta_strategy.forecast.oos_features import (
    ForecastFallbackFeature,
    ForecastFeatureLeakageError,
    OutOfSampleForecastFeature,
    generate_oos_forecast_features,
    reject_full_history_forecast_artifact_for_historical_features,
    select_live_forecast_feature,
    validate_oos_fold,
)
from backend.app.algorithms.meta_strategy.ml_features import ForbiddenMLFeatureFieldError, build_candidate_meta_features
from backend.tests.test_decision_snapshot_v2_archive import CONFIG_HASH, NOW, snapshot


START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


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


class OutOfSampleForecastFeatureTest(unittest.TestCase):
    def test_walk_forward_predictions_store_training_window_end_and_are_later(self) -> None:
        features = generate_oos_forecast_features(
            rows(),
            symbol="SPY",
            requested_folds=2,
            embargo_minutes=5,
            min_train_rows=20,
            min_validation_rows=5,
        )

        self.assertGreater(len(features), 0)
        self.assertTrue(all(feature.status == "out_of_sample" for feature in features))
        self.assertTrue(all(feature.trainingWindowEndUtc < feature.decisionTimestampUtc for feature in features))
        self.assertTrue(all(feature.trainingWindowEndUtc is not None for feature in features))
        self.assertIn("train-end-", features[0].artifactId)

    def test_forecast_model_cannot_predict_rows_from_its_own_fitting_period(self) -> None:
        overlap = {
            "fold": 1,
            "trainRows": rows(12)[:10],
            "validationRows": rows(12)[9:],
        }

        with self.assertRaisesRegex(ForecastFeatureLeakageError, "overlaps validation"):
            validate_oos_fold(overlap)

    def test_final_full_history_artifact_cannot_make_historical_meta_features(self) -> None:
        with self.assertRaisesRegex(ForecastFeatureLeakageError, "full-history"):
            reject_full_history_forecast_artifact_for_historical_features(
                {
                    "artifactId": "forecast-full-history",
                    "trainedOnFullHistory": True,
                    "trainingWindowEndUtc": "2026-01-05T20:00:00+00:00",
                }
            )

    def test_live_inference_uses_only_approved_artifact_trained_before_decision(self) -> None:
        selected = select_live_forecast_feature(
            decision_timestamp_utc=NOW,
            symbol="SPY",
            approved_artifacts=[
                {
                    "artifactId": "too-new",
                    "symbol": "SPY",
                    "approved": True,
                    "trainingWindowStartUtc": (NOW - timedelta(days=2)).isoformat(),
                    "trainingWindowEndUtc": (NOW + timedelta(minutes=1)).isoformat(),
                },
                {
                    "artifactId": "approved-old",
                    "symbol": "SPY",
                    "approved": True,
                    "trainingWindowStartUtc": (NOW - timedelta(days=3)).isoformat(),
                    "trainingWindowEndUtc": (NOW - timedelta(minutes=1)).isoformat(),
                },
            ],
        )

        self.assertIsInstance(selected, OutOfSampleForecastFeature)
        self.assertEqual(selected.artifactId, "approved-old")
        self.assertLess(selected.trainingWindowEndUtc, NOW)

    def test_missing_approved_live_forecast_returns_explicit_fallback(self) -> None:
        selected = select_live_forecast_feature(
            decision_timestamp_utc=NOW,
            symbol="SPY",
            approved_artifacts=[
                {
                    "artifactId": "unapproved",
                    "symbol": "SPY",
                    "approved": False,
                    "trainingWindowEndUtc": (NOW - timedelta(minutes=1)).isoformat(),
                }
            ],
        )

        self.assertIsInstance(selected, ForecastFallbackFeature)
        self.assertEqual(selected.status, "missing_approved_forecast_model")
        self.assertIn("forecast_model.missing_approved_artifact", selected.reasonCodes)

    def test_meta_feature_builder_rejects_forecast_trained_through_decision(self) -> None:
        bad_forecast = OutOfSampleForecastFeature(
            status="out_of_sample",
            rowId="snapshot-1",
            symbol="SPY",
            decisionTimestampUtc=NOW,
            trainingWindowStartUtc=NOW - timedelta(days=1),
            trainingWindowEndUtc=NOW,
            artifactId="leaky-forecast",
            probabilityBuySuccess=0.6,
            probabilitySellSuccess=0.2,
            probabilityTimeout=0.2,
            explanation="Invalid leaky feature.",
        )

        with self.assertRaisesRegex(ForbiddenMLFeatureFieldError, "training window must end before"):
            build_candidate_meta_features(snapshot(), forecastFeature=bad_forecast)

    def test_meta_feature_builder_accepts_oos_forecast_and_records_missing_fallback(self) -> None:
        good_forecast = OutOfSampleForecastFeature(
            status="out_of_sample",
            rowId="snapshot-1",
            symbol="SPY",
            decisionTimestampUtc=NOW,
            trainingWindowStartUtc=NOW - timedelta(days=1),
            trainingWindowEndUtc=NOW - timedelta(minutes=10),
            validationWindowStartUtc=NOW,
            validationWindowEndUtc=NOW + timedelta(minutes=30),
            fold=1,
            artifactId="fold-1-forecast",
            probabilityBuySuccess=0.6,
            probabilitySellSuccess=0.25,
            probabilityTimeout=0.15,
            explanation="Valid OOS feature.",
        )

        features = build_candidate_meta_features(snapshot(), forecastFeature=good_forecast)
        fallback = build_candidate_meta_features(snapshot())

        self.assertEqual(features.featureValues["forecast_status"], "out_of_sample")
        self.assertEqual(features.featureValues["forecast_probability_buy_success"], 0.6)
        self.assertEqual(features.featureValues["forecast_training_end_age_minutes"], 10.0)
        self.assertEqual(fallback.featureValues["forecast_status"], "missing_approved_forecast_model")
        self.assertEqual(fallback.featureValues["forecast_probability_buy_success__missing"], 1)


if __name__ == "__main__":
    unittest.main()
