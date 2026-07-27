from __future__ import annotations

import unittest

from backend.app.algorithms.regime.final_acceptance import (
    REGIME_FINAL_ACCEPTANCE_REQUIRED_TESTS,
    RegimeAcceptanceStatus,
    RegimeFinalAcceptanceEvidence,
    build_regime_final_acceptance_report,
    regime_acceptance_is_complete,
)
from backend.app.algorithms.regime.rollout import (
    REQUIRED_ML_PROMOTION_EVIDENCE,
    REGIME_AUTOMATIC_ORDER_SUBMISSION_ENABLED,
    REGIME_PAPER_SUBMISSION_ENABLED,
    RegimeRolloutEvidence,
    RegimeRolloutFlags,
    RegimeRolloutValidation,
    evaluate_regime_rollout_phase,
    evaluate_regime_rollout_stage,
    paper_submission_allowed,
    regime_rollout_feature_flags,
    regime_rollout_status,
    rollback_configuration,
)
from backend.tests.test_regime_phase17_rollout import stage_c_evidence, stage_d_evidence, stage_e_evidence


class RegimeRolloutAcceptanceContractTest(unittest.TestCase):
    def test_default_rollout_blocks_paper_and_live_submission(self) -> None:
        flags = regime_rollout_feature_flags({})
        status = regime_rollout_status(flags=flags, evidence=RegimeRolloutEvidence())

        self.assertFalse(flags.paper_submission_enabled)
        self.assertFalse(flags.automatic_order_submission_enabled)
        self.assertFalse(status["paper_submission_allowed"])
        self.assertFalse(status["automatic_order_submission_allowed"])
        self.assertFalse(status["live_trading_allowed"])
        self.assertIn("regime.rollout.live_trading_never_allowed", status["reason_codes"])

    def test_stage_c_intent_validation_fails_closed_if_broker_order_is_created(self) -> None:
        result = evaluate_regime_rollout_stage(
            "stage_c_paper_intent_validation",
            evidence=stage_c_evidence(broker_orders_created_in_intent_validation=1),
        )

        self.assertFalse(result.enabled)
        self.assertIn("regime.rollout.intent_validation_created_broker_orders", result.reason_codes)

    def test_stage_d_requires_all_limited_spy_controls_and_explicit_paper_flag(self) -> None:
        no_flag = evaluate_regime_rollout_stage("stage_d_limited_spy_paper_submission", evidence=stage_d_evidence())
        missing_control = evaluate_regime_rollout_stage(
            "stage_d_limited_spy_paper_submission",
            evidence=stage_d_evidence(no_pyramiding_validated=False),
            flags=RegimeRolloutFlags(paper_submission_enabled=True),
        )
        enabled = evaluate_regime_rollout_stage(
            "stage_d_limited_spy_paper_submission",
            evidence=stage_d_evidence(),
            flags=RegimeRolloutFlags(paper_submission_enabled=True),
        )

        self.assertFalse(no_flag.enabled)
        self.assertIn("regime.rollout.paper_submission_flag_disabled", no_flag.reason_codes)
        self.assertFalse(missing_control.enabled)
        self.assertIn("regime.rollout.evidence_missing:no_pyramiding_validated", missing_control.reason_codes)
        self.assertTrue(enabled.enabled)
        self.assertTrue(paper_submission_allowed(flags=RegimeRolloutFlags(paper_submission_enabled=True), evidence=stage_d_evidence()))

    def test_legacy_phase_names_are_mapped_to_new_stages_without_enabling_by_default(self) -> None:
        result = evaluate_regime_rollout_phase("limited_paper_orders", evidence=stage_d_evidence())

        self.assertEqual(result.stage, "stage_d_limited_spy_paper_submission")
        self.assertFalse(result.enabled)
        self.assertIn("regime.rollout.paper_submission_flag_disabled", result.reason_codes)

    def test_legacy_validation_is_conservative_and_live_trading_still_blocks(self) -> None:
        result = evaluate_regime_rollout_phase(
            "promotion_review",
            validation=RegimeRolloutValidation(
                historical_characterization_passed=True,
                dedicated_backtest_passed=True,
                untouched_oos_passed=True,
                paper_shadow_decisions_passed=True,
                old_new_decision_comparison_passed=True,
                limited_paper_orders_approved=True,
                global_gate_monitoring_passed=True,
                performance_review_passed=True,
                tests_passed=True,
                live_trading_enabled=True,
            ),
            flags=RegimeRolloutFlags(paper_submission_enabled=True),
        )

        self.assertFalse(result.enabled)
        self.assertIn("regime.rollout.live_trading_never_allowed", result.reason_codes)

    def test_final_acceptance_is_evidence_derived_and_nonpassing_statuses_block_completion(self) -> None:
        empty = build_regime_final_acceptance_report()
        complete_without_ml = build_regime_final_acceptance_report(_acceptance_evidence(stage_e_evidence()))
        complete_with_ml = build_regime_final_acceptance_report(
            _acceptance_evidence(stage_e_evidence(persisted_evidence_ids=frozenset(REQUIRED_ML_PROMOTION_EVIDENCE)))
        )

        self.assertFalse(empty["complete"])
        self.assertIn(RegimeAcceptanceStatus.INSUFFICIENT_EVIDENCE.value, empty["counts"])
        self.assertFalse(complete_without_ml["complete"])
        self.assertEqual(
            _item(complete_without_ml, "Future ML promotion is blocked without separate stability, improvement, calibration, drift and rollback evidence.")["status"],
            RegimeAcceptanceStatus.INSUFFICIENT_EVIDENCE.value,
        )
        self.assertTrue(complete_with_ml["complete"])
        self.assertTrue(regime_acceptance_is_complete(_acceptance_evidence(stage_e_evidence(persisted_evidence_ids=frozenset(REQUIRED_ML_PROMOTION_EVIDENCE)))))

    def test_final_acceptance_fails_on_live_or_automatic_submission_evidence(self) -> None:
        live = build_regime_final_acceptance_report(_acceptance_evidence(stage_e_evidence(live_trading_enabled=True)))
        automatic = build_regime_final_acceptance_report(
            _acceptance_evidence(stage_e_evidence(automatic_order_submission_enabled=True))
        )

        self.assertEqual(_item(live, "Live trading remains impossible.")["status"], RegimeAcceptanceStatus.FAIL.value)
        self.assertEqual(_item(automatic, "Automatic order submission remains disabled by default.")["status"], RegimeAcceptanceStatus.FAIL.value)

    def test_environment_and_rollback_expose_paper_submission_flags(self) -> None:
        flags = regime_rollout_feature_flags(
            {
                REGIME_PAPER_SUBMISSION_ENABLED: "true",
                REGIME_AUTOMATIC_ORDER_SUBMISSION_ENABLED: "true",
            }
        )
        rollback = rollback_configuration()

        self.assertTrue(flags.paper_submission_enabled)
        self.assertTrue(flags.automatic_order_submission_enabled)
        self.assertFalse(rollback[REGIME_PAPER_SUBMISSION_ENABLED])
        self.assertFalse(rollback[REGIME_AUTOMATIC_ORDER_SUBMISSION_ENABLED])


def _acceptance_evidence(rollout_evidence: RegimeRolloutEvidence) -> RegimeFinalAcceptanceEvidence:
    return RegimeFinalAcceptanceEvidence(
        passing_test_files=frozenset(REGIME_FINAL_ACCEPTANCE_REQUIRED_TESTS),
        frontend_typecheck_passed=True,
        frontend_tests_passed=True,
        frontend_build_passed=True,
        backend_authority_scan_passed=True,
        no_live_trading_scan_passed=True,
        rollout_evidence=rollout_evidence,
    )


def _item(report: dict[str, object], statement: str) -> dict[str, object]:
    return next(item for item in report["items"] if item["statement"] == statement)  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
