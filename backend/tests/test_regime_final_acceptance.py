from __future__ import annotations

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.algorithms.regime.final_acceptance import (
    REGIME_FINAL_ACCEPTANCE_ITEMS,
    REGIME_FINAL_ACCEPTANCE_REQUIRED_TESTS,
    REGIME_FINAL_ACCEPTANCE_VERSION,
    RegimeAcceptanceStatus,
    RegimeFinalAcceptanceEvidence,
    build_regime_final_acceptance_report,
    regime_acceptance_is_complete,
)
from backend.app.algorithms.regime.rollout import REQUIRED_ML_PROMOTION_EVIDENCE, RegimeRolloutEvidence
from backend.app.main import app
from backend.tests.test_regime_phase17_rollout import stage_e_evidence


ROOT = Path(__file__).resolve().parents[2]

EXPECTED_REGIME_FINAL_ACCEPTANCE_STATEMENTS = {
    "Stage A deterministic offline validation passed.",
    "Stage B background shadow runtime evidence passed.",
    "Stage C paper intent validation evidence passed without broker submission.",
    "Stage D limited SPY paper submission gates passed.",
    "Stage E expanded paper validation gates passed.",
    "Focused, full backend, frontend and acceptance tests passed.",
    "Paper submission remains disabled until all preceding gates pass.",
    "Automatic order submission remains disabled by default.",
    "Live trading remains impossible.",
    "ML remains shadow-only through every staged rollout step.",
    "Future ML promotion is blocked without separate stability, improvement, calibration, drift and rollback evidence.",
    "Backend Python remains the only authoritative decision and backtest path.",
    "No live-trading endpoint or mode is enabled.",
}


