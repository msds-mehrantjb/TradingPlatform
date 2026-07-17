from __future__ import annotations

import importlib
import inspect
import unittest
from collections import Counter

from backend.app.algorithms.weighted_voting.catalog import (
    WEIGHTED_VOTING_BASELINE_STRATEGY_WEIGHT,
    WEIGHTED_VOTING_CATALOG_VERSION,
    WEIGHTED_VOTING_MAXIMUM_STRATEGY_WEIGHT,
    WEIGHTED_VOTING_MINIMUM_STRATEGY_WEIGHT,
    WEIGHTED_VOTING_STRATEGY_CATALOG,
    weighted_voting_dedicated_strategy_inventory,
    weighted_voting_enabled_strategy_catalog,
)
from backend.app.algorithms.weighted_voting.models import WeightedStrategyFamily
from backend.app.algorithms.weighted_voting.strategies.base import WeightedVotingStrategyBase


EXPECTED_STRATEGIES = (
    ("S1", "Opening Range Breakout", WeightedStrategyFamily.BREAKOUT, "opening_range_breakout"),
    ("S2", "First Pullback After Open", WeightedStrategyFamily.TREND, "first_pullback_after_open"),
    ("S3", "VWAP Trend Continuation", WeightedStrategyFamily.TREND, "vwap_trend_continuation"),
    ("S4", "VWAP Mean Reversion", WeightedStrategyFamily.MEAN_REVERSION, "vwap_mean_reversion"),
    ("S5", "Failed Breakout Reversal", WeightedStrategyFamily.REVERSAL, "failed_breakout_reversal"),
    ("S6", "Liquidity Sweep Reversal", WeightedStrategyFamily.REVERSAL, "liquidity_sweep_reversal"),
    ("S7", "Bollinger/ATR Reversion", WeightedStrategyFamily.MEAN_REVERSION, "bollinger_atr_reversion"),
    ("S8", "Volatility Breakout", WeightedStrategyFamily.BREAKOUT, "volatility_breakout"),
)


