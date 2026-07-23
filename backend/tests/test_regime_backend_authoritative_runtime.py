from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.app.algorithms.regime.backtest.engine import run_regime_backtest
from backend.app.algorithms.regime.execution_pipeline import REGIME_EXECUTION_PIPELINE_MODULES, execute_regime_pipeline
from backend.app.algorithms.regime.strategy_registry import regime_strategy_inventory


ROOT = Path(__file__).resolve().parents[2]


def fixture_candles(count: int = 70) -> list[dict[str, float | str]]:
    candles: list[dict[str, float | str]] = []
    price = 100.0
    start = datetime(2026, 7, 23, 16, 0, tzinfo=UTC)
    for index in range(count):
        price += 0.08
        timestamp = (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        candles.append(
            {
                "timestamp": timestamp,
                "open": price - 0.03,
                "high": price + 0.12,
                "low": price - 0.12,
                "close": price,
                "volume": 120_000 + index,
            }
        )
    return candles


class RegimeBackendAuthoritativeRuntimeTest(unittest.TestCase):
    def test_backend_pipeline_executes_classifier_strategy_sizing_and_order_validation(self) -> None:
        result = execute_regime_pipeline(
            {
                "marketData": {"symbol": "SPY", "primaryCandles": fixture_candles()},
                "account": {
                    "availableBuyingPower": 25_000,
                    "remainingAlgorithmRiskDollars": 500,
                    "globalRiskCapacityQuantity": 1_000,
                },
            }
        )

        self.assertEqual(result["algorithmId"], "regime")
        self.assertEqual(result["runtime"], "backend.app.algorithms.regime.execution_pipeline")
        self.assertIn("classifier", REGIME_EXECUTION_PIPELINE_MODULES)
        self.assertIn("router", REGIME_EXECUTION_PIPELINE_MODULES)
        self.assertIn("sizing", REGIME_EXECUTION_PIPELINE_MODULES)
        self.assertIn("order_validation", REGIME_EXECUTION_PIPELINE_MODULES)
        self.assertEqual(result["decision"]["algorithm_id"], "regime")
        self.assertEqual(result["decision"]["raw_classification"]["raw_regime"] in result["decision"]["confirmed_state"]["confirmed_regime"], True)
        self.assertIsInstance(result["decision"]["strategy_outputs"], list)
        self.assertGreaterEqual(len(result["decision"]["strategy_outputs"]), 2)
        self.assertEqual(
            {item["strategy_id"] for item in result["decision"]["strategy_outputs"]},
            {"adx_atr_regime_classifier", "cash_avoid_filter"},
        )
        self.assertIn(result["decision"]["signal"], {"Buy", "Sell", "Hold"})
        self.assertIn("valid", result["orderValidation"])

    def test_backend_backtest_uses_backend_pipeline(self) -> None:
        result = run_regime_backtest({"symbol": "SPY", "candles": fixture_candles()})

        self.assertEqual(result["algorithmId"], "regime")
        self.assertEqual(result["authoritativeEngine"], "backend.app.algorithms.regime.backtest.engine")
        self.assertEqual(result["engineVersion"], "regime_backtest_v3_backend")
        self.assertEqual(result["candles"], 70)
        self.assertEqual(result["metrics"]["decisionCount"], 70)
        self.assertIn("backend_authoritative_runtime", result["diagnostics"])

    def test_backend_strategy_catalog_has_dedicated_role_inventory(self) -> None:
        inventory = regime_strategy_inventory()

        self.assertEqual(inventory["strategyCount"], 2)
        self.assertEqual(inventory["directionalCount"], 0)
        self.assertEqual(inventory["confirmationCount"], 0)
        self.assertEqual(inventory["contextCount"], 1)
        self.assertEqual(inventory["safetyCount"], 1)
        self.assertEqual(inventory["moduleInventory"]["regime"][0]["id"], "adx_atr_regime_classifier")
        self.assertEqual(inventory["moduleInventory"]["safety"][0]["id"], "cash_avoid_filter")
        self.assertEqual(inventory["aliases"]["adx_trend_strength_regime"], "adx_atr_regime_classifier")

    def test_backend_regime_package_does_not_import_frontend_runtime(self) -> None:
        backend_files = list((ROOT / "backend" / "app" / "algorithms" / "regime").rglob("*.py"))
        backend_source = "\n".join(path.read_text(encoding="utf-8") for path in backend_files)

        self.assertNotIn("frontend/src/algorithms/regime", backend_source)
        self.assertNotIn("client_core_available", backend_source)
        self.assertNotIn("TypeScript core", backend_source)

    def test_frontend_no_longer_contains_regime_runtime_or_fallback(self) -> None:
        frontend_regime_path = ROOT / "frontend" / "src" / "algorithms" / "regime"
        main_source = (ROOT / "frontend" / "src" / "main.ts").read_text(encoding="utf-8")

        self.assertFalse(frontend_regime_path.exists())
        self.assertIn("evaluateRegimeOnBackend", main_source)
        self.assertNotIn("calculateRegimeDecision(", main_source)
        self.assertNotIn("buildRegimeMarketContext(", main_source)
        self.assertNotIn("buildRegimeTargetOrder(", main_source)


if __name__ == "__main__":
    unittest.main()
