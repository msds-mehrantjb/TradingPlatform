from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.algorithms.wca.final_acceptance import (
    WCA_FINAL_ACCEPTANCE_ITEMS,
    WCA_FINAL_ACCEPTANCE_VERSION,
    WcaAcceptanceStatus,
    build_wca_final_acceptance_report,
    wca_acceptance_is_complete,
)


ROOT = Path(__file__).resolve().parents[2]

EXPECTED_STEP21_CHECKLIST: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Architecture",
        (
            "WCA is an isolated backend algorithm.",
            "Strategies are isolated modules.",
            "Frontend is presentation-only.",
            "Live, paper, and backtest use the same engine.",
            "WCA does not depend on ML.",
        ),
    ),
    (
        "Strategies",
        (
            "Only primary alpha strategies cast votes.",
            "Context indicators are modifiers.",
            "Risk filters are gates.",
            "Duplicate strategy logic is removed.",
            "Hold and Not Applicable are different.",
            "Strategy-family concentration is controlled.",
        ),
    ),
    (
        "Confidence and weights",
        (
            "Confidence is statistically calibrated.",
            "Weights are leakage-free.",
            "Weights use sample reliability and shrinkage.",
            "Family and strategy caps are enforced.",
            "Weight snapshots are versioned and reproducible.",
        ),
    ),
    (
        "Settings",
        (
            "User defaults remain the baseline.",
            "Dynamic profiles are bounded.",
            "Effective settings do not overwrite defaults.",
            "Initial dynamic behavior is defensive only.",
            "Profile changes use hysteresis.",
        ),
    ),
    (
        "Risk and execution",
        (
            "Local and global gates are separate.",
            "Account risk is aggregated across algorithms.",
            "New entries and risk-reducing exits use separate permissions.",
            "Protective stops cannot be overridden or delayed by forecasts.",
            "Final order validation occurs after every override.",
            "Duplicate broker orders are prevented atomically.",
            "Broker positions and orders are reconciled.",
        ),
    ),
    (
        "Backtesting",
        (
            "The backtest is backend-authoritative.",
            "There is no same-candle signal/fill bias.",
            "Early-session strategies receive proper warm-up data.",
            "Costs and open-position drawdown are included.",
            "Full-history, walk-forward, and holdout results exist.",
            "Dynamic settings use the same resolver as paper trading.",
            "Smoke-test results are not used as profitability proof.",
        ),
    ),
    (
        "ML isolation",
        (
            "ML may read WCA outputs.",
            "ML cannot write into WCA.",
            "ML cannot block WCA entries.",
            "ML cannot delay WCA exits.",
            "ML failure cannot stop WCA evaluation or backtesting.",
        ),
    ),
    (
        "Deployment",
        (
            "Shadow comparison completed.",
            "Critical tests pass.",
            "Paper trading is stable.",
            "Rollback is tested.",
            "Real-money execution remains disabled unless explicitly enabled through a separate controlled process.",
        ),
    ),
)


class WcaStep21FinalAcceptanceTests(unittest.TestCase):
    def test_final_acceptance_report_covers_every_required_statement(self) -> None:
        report = build_wca_final_acceptance_report()

        self.assertEqual(report["algorithmId"], "wca")
        self.assertEqual(report["version"], WCA_FINAL_ACCEPTANCE_VERSION)
        self.assertEqual(len(report["items"]), 45)
        self.assertEqual(len(WCA_FINAL_ACCEPTANCE_ITEMS), 45)
        self.assertEqual(
            {item.category for item in WCA_FINAL_ACCEPTANCE_ITEMS},
            {
                "Architecture",
                "Strategies",
                "Confidence and weights",
                "Settings",
                "Risk and execution",
                "Backtesting",
                "ML isolation",
                "Deployment",
            },
        )

    def test_step21_user_checklist_is_represented_exactly(self) -> None:
        actual = tuple(
            (category, tuple(item.statement for item in WCA_FINAL_ACCEPTANCE_ITEMS if item.category == category))
            for category, _statements in EXPECTED_STEP21_CHECKLIST
        )

        self.assertEqual(actual, EXPECTED_STEP21_CHECKLIST)
        self.assertEqual(
            sum(len(statements) for _category, statements in EXPECTED_STEP21_CHECKLIST),
            len(WCA_FINAL_ACCEPTANCE_ITEMS),
        )

    def test_pending_items_prevent_completion_declaration(self) -> None:
        report = build_wca_final_acceptance_report()

        self.assertFalse(report["complete"])
        self.assertFalse(wca_acceptance_is_complete())
        self.assertEqual(report["counts"], {"pass": 38, "pending": 7, "fail": 0})
        self.assertEqual(report["counts"]["fail"], 0)
        self.assertEqual(report["counts"]["pending"], 7)
        self.assertEqual(len(report["blockingStatements"]), 7)
        self.assertNotIn("Frontend is presentation-only.", report["blockingStatements"])
        self.assertIn("Paper trading is stable.", report["blockingStatements"])
        self.assertIn("Shadow comparison completed.", report["blockingStatements"])

    def test_every_acceptance_evidence_path_exists(self) -> None:
        for item in WCA_FINAL_ACCEPTANCE_ITEMS:
            self.assertTrue(item.evidence, item.statement)
            for evidence in item.evidence:
                if evidence.startswith("backend/") or evidence.startswith("frontend/") or evidence.startswith("scripts/"):
                    self.assertTrue((ROOT / evidence).exists(), f"{item.statement}: {evidence}")

    def test_frontend_legacy_calculations_are_removed_from_wca_path(self) -> None:
        frontend = (ROOT / "frontend" / "src" / "main.ts").read_text(encoding="utf-8")
        self.assertNotIn("calculateConfidenceAggregation", frontend)
        self.assertNotIn("calculateConfidenceAggregationFromMarket", frontend)
        self.assertNotIn("refreshWcaBackendShadow", frontend)
        self.assertNotIn("WCA_BACKEND_ENGINE_ENABLED", frontend)
        self.assertIn("wcaBackendDecisionAsConfidenceResult", frontend)
        self.assertIn("wcaBackendTargetOrderRecommendation", frontend)

        presentation_item = next(item for item in WCA_FINAL_ACCEPTANCE_ITEMS if item.statement == "Frontend is presentation-only.")
        self.assertEqual(presentation_item.status, WcaAcceptanceStatus.PASS)
        self.assertIn("frontend/src/main.ts", presentation_item.evidence)

    def test_documented_checklist_is_not_marked_complete(self) -> None:
        doc = (ROOT / "docs" / "wca" / "final_acceptance_checklist.md").read_text(encoding="utf-8")

        self.assertIn("Status: NOT COMPLETE", doc)
        self.assertIn("Frontend is presentation-only.", doc)
        self.assertIn("Shadow comparison completed.", doc)
        self.assertIn("Paper trading is stable.", doc)
        for _category, statements in EXPECTED_STEP21_CHECKLIST:
            for statement in statements:
                self.assertIn(statement, doc)
        self.assertNotIn("Status: COMPLETE", doc)

    def test_step21_final_gate_is_registered_with_critical_ci_checks(self) -> None:
        ci_source = (ROOT / "scripts" / "ci_quality_gates.py").read_text(encoding="utf-8")

        self.assertIn("safety-critical-regression-tests", ci_source)
        self.assertIn("test_wca_step19_comprehensive.py", ci_source)
        self.assertIn("test_wca_step21_final_acceptance.py", ci_source)


if __name__ == "__main__":
    unittest.main()
