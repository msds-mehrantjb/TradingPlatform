import unittest

from backend.app.algorithms.regime.backtest.engine import run_regime_backtest
from backend.app.algorithms.regime.execution_pipeline import execute_regime_pipeline
from backend.tests.regime.fixtures.candles import candles


class RuntimeParityTest(unittest.TestCase):
    def test_live_paper_and_backtest_expose_same_backend_authority(self):
        payload = {"marketData": {"symbol": "SPY", "primaryCandles": candles(count=40)}, "account": {"availableBuyingPower": 10_000}}
        live = execute_regime_pipeline(payload)
        paper = execute_regime_pipeline({**payload, "mode": "paper"})
        backtest = run_regime_backtest({"symbol": "SPY", "candles": candles(count=40)})

        self.assertEqual(live["runtime"], "backend.app.algorithms.regime.execution_pipeline")
        self.assertEqual(paper["runtime"], live["runtime"])
        self.assertEqual(live["pipeline"], paper["pipeline"])
        for module in ("classifier", "router", "dynamic_profile", "family_aggregation", "sizing", "trade_management"):
            self.assertIn(module, live["pipeline"])
        self.assertIn("tradeManagement", live)
        self.assertIn("tradeManagement", paper)
        self.assertEqual(backtest["authoritativeEngine"], "backend.app.algorithms.regime.backtest.engine")
        self.assertIn("backend_authoritative_runtime", backtest["diagnostics"])
        self.assertTrue(all("tradeManagement" in decision for decision in backtest["decisions"]))
