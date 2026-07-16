from __future__ import annotations

import unittest

from backend.app.domain.models import StrategyRole
from backend.app.strategies.registry import (
    AGGREGATOR_STRATEGIES,
    ALL_STRATEGIES,
    CONTEXT_STRATEGIES,
    DIRECTIONAL_STRATEGIES,
    REGIME_STRATEGIES,
    SAFETY_STRATEGIES,
    STRATEGY_ALIAS_MAP,
    StrategyCollection,
    canonical_strategy_id,
    directional_strategy_input_ids,
    directional_strategy_inputs,
    directional_voters_from,
    resolve_strategy,
    resolve_strategy_list,
)


EXPECTED_DIRECTIONAL_NAMES = [
    "Multi-Timeframe Trend Alignment",
    "First Pullback After Open",
    "VWAP Trend Continuation",
    "Opening Range Breakout",
    "Volatility Breakout",
    "Failed Breakout Reversal",
    "Liquidity Sweep Reversal",
    "VWAP Mean Reversion",
    "Bollinger/ATR Reversion",
    "Gap Continuation / Gap Fade",
]


class StrategyRegistryV2Test(unittest.TestCase):
    def test_registry_has_exactly_ten_initial_directional_strategies(self) -> None:
        self.assertEqual([entry.strategyName for entry in directional_strategy_inputs()], EXPECTED_DIRECTIONAL_NAMES)
        self.assertEqual(len(DIRECTIONAL_STRATEGIES), 10)
        self.assertEqual(len(set(directional_strategy_input_ids())), 10)

    def test_non_directional_modules_cannot_be_inserted_as_directional_voters(self) -> None:
        blocked_names = [
            CONTEXT_STRATEGIES[0].strategyName,
            REGIME_STRATEGIES[0].strategyName,
            SAFETY_STRATEGIES[0].strategyName,
            AGGREGATOR_STRATEGIES[0].strategyName,
        ]

        for name in blocked_names:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "not a directional voter"):
                    directional_voters_from([name])

    def test_alias_resolution_deduplicates_old_names(self) -> None:
        self.assertEqual(STRATEGY_ALIAS_MAP["Failed Breakout Strategy"], "failed_breakout_reversal")
        self.assertEqual(STRATEGY_ALIAS_MAP["Bollinger Band Reversion"], "bollinger_atr_reversion")
        self.assertEqual(STRATEGY_ALIAS_MAP["ATR Overextension Reversion"], "bollinger_atr_reversion")

        resolved = resolve_strategy_list(
            [
                "Bollinger Band Reversion",
                "ATR Overextension Reversion",
                "Bollinger/ATR Reversion",
                "bollinger_atr_reversion",
            ]
        )

        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].strategyId, "bollinger_atr_reversion")

        failed_breakout = resolve_strategy_list(["Failed Breakout Strategy", "Failed Breakout Reversal"])
        self.assertEqual(len(failed_breakout), 1)
        self.assertEqual(failed_breakout[0].strategyId, "failed_breakout_reversal")

    def test_ensemble_is_registered_only_as_aggregator_and_not_own_input(self) -> None:
        ensemble = resolve_strategy("Ensemble Strategy Voting")

        self.assertEqual(ensemble.role, StrategyRole.AGGREGATOR.value)
        self.assertEqual(ensemble.collection, StrategyCollection.AGGREGATOR.value)
        self.assertNotIn(ensemble.strategyId, directional_strategy_input_ids())
        self.assertNotIn(ensemble.strategyName, [entry.strategyName for entry in directional_strategy_inputs()])

    def test_all_strategy_ids_are_unique_and_aliases_point_to_existing_modules(self) -> None:
        ids = [entry.strategyId for entry in ALL_STRATEGIES]

        self.assertEqual(len(ids), len(set(ids)))
        for alias, target_id in STRATEGY_ALIAS_MAP.items():
            with self.subTest(alias=alias):
                self.assertEqual(canonical_strategy_id(alias), target_id)
                self.assertIn(target_id, ids)

    def test_every_registry_entry_declares_required_identity_and_inputs(self) -> None:
        for entry in ALL_STRATEGIES:
            with self.subTest(strategy=entry.strategyName):
                self.assertTrue(entry.strategyId)
                self.assertTrue(entry.strategyName)
                self.assertTrue(entry.strategyVersion)
                self.assertTrue(entry.family)
                self.assertTrue(entry.role)
                self.assertGreater(len(entry.requiredInputs), 0)
                self.assertIsInstance(entry.enabled, bool)


if __name__ == "__main__":
    unittest.main()
