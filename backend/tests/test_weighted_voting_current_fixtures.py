from __future__ import annotations

import json
import unittest
from pathlib import Path


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "weighted_voting_current_behavior.json"
SCORE_ORDER = ("Buy", "Sell", "Hold")


class WeightedVotingCurrentFixtureTest(unittest.TestCase):
    def setUp(self) -> None:
        with FIXTURE_PATH.open("r", encoding="utf-8") as handle:
            self.fixture = json.load(handle)

    def test_fixture_covers_required_current_behavior_shapes(self) -> None:
        required = set(self.fixture["requiredCoverage"])
        covered = {tag for scenario in self.fixture["scenarios"] for tag in scenario["coverage"]}

        self.assertTrue(required.issubset(covered), sorted(required - covered))

    def test_each_scenario_reproduces_current_weighted_score_and_winner_math(self) -> None:
        catalog_keys = {row["key"] for row in self.fixture["strategyCatalog"]}

        for scenario in self.fixture["scenarios"]:
            with self.subTest(scenario=scenario["id"]):
                rows = scenario["strategyRows"]
                gates = scenario["gates"]
                expected = scenario["expectedCurrentOutput"]

                self.assertEqual({row["key"] for row in rows}, catalog_keys)
                self.assertAlmostEqual(sum(row["finalWeight"] for row in rows), 1.0, places=6)

                for row in rows:
                    self.assertAlmostEqual(row["pBuy"] + row["pSell"] + row["pHold"], 1.0, places=6)
                    self.assertGreaterEqual(row["finalWeight"], 0)

                scores = {
                    "Buy": round(sum(row["finalWeight"] * row["pBuy"] for row in rows), 4),
                    "Sell": round(sum(row["finalWeight"] * row["pSell"] for row in rows), 4),
                    "Hold": round(sum(row["finalWeight"] * row["pHold"] for row in rows), 4),
                }
                sorted_scores = sorted(
                    ((signal, scores[signal]) for signal in SCORE_ORDER),
                    key=lambda item: item[1],
                    reverse=True,
                )
                raw_winner = sorted_scores[0][0]
                margin = round(sorted_scores[0][1] - sorted_scores[1][1], 4)
                failed_gates = [gate["label"] for gate in gates if gate["status"] == "fail"]
                final_signal = "Hold" if failed_gates else raw_winner

                self.assertEqual(scores["Buy"], expected["buyScore"])
                self.assertEqual(scores["Sell"], expected["sellScore"])
                self.assertEqual(scores["Hold"], expected["holdScore"])
                self.assertEqual(raw_winner, expected["rawWinner"])
                self.assertEqual(margin, expected["margin"])
                self.assertEqual(failed_gates, expected["failedGates"])
                self.assertEqual(final_signal, expected["finalSignal"])


if __name__ == "__main__":
    unittest.main()
