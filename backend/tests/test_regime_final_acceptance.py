from __future__ import annotations

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.algorithms.regime.final_acceptance import (
    REGIME_FINAL_ACCEPTANCE_ITEMS,
    REGIME_FINAL_ACCEPTANCE_VERSION,
    build_regime_final_acceptance_report,
    regime_acceptance_is_complete,
)
from backend.app.main import app


ROOT = Path(__file__).resolve().parents[2]

EXPECTED_REGIME_FINAL_ACCEPTANCE_STATEMENTS = {
    "Regime logic is isolated from main.ts.",
    "Allowed Sell decisions remain Sell.",
    "Regime no longer uses WCA sizing or order adapters.",
    "Directional, context, and safety roles are separated.",
    "Strategy aliases cannot double vote.",
    "Regime classification is deterministic and explainable.",
    "Hysteresis is configurable and tested.",
    "Dynamic settings derive from immutable defaults.",
    "Dynamic risk cannot exceed permitted limits.",
    "Global account risk is enforced across all algorithms.",
    "Global evaluation is enforced server-side.",
    "Regime has a dedicated backtest.",
    "Daily backtesting includes Regime independently.",
    "Regime archives reference Regime results.",
    "ML defaults to shadow mode.",
    "ML has no lookahead leakage.",
    "Regime ML cannot move beyond shadow until deterministic walk-forward, untouched holdout, and paper-stability requirements pass.",
    "Other algorithms' outputs remain unchanged.",
    "Frontend build passes.",
    "Backend tests pass.",
    "Frontend tests pass.",
    "Paper-trading rollout is disabled by default or controlled through feature flags.",
}


class RegimeFinalAcceptanceTest(unittest.TestCase):
    def test_final_acceptance_report_covers_the_definition_of_done(self) -> None:
        report = build_regime_final_acceptance_report()

        self.assertEqual(report["algorithmId"], "regime")
        self.assertEqual(report["version"], REGIME_FINAL_ACCEPTANCE_VERSION)
        self.assertEqual(len(report["items"]), 22)
        self.assertEqual(len(REGIME_FINAL_ACCEPTANCE_ITEMS), 22)
        self.assertEqual(
            {item.statement for item in REGIME_FINAL_ACCEPTANCE_ITEMS},
            EXPECTED_REGIME_FINAL_ACCEPTANCE_STATEMENTS,
        )
        self.assertEqual(report["counts"], {"pass": 22, "pending": 0, "fail": 0})
        self.assertTrue(report["complete"])
        self.assertEqual(report["blockingStatements"], [])
        self.assertTrue(regime_acceptance_is_complete())

    def test_every_acceptance_evidence_path_exists(self) -> None:
        for item in REGIME_FINAL_ACCEPTANCE_ITEMS:
            self.assertTrue(item.evidence, item.statement)
            for evidence in item.evidence:
                if evidence.startswith(("backend/", "frontend/", "scripts/")):
                    self.assertTrue((ROOT / evidence).exists(), f"{item.statement}: {evidence}")

    def test_rollout_status_exposes_final_acceptance_without_enabling_live_trading(self) -> None:
        response = TestClient(app).get("/api/regime/rollout/status")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["feature_flags"]["REGIME_V2_ENABLED"])
        self.assertTrue(body["feature_flags"]["REGIME_DYNAMIC_PROFILE_ENABLED"])
        self.assertEqual(body["feature_flags"]["REGIME_ML_MODE"], "shadow")
        self.assertFalse(body["feature_flags"]["REGIME_SHORT_ENTRIES_ENABLED"])
        self.assertFalse(body["limited_paper_orders_allowed"])
        self.assertFalse(body["live_trading_allowed"])
        self.assertTrue(body["finalAcceptance"]["complete"])
        self.assertEqual(body["finalAcceptance"]["counts"], {"pass": 22, "pending": 0, "fail": 0})

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


if __name__ == "__main__":
    unittest.main()