class RegimeFinalAcceptanceTest(unittest.TestCase):
    def test_final_acceptance_report_covers_stage_17_definition_of_done(self) -> None:
        report = build_regime_final_acceptance_report()

        self.assertEqual(report["algorithmId"], "regime")
        self.assertEqual(report["version"], REGIME_FINAL_ACCEPTANCE_VERSION)
        self.assertEqual(len(report["items"]), len(EXPECTED_REGIME_FINAL_ACCEPTANCE_STATEMENTS))
        self.assertEqual(len(REGIME_FINAL_ACCEPTANCE_ITEMS), len(EXPECTED_REGIME_FINAL_ACCEPTANCE_STATEMENTS))
        self.assertEqual({item.statement for item in REGIME_FINAL_ACCEPTANCE_ITEMS}, EXPECTED_REGIME_FINAL_ACCEPTANCE_STATEMENTS)
        self.assertFalse(report["complete"])
        self.assertFalse(regime_acceptance_is_complete())
        self.assertGreater(report["counts"]["INSUFFICIENT_EVIDENCE"], 0)
        self.assertGreater(report["counts"]["NOT_RUN"], 0)
        self.assertNotIn("pending", report["counts"])

    def test_default_report_does_not_classify_not_run_or_insufficient_evidence_as_passing(self) -> None:
        report = build_regime_final_acceptance_report()
        by_statement = {item["statement"]: item for item in report["items"]}

        self.assertEqual(by_statement["Stage A deterministic offline validation passed."]["status"], RegimeAcceptanceStatus.INSUFFICIENT_EVIDENCE.value)
        self.assertEqual(by_statement["Focused, full backend, frontend and acceptance tests passed."]["status"], RegimeAcceptanceStatus.NOT_RUN.value)
        self.assertEqual(by_statement["Paper submission remains disabled until all preceding gates pass."]["status"], RegimeAcceptanceStatus.PASS.value)
        self.assertFalse(report["complete"])
        self.assertIn("Stage A deterministic offline validation passed.", report["blockingStatements"])

    def test_complete_evidence_passes_every_non_ml_promotion_item_but_ml_promotion_requires_future_task(self) -> None:
        report = build_regime_final_acceptance_report(complete_acceptance_evidence())
        by_statement = {item["statement"]: item for item in report["items"]}

        self.assertFalse(report["complete"])
        for statement, item in by_statement.items():
            if statement == "Future ML promotion is blocked without separate stability, improvement, calibration, drift and rollback evidence.":
                self.assertEqual(item["status"], RegimeAcceptanceStatus.INSUFFICIENT_EVIDENCE.value)
            else:
                self.assertEqual(item["status"], RegimeAcceptanceStatus.PASS.value, statement)

        ml_report = build_regime_final_acceptance_report(
            complete_acceptance_evidence(
                rollout_evidence=stage_e_evidence(persisted_evidence_ids=frozenset(REQUIRED_ML_PROMOTION_EVIDENCE))
            )
        )
        self.assertTrue(ml_report["complete"])
        self.assertTrue(regime_acceptance_is_complete(complete_acceptance_evidence(rollout_evidence=stage_e_evidence(persisted_evidence_ids=frozenset(REQUIRED_ML_PROMOTION_EVIDENCE)))))

    def test_live_trading_or_early_broker_orders_fail_final_acceptance(self) -> None:
        live_report = build_regime_final_acceptance_report(
            complete_acceptance_evidence(rollout_evidence=stage_e_evidence(live_trading_enabled=True))
        )
        broker_report = build_regime_final_acceptance_report(
            complete_acceptance_evidence(rollout_evidence=stage_e_evidence(broker_orders_created_in_shadow=1))
        )

        live_item = next(item for item in live_report["items"] if item["statement"] == "Live trading remains impossible.")
        broker_item = next(item for item in broker_report["items"] if item["statement"] == "Paper submission remains disabled until all preceding gates pass.")

        self.assertEqual(live_item["status"], RegimeAcceptanceStatus.FAIL.value)
        self.assertEqual(broker_item["status"], RegimeAcceptanceStatus.FAIL.value)
        self.assertFalse(live_report["complete"])
        self.assertFalse(broker_report["complete"])

    def test_every_acceptance_evidence_path_exists(self) -> None:
        for item in REGIME_FINAL_ACCEPTANCE_ITEMS:
            self.assertTrue(item.evidence, item.statement)
            for evidence in item.evidence:
                if evidence.startswith(("backend/", "frontend/", "scripts/")):
                    self.assertTrue((ROOT / evidence).exists(), f"{item.statement}: {evidence}")

    def test_rollout_status_exposes_evidence_derived_final_acceptance_without_enabling_live_trading(self) -> None:
        response = TestClient(app).get("/api/regime/rollout/status")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["paper_submission_allowed"])
        self.assertFalse(body["automatic_order_submission_allowed"])
        self.assertFalse(body["live_trading_allowed"])
        self.assertFalse(body["finalAcceptance"]["complete"])
        self.assertTrue(body["finalAcceptance"]["evidenceDerived"])
        self.assertIn("INSUFFICIENT_EVIDENCE", body["finalAcceptance"]["counts"])
        self.assertIn("NOT_RUN", body["finalAcceptance"]["counts"])

    def test_quality_gates_include_backend_frontend_and_regime_acceptance_checks(self) -> None:
        ci_source = (ROOT / "scripts" / "ci_quality_gates.py").read_text(encoding="utf-8")
        package_json = (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")

        self.assertIn("test_regime_final_acceptance.py", ci_source)
        self.assertIn("test_regime_phase17_rollout.py", ci_source)
        self.assertIn("frontend-tests", ci_source)
        self.assertIn("frontend-build", ci_source)
        self.assertIn("pytest", ci_source)
        self.assertIn("\"test\"", package_json)
        self.assertIn("tests/V2DecisionPanel.test.ts", package_json)

    def test_backend_acceptance_evidence_does_not_claim_frontend_regime_authority(self) -> None:
        backend_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "backend" / "app" / "algorithms" / "regime").glob("*.py")
        )

        self.assertNotIn("frontend/src/algorithms/regime", backend_text)
        self.assertNotIn("client_core_available", backend_text)
        self.assertNotIn("TypeScript core", backend_text)
        self.assertIn("backend.app.algorithms.regime.execution_pipeline", backend_text)


def complete_acceptance_evidence(**overrides) -> RegimeFinalAcceptanceEvidence:
    payload = {
        "passing_test_files": frozenset(REGIME_FINAL_ACCEPTANCE_REQUIRED_TESTS),
        "frontend_typecheck_passed": True,
        "frontend_tests_passed": True,
        "frontend_build_passed": True,
        "backend_authority_scan_passed": True,
        "no_live_trading_scan_passed": True,
        "rollout_evidence": stage_e_evidence(),
    }
    payload.update(overrides)
    return RegimeFinalAcceptanceEvidence(**payload)


if __name__ == "__main__":
    unittest.main()
