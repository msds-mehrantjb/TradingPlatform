from __future__ import annotations

import unittest

from backend.app.algorithms.voting_ensemble.strategies.registry import (
    VOTING_ENSEMBLE_CONTEXT_STRATEGIES,
    VOTING_ENSEMBLE_DIRECTIONAL_STRATEGIES,
    VOTING_ENSEMBLE_MODULE_INVENTORY,
    VOTING_ENSEMBLE_REGIME_STRATEGIES,
    VOTING_ENSEMBLE_SAFETY_STRATEGIES,
    VOTING_ENSEMBLE_STRATEGIES,
    StrategyCollection,
    active_module_ids,
    directional_strategy_input_ids,
    resolve_strategy,
    shadow_module_ids,
)
from backend.app.algorithms.voting_ensemble.service import VotingEnsembleService


class VotingEnsembleModuleInventoryTest(unittest.TestCase):
    maxDiff = None

    def test_authoritative_inventory_matches_requested_active_shadow_split(self) -> None:
        self.assertEqual(
            module_pairs(VOTING_ENSEMBLE_MODULE_INVENTORY.directional),
            (
                ("multi_timeframe_trend_alignment", "active"),
                ("first_pullback_after_open", "active"),
                ("failed_breakout_reversal", "active"),
                ("liquidity_sweep_reversal", "active"),
                ("bollinger_atr_reversion", "active"),
            ),
        )
        self.assertEqual(
            module_pairs(VOTING_ENSEMBLE_MODULE_INVENTORY.context),
            (
                ("relative_strength_qqq_iwm", "active"),
                ("market_breadth_momentum", "active"),
            ),
        )
        self.assertEqual(
            module_pairs(VOTING_ENSEMBLE_MODULE_INVENTORY.regime),
            (
                ("adx_atr_regime_classifier", "active"),
            ),
        )
        self.assertEqual(module_pairs(VOTING_ENSEMBLE_MODULE_INVENTORY.safety), (("cash_avoid_trading_filter", "active"),))

    def test_registry_is_derived_from_inventory_statuses(self) -> None:
        inventory_ids = {
            module.id: module.status
            for collection in (
                VOTING_ENSEMBLE_MODULE_INVENTORY.directional,
                VOTING_ENSEMBLE_MODULE_INVENTORY.context,
                VOTING_ENSEMBLE_MODULE_INVENTORY.regime,
                VOTING_ENSEMBLE_MODULE_INVENTORY.safety,
            )
            for module in collection
        }
        registry_entries = (
            *VOTING_ENSEMBLE_DIRECTIONAL_STRATEGIES,
            *VOTING_ENSEMBLE_CONTEXT_STRATEGIES,
            *VOTING_ENSEMBLE_REGIME_STRATEGIES,
            *VOTING_ENSEMBLE_SAFETY_STRATEGIES,
        )

        self.assertEqual({entry.strategyId for entry in registry_entries}, set(inventory_ids))
        for entry in registry_entries:
            with self.subTest(strategy=entry.strategyId):
                self.assertEqual(entry.status, inventory_ids[entry.strategyId])
                self.assertEqual(entry.enabled, entry.status == "active")

    def test_runtime_directional_inputs_are_active_only(self) -> None:
        self.assertEqual(
            directional_strategy_input_ids(),
            (
                "multi_timeframe_trend_alignment",
                "first_pullback_after_open",
                "failed_breakout_reversal",
                "liquidity_sweep_reversal",
                "bollinger_atr_reversion",
            ),
        )
        self.assertEqual(
            shadow_module_ids(StrategyCollection.DIRECTIONAL),
            (),
        )

    def test_removed_modules_are_not_registered_as_runtime_inventory_rows(self) -> None:
        self.assertEqual(active_module_ids(StrategyCollection.CONTEXT), ("relative_strength_qqq_iwm", "market_breadth_momentum"))
        self.assertEqual(shadow_module_ids(StrategyCollection.CONTEXT), ())

        with self.assertRaises(KeyError):
            resolve_strategy("VWAP Trend Continuation")

        self.assertEqual(len({entry.strategyId for entry in VOTING_ENSEMBLE_STRATEGIES}), len(VOTING_ENSEMBLE_STRATEGIES))

    def test_service_status_exposes_inventory_and_active_runtime_modules(self) -> None:
        status = VotingEnsembleService().status()

        self.assertEqual(status["moduleInventory"], VOTING_ENSEMBLE_MODULE_INVENTORY.model_dump(mode="json"))
        self.assertEqual(
            status["directionalStrategies"],
            [
                "multi_timeframe_trend",
                "first_pullback_after_open",
                "failed_breakout_strategy",
                "liquidity_sweep_reversal",
                "bollinger_band_reversion",
            ],
        )
        self.assertEqual(status["dynamicRoleStrategies"], [])
        self.assertEqual(status["contextSignals"], ["relative_strength", "market_breadth"])


def module_pairs(modules) -> tuple[tuple[str, str], ...]:
    return tuple((module.id, module.status) for module in modules)


if __name__ == "__main__":
    unittest.main()
