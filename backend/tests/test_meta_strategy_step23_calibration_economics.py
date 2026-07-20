from __future__ import annotations

import unittest

from backend.app.algorithms.meta_strategy.training import (
    MetaTrainingConfig,
    evaluate_calibration_report,
    evaluate_economic_promotion_report,
    evaluate_model_economics,
)
from backend.tests.test_meta_probability_calibration import oof_rows
from backend.tests.test_meta_strategy_economic_promotion import economic_fixture
from backend.tests.test_meta_strategy_nested_training import examples


class MetaStrategyStep23CalibrationEconomicsTest(unittest.TestCase):
    def test_calibration_report_evaluates_required_metrics_by_side_and_regime(self) -> None:
        report = evaluate_calibration_report(
            oof_rows(),
            minimum_rows=30,
            minimum_isotonic_rows=60,
            maximum_brier=0.30,
            maximum_log_loss=1.20,
            maximum_ece=0.20,
        )

        self.assertEqual(report["source"], "inner_out_of_fold")
        self.assertIn(report["method"], {"none", "sigmoid_platt", "isotonic"})
        self.assertIsInstance(report["brierScore"], float)
        self.assertIsInstance(report["logLoss"], float)
        self.assertIsInstance(report["expectedCalibrationError"], float)
        self.assertEqual(len(report["reliabilityCurve"]), 10)
        self.assertIn("BUY", report["calibrationByCandidateSide"])
        self.assertIn("SELL", report["calibrationByCandidateSide"])
        self.assertIn("range", report["calibrationByRegime"])
        self.assertIn("trend", report["calibrationByRegime"])
        self.assertTrue(report["probabilitySizingApproved"])
        self.assertIn("meta_strategy.calibration.out_of_fold_required", report["reasonCodes"])

    def test_in_sample_calibration_rows_are_rejected(self) -> None:
        rows = oof_rows(30)
        rows[0]["source"] = "in_sample"

        with self.assertRaisesRegex(ValueError, "out-of-fold"):
            evaluate_calibration_report(rows, minimum_rows=20)

    def test_model_economics_evaluates_required_trade_metrics(self) -> None:
        rows = examples(90)
        predictions = [row["label"] for row in rows]
        report = evaluate_model_economics(predictions=predictions, rows=rows)

        self.assertIn("netExpectancy", report)
        self.assertIn("netPnl", report)
        self.assertIn("drawdown", report)
        self.assertIn("profitFactor", report)
        self.assertIn("coverage", report)
        self.assertIn("rejectionRate", report)
        self.assertIn("BUY", report["performanceBySide"])
        self.assertIn("SELL", report["performanceBySide"])
        self.assertIn("range", report["performanceByRegime"])
        self.assertIn("meta_strategy.economic.metrics_evaluated", report["reasonCodes"])

    def test_promotion_does_not_rely_only_on_classification_accuracy(self) -> None:
        config = MetaTrainingConfig(
            maximumPromotionDrawdownMultiple=2.0,
            minimumPositiveEconomicOuterFolds=2,
            minimumDirectionalTradesPerSide=1,
        ).normalized()
        promotion = evaluate_economic_promotion_report(
            model_metrics=economic_fixture(net_pnl=20.0, expectancy=2.0, drawdown=1.0),
            baseline_metrics=economic_fixture(net_pnl=5.0, expectancy=0.1, drawdown=2.0),
            outer_summary={"positiveEconomicFolds": 2, "singleFoldProfitShare": 0.5},
            calibration={"probabilitySizingApproved": True},
            config=config,
        )

        self.assertFalse(promotion["classificationAccuracyUsedForPromotion"])
        self.assertIn("meta_strategy.promotion.not_accuracy_only", promotion["reasonCodes"])
        self.assertIn("net_expectancy_after_costs", promotion["promotionDependsOn"])
        self.assertIn("drawdown", promotion["promotionDependsOn"])
        self.assertIn("fold_concentration", promotion["promotionDependsOn"])

    def test_model_with_unacceptable_drawdown_cannot_pass(self) -> None:
        config = MetaTrainingConfig(
            maximumPromotionDrawdownMultiple=1.0,
            minimumPositiveEconomicOuterFolds=2,
            minimumDirectionalTradesPerSide=1,
        ).normalized()
        promotion = evaluate_economic_promotion_report(
            model_metrics=economic_fixture(net_pnl=20.0, expectancy=2.0, drawdown=12.0, buy_expectancy=1.0, sell_expectancy=1.0),
            baseline_metrics=economic_fixture(net_pnl=8.0, expectancy=0.5, drawdown=4.0),
            outer_summary={"positiveEconomicFolds": 2, "singleFoldProfitShare": 0.5},
            calibration={"probabilitySizingApproved": True},
            config=config,
        )

        self.assertFalse(promotion["promoted"])
        self.assertIn("economic.drawdown_unacceptable", promotion["rejectedReasonCodes"])
        self.assertIn("economic.drawdown_unacceptable", promotion["reasonCodes"])

    def test_fold_concentration_blocks_promotion(self) -> None:
        config = MetaTrainingConfig(
            maximumSingleFoldProfitShare=0.50,
            minimumPositiveEconomicOuterFolds=2,
            minimumDirectionalTradesPerSide=1,
        ).normalized()
        promotion = evaluate_economic_promotion_report(
            model_metrics=economic_fixture(net_pnl=20.0, expectancy=2.0, drawdown=1.0),
            baseline_metrics=economic_fixture(net_pnl=8.0, expectancy=0.5, drawdown=4.0),
            outer_summary={"positiveEconomicFolds": 2, "singleFoldProfitShare": 0.90},
            calibration={"probabilitySizingApproved": True},
            config=config,
        )

        self.assertFalse(promotion["promoted"])
        self.assertIn("economic.fold_profit_concentration", promotion["rejectedReasonCodes"])


if __name__ == "__main__":
    unittest.main()
