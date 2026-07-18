from __future__ import annotations

import ast
import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.algorithms.weighted_voting.service import WeightedVotingService


REPO_ROOT = Path(__file__).parents[2]
FRONTEND_MAIN = REPO_ROOT / "frontend" / "src" / "main.ts"
PACKAGE_PATH = REPO_ROOT / "backend" / "app" / "algorithms" / "weighted_voting"
SESSION_OPEN = datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc)


class WeightedVotingMlDecouplingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.frontend_source = FRONTEND_MAIN.read_text(encoding="utf-8")

    def test_weighted_voting_output_is_identical_when_ml_inputs_fail(self) -> None:
        service = WeightedVotingService(store=MemoryStore())
        baseline_payload = evaluate_payload()
        failed_ml_payload = copy.deepcopy(baseline_payload)
        failed_ml_payload.update(
            {
                "mlComparison": {"status": "error", "bestByTimeframe": []},
                "dynamicArtifact": {"status": "stale", "mlComparison": {"status": "error"}},
                "candidateDatasets": {"status": "unavailable"},
                "mlDiagnostics": {"status": "error"},
                "metaLabels": {"status": "unavailable"},
                "futurePricePrediction": {"status": "error"},
                "marketForecast": {"status": "error"},
                "dynamicMlArtifacts": {"status": "stale"},
                "tradingRagReadiness": {"status": "unavailable"},
                "modelTrustStatus": "untrusted",
            }
        )

        baseline = service.evaluate(baseline_payload)
        failed_ml = service.evaluate(failed_ml_payload)

        self.assertEqual(stable_json(failed_ml), stable_json(baseline))

    def test_backend_evaluation_has_no_ml_gate(self) -> None:
        service = WeightedVotingService(store=MemoryStore())
        result = service.evaluate(evaluate_payload())
        serialized = stable_json(result)

        self.assertNotRegex(serialized, r"Meta Label|Weighted Forecast Safety|Trading RAG readiness")

    def test_service_status_declares_rule_based_non_ml_exclusions(self) -> None:
        status = WeightedVotingService(store=MemoryStore()).status()
        excluded = {item["componentId"] for item in status["excludedComponents"]["excludedComponents"]}

        self.assertEqual(status["excludedComponents"]["algorithmClass"], "rule_based_statistical_weighted_ensemble")
        self.assertFalse(status["excludedComponents"]["mlDriven"])
        self.assertIn("machine_learning_selector", excluded)
        self.assertIn("meta_label_model", excluded)
        self.assertIn("market_price_forecast_model", excluded)
        self.assertIn("frontend_calculated_authoritative_signal", excluded)
        self.assertIn("frontend_calculated_authoritative_quantity", excluded)
        self.assertIn("voting_ensemble_output", excluded)
        self.assertIn("wca_output", excluded)
        self.assertIn("regime_based_trading_output", excluded)
        self.assertIn("meta_strategy_output", excluded)

    def test_weighted_frontend_has_no_ml_gate_or_forecast_blocker(self) -> None:
        self.assertNotIn("function calculateWeightedVote", self.frontend_source)
        self.assertNotIn("function weightedTargetOrderFailedGates", self.frontend_source)
        weighted_slice = source_between(self.frontend_source, "function weightedVotingBackendSummary", "function latestWeightedCalculationCandles()")

        self.assertNotIn("Meta Label", weighted_slice)
        self.assertNotIn("metaLabel", weighted_slice)
        self.assertNotRegex(weighted_slice, r"dynamicArtifact|mlComparison|Trading RAG|marketForecast|forecast")

    def test_weighted_daily_refresh_starts_before_artifact_polling(self) -> None:
        daily_refresh_slice = source_between(
            self.frontend_source,
            "async function maybeRunDailyAlgorithmBacktests",
            "async function waitForDailyBacktestArtifacts",
        )

        weighted_index = daily_refresh_slice.index("runWeightedDailyBacktestRefresh")
        artifact_index = daily_refresh_slice.index("waitForDailyBacktestArtifacts")
        self.assertLess(weighted_index, artifact_index)
        self.assertIn("weightedRefreshResultPromise", daily_refresh_slice)

    def test_weighted_backend_package_has_no_ml_imports(self) -> None:
        forbidden_terms = ("ml", "meta_strategy", "market_forecast", "prediction", "rag", "dynamic_artifact")
        violations = []
        for path in sorted(PACKAGE_PATH.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                imported_modules: list[str] = []
                if isinstance(node, ast.Import):
                    imported_modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_modules = [node.module]
                for module_name in imported_modules:
                    if any(term in module_name.lower() for term in forbidden_terms):
                        violations.append(f"{path.relative_to(PACKAGE_PATH)} imports {module_name}")

        self.assertEqual(violations, [])


def source_between(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    end_index = source.index(end, start_index)
    return source[start_index:end_index]


def stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot


def evaluate_payload() -> dict:
    rows = candle_rows(count=95)
    return {
        "symbol": "SPY",
        "data_timestamp": rows[-1]["timestamp"],
        "candles": rows,
        "bid": rows[-1]["close"] - 0.01,
        "ask": rows[-1]["close"] + 0.01,
        "account_equity": 100000,
        "available_buying_power": 100000,
        "capital_available": 100000,
    }


def candle_rows(count: int = 390) -> list[dict]:
    rows = []
    for index in range(count):
        base = 100.0 + index * 0.03
        rows.append(
            {
                "timestamp": (SESSION_OPEN + timedelta(minutes=index)).isoformat(),
                "open": base,
                "high": base + 0.45,
                "low": base - 0.18,
                "close": base + 0.08,
                "volume": 200000 if index != 5 else 5000,
            }
        )
    return rows


if __name__ == "__main__":
    unittest.main()
