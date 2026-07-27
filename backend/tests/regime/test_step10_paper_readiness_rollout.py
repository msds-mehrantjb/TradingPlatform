from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from backend.app.algorithms.regime.rollout import (
    REGIME_PAPER_READINESS_STAGE_TEST_GROUPS,
    REGIME_PAPER_READINESS_STAGES,
    REGIME_PAPER_READINESS_TEST_GROUPS,
    RegimePaperReadinessEvidence,
    RegimeReadinessStatus,
    RegimeRolloutEvidence,
    RegimeRolloutFlags,
    build_regime_paper_readiness_report,
)
from backend.app.main import app
from backend.tests.test_regime_phase17_rollout import stage_d_evidence


class RegimeStep10PaperReadinessRolloutTest(unittest.TestCase):
    def test_default_report_is_evidence_derived_and_blocks_paper_submission(self) -> None:
        report = build_regime_paper_readiness_report()

        self.assertEqual(report["algorithmId"], "regime")
        self.assertTrue(report["evidenceDerived"])
        self.assertEqual(tuple(report["allowedStatuses"]), tuple(status.value for status in RegimeReadinessStatus))
        self.assertFalse(report["complete"])
        self.assertFalse(report["paperSubmissionAllowed"])
        self.assertFalse(report["automaticPaperTradingEnabled"])
        self.assertFalse(report["liveTradingAllowed"])
        self.assertGreater(report["counts"][RegimeReadinessStatus.INSUFFICIENT_EVIDENCE.value], 0)
        self.assertIn(RegimeReadinessStatus.NOT_RUN.value, report["nonPassingStatuses"])
        self.assertIn(RegimeReadinessStatus.INSUFFICIENT_EVIDENCE.value, report["nonPassingStatuses"])

    def test_every_required_step_10_test_group_is_reported(self) -> None:
        report = build_regime_paper_readiness_report()
        group_ids = {item["id"] for item in report["testGroups"]}

        self.assertEqual(group_ids, set(REGIME_PAPER_READINESS_TEST_GROUPS))
        for stage in REGIME_PAPER_READINESS_STAGES:
            self.assertTrue(REGIME_PAPER_READINESS_STAGE_TEST_GROUPS[stage], stage)

    def test_passed_test_group_without_persisted_evidence_is_insufficient(self) -> None:
        report = build_regime_paper_readiness_report(
            RegimePaperReadinessEvidence(passed_test_groups=frozenset({"authority_boundary"}))
        )
        item = _test_group(report, "authority_boundary")

        self.assertEqual(item["status"], RegimeReadinessStatus.INSUFFICIENT_EVIDENCE.value)
        self.assertIn("regime.readiness.test_group_evidence_missing:authority_boundary", item["reasonCodes"])

    def test_stages_one_to_three_pass_with_complete_test_and_observation_evidence(self) -> None:
        report = build_regime_paper_readiness_report(_complete_readiness_evidence())
        status = report["stageStatus"]

        self.assertEqual(status["stage_1_offline_only"], RegimeReadinessStatus.PASS.value)
        self.assertEqual(status["stage_2_background_shadow"], RegimeReadinessStatus.PASS.value)
        self.assertEqual(status["stage_3_paper_intent_only"], RegimeReadinessStatus.PASS.value)
        self.assertEqual(status["stage_4_limited_automated_paper_trading"], RegimeReadinessStatus.INSUFFICIENT_EVIDENCE.value)
        self.assertFalse(report["paperSubmissionAllowed"])
        self.assertIn(
            "regime.readiness.paper_submission_flag_disabled",
            _stage(report, "stage_4_limited_automated_paper_trading")["reasonCodes"],
        )

    def test_stage_four_requires_explicit_paper_flag_after_all_prior_gates_pass(self) -> None:
        report = build_regime_paper_readiness_report(
            _complete_readiness_evidence(flags=RegimeRolloutFlags(paper_submission_enabled=True))
        )

        self.assertTrue(report["complete"])
        self.assertEqual(report["stageStatus"]["stage_4_limited_automated_paper_trading"], RegimeReadinessStatus.PASS.value)
        self.assertTrue(report["paperSubmissionAllowed"])
        self.assertFalse(report["automaticPaperTradingEnabled"])
        self.assertFalse(report["liveTradingAllowed"])

    def test_live_trading_or_early_broker_orders_fail_readiness(self) -> None:
        live = build_regime_paper_readiness_report(_complete_readiness_evidence(rollout_evidence=stage_d_evidence(live_trading_enabled=True)))
        early_broker = build_regime_paper_readiness_report(
            _complete_readiness_evidence(rollout_evidence=stage_d_evidence(broker_orders_created_in_shadow=1))
        )

        self.assertEqual(live["stageStatus"]["stage_1_offline_only"], RegimeReadinessStatus.FAIL.value)
        self.assertEqual(early_broker["stageStatus"]["stage_1_offline_only"], RegimeReadinessStatus.FAIL.value)
        self.assertFalse(live["paperSubmissionAllowed"])
        self.assertFalse(early_broker["paperSubmissionAllowed"])

    def test_failed_test_group_blocks_its_stage(self) -> None:
        report = build_regime_paper_readiness_report(
            _complete_readiness_evidence(
                passed_test_groups=frozenset(set(REGIME_PAPER_READINESS_TEST_GROUPS) - {"transaction_cost_gate"}),
                failed_test_groups=frozenset({"transaction_cost_gate"}),
            )
        )

        self.assertEqual(_test_group(report, "transaction_cost_gate")["status"], RegimeReadinessStatus.FAIL.value)
        self.assertEqual(report["stageStatus"]["stage_3_paper_intent_only"], RegimeReadinessStatus.FAIL.value)

    def test_rollout_status_api_exposes_step_10_paper_readiness(self) -> None:
        response = TestClient(app).get("/api/regime/rollout/status")
        direct = TestClient(app).get("/api/regime/rollout/paper-readiness")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(direct.status_code, 200, direct.text)
        self.assertIn("paperReadiness", response.json())
        self.assertEqual(direct.json()["algorithmId"], "regime")
        self.assertFalse(direct.json()["paperSubmissionAllowed"])


def _complete_readiness_evidence(**overrides) -> RegimePaperReadinessEvidence:
    payload = {
        "rollout_evidence": stage_d_evidence(),
        "passed_test_groups": frozenset(REGIME_PAPER_READINESS_TEST_GROUPS),
        "persisted_evidence_ids": frozenset(REGIME_PAPER_READINESS_TEST_GROUPS),
        "completed_bar_reliability_observed": True,
        "runtime_latency_observed": True,
        "persistent_hysteresis_observed": True,
        "strategy_opportunity_frequency_observed": True,
        "blocker_frequency_observed": True,
        "restart_recovery_observed": True,
        "duplicate_decision_prevention_observed": True,
        "replay_parity_observed": True,
        "sizing_observed": True,
        "stops_targets_observed": True,
        "cost_gate_observed": True,
        "reservation_behaviour_observed": True,
        "outbox_idempotency_observed": True,
        "outbox_expiry_observed": True,
    }
    payload.update(overrides)
    return RegimePaperReadinessEvidence(**payload)


def _test_group(report: dict[str, object], group_id: str) -> dict[str, object]:
    return next(item for item in report["testGroups"] if item["id"] == group_id)  # type: ignore[index]


def _stage(report: dict[str, object], stage_id: str) -> dict[str, object]:
    return next(item for item in report["stages"] if item["id"] == stage_id)  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
