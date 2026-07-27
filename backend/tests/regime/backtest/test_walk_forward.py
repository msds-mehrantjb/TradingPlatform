import unittest

from backend.app.algorithms.regime.backtest.walk_forward import walk_forward_summary
from backend.tests.regime.fixtures.candles import candles


class WalkForwardTest(unittest.TestCase):
    def test_walk_forward_summary_marks_holdout_untouched_without_auto_passing(self):
        summary = walk_forward_summary(candles(count=20), [{"tradeId": "t1"}])

        self.assertFalse(summary["accepted"])
        self.assertFalse(summary["walkForwardStable"])
        self.assertTrue(summary["holdoutUntouched"])
        self.assertEqual(summary["folds"], 2)
        self.assertIn("regime.backtest.walk_forward.insufficient_fold_evidence", summary["reasonCodes"])
