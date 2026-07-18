import unittest
from backend.app.algorithms.regime.family_aggregation import aggregate_family_scores
from backend.app.algorithms.regime.contracts import RegimeStrategyEvaluation

class FamilyAggregationTest(unittest.TestCase):
    def test_preserves_sell_and_handles_conflicts(self):
        outputs = (
            RegimeStrategyEvaluation("a", "A", "trend", "directional", "Sell", 0.9, 0.5, True, "sell"),
            RegimeStrategyEvaluation("b", "B", "breakout", "directional", "Buy", 0.2, 0.1, True, "buy"),
        )
        result = aggregate_family_scores(outputs)
        self.assertEqual(result["signal"], "Sell")
        self.assertGreater(result["scores"]["sell"], result["scores"]["buy"])

