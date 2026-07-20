from __future__ import annotations

import copy
import unittest

from backend.app.algorithms.meta_strategy.training import (
    ChronologicalValidationError,
    MetaTrainingConfig,
    build_chronological_validation_report,
    build_nested_walk_forward_plan,
    build_validated_chronological_walk_forward_plan,
    prohibit_random_split_policy,
    validate_chronological_examples,
    validate_chronological_training_plan,
)
from backend.tests.test_meta_strategy_nested_training import examples


def config() -> MetaTrainingConfig:
    return MetaTrainingConfig(
        minimumTotalCandidates=120,
        minimumBuyCandidates=20,
        minimumSellCandidates=20,
        minimumPositiveOutcomes=40,
        minimumNegativeOutcomes=20,
        minimumCandidatesPerOuterFold=12,
        minimumTradingSessions=4,
        minimumRegimesRepresented=2,
        outerFolds=3,
        innerFolds=2,
        maximumHoldingHorizonMinutes=10,
        embargoMinutes=10,
    ).normalized()


class MetaStrategyStep22ChronologicalValidationTest(unittest.TestCase):
    def test_validated_chronological_report_requires_nested_walk_forward_and_persists_windows(self) -> None:
        rows = examples()
        plan = build_nested_walk_forward_plan(rows, config())
        report = build_chronological_validation_report(rows, plan, config())
        validated = build_validated_chronological_walk_forward_plan(rows, config())

        self.assertTrue(report.valid)
        self.assertEqual(report.method, "nested_chronological_purged_walk_forward")
        self.assertTrue(report.nestedWalkForward)
        self.assertTrue(report.randomSplittingProhibited)
        self.assertEqual(len(report.foldWindows), config().outerFolds)
        self.assertTrue(report.finalHoldoutWindow["untouched"])
        self.assertIn("meta_strategy.training.minimum_coverage_valid", report.reasonCodes)
        self.assertIn("chronologicalValidation", validated)
        for fold_window in report.foldWindows:
            self.assertIn("trainingWindowStart", fold_window)
            self.assertIn("trainingWindowEnd", fold_window)
            self.assertIn("validationWindowStart", fold_window)
            self.assertIn("validationWindowEnd", fold_window)
            self.assertIn("labelWindowCutoff", fold_window)
            self.assertGreaterEqual(fold_window["embargoMinutes"], config().maximumHoldingHorizonMinutes)

    def test_random_splitting_is_prohibited(self) -> None:
        with self.assertRaisesRegex(ChronologicalValidationError, "random_splitting_prohibited"):
            prohibit_random_split_policy({"method": "random_split", "shuffle": True})

        with self.assertRaisesRegex(ChronologicalValidationError, "random_splitting_prohibited"):
            validate_chronological_training_plan(
                build_nested_walk_forward_plan(examples(), config()),
                config(),
                split_policy="random_shuffle_split",
            )

    def test_minimum_sample_side_label_session_and_regime_coverage_is_enforced(self) -> None:
        with self.assertRaisesRegex(ChronologicalValidationError, "minimum_coverage_failed"):
            validate_chronological_examples(examples(30), config())

        only_buy = [row for row in examples() if row["label"] == "BUY"]
        with self.assertRaisesRegex(ChronologicalValidationError, "sellCandidates"):
            validate_chronological_examples(only_buy, config())

    def test_fold_leakage_is_rejected_for_overlap_and_unpurged_label_windows(self) -> None:
        plan = build_nested_walk_forward_plan(examples(), config())
        overlap = copy.deepcopy(plan)
        overlap["outerFolds"][0]["trainRows"].append(copy.deepcopy(overlap["outerFolds"][0]["validationRows"][0]))

        with self.assertRaisesRegex(ChronologicalValidationError, "fold_train_validation_overlap"):
            validate_chronological_training_plan(overlap, config())

        unpurged = copy.deepcopy(plan)
        unpurged["outerFolds"][0]["trainRows"][0]["labelEnd"] = unpurged["outerFolds"][0]["labelWindowCutoff"]
        with self.assertRaisesRegex(ChronologicalValidationError, "fold_purge_leakage"):
            validate_chronological_training_plan(unpurged, config())

    def test_holdout_isolation_is_enforced(self) -> None:
        plan = build_nested_walk_forward_plan(examples(), config())
        leaked = copy.deepcopy(plan)
        leaked["outerFolds"][0]["trainRows"].append(copy.deepcopy(leaked["finalTestRows"][0]))

        with self.assertRaisesRegex(ChronologicalValidationError, "final_holdout_reused_in_fold"):
            validate_chronological_training_plan(leaked, config())

    def test_unsorted_examples_are_rejected_before_training(self) -> None:
        rows = examples()
        rows[0], rows[1] = rows[1], rows[0]

        with self.assertRaisesRegex(ChronologicalValidationError, "examples_not_chronological"):
            validate_chronological_examples(rows, config())


if __name__ == "__main__":
    unittest.main()
