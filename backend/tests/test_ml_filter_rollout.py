from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from pydantic import ValidationError

from backend.app.backtesting import (
    MLFilterOutcomeSample,
    ReplayDecisionSnapshot,
    build_ml_filter_rollout_report,
    build_ml_filter_shadow_comparison_report,
)
from backend.app.domain.models import Signal
from backend.app.ml.features import MLFeatureSet
from backend.app.ml.inference import SafeMLInferenceConfig, apply_safe_ml_inference


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)


class MLFilterRolloutTest(unittest.TestCase):
    def test_shadow_comparison_reports_acceptance_cost_calibration_and_regime_stability(self) -> None:
        samples = [
            outcome("s1", accepted=True, pnl=80.0, success=True, regime="strong_trend", probability=0.80),
            outcome("s2", accepted=False, pnl=40.0, success=True, regime="strong_trend", probability=0.70),
            outcome("s3", accepted=True, pnl=-20.0, success=False, regime="range", probability=0.30),
            outcome("s4", accepted=False, pnl=-10.0, success=False, regime="range", probability=0.20),
        ]

        report = build_ml_filter_shadow_comparison_report(
            samples=samples,
            minimum_samples=4,
            maximum_false_rejection_cost=100,
            minimum_coverage=0.50,
            maximum_calibration_error=0.35,
            maximum_regime_instability=0.10,
            generatedAt=NOW,
        )

        self.assertEqual(report.deterministicCandidateCount, 4)
        self.assertEqual(report.acceptedCount, 2)
        self.assertEqual(report.rejectedCount, 2)
        self.assertEqual(report.falseRejectionCost, 40.0)
        self.assertEqual(report.coverage, 0.5)
        self.assertEqual(report.regimeStability, 0.0)
        self.assertTrue(report.passed)
        self.assertIn("ml_filter.shadow_comparison_passed", report.reasonCodes)

    def test_shadow_mode_records_ml_without_order_effect(self) -> None:
        report = build_ml_filter_rollout_report(
            snapshot=snapshot(ml_mode="SHADOW", final_signal="BUY", accepted=True, applied=False, order_eligible=True),
            stage="SHADOW",
            generatedAt=NOW,
        )

        self.assertEqual(report.stage, "SHADOW")
        self.assertFalse(report.mlAppliedToOrder)
        self.assertFalse(report.automaticPaperEntryAllowed)
        self.assertTrue(report.staticRiskSizing.unchanged)
        self.assertIn("ml_filter.shadow_record_only", report.reasonCodes)

    def test_filter_active_can_reject_only_and_keeps_static_sizing(self) -> None:
        baseline = snapshot(ml_mode="SHADOW", final_signal="BUY", accepted=True, applied=False, order_eligible=True)
        filtered = snapshot(ml_mode="FILTER", final_signal="HOLD", accepted=False, applied=True, order_eligible=False)

        report = build_ml_filter_rollout_report(
            snapshot=filtered,
            deterministicBaselineSnapshot=baseline,
            stage="FILTER_ACTIVE",
            shadowComparisonPassed=True,
            generatedAt=NOW,
        )

        self.assertEqual(report.deterministicSignal, Signal.BUY.value)
        self.assertEqual(report.finalSignal, Signal.HOLD.value)
        self.assertFalse(report.mlCandidateAccepted)
        self.assertTrue(report.mlAppliedToOrder)
        self.assertTrue(report.staticRiskSizing.unchanged)
        self.assertFalse(report.automaticPaperEntryAllowed)
        self.assertIn("ml_filter.filter_active_accept_reject_only", report.reasonCodes)

    def test_active_entries_require_shadow_comparison_pass(self) -> None:
        report = build_ml_filter_rollout_report(
            snapshot=snapshot(ml_mode="FILTER", final_signal="BUY", accepted=True, applied=True, order_eligible=True),
            deterministicBaselineSnapshot=snapshot(ml_mode="SHADOW", final_signal="BUY", accepted=True, applied=False, order_eligible=True),
            stage="FILTER_ACTIVE",
            shadowComparisonPassed=True,
            generatedAt=NOW,
        )
        payload = report.model_dump(mode="json")
        payload["rolloutConfig"]["shadowComparisonPassed"] = False

        with self.assertRaisesRegex(ValidationError, "requires a passing shadow comparison"):
            type(report).model_validate(payload)

    def test_ml_filter_cannot_flip_direction_or_create_trade_from_hold(self) -> None:
        flipped = build_ml_filter_rollout_report(
            snapshot=snapshot(ml_mode="FILTER", final_signal="HOLD", accepted=False, applied=True, order_eligible=False),
            stage="FILTER_ACTIVE",
            shadowComparisonPassed=True,
            generatedAt=NOW,
        )
        flip_payload = flipped.model_dump(mode="json")
        flip_payload["finalSignal"] = "SELL"

        with self.assertRaisesRegex(ValidationError, "cannot alter candidate direction"):
            type(flipped).model_validate(flip_payload)

        created = build_ml_filter_rollout_report(
            snapshot=snapshot(
                ml_mode="FILTER",
                deterministic_signal="HOLD",
                ensemble_signal="HOLD",
                final_signal="HOLD",
                accepted=False,
                applied=True,
                order_eligible=False,
                has_candidate=False,
            ),
            stage="FILTER_ACTIVE",
            shadowComparisonPassed=True,
            generatedAt=NOW,
        )
        create_payload = created.model_dump(mode="json")
        create_payload["finalSignal"] = "BUY"

        with self.assertRaisesRegex(ValidationError, "cannot create a trade from Hold"):
            type(created).model_validate(create_payload)

    def test_static_risk_change_is_rejected(self) -> None:
        baseline = snapshot(ml_mode="SHADOW", final_signal="BUY", accepted=True, applied=False, order_eligible=True, risk_dollars=100.0)
        filtered = snapshot(ml_mode="FILTER", final_signal="BUY", accepted=True, applied=True, order_eligible=True, risk_dollars=25.0)

        with self.assertRaisesRegex(ValidationError, "static risk sizing unchanged"):
            build_ml_filter_rollout_report(
                snapshot=filtered,
                deterministicBaselineSnapshot=baseline,
                stage="FILTER_ACTIVE",
                shadowComparisonPassed=True,
                generatedAt=NOW,
            )

    def test_model_unavailable_fallback_is_explicit_and_does_not_change_direction_or_sizing(self) -> None:
        prediction = apply_safe_ml_inference(
            deterministic_signal=Signal.BUY,
            feature_set=feature_set(),
            model_artifact=None,
            config=SafeMLInferenceConfig(mode="FILTER", fallbackBehavior="DETERMINISTIC_BASELINE"),
            predicted_at=NOW,
            session_date=SESSION_DATE,
        )
        report = build_ml_filter_rollout_report(
            snapshot=snapshot(
                ml_mode=str(prediction.effectiveMode),
                final_signal=str(prediction.finalSignal),
                accepted=prediction.candidateAccepted,
                applied=prediction.appliedToOrder,
                order_eligible=True,
                ml_payload=prediction.model_dump(mode="json"),
            ),
            deterministicBaselineSnapshot=snapshot(ml_mode="SHADOW", final_signal="BUY", accepted=True, applied=False, order_eligible=True),
            stage="FILTER_ACTIVE",
            shadowComparisonPassed=True,
            fallbackBehavior="DETERMINISTIC_BASELINE",
            generatedAt=NOW,
        )

        self.assertTrue(report.fallback.exercised)
        self.assertEqual(report.finalSignal, Signal.BUY.value)
        self.assertTrue(report.staticRiskSizing.unchanged)
        self.assertIn("ml.model_unavailable", report.fallback.reasonCodes)


