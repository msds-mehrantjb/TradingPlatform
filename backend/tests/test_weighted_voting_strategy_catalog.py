from __future__ import annotations

import importlib
import inspect
import unittest
from collections import Counter

from backend.app.algorithms.weighted_voting.catalog import (
    WEIGHTED_VOTING_CATALOG_VERSION,
    WEIGHTED_VOTING_STRATEGY_CATALOG,
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


if __name__ == "__main__":
    unittest.main()
