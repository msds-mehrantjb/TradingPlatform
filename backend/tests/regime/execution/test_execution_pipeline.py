import unittest

from backend.app.algorithms.regime.execution_pipeline import REGIME_EXECUTION_PIPELINE_MODULES, execute_regime_pipeline
from backend.tests.regime.fixtures.candles import candles


class ExecutionPipelineTest(unittest.TestCase):
    def test_live_payload_uses_backend_authoritative_sequence(self):
        result = execute_regime_pipeline(
            {
                "marketData": {"symbol": "SPY", "primaryCandles": candles()},
                "settings": {
                    "minimumWinningScore": 0,
                    "minimumSignalEdge": 0,
                    "minimumActiveStrategies": 1,
                    "minimumIndependentFamilies": 1,
                    "minimumRegimeConfidence": 0,
                },
                "account": {"availableBuyingPower": 25_000, "remainingAlgorithmRiskDollars": 500},
            }
        )

        self.assertEqual(result["algorithmId"], "regime")
        self.assertEqual(result["runtime"], "backend.app.algorithms.regime.execution_pipeline")
        self.assertEqual(tuple(result["pipeline"]), REGIME_EXECUTION_PIPELINE_MODULES)
        self.assertIn("decision", result)
        self.assertIn("orderValidation", result)

