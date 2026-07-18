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
        self.assertEqual(payload["engineVersion"], "regime_backtest_v2")
        self.assertIn("frontend/src/algorithms/regime/backtest/engine.ts", payload["authoritativeCore"])
        self.assertEqual(
            payload["fileInventory"],
            [
                "engine.ts",
                "execution-simulator.ts",
                "metrics.ts",
                "diagnostics.ts",
                "walk-forward.ts",
                "runner.ts",
                "types.ts",
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
        self.assertNotIn("wca/backtest", payload["authoritativeCore"].lower())

    def test_regime_backtest_route_discovery_does_not_expose_wca_routes(self) -> None:
        client = TestClient(app)
        response = client.get("/api/regime/backtests/routes")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        paths = {route["path"] for route in payload["routes"]}
        self.assertIn("/api/regime/backtests/status", paths)
        self.assertTrue(all("/api/wca/" not in path for path in paths))


if __name__ == "__main__":
    unittest.main()
