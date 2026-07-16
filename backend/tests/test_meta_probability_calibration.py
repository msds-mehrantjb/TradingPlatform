from __future__ import annotations

import unittest

from backend.app.meta_strategy_training import (
    apply_probability_calibration_model,
    tune_probability_calibration_from_probability_rows,
)


def oof_rows(count: int = 90):
    labels = ["BUY", "SELL", "HOLD"]
    rows = []
    for index in range(count):
        label = labels[index % 3]
        if label == "BUY":
            probabilities = {"BUY": 0.72, "SELL": 0.13, "HOLD": 0.15}
        elif label == "SELL":
            probabilities = {"BUY": 0.12, "SELL": 0.74, "HOLD": 0.14}
        else:
            probabilities = {"BUY": 0.18, "SELL": 0.16, "HOLD": 0.66}
        rows.append(
            {
                "probabilities": probabilities,
                "label": label,
                "candidateSide": label,
                "marketRegime": "trend" if index % 2 else "range",
                "rowId": f"oof-{index}",
                "source": "inner_out_of_fold",
            }
        )
    return rows


class MetaProbabilityCalibrationTest(unittest.TestCase):
    def test_calibration_uses_oof_rows_and_records_method_rows_and_diagrams(self) -> None:
        calibration = tune_probability_calibration_from_probability_rows(
            oof_rows(),
            minimum_rows=30,
            minimum_isotonic_rows=60,
            maximum_brier=0.30,
            maximum_log_loss=1.20,
            maximum_ece=0.20,
        )

        methods = {row["method"] for row in calibration["methodsEvaluated"]}
        self.assertIn("sigmoid_platt", methods)
        self.assertIn("isotonic", methods)
        self.assertIn(calibration["method"], methods)
        self.assertEqual(calibration["trainingRows"], 90)
        self.assertEqual(calibration["source"], "inner_out_of_fold")
        self.assertTrue(calibration["probabilitySizingApproved"])
        self.assertEqual(len(calibration["metrics"]["reliabilityCurve"]), 10)
        self.assertIn("BUY", calibration["metrics"]["byCandidateSide"])
        self.assertIn("range", calibration["metrics"]["byMarketRegime"])

    def test_calibration_rejects_in_sample_probability_rows(self) -> None:
        rows = oof_rows(12)
        rows[0]["source"] = "in_sample"

        with self.assertRaisesRegex(ValueError, "out-of-fold"):
            tune_probability_calibration_from_probability_rows(rows)

    def test_insufficient_or_poor_calibration_cannot_approve_probability_sizing(self) -> None:
        calibration = tune_probability_calibration_from_probability_rows(
            oof_rows(12),
            minimum_rows=60,
            minimum_isotonic_rows=80,
            maximum_brier=0.01,
            maximum_log_loss=0.01,
            maximum_ece=0.01,
        )

        self.assertFalse(calibration["probabilitySizingApproved"])
        self.assertIn("calibration.insufficient_oof_rows", calibration["approvalReasonCodes"])

    def test_calibrated_distribution_remains_probability_distribution(self) -> None:
        calibration = tune_probability_calibration_from_probability_rows(oof_rows(), minimum_rows=30, minimum_isotonic_rows=60)
        probabilities = apply_probability_calibration_model({"BUY": 0.7, "SELL": 0.2, "HOLD": 0.1}, calibration)

        self.assertAlmostEqual(sum(probabilities.values()), 1.0, places=6)
        self.assertTrue(all(0 <= value <= 1 for value in probabilities.values()))


if __name__ == "__main__":
    unittest.main()

