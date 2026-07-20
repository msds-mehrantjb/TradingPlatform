from __future__ import annotations

import ast
import unittest
from datetime import timedelta
from pathlib import Path

from backend.app.algorithms.meta_strategy import (
    MetaStrategyBacktestComparisonRequest,
    build_holdout_comparison_report,
    build_walk_forward_comparison_report,
)
from backend.tests.test_meta_strategy_step7_market_snapshot import DECISION_TIMESTAMP, request_with


ROOT = Path(__file__).resolve().parents[2]
COMPARISONS_PATH = ROOT / "backend" / "app" / "algorithms" / "meta_strategy" / "backtest" / "comparisons.py"
EXPECTED_SCENARIOS = (
    "DETERMINISTIC_META_STRATEGY",
    "ML_SHADOW",
    "ML_FILTER",
    "ML_RISK_REDUCTION",
    "NO_TRADE_BASELINE",
    "BUY_AND_HOLD_REFERENCE",
)


class MetaStrategyStep36BacktestComparisonsTest(unittest.TestCase):
    def test_comparison_report_contains_all_required_scenarios_and_metric_sections(self) -> None:
        report = build_walk_forward_comparison_report(comparison_request())

        self.assertEqual(tuple(comparison.scenario for comparison in report.comparisons), EXPECTED_SCENARIOS)
        for comparison in report.comparisons:
            with self.subTest(scenario=comparison.scenario):
                metrics = comparison.metrics
                self.assertEqual(metrics.algorithm_id, "meta_strategy")
                self.assertIsInstance(metrics.net_pnl, float)
                self.assertIsInstance(metrics.expectancy, float)
                self.assertGreaterEqual(metrics.drawdown, 0.0)
                self.assertGreaterEqual(metrics.coverage, 0.0)
                self.assertLessEqual(metrics.coverage, 1.0)
                self.assertGreaterEqual(metrics.acceptance_rate, 0.0)
                self.assertLessEqual(metrics.acceptance_rate, 1.0)
                self.assertGreaterEqual(metrics.rejection_rate, 0.0)
                self.assertLessEqual(metrics.rejection_rate, 1.0)
                self.assertIn("BUY", metrics.performance_by_side)
                self.assertIn("SELL", metrics.performance_by_side)
                self.assertIsInstance(metrics.performance_by_regime, dict)
                self.assertIsInstance(metrics.performance_by_probability_bucket, dict)
                self.assertIn("brierScore", metrics.calibration)
                self.assertIn("costMultiplier:0", metrics.cost_sensitivity)
                self.assertIn("costMultiplier:1", metrics.cost_sensitivity)
                self.assertIn("costMultiplier:2", metrics.cost_sensitivity)

    def test_walk_forward_and_holdout_reports_are_reproducible(self) -> None:
        request = comparison_request()

        walk_forward_1 = build_walk_forward_comparison_report(request)
        walk_forward_2 = build_walk_forward_comparison_report(request)
        holdout_1 = build_holdout_comparison_report(request)
        holdout_2 = build_holdout_comparison_report(request)

        self.assertEqual(walk_forward_1.report_hash, walk_forward_2.report_hash)
        self.assertEqual(holdout_1.report_hash, holdout_2.report_hash)
        self.assertNotEqual(walk_forward_1.report_hash, holdout_1.report_hash)
        self.assertTrue(walk_forward_1.reproducible)
        self.assertTrue(holdout_1.reproducible)

    def test_artifacts_versions_and_runtime_parity_are_recorded(self) -> None:
        report = build_walk_forward_comparison_report(comparison_request())

        self.assertTrue(report.runtime_parity_passed)
        self.assertIn("algorithmVersion", report.versions)
        self.assertIn("featureSchemaVersion", report.versions)
        self.assertEqual(report.artifact_manifest[0]["artifactId"], "artifact-old")
        self.assertEqual(report.artifact_manifest[1]["artifactId"], "artifact-future")
        pipeline_comparisons = [comparison for comparison in report.comparisons if comparison.backtest_result is not None]
        self.assertTrue(all(comparison.backtest_result.runtime_parity.passed for comparison in pipeline_comparisons if comparison.backtest_result))

    def test_comparison_layer_does_not_duplicate_decision_logic(self) -> None:
        tree = ast.parse(COMPARISONS_PATH.read_text(encoding="utf-8"))
        imported = imported_module_names(tree)

        self.assertIn("backend.app.algorithms.meta_strategy.backtest.engine", imported)
        self.assertNotIn("backend.app.algorithms.meta_strategy.candidate_generator", imported)
        self.assertNotIn("backend.app.algorithms.meta_strategy.family_aggregation", imported)
        self.assertNotIn("backend.app.algorithms.meta_strategy.inference.predictor", imported)


def comparison_request() -> MetaStrategyBacktestComparisonRequest:
    return MetaStrategyBacktestComparisonRequest(
        decision_requests=(
            request_with(decision_timestamp=DECISION_TIMESTAMP),
            request_with(decision_timestamp=DECISION_TIMESTAMP + timedelta(minutes=5)),
        ),
        model_artifacts=(
            {"artifactId": "artifact-old", "modelArtifactVersion": "meta_strategy_model_artifact_v1", "availableAt": (DECISION_TIMESTAMP - timedelta(days=1)).isoformat()},
            {"artifactId": "artifact-future", "modelArtifactVersion": "meta_strategy_model_artifact_v1", "availableAt": (DECISION_TIMESTAMP + timedelta(days=1)).isoformat()},
        ),
    )


def imported_module_names(tree: ast.AST) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)


if __name__ == "__main__":
    unittest.main()