class WeightedVotingStrategyCatalogTest(unittest.TestCase):
    def test_catalog_contains_exactly_the_authoritative_eight_strategies(self) -> None:
        actual = tuple((entry.strategy_id, entry.name, entry.family, entry.module_name) for entry in WEIGHTED_VOTING_STRATEGY_CATALOG)

        self.assertEqual(actual, EXPECTED_STRATEGIES)
        self.assertEqual(WEIGHTED_VOTING_CATALOG_VERSION, "weighted_voting_catalog_v2")

    def test_families_are_balanced_two_per_family(self) -> None:
        counts = Counter(entry.family for entry in WEIGHTED_VOTING_STRATEGY_CATALOG)

        self.assertEqual(
            counts,
            {
                WeightedStrategyFamily.BREAKOUT: 2,
                WeightedStrategyFamily.TREND: 2,
                WeightedStrategyFamily.MEAN_REVERSION: 2,
                WeightedStrategyFamily.REVERSAL: 2,
            },
        )

    def test_each_strategy_has_complete_unique_rule_metadata(self) -> None:
        purposes = [entry.purpose for entry in WEIGHTED_VOTING_STRATEGY_CATALOG]
        self.assertEqual(len(set(purposes)), len(purposes))

        for entry in WEIGHTED_VOTING_STRATEGY_CATALOG:
            with self.subTest(strategy_id=entry.strategy_id):
                self.assertTrue(entry.purpose)
                self.assertGreaterEqual(len(entry.required_data), 3)
                self.assertIsInstance(entry.optional_data, tuple)
                self.assertTrue(entry.valid_session_window)
                self.assertGreater(entry.minimum_warmup, 0)
                self.assertGreaterEqual(len(entry.invalid_market_conditions), 2)
                self.assertTrue(entry.buy_rule.startswith("Buy when"))
                self.assertTrue(entry.sell_rule.startswith("Sell when"))
                self.assertTrue(entry.hold_rule.startswith("Hold"))
                self.assertGreaterEqual(len(entry.confidence_components), 3)
                self.assertTrue(entry.invalidation_condition.startswith("Invalidate"))
                self.assertTrue(entry.data_quality_classification.startswith("requires"))
                self.assertEqual(entry.version, f"weighted_strategy_{entry.strategy_id}_v1")
                self.assertTrue(entry.enabled)
                self.assertEqual(entry.display_name, entry.name)
                self.assertEqual(entry.baseline_weight, WEIGHTED_VOTING_BASELINE_STRATEGY_WEIGHT)
                self.assertEqual(entry.minimum_weight, WEIGHTED_VOTING_MINIMUM_STRATEGY_WEIGHT)
                self.assertEqual(entry.maximum_weight, WEIGHTED_VOTING_MAXIMUM_STRATEGY_WEIGHT)
                self.assertEqual(entry.eligible_sessions, (entry.valid_session_window,))
                self.assertTrue(entry.eligible_market_conditions)
                self.assertTrue(entry.long_allowed)
                self.assertTrue(entry.short_allowed)
                self.assertEqual(entry.strategy_implementation_version, entry.version)
                self.assertEqual(entry.dedicated_file, f"backend/app/algorithms/weighted_voting/strategies/{entry.module_name}.py")

    def test_catalog_is_authoritative_for_enabled_strategies_and_weights(self) -> None:
        enabled = weighted_voting_enabled_strategy_catalog()

        self.assertEqual(enabled, WEIGHTED_VOTING_STRATEGY_CATALOG)
        self.assertAlmostEqual(sum(entry.baseline_weight for entry in enabled), 1.0, places=10)
        self.assertEqual({entry.minimum_weight for entry in enabled}, {WEIGHTED_VOTING_MINIMUM_STRATEGY_WEIGHT})
        self.assertEqual({entry.maximum_weight for entry in enabled}, {WEIGHTED_VOTING_MAXIMUM_STRATEGY_WEIGHT})
        self.assertEqual(len({entry.strategy_id for entry in enabled}), 8)
        self.assertEqual(len({entry.display_name for entry in enabled}), 8)

    def test_strategy_modules_match_catalog_without_aliasing_other_algorithms(self) -> None:
        for entry in WEIGHTED_VOTING_STRATEGY_CATALOG:
            with self.subTest(strategy_id=entry.strategy_id):
                module = importlib.import_module(f"backend.app.algorithms.weighted_voting.strategies.{entry.module_name}")
                strategy_classes = [
                    obj
                    for _, obj in inspect.getmembers(module, inspect.isclass)
                    if issubclass(obj, WeightedVotingStrategyBase) and obj is not WeightedVotingStrategyBase
                ]

                self.assertEqual(len(strategy_classes), 1)
                strategy_class = strategy_classes[0]
                self.assertEqual(strategy_class.strategy_id, entry.strategy_id)
                self.assertEqual(strategy_class.name, entry.name)
                self.assertEqual(strategy_class.family, entry.family)
                self.assertTrue(strategy_class.__module__.startswith("backend.app.algorithms.weighted_voting.strategies."))

    def test_dedicated_strategy_inventory_owns_separate_implementations(self) -> None:
        inventory = weighted_voting_dedicated_strategy_inventory()

        self.assertEqual(
            tuple((item.strategy_id, item.name, item.family, item.module_name) for item in inventory),
            EXPECTED_STRATEGIES,
        )
        self.assertEqual(len({item.implementation_path for item in inventory}), 8)
        self.assertEqual(len({item.implementation_module for item in inventory}), 8)

        for item in inventory:
            with self.subTest(strategy_id=item.strategy_id):
                module = importlib.import_module(item.implementation_module)
                strategy_class = getattr(module, item.class_name)

                self.assertTrue(item.implementation_path.startswith("backend/app/algorithms/weighted_voting/strategies/"))
                self.assertTrue(item.implementation_path.endswith(".py"))
                self.assertTrue(issubclass(strategy_class, WeightedVotingStrategyBase))
                self.assertEqual(strategy_class.strategy_id, item.strategy_id)
                self.assertEqual(strategy_class.name, item.name)
                self.assertEqual(strategy_class.family, item.family)
                self.assertEqual(strategy_class.__module__, item.implementation_module)

    def test_dedicated_strategy_inventory_declares_full_owned_behavior_surface(self) -> None:
        required_fields = (
            "required_indicators",
            "required_data",
            "required_candle_history",
            "data_readiness_checks",
            "market_condition_permissions",
            "eligible_sessions",
            "eligible_market_conditions",
            "entry_conditions",
            "buy_conditions",
            "sell_conditions",
            "hold_conditions",
            "confidence_calculation",
            "expected_return_estimate",
            "invalidation_level",
            "stop_reference",
            "target_reference",
            "reason_codes",
            "explanation",
            "performance_history",
            "state_namespace",
            "dedicated_file",
        )

        for item in weighted_voting_dedicated_strategy_inventory():
            with self.subTest(strategy_id=item.strategy_id):
                for field_name in required_fields:
                    value = getattr(item, field_name)
                    self.assertTrue(value, field_name)

                self.assertGreaterEqual(len(item.required_indicators), 3)
                self.assertTrue(item.enabled)
                self.assertEqual(item.display_name, item.name)
                self.assertEqual(item.baseline_weight, WEIGHTED_VOTING_BASELINE_STRATEGY_WEIGHT)
                self.assertEqual(item.minimum_weight, WEIGHTED_VOTING_MINIMUM_STRATEGY_WEIGHT)
                self.assertEqual(item.maximum_weight, WEIGHTED_VOTING_MAXIMUM_STRATEGY_WEIGHT)
                self.assertTrue(item.long_allowed)
                self.assertTrue(item.short_allowed)
                self.assertIn("completed 1-minute candles", item.required_candle_history)
                self.assertTrue(all(code.startswith("weighted_voting.") for code in item.reason_codes))
                self.assertEqual(item.dedicated_file, item.implementation_path)
                self.assertIn(item.strategy_id, item.state_namespace)
                self.assertIn(item.strategy_id, item.performance_history)
                self.assertIn("Weighted Voting", item.explanation)


if __name__ == "__main__":
    unittest.main()