def outcome(
    snapshot_id: str,
    *,
    accepted: bool,
    pnl: float,
    success: bool,
    regime: str,
    probability: float,
) -> MLFilterOutcomeSample:
    return MLFilterOutcomeSample(
        snapshotId=snapshot_id,
        decisionTimestampUtc=NOW,
        sessionDate=SESSION_DATE,
        deterministicSignal=Signal.BUY,
        deterministicWouldTrade=True,
        mlWouldAcceptCandidate=accepted,
        realizedSuccess=success,
        realizedNetPnlAfterCosts=pnl,
        deterministicMaxDrawdown=50.0,
        mlFilteredMaxDrawdown=40.0 if accepted else 45.0,
        deterministicExpectancy=20.0,
        mlFilteredExpectancy=25.0 if accepted else 18.0,
        calibratedProbability=probability,
        regime=regime,
    )


def snapshot(
    *,
    ml_mode: str,
    final_signal: str,
    accepted: bool,
    applied: bool,
    order_eligible: bool,
    deterministic_signal: str = "BUY",
    ensemble_signal: str = "BUY",
    has_candidate: bool = True,
    risk_dollars: float = 100.0,
    ml_payload: dict | None = None,
) -> ReplayDecisionSnapshot:
    ml = ml_payload or {
        "mode": ml_mode,
        "effectiveMode": ml_mode,
        "deterministicSignal": deterministic_signal,
        "finalSignal": final_signal,
        "candidateAccepted": accepted,
        "mlWouldAcceptCandidate": accepted,
        "appliedToOrder": applied,
        "recommendedRiskCap": 1.0,
        "modelHealth": {"status": "OK", "score": 1.0},
        "reasonCodes": [f"ml.mode_{ml_mode.lower()}"],
    }
    return ReplayDecisionSnapshot(
        snapshotId=f"ml-filter-{ml_mode.lower()}-{final_signal.lower()}",
        symbol="SPY",
        decisionTimestampUtc=NOW,
        sessionDate=SESSION_DATE,
        maxInputTimestampUtc=NOW,
        featureSnapshot={"engineVersion": "point_in_time_feature_engine_v1", "dataReady": True},
        strategyOutputs=[{"strategyId": "multi_timeframe_trend_alignment", "signal": ensemble_signal, "dataReady": True}],
        contextOutputs=[],
        regimeState={"label": "strong_trend"},
        gateDecision={"eligible": True, "configurationHash": "gate-hash", "reasonCodes": []},
        deterministicCandidate=({"candidateId": "candidate-ml-filter", "signal": ensemble_signal} if has_candidate else None),
        ensembleDecision={"signal": ensemble_signal, "configurationHash": "ensemble-hash", "engineVersion": "family_aware_deterministic_ensemble_v1"},
        mlInference=ml,
        effectivePolicy={
            "mode": ml_mode,
            "configurationHash": "policy-hash",
            "baselineSettings": {"settingsVersion": "replay_baseline_settings_v2"},
            "riskDollars": risk_dollars,
            "maxQuantity": 100,
            "maxNotional": 1000.0,
        },
        orderPlan=(
            {
                "orderPlanId": "order-ml-filter",
                "candidateId": "candidate-ml-filter",
                "symbol": "SPY",
                "side": ensemble_signal if ensemble_signal != "HOLD" else "BUY",
                "orderType": "LIMIT",
                "quantity": 1,
                "entryPrice": 100,
                "stopPrice": 99,
                "targetPrice": 102,
                "limitPrice": 100,
                "timeInForce": "DAY",
                "eligible": True,
                "validationErrors": [],
                "explanation": "Synthetic accepted ML filter order.",
                "generatedAt": NOW,
                "sessionDate": SESSION_DATE,
                "configurationHash": "policy-hash",
            }
            if order_eligible
            else {
                "orderPlanId": "no-order-ml-filter",
                "candidateId": "candidate-ml-filter",
                "symbol": "SPY",
                "side": ensemble_signal if ensemble_signal != "HOLD" else "BUY",
                "orderType": "NO_ORDER",
                "quantity": 0,
                "entryPrice": 100,
                "stopPrice": 99,
                "targetPrice": 102,
                "timeInForce": "DAY",
                "eligible": False,
                "validationErrors": ["order.blocked_by_ml_filter"],
                "explanation": "Synthetic rejected ML filter order.",
                "generatedAt": NOW,
                "sessionDate": SESSION_DATE,
                "configurationHash": "policy-hash",
            }
        ),
        fill=None,
        exit=None,
        reasonCodes=["synthetic.ml_filter"],
    )


def feature_set() -> MLFeatureSet:
    return MLFeatureSet(
        schemaHash="ml-filter-test-schema",
        snapshotId="snapshot-ml-filter",
        symbol="SPY",
        decisionTimestampUtc=NOW.isoformat(),
        featureValues={"target_distance": 1.5, "stop_distance": 1.0, "expected_transaction_cost": 0.02},
        missingIndicators={"target_distance": False, "stop_distance": False, "expected_transaction_cost": False},
        forbiddenFieldsChecked=["finalOutcome", "fills", "brokerSubmissionResult", "metaModelPrediction"],
        explanation="Synthetic ML filter features.",
    )


if __name__ == "__main__":
    unittest.main()
