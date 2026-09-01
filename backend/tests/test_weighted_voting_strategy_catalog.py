from __future__ import annotations

import importlib
import inspect
import pkgutil
import unittest
from collections import Counter
from dataclasses import replace

from backend.app.algorithms.weighted_voting.catalog import (
    WEIGHTED_VOTING_BASELINE_STRATEGY_WEIGHT,
    WEIGHTED_VOTING_CATALOG_VERSION,
    WEIGHTED_VOTING_MAXIMUM_STRATEGY_WEIGHT,
    WEIGHTED_VOTING_MINIMUM_STRATEGY_WEIGHT,
    WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS,
    WEIGHTED_VOTING_SHADOW_STRATEGY_IDS,
    WEIGHTED_VOTING_STRATEGY_CATALOG,
    weighted_voting_active_strategy_catalog,
    weighted_voting_dedicated_strategy_inventory,
    weighted_voting_enabled_strategy_catalog,
)
from backend.app.algorithms.weighted_voting.catalog import _equal_voting_share
from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.models import WeightedStrategyFamily
from backend.app.algorithms.weighted_voting.rollout import WeightedVotingSmallAllocationGuardrails
from backend.app.algorithms.weighted_voting.weight_engine import (
    WEIGHTED_VOTING_ACTIVE_VOTER_IDS,
    WEIGHTED_VOTING_BASELINE_WEIGHTS,
)
from backend.app.algorithms.weighted_voting.signal_engine import WEIGHTED_VOTING_STRATEGY_CLASS_BY_ID
from backend.app.algorithms.weighted_voting.strategies.base import WeightedVotingStrategyBase


STRATEGY_PACKAGE = "backend.app.algorithms.weighted_voting.strategies"


def _roster_views(entries) -> tuple[tuple[str, ...], tuple[str, ...], float]:
    """Recompute the active ids, shadow ids and equal share from a catalogue."""
    return (
        tuple(entry.strategy_id for entry in entries if entry.contributes_to_vote),
        tuple(entry.strategy_id for entry in entries if entry.shadow_records_only),
        _equal_voting_share(entries),
    )


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
# Only these four carry voting weight. A strategy reaches "active" through the
# lifecycle evidence gate, never by being edited into this map.
EXPECTED_LIFECYCLES = {
    "S1": "shadow",
    "S2": "active",
    "S3": "shadow",
    "S4": "shadow",
    "S5": "active",
    "S6": "active",
    "S7": "active",
    "S8": "shadow",
}


