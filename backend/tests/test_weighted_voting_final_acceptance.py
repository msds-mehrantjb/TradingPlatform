from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.algorithms.weighted_voting.final_acceptance import (
    WEIGHTED_VOTING_FINAL_ACCEPTANCE_ITEMS,
    WEIGHTED_VOTING_FINAL_ACCEPTANCE_VERSION,
    WeightedVotingAcceptanceStatus,
    build_weighted_voting_final_acceptance_report,
    weighted_voting_acceptance_is_complete,
)


ROOT = Path(__file__).resolve().parents[2]


class WeightedVotingFinalAcceptanceTest(unittest.TestCase):
    def test_final_acceptance_report_covers_every_required_statement(self) -> None:
        report = build_weighted_voting_final_acceptance_report()

        self.assertEqual(report["algorithmId"], "weighted_voting")
        self.assertEqual(report["version"], WEIGHTED_VOTING_FINAL_ACCEPTANCE_VERSION)
        self.assertEqual(len(report["items"]), 14)
        self.assertEqual(len(WEIGHTED_VOTING_FINAL_ACCEPTANCE_ITEMS), 14)
        self.assertEqual(report["counts"], {"pass": 14, "pending": 0, "fail": 0})
        self.assertTrue(report["complete"])
        self.assertTrue(weighted_voting_acceptance_is_complete())
        self.assertEqual(report["blockingStatements"], [])

    def test_all_user_final_acceptance_conditions_are_exactly_represented(self) -> None:
        statements = {item.statement for item in WEIGHTED_VOTING_FINAL_ACCEPTANCE_ITEMS}

        self.assertEqual(
            statements,
            {
                "It runs with all ML systems disabled.",
                "It runs when every other algorithm is unavailable.",
                "Changing another algorithm does not change its output.",
                "Weighted winner always determines candidate direction.",
                "Automatic mode cannot bypass local gates.",
                "Actual quotes are used for spread.",
                "Defaults remain the dynamic settings baseline.",
                "Dynamic values remain inside envelopes and hard limits.",
                "Backtesting and paper trading call the same decision functions.",
                "Weights use only completed prior data.",
                "Global gates only reduce, reject, or emergency-exit.",
                "Positions, P/L, risk, and capital remain attributable to Weighted Voting.",
                "The system remains paper-trading only.",
                "All unit, integration, isolation, property, and replay tests pass.",
            },
        )

    def test_every_required_item_is_passing_with_existing_evidence(self) -> None:
        for item in WEIGHTED_VOTING_FINAL_ACCEPTANCE_ITEMS:
            with self.subTest(statement=item.statement):
                self.assertTrue(item.required_for_completion)
                self.assertEqual(item.status, WeightedVotingAcceptanceStatus.PASS)
                self.assertTrue(item.evidence)
                for evidence in item.evidence:
                    if evidence.startswith(("backend/", "frontend/", "scripts/", "docs/")):
                        self.assertTrue((ROOT / evidence).exists(), f"{item.statement}: {evidence}")

    def test_documented_matrix_matches_executable_acceptance(self) -> None:
        doc = (ROOT / "docs" / "weighted_voting" / "final_acceptance_validation.md").read_text(encoding="utf-8")

        self.assertIn("Acceptance status: PASS", doc)
        doc_labels = {
            "It runs with all ML systems disabled.": "Runs with all ML systems disabled",
            "It runs when every other algorithm is unavailable.": "Runs when every other algorithm is unavailable",
            "Changing another algorithm does not change its output.": "Changing another algorithm does not change Weighted Voting output",
            "Weighted winner always determines candidate direction.": "Weighted winner always determines candidate direction",
            "Automatic mode cannot bypass local gates.": "Automatic mode cannot bypass local gates",
            "Actual quotes are used for spread.": "Actual quotes are used for spread",
            "Defaults remain the dynamic settings baseline.": "Defaults remain the dynamic settings baseline",
            "Dynamic values remain inside envelopes and hard limits.": "Dynamic values remain inside envelopes and hard limits",
            "Backtesting and paper trading call the same decision functions.": "Backtesting and paper trading call the same decision functions",
            "Weights use only completed prior data.": "Weights use only completed prior data",
            "Global gates only reduce, reject, or emergency-exit.": "Global gates only reduce, reject, exit-only, or emergency-exit",
            "Positions, P/L, risk, and capital remain attributable to Weighted Voting.": "Positions, P/L, risk, and capital remain attributable to Weighted Voting",
            "The system remains paper-trading only.": "System remains paper-trading only",
            "All unit, integration, isolation, property, and replay tests pass.": "Unit, integration, isolation, property, and replay tests pass",
        }
        for item in WEIGHTED_VOTING_FINAL_ACCEPTANCE_ITEMS:
            self.assertIn(doc_labels[item.statement], doc)
        self.assertIn("Weighted Voting V2 satisfies the final acceptance conditions", doc)

    def test_final_acceptance_gate_is_registered_with_ci_checks(self) -> None:
        ci_source = (ROOT / "scripts" / "ci_quality_gates.py").read_text(encoding="utf-8")

        self.assertIn("weighted-voting-final-acceptance", ci_source)
        self.assertIn("test_weighted_voting_final_acceptance.py", ci_source)
        self.assertIn("test_weighted_voting_step32_comprehensive.py", ci_source)


if __name__ == "__main__":
    unittest.main()
