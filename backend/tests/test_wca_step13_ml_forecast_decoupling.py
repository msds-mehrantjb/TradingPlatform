from __future__ import annotations

import ast
import builtins
import importlib
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.algorithms.wca.contracts import WcaEvaluateRequest
from backend.app.algorithms.wca.engine import evaluate_wca_legacy
from backend.app.algorithms.wca.feature_snapshot import build_wca_feature_snapshot

ROOT = Path(__file__).resolve().parents[2]
WCA_PATH = ROOT / "backend" / "app" / "algorithms" / "wca"
FRONTEND_MAIN = ROOT / "frontend" / "src" / "main.ts"
FIXTURE_PATH = ROOT / "backend" / "tests" / "fixtures" / "wca" / "golden_snapshots.json"

FORBIDDEN_IMPORT_PREFIXES = (
    "backend.app.market_forecast",
    "backend.app.market_forecast_worker",
    "backend.app.train_market_forecast",
    "backend.app.meta_strategy_training",
    "backend.app.backtesting.ml_filter_rollout",
    "backend.app.backtesting.dynamic_policy_shadow",
)


class WcaStep13MlForecastDecouplingTests(unittest.TestCase):
    def test_wca_package_imports_no_market_forecast_or_meta_training_modules(self) -> None:
        violations: list[str] = []
        for path in sorted(WCA_PATH.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                module_name = ""
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        module_name = alias.name
                        if module_name.startswith(FORBIDDEN_IMPORT_PREFIXES):
                            violations.append(f"{path.relative_to(ROOT)} imports {module_name}")
                elif isinstance(node, ast.ImportFrom):
                    module_name = node.module or ""
                    if module_name.startswith(FORBIDDEN_IMPORT_PREFIXES):
                        violations.append(f"{path.relative_to(ROOT)} imports {module_name}")

        self.assertEqual(violations, [])

    def test_wca_evaluates_when_ml_services_are_offline(self) -> None:
        snapshot = _first_wca_snapshot()

        with _ml_imports_forbidden():
            result = evaluate_wca_legacy(WcaEvaluateRequest.model_validate(snapshot))

        self.assertEqual(result.algorithm_id, "wca")
        self.assertIsNotNone(result.decision)
        self.assertIn(result.signal, {"Buy", "Sell", "Hold"})

    def test_wca_backtest_module_imports_when_ml_artifacts_do_not_exist(self) -> None:
        with _ml_imports_forbidden():
            module = importlib.import_module("backend.app.algorithms.wca.backtest.engine")

        self.assertTrue(hasattr(module, "BacktestRunConfiguration"))
        self.assertFalse(hasattr(module, "marketForecastArtifact"))

    def test_wca_is_absent_from_forecast_override_mode_lists(self) -> None:
        source = FRONTEND_MAIN.read_text(encoding="utf-8")

        buy_blockers_body = _function_body(source, "forecastBuySafetyBlockers")
        stop_override_body = _function_body(source, "forecastStopOverrideKeepReason")
        buy_gates_body = _function_body(source, "confidenceTargetOrderFailedGates")

        self.assertNotIn('"confidence"', buy_blockers_body)
        self.assertNotIn('"confidence"', stop_override_body)
        self.assertNotIn("WCA Forecast Safety", source)
        self.assertIn('mode === "regime"', buy_gates_body)

    def test_wca_daily_backtest_starts_before_ml_artifact_polling(self) -> None:
        source = FRONTEND_MAIN.read_text(encoding="utf-8")
        scheduler_body = _function_body(source, "maybeRunDailyAlgorithmBacktests")

        self.assertLess(
            scheduler_body.index("const confidenceRefreshResultPromise = runConfidenceDailyBacktestFromPreparedCandles"),
            scheduler_body.index("waitForDailyBacktestArtifacts"),
        )

    def test_wca_feature_snapshot_is_read_only_baseline_for_external_consumers(self) -> None:
        result = evaluate_wca_legacy(WcaEvaluateRequest.model_validate(_first_wca_snapshot()))
        snapshot = build_wca_feature_snapshot(result.decision)
        payload = snapshot.model_dump(mode="json")

        self.assertEqual(snapshot.algorithm_id, "wca")
        self.assertEqual(snapshot.schema_version, "wca_read_only_feature_snapshot_v1")
        self.assertEqual(snapshot.final_wca_decision, result.decision.aggregation.post_local_gate_decision)
        self.assertNotIn("ml", snapshot.reason_codes)
        self.assertIn("strategy_signals", payload)
        self.assertIn("strategy_calibrated_confidences", payload)
        self.assertIn("effective_weights", payload)
        forbidden_mutation_fields = {
            "weight_update",
            "settings_update",
            "threshold_override",
            "stop_override",
            "trade_history_update",
            "replacement_decision",
        }
        self.assertTrue(forbidden_mutation_fields.isdisjoint(payload))


def _first_wca_snapshot() -> dict:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return fixture["snapshots"][0]


def _ml_imports_forbidden():
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith(FORBIDDEN_IMPORT_PREFIXES):
            raise ImportError(f"ML/forecast service offline for WCA test: {name}")
        return original_import(name, globals, locals, fromlist, level)

    return patch("builtins.__import__", side_effect=guarded_import)


def _function_body(source: str, function_name: str) -> str:
    match = re.search(rf"function {re.escape(function_name)}\([^)]*\) \{{", source)
    if not match:
        raise AssertionError(f"{function_name} not found")
    depth = 0
    for index in range(match.end() - 1, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[match.start(): index + 1]
    raise AssertionError(f"{function_name} body did not close")


if __name__ == "__main__":
    unittest.main()
