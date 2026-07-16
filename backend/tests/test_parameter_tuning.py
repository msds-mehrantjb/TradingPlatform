from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.app.backtesting import (
    ParameterCandidateEvaluation,
    ParameterConfiguration,
    ParameterGroup,
    ParameterTuningConfig,
    ParameterTuningFoldInput,
    build_parameter_tuning_report,
    parameter_configuration_hash,
    select_parameters_for_fold,
)


START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
TRAIN_END = START + timedelta(days=5)
VALIDATION_START = TRAIN_END + timedelta(hours=1)
VALIDATION_END = VALIDATION_START + timedelta(days=2)


class ParameterTuningTest(unittest.TestCase):
    def test_selection_uses_training_fold_only_and_prefers_stable_region(self) -> None:
        fold = ParameterTuningFoldInput(
            foldId="outer_1",
            trainingWindowStartUtc=START,
            trainingWindowEndUtc=TRAIN_END,
            validationWindowStartUtc=VALIDATION_START,
            validationWindowEndUtc=VALIDATION_END,
            candidates=[
                candidate("sharp", 0.72, min_score=0.72),
                candidate("broad_a", 0.70, min_score=0.58),
                candidate("broad_b", 0.695, min_score=0.60),
                candidate("weak", 0.50, min_score=0.40),
            ],
        )

        result = select_parameters_for_fold(
            fold,
            tuning_config=ParameterTuningConfig(scoreTolerance=0.03, sharpOptimumPenalty=0.06, stableRegionBonus=0.005),
        )

        self.assertEqual(result.selectedCandidateId, "broad_a")
        self.assertEqual(result.selectionSource, "training_fold_only")
        self.assertLess(result.trainingWindowEndUtc, result.validationWindowStartUtc)
        self.assertIn("ensembleAggregation.minimumFinalScore", [item.parameterPath for item in result.sensitivityReports])

    def test_outer_validation_window_cannot_affect_parameter_selection(self) -> None:
        with self.assertRaisesRegex(ValueError, "outer validation data cannot affect parameter selection"):
            ParameterTuningFoldInput(
                foldId="outer_1",
                trainingWindowStartUtc=START,
                trainingWindowEndUtc=TRAIN_END,
                validationWindowStartUtc=VALIDATION_START,
                validationWindowEndUtc=VALIDATION_END,
                candidates=[
                    candidate(
                        "leaky",
                        0.9,
                        min_score=0.4,
                        evaluation_end=VALIDATION_START + timedelta(minutes=5),
                    )
                ],
            )

    def test_final_test_data_is_loaded_only_after_choices_are_frozen(self) -> None:
        with self.assertRaisesRegex(ValueError, "final-test data may be loaded only after all parameter choices are frozen"):
            build_parameter_tuning_report(
                [fold_input()],
                generated_at=START,
                choices_frozen_at=VALIDATION_END,
                final_test_loaded_at=VALIDATION_END - timedelta(seconds=1),
            )

        report = build_parameter_tuning_report(
            [fold_input()],
            generated_at=START,
            choices_frozen_at=VALIDATION_END,
            final_test_loaded_at=VALIDATION_END + timedelta(seconds=1),
        )
        self.assertEqual(report.finalTestPolicy, "Final-test data is loaded only after fold-level selections and the frozen configuration hash are fixed.")

    def test_sensitivity_report_identifies_unstable_parameters(self) -> None:
        result = select_parameters_for_fold(
            fold_input(),
            tuning_config=ParameterTuningConfig(scoreTolerance=0.01, unstableScoreRange=0.05, minimumStableValues=2),
        )

        self.assertIn("ensembleAggregation.minimumFinalScore", result.unstableParameters)
        sensitivity = {item.parameterPath: item for item in result.sensitivityReports}
        self.assertTrue(sensitivity["ensembleAggregation.minimumFinalScore"].unstable)
        self.assertIn("sensitivity.unstable_parameter", sensitivity["ensembleAggregation.minimumFinalScore"].reasonCodes)

    def test_configuration_hash_uniquely_identifies_effective_experiment_settings(self) -> None:
        base = configuration(min_score=0.58)
        changed = configuration(min_score=0.61)

        self.assertNotEqual(parameter_configuration_hash(base), parameter_configuration_hash(changed))

    def test_missing_configuration_group_is_rejected(self) -> None:
        incomplete = candidate("incomplete", 0.5, min_score=0.5, groups=[ParameterGroup.STRATEGY_DETECTION])
        fold = ParameterTuningFoldInput(
            foldId="outer_1",
            trainingWindowStartUtc=START,
            trainingWindowEndUtc=TRAIN_END,
            validationWindowStartUtc=VALIDATION_START,
            validationWindowEndUtc=VALIDATION_END,
            candidates=[incomplete],
        )

        with self.assertRaisesRegex(ValueError, "missing configuration groups"):
            select_parameters_for_fold(fold)


def fold_input() -> ParameterTuningFoldInput:
    return ParameterTuningFoldInput(
        foldId="outer_1",
        trainingWindowStartUtc=START,
        trainingWindowEndUtc=TRAIN_END,
        validationWindowStartUtc=VALIDATION_START,
        validationWindowEndUtc=VALIDATION_END,
        candidates=[
            candidate("selected", 0.75, min_score=0.60),
            candidate("near", 0.70, min_score=0.62),
            candidate("poor", 0.55, min_score=0.35),
        ],
    )


def candidate(
    candidate_id: str,
    score: float,
    *,
    min_score: float,
    evaluation_end: datetime = TRAIN_END,
    groups: list[ParameterGroup] | None = None,
) -> ParameterCandidateEvaluation:
    return ParameterCandidateEvaluation(
        candidateId=candidate_id,
        configuration=configuration(min_score=min_score),
        trainingScore=score,
        evaluationSource="training_fold",
        evaluationWindowStartUtc=START,
        evaluationWindowEndUtc=evaluation_end,
        evaluatedParameterGroups=groups
        or [
            ParameterGroup.STRATEGY_DETECTION,
            ParameterGroup.ENSEMBLE_AGGREGATION,
            ParameterGroup.ML_FILTERING,
            ParameterGroup.DYNAMIC_POLICY,
            ParameterGroup.GLOBAL_RISK,
            ParameterGroup.EXECUTION_ASSUMPTIONS,
        ],
        sampleCount=100,
    )


def configuration(*, min_score: float) -> ParameterConfiguration:
    return ParameterConfiguration(
        strategyDetection={"multiTimeframeTrend": {"minSlopeMagnitude": 0.01}},
        ensembleAggregation={"minimumFinalScore": min_score, "minimumSupportingFamilies": 2},
        mlFiltering={"minimumProbability": 0.56},
        dynamicPolicy={"riskMultiplier": 0.75},
        globalRisk={"maxDailyLossR": 2.0},
        executionAssumptions={"stopMultiplier": 0.75, "targetMultiplier": 1.2},
    )


if __name__ == "__main__":
    unittest.main()