class WeightedVotingStrategyCatalogTest(unittest.TestCase):
    def test_catalog_contains_every_implemented_strategy(self) -> None:
        actual = tuple((entry.strategy_id, entry.name, entry.family, entry.module_name) for entry in WEIGHTED_VOTING_STRATEGY_CATALOG)

        self.assertEqual(actual, EXPECTED_STRATEGIES)
        self.assertEqual(WEIGHTED_VOTING_CATALOG_VERSION, "weighted_voting_catalog_v3")

    def test_every_strategy_module_on_disk_is_registered_in_the_catalog(self) -> None:
        """A module the catalogue does not name never runs and never appears in any inventory.

        The signal engine only checks catalogue against its own registrations, so a strategy
        missing from both is invisible to every other guard. This is the check that catches it.
        """
        package = importlib.import_module(STRATEGY_PACKAGE)
        implemented = {}
        for module_info in pkgutil.iter_modules(package.__path__):
            if module_info.name in {"base", "common"}:
                continue
            module = importlib.import_module(f"{STRATEGY_PACKAGE}.{module_info.name}")
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, WeightedVotingStrategyBase) and obj is not WeightedVotingStrategyBase and obj.__module__ == module.__name__:
                    implemented[obj.strategy_id] = module_info.name

        self.assertEqual(
            implemented,
            {entry.strategy_id: entry.module_name for entry in WEIGHTED_VOTING_STRATEGY_CATALOG},
        )
        self.assertEqual(set(WEIGHTED_VOTING_STRATEGY_CLASS_BY_ID), set(implemented))

    def test_declared_limits_are_the_limits_each_module_enforces(self) -> None:
        """The inventory advertises warm-up and session window, so they must be true.

        S2 previously declared a 20-candle warm-up while its module required 25, which
        made the published inventory and the readiness gate disagree with the strategy.
        Modules now read both from the catalogue, and this pins that down.
        """
        published = {item.strategy_id: item for item in weighted_voting_dedicated_strategy_inventory()}
        for entry in WEIGHTED_VOTING_STRATEGY_CATALOG:
            with self.subTest(strategy_id=entry.strategy_id):
                strategy = WEIGHTED_VOTING_STRATEGY_CLASS_BY_ID[entry.strategy_id]()

                self.assertEqual(strategy.minimum_warmup, entry.minimum_warmup)
                self.assertEqual(strategy.session_window, entry.session_window_bounds)
                self.assertIn(entry.session_window_bounds[0], entry.valid_session_window)
                self.assertIn(entry.session_window_bounds[1], entry.valid_session_window)
                self.assertIn(str(entry.minimum_warmup), published[entry.strategy_id].required_candle_history)

    def test_every_roster_view_is_derived_from_lifecycle_not_restated(self) -> None:
        """Lifecycle is the only place a strategy's voting status is written down.

        Four separate copies of the roster used to exist. Each one that drifts breaks a
        different half of a promotion, and all of them fail silently.
        """
        derived_active, derived_shadow, derived_share = _roster_views(WEIGHTED_VOTING_STRATEGY_CATALOG)

        self.assertEqual(WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS, derived_active)
        self.assertEqual(WEIGHTED_VOTING_SHADOW_STRATEGY_IDS, derived_shadow)
        self.assertEqual(WEIGHTED_VOTING_BASELINE_STRATEGY_WEIGHT, derived_share)
        self.assertEqual(WEIGHTED_VOTING_ACTIVE_VOTER_IDS, WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS)
        self.assertEqual(
            WeightedVotingSmallAllocationGuardrails().approved_active_strategy_ids,
            WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS,
        )
        self.assertEqual(WeightedVotingConfig().equal_seed_weight, WEIGHTED_VOTING_BASELINE_STRATEGY_WEIGHT)
        self.assertEqual(
            WEIGHTED_VOTING_BASELINE_WEIGHTS,
            {
                entry.strategy_id: (WEIGHTED_VOTING_BASELINE_STRATEGY_WEIGHT if entry.contributes_to_vote else 0.0)
                for entry in WEIGHTED_VOTING_STRATEGY_CATALOG
            },
        )

    def test_promoting_a_shadow_strategy_keeps_every_roster_view_consistent(self) -> None:
        """The failure this guards against: a promotion that only half lands.

        Flipping a lifecycle to active used to make the signal engine treat the strategy
        as a voter while the weight engine, still reading its own hardcoded roster, gave
        it zero weight -- so it looked promoted and voted with nothing.
        """
        promoted = tuple(
                replace(entry, lifecycle="active") if entry.strategy_id == "S1" else entry
            for entry in WEIGHTED_VOTING_STRATEGY_CATALOG
        )
        active, shadow, share = _roster_views(promoted)

        self.assertEqual(active, ("S1", "S2", "S5", "S6", "S7"))
        self.assertNotIn("S1", shadow)
        self.assertAlmostEqual(share, 0.2, places=10)
        # The baselines stay a distribution over whoever votes, for any roster size.
        self.assertAlmostEqual(share * len(active), 1.0, places=10)

    def test_families_match_weighted_voting_mission(self) -> None:
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

    def test_every_family_is_represented_among_the_catalogued_strategies(self) -> None:
        """Breakout coverage exists in the catalogue even while it is still shadow-only."""
        self.assertEqual(
            {entry.family for entry in WEIGHTED_VOTING_STRATEGY_CATALOG},
            set(WeightedStrategyFamily),
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
                self.assertEqual(entry.lifecycle, EXPECTED_LIFECYCLES[entry.strategy_id])
                self.assertTrue(entry.lifecycle_reason)
                self.assertTrue(entry.enabled)
                self.assertTrue(entry.executes)
                self.assertEqual(entry.contributes_to_vote, entry.strategy_id in WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS)
                self.assertEqual(entry.shadow_records_only, entry.lifecycle == "shadow")
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

    def test_catalog_is_authoritative_for_enabled_and_active_strategies(self) -> None:
        enabled = weighted_voting_enabled_strategy_catalog()
        active = weighted_voting_active_strategy_catalog()

        self.assertEqual(enabled, WEIGHTED_VOTING_STRATEGY_CATALOG)
        self.assertEqual(tuple(entry.strategy_id for entry in active), WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS)
        self.assertEqual(tuple(entry.strategy_id for entry in enabled if entry.shadow_records_only), WEIGHTED_VOTING_SHADOW_STRATEGY_IDS)
        self.assertEqual({entry.minimum_weight for entry in enabled}, {WEIGHTED_VOTING_MINIMUM_STRATEGY_WEIGHT})
        self.assertEqual({entry.maximum_weight for entry in enabled}, {WEIGHTED_VOTING_MAXIMUM_STRATEGY_WEIGHT})
        self.assertEqual(len({entry.strategy_id for entry in enabled}), 8)
        self.assertEqual(len({entry.display_name for entry in enabled}), 8)
        # The distribution is over the voters; a shadow strategy holds a catalogue baseline
        # but is published and weighted at zero until it is promoted.
        self.assertAlmostEqual(sum(entry.baseline_weight for entry in active), 1.0, places=10)

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
            "lifecycle",
            "lifecycle_reason",
            "shadow_performance_state",
            "signal_correlation_state",
            "return_correlation_state",
        )

        for item in weighted_voting_dedicated_strategy_inventory():
            with self.subTest(strategy_id=item.strategy_id):
                for field_name in required_fields:
                    value = getattr(item, field_name)
                    self.assertTrue(value, field_name)

                self.assertGreaterEqual(len(item.required_indicators), 3)
                self.assertTrue(item.enabled)
                self.assertTrue(item.executes)
                self.assertEqual(item.lifecycle, EXPECTED_LIFECYCLES[item.strategy_id])
                self.assertEqual(
                    item.voting_influence,
                    WEIGHTED_VOTING_BASELINE_STRATEGY_WEIGHT if item.lifecycle == "active" else 0.0,
                )
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
                self.assertIn(item.strategy_id, item.shadow_performance_state)
                self.assertIn(item.strategy_id, item.signal_correlation_state)
                self.assertIn(item.strategy_id, item.return_correlation_state)
                self.assertIn("Weighted Voting", item.explanation)

    def test_catalog_contains_no_duplicate_ensemble_or_context_voters(self) -> None:
        names = tuple(entry.name.lower() for entry in WEIGHTED_VOTING_STRATEGY_CATALOG)

        self.assertFalse(any("ensemble" in name for name in names))
        self.assertFalse(any("adx" == name or "atr regime" == name or "spread quality" == name for name in names))


if __name__ == "__main__":
    unittest.main()
