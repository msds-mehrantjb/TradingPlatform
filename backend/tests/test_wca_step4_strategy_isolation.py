from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.algorithms.wca.strategy_registry import StrategyConfig
from backend.app.algorithms.wca.strategies.primary_voters import WCA_PRIMARY_VOTERS
from test_wca_step3_strategy_catalog import STRATEGY_CASES


ROOT = Path(__file__).parents[2]
STRATEGY_PATH = ROOT / "backend" / "app" / "algorithms" / "wca" / "strategies"
STRATEGY_PACKAGE = "backend.app.algorithms.wca.strategies"

ISOLATED_STRATEGY_MODULES = (
    ("moving_average_trend", "MovingAverageTrendStrategy"),
    ("trend_pullback", "TrendPullbackStrategy"),
    ("vwap_trend_continuation", "VwapTrendContinuationStrategy"),
    ("vwap_mean_reversion", "VwapMeanReversionStrategy"),
    ("rsi_mean_reversion", "RsiMeanReversionStrategy"),
    ("bollinger_atr_reversion", "BollingerAtrReversionStrategy"),
    ("opening_range_breakout", "OpeningRangeBreakoutStrategy"),
    ("intraday_volatility_breakout", "IntradayVolatilityBreakoutStrategy"),
    ("failed_breakout_reversal", "FailedBreakoutReversalStrategy"),
    ("liquidity_sweep_reversal", "LiquiditySweepReversalStrategy"),
    ("gap_continuation_fade", "GapContinuationFadeStrategy"),
)


class WcaStep4StrategyIsolationTest(unittest.TestCase):
    def test_each_primary_strategy_imports_and_evaluates_independently(self) -> None:
        for module_name, class_name in ISOLATED_STRATEGY_MODULES:
            with self.subTest(strategy=module_name):
                module = importlib.import_module(f"{STRATEGY_PACKAGE}.{module_name}")
                strategy = getattr(module, class_name)()
                fixture = STRATEGY_CASES[module_name][0][1]

                result = strategy.evaluate(fixture, StrategyConfig())

                self.assertEqual(strategy.definition.slug, module_name)
                self.assertEqual(result.strategy_id, strategy.strategy_id)
                self.assertEqual(result.strategy_version, strategy.version)
                self.assertTrue(strategy.family)
                self.assertTrue(strategy.configuration.enabled)
                self.assertTrue(strategy.minimum_data_requirements)
                self.assertTrue(strategy.performance_history_identifier)
                self.assertTrue(strategy.backtest_diagnostic_identifier)

    def test_primary_registry_outputs_are_versioned(self) -> None:
        for voter in WCA_PRIMARY_VOTERS:
            for label, snapshot, _expected_status, _expected_side in STRATEGY_CASES[voter.definition.slug]:
                with self.subTest(strategy=voter.definition.slug, case=label):
                    result = voter.evaluate(snapshot, StrategyConfig())
                    self.assertEqual(result.strategy_version, voter.version)

    def test_removing_one_strategy_from_registry_does_not_change_another_raw_output(self) -> None:
        module = importlib.import_module(f"{STRATEGY_PACKAGE}.moving_average_trend")
        strategy = module.MovingAverageTrendStrategy()
        snapshot = STRATEGY_CASES["moving_average_trend"][0][1]
        baseline = strategy.evaluate(snapshot, StrategyConfig()).deterministic_json()

        from backend.app.algorithms.wca import strategy_registry

        reduced_registry = tuple(row for row in strategy_registry.WCA_STRATEGY_REGISTRY if row.slug != "trend_pullback")
        with patch.object(strategy_registry, "WCA_STRATEGY_REGISTRY", reduced_registry):
            after_removal = strategy.evaluate(snapshot, StrategyConfig()).deterministic_json()

        self.assertEqual(after_removal, baseline)

    def test_strategy_files_have_no_module_level_mutable_collections(self) -> None:
        violations: list[str] = []
        for path in sorted(STRATEGY_PATH.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in tree.body:
                value = None
                if isinstance(node, ast.Assign):
                    value = node.value
                elif isinstance(node, ast.AnnAssign):
                    value = node.value
                if isinstance(value, (ast.Dict, ast.List, ast.Set)):
                    violations.append(f"{path.name}:{node.lineno}")

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
