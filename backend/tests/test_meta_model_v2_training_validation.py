from __future__ import annotations

import unittest
from datetime import timedelta
from pathlib import Path

from backend.app.algorithms.meta_strategy.training.training_core import (
    DEFAULT_META_LABEL_VERSION,
    build_meta_model_v2_validation_package,
    train_and_validate_meta_model_v2,
    v2_training_compatibility_report,
)
from backend.tests.test_meta_strategy_champion_challengers import patched_optional_boosters_unavailable
from backend.tests.test_meta_strategy_nested_training import START, labeled_row, patched_training_io


class MetaModelV2TrainingValidationTest(unittest.TestCase):
    def test_training_uses_only_compatible_v2_rows_and_produces_validation_package(self) -> None:
        rows = [v2_labeled_row(index) for index in range(180)]
        rows.append(v1_labeled_row(1000))
        rows.append(v2_labeled_row(1001, market_feed="demo"))

        with patched_training_io(rows), patched_optional_boosters_unavailable():
            result = train_and_validate_meta_model_v2(
                decision_snapshot_dir=Path("unused"),
                symbol="SPY",
                minimum_total_candidates=120,
                minimum_buy_candidates=20,
                minimum_sell_candidates=20,
                minimum_positive_outcomes=40,
                minimum_negative_outcomes=20,
                minimum_candidates_per_outer_fold=12,
                minimum_trading_sessions=4,
                minimum_regimes_represented=2,
                minimum_calibration_rows=20,
                minimum_isotonic_rows=40,
                outer_folds=2,
                inner_folds=2,
                maximum_holding_horizon_minutes=10,
                embargo_minutes=10,
            )

        validation = result["v2TrainingValidation"]
        self.assertEqual(validation["version"], "meta_model_v2_training_validation_v1")
        self.assertEqual(validation["trainingRows"], 180)
        self.assertEqual(validation["compatibility"]["excludedRowCount"], 2)
        self.assertIn("v2_training.v1_snapshot_excluded", validation["compatibility"]["excludedReasonCounts"])
        self.assertIn("v2_training.demo_or_fallback_market_data", validation["compatibility"]["excludedReasonCounts"])
        self.assertGreater(len(validation["outerFoldReports"]), 0)
        self.assertTrue(validation["finalHoldoutReport"]["untouched"])
        self.assertEqual(validation["calibrationReport"]["source"], "inner_out_of_fold")
        self.assertIn("deterministicBaseline", validation["economicComparison"])
        self.assertIn("buyPerformance", validation["sideAndRegimeBreakdown"])
        self.assertIn("performanceByRegime", validation["sideAndRegimeBreakdown"])
        self.assertEqual(validation["modelCard"]["objective"], "candidate_success_probability")
        self.assertEqual(validation["modelCard"]["labelVersion"], DEFAULT_META_LABEL_VERSION)
        self.assertFalse(result["trusted"] and not validation["promotionDecision"]["trusted"])
        self.assertIn("documentedPromotionCriteria", validation["promotionDecision"]["criteria"])

    def test_upstream_forecast_features_must_be_out_of_sample(self) -> None:
        good = v2_labeled_row(1)
        leaky = v2_labeled_row(2)
        leaky["forecastFeature"]["trainingWindowEndUtc"] = leaky["decisionTimestampUtc"]

        report = v2_training_compatibility_report([good, leaky], label_version=DEFAULT_META_LABEL_VERSION)

        self.assertEqual(report["compatibleRowCount"], 1)
        self.assertEqual(report["excludedRowCount"], 1)
        self.assertIn("v2_training.upstream_forecast_not_out_of_sample", report["excludedRows"][0]["reasonCodes"])

    def test_promotion_decision_cannot_be_trusted_when_source_or_criteria_fail(self) -> None:
        package = build_meta_model_v2_validation_package(
            training_result={
                "trusted": True,
                "featureSchemaVersion": "meta_strategy_training_feature_vector_v2",
                "featureSchemaHash": "hash",
                "labelVersion": DEFAULT_META_LABEL_VERSION,
                "validationPolicy": {"finalHoldoutPolicy": "final holdout was reused"},
                "models": {
                    "logistic_regression_champion": {
                        "calibration": {
                            "source": "inner_out_of_fold",
                            "probabilitySizingApproved": False,
                            "approvalReasonCodes": ["calibration.ece_too_high"],
                        }
                    }
                },
                "metrics": {"economicPromotion": {"promoted": False, "rejectedReasonCodes": ["economic.drawdown_unacceptable"]}},
            },
            compatibility={"rawRows": 1, "compatibleRowCount": 1, "excludedRowCount": 0, "excludedRows": [], "excludedReasonCounts": {}},
            label_version=DEFAULT_META_LABEL_VERSION,
        )

        self.assertFalse(package["promotionDecision"]["trusted"])
        self.assertIn("validation.final_holdout_not_confirmed_untouched", package["promotionDecision"]["rejectedReasonCodes"])
        self.assertIn("calibration.not_approved", package["promotionDecision"]["rejectedReasonCodes"])
        self.assertIn("economic.drawdown_unacceptable", package["promotionDecision"]["rejectedReasonCodes"])


def v2_labeled_row(index: int, *, market_feed: str = "alpaca_paper") -> dict:
    row = labeled_row(index)
    decision_at = START + timedelta(minutes=index * 5)
    row.update(
        {
            "snapshotSchemaVersion": "decision_snapshot_v2",
            "strategySchemaVersion": "strategy_schema_v2",
            "featureSchemaVersion": "candidate_meta_feature_schema_v1",
            "labelVersion": DEFAULT_META_LABEL_VERSION,
            "algorithmVersion": "deterministic_v2_static_baseline_v1",
            "marketDataFeed": market_feed,
            "eligibleForTraining": True,
            "trainingCompatibleWithV2": True,
            "rawMarketReferences": {"provider": market_feed},
            "forecastFeature": {
                "status": "out_of_sample",
                "trainingWindowEndUtc": (decision_at - timedelta(minutes=5)).isoformat(),
                "artifactId": f"forecast-fold-{index // 30}",
            },
        }
    )
    return row


def v1_labeled_row(index: int) -> dict:
    row = labeled_row(index)
    row.update(
        {
            "snapshotSchemaVersion": "voting_ensemble_v1",
            "sourceSchemaVersion": "voting_ensemble_v1",
            "eligibleForTraining": True,
            "trainingCompatibleWithV2": False,
            "labelVersion": DEFAULT_META_LABEL_VERSION,
            "marketDataFeed": "alpaca_paper",
        }
    )
    return row


if __name__ == "__main__":
    unittest.main()
