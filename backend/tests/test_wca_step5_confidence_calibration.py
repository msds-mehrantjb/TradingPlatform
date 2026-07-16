from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.wca.confidence import ConfidenceCalibrationConfig, build_calibration_table, calibrate_evaluation
from backend.app.algorithms.wca.contracts import WcaConfidenceCalibrationOutcome, WcaEvaluationStatus, WcaSide, WcaStrategyEvaluation
from backend.app.algorithms.wca.strategies.moving_average_trend import MovingAverageTrendStrategy
from backend.app.algorithms.wca.strategies.primary_voters import WCA_PRIMARY_VOTERS
from test_wca_step3_strategy_catalog import STRATEGY_CASES


UTC = timezone.utc


class WcaStep5ConfidenceCalibrationTest(unittest.TestCase):
    def test_active_strategy_output_contains_common_confidence_contract(self) -> None:
        voters = {voter.definition.slug: voter for voter in WCA_PRIMARY_VOTERS}
        for slug, cases in STRATEGY_CASES.items():
            snapshot = cases[0][1]
            with self.subTest(strategy=slug):
                result = voters[slug].evaluate(snapshot)

                self.assertEqual(result.status, WcaEvaluationStatus.ACTIVE.value)
                self.assertEqual(result.direction, result.signal)
                self.assertEqual(result.raw_confidence, result.confidence)
                self.assertEqual(result.calibrated_confidence, result.confidence)
                self.assertGreaterEqual(result.evidence_strength, 0)
                self.assertLessEqual(result.evidence_strength, 1)
                self.assertEqual(result.data_quality_status, WcaEvaluationStatus.ACTIVE.value)
                self.assertEqual(result.calibration_version, "wca_confidence_calibration_disabled_v1")

    def test_calibration_table_uses_only_outcomes_available_before_evaluation_time(self) -> None:
        as_of = datetime(2026, 1, 10, 14, 30, tzinfo=UTC)
        outcomes = (
            outcome(raw=0.72, success=True, available_at=as_of - timedelta(minutes=1)),
            outcome(raw=0.76, success=False, available_at=as_of),
            outcome(raw=0.74, success=False, available_at=as_of + timedelta(minutes=1)),
        )

        table = build_calibration_table(
            strategy_id="C1",
            strategy_version="wca_moving_average_trend_v1",
            outcomes=outcomes,
            as_of=as_of,
            config=ConfidenceCalibrationConfig(minimum_samples=1),
        )

        populated = [row for row in table.bins if row.sample_count]
        self.assertEqual(len(populated), 1)
        self.assertEqual(populated[0].sample_count, 1)
        self.assertEqual(populated[0].success_count, 1)

    def test_insufficient_history_caps_high_calibrated_confidence(self) -> None:
        as_of = datetime(2026, 1, 10, 14, 30, tzinfo=UTC)
        table = build_calibration_table(
            strategy_id="C1",
            strategy_version="wca_moving_average_trend_v1",
            outcomes=(outcome(raw=0.92, success=True, available_at=as_of - timedelta(days=1)),),
            as_of=as_of,
            config=ConfidenceCalibrationConfig(minimum_samples=30, max_unseeded_confidence=0.60),
        )
        evaluation = evaluation_with_raw_confidence(0.92)

        calibrated = calibrate_evaluation(
            evaluation,
            table=table,
            config=ConfidenceCalibrationConfig(minimum_samples=30, max_unseeded_confidence=0.60),
        )

        self.assertEqual(calibrated.raw_confidence, 0.92)
        self.assertEqual(calibrated.calibrated_confidence, 0.60)
        self.assertEqual(calibrated.confidence, 0.60)
        self.assertIn("wca.confidence_calibration.insufficient_samples", calibrated.reason_codes)

    def test_beta_binomial_calibration_uses_strategy_prior(self) -> None:
        as_of = datetime(2026, 1, 10, 14, 30, tzinfo=UTC)
        historical = tuple(
            outcome(raw=0.72, success=index < 24, available_at=as_of - timedelta(days=1, minutes=index))
            for index in range(30)
        )
        config = ConfidenceCalibrationConfig(minimum_samples=30, prior_success_rate=0.50, prior_strength=20)
        table = build_calibration_table(
            strategy_id="C1",
            strategy_version="wca_moving_average_trend_v1",
            outcomes=historical,
            as_of=as_of,
            config=config,
        )

        calibrated = calibrate_evaluation(evaluation_with_raw_confidence(0.72), table=table, config=config)

        self.assertEqual(calibrated.raw_confidence, 0.72)
        self.assertAlmostEqual(calibrated.calibrated_confidence, 0.68, places=4)
        self.assertEqual(calibrated.confidence, calibrated.calibrated_confidence)
        self.assertIn("wca.confidence_calibration.beta_binomial", calibrated.reason_codes)

    def test_calibration_can_be_disabled_for_parity(self) -> None:
        as_of = datetime(2026, 1, 10, 14, 30, tzinfo=UTC)
        config = ConfidenceCalibrationConfig(enabled=False, minimum_samples=1)
        table = build_calibration_table(
            strategy_id="C1",
            strategy_version="wca_moving_average_trend_v1",
            outcomes=(outcome(raw=0.90, success=False, available_at=as_of - timedelta(days=1)),),
            as_of=as_of,
            config=config,
        )

        calibrated = calibrate_evaluation(evaluation_with_raw_confidence(0.90), table=table, config=config)

        self.assertEqual(calibrated.raw_confidence, 0.90)
        self.assertEqual(calibrated.calibrated_confidence, 0.90)
        self.assertEqual(calibrated.confidence, 0.90)
        self.assertEqual(calibrated.calibration_version, "wca_confidence_calibration_disabled_v1")


def outcome(*, raw: float, success: bool, available_at: datetime) -> WcaConfidenceCalibrationOutcome:
    return WcaConfidenceCalibrationOutcome(
        strategy_id="C1",
        strategy_version="wca_moving_average_trend_v1",
        raw_confidence=raw,
        realized_success=success,
        decision_timestamp=available_at - timedelta(minutes=5),
        outcome_available_at=available_at,
    )


def evaluation_with_raw_confidence(raw_confidence: float) -> WcaStrategyEvaluation:
    strategy = MovingAverageTrendStrategy()
    return WcaStrategyEvaluation(
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.version,
        name=strategy.name,
        status=WcaEvaluationStatus.ACTIVE,
        signal=WcaSide.BUY,
        confidence=raw_confidence,
        raw_confidence=raw_confidence,
        calibrated_confidence=raw_confidence,
        direction=WcaSide.BUY,
        applicability=WcaEvaluationStatus.ACTIVE,
        evidence_strength=raw_confidence,
        data_quality_status=WcaEvaluationStatus.ACTIVE,
        base_weight=strategy.base_weight,
        effective_weight=strategy.base_weight,
        contribution=round(strategy.base_weight * raw_confidence, 4),
        reason_codes=("wca.strategy.moving_average_trend",),
    )


if __name__ == "__main__":
    unittest.main()
