from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from backend.app.main import app


class RegimePhase13BacktestApiTest(unittest.TestCase):
    def test_regime_backtest_status_route_is_independent(self) -> None:
        client = TestClient(app)
        response = client.get("/api/regime/backtests/status")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["algorithmId"], "regime")
        self.assertEqual(payload["engineVersion"], "regime_backtest_v3_backend")
        self.assertEqual(payload["status"], "backend_runtime_available")
        self.assertEqual(payload["authoritativeRuntime"], "backend.app.algorithms.regime.execution_pipeline")
        self.assertEqual(payload["authoritativeEngine"], "backend.app.algorithms.regime.backtest.engine")
        self.assertEqual(payload["runtimeLocation"], "backend/app/algorithms/regime")
        self.assertEqual(payload["frontendRole"], "API client and presentation only")
        self.assertEqual(
            payload["fileInventory"],
            [
                "__init__.py",
                "engine.py",
                "execution.py",
                "ledger.py",
                "metrics.py",
                "walk_forward.py",
            ],
        )
        self.assertEqual(
            payload["ownedCapabilities"],
            [
                "Regime replay",
                "Warm-up handling",
                "Point-in-time classification",
                "Hysteresis replay",
                "Strategy routing",
                "Dynamic-profile reconstruction",
                "Family aggregation",
                "Entry and exit simulation",
                "Costs and slippage",
                "Position ledger",
                "Trade ledger",
                "Regime-segmented performance",
                "Strategy-family attribution",
                "Walk-forward validation",
                "Untouched holdout testing",
                "Daily independent backtests",
            ],
        )
        self.assertTrue(payload["isolatedFromWca"])
        self.assertNotIn("/api/wca/", str(payload).lower())
        self.assertNotIn("frontend/src/algorithms/regime", str(payload).lower())
        self.assertNotIn("wca/backtest", payload["authoritativeEngine"].lower())

    def test_regime_backtest_route_discovery_does_not_expose_wca_routes(self) -> None:
        client = TestClient(app)
        response = client.get("/api/regime/backtests/routes")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        paths = {route["path"] for route in payload["routes"]}
        self.assertIn("/api/regime/backtests/status", paths)
        self.assertIn("/api/regime/evaluate", paths)
        self.assertIn("/api/regime/backtests/run", paths)
        self.assertTrue(all("/api/wca/" not in path for path in paths))


if __name__ == "__main__":
    unittest.main()
