import unittest

from backend.app.algorithms.regime.backtest.engine import REGIME_BACKTEST_ENGINE_VERSION, run_regime_backtest
from backend.tests.regime.fixtures.candles import candles


class BacktestEngineTest(unittest.TestCase):
    def test_backtest_uses_backend_authoritative_engine(self):
        result = run_regime_backtest({"symbol": "spy", "candles": candles(count=30), "startingCapital": 10_000})

        self.assertEqual(result["algorithmId"], "regime")
        self.assertEqual(result["engineVersion"], REGIME_BACKTEST_ENGINE_VERSION)
        self.assertEqual(result["authoritativeEngine"], "backend.app.algorithms.regime.backtest.engine")
        self.assertEqual(result["candles"], 30)
        self.assertEqual(len(result["decisions"]), 30)

