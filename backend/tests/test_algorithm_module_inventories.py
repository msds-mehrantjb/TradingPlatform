from __future__ import annotations

import unittest
from dataclasses import asdict

from backend.app.algorithms.meta_strategy.strategy_registry import (
    CONTEXT_STRATEGIES,
    DIRECTIONAL_STRATEGIES,
    META_STRATEGY_MODULE_INVENTORY,
    REGIME_STRATEGIES,
    SAFETY_STRATEGIES,
)
from backend.app.algorithms.regime.strategy_registry import (
    REGIME_MODULE_INVENTORY,
    REGIME_STRATEGY_DEFINITIONS,
    regime_strategy_inventory,
)
from backend.app.algorithms.wca.strategy_registry import (
    WCA_HARD_FILTER_REGISTRY,
    WCA_MODIFIER_REGISTRY,
    WCA_MODULE_INVENTORY,
    WCA_STRATEGY_REGISTRY,
    wca_module_inventory,
)
from backend.app.algorithms.weighted_voting.catalog import (
    WEIGHTED_VOTING_MODULE_INVENTORY,
    weighted_voting_dedicated_strategy_inventory,
    weighted_voting_module_inventory,
)


class AlgorithmModuleInventoriesTest(unittest.TestCase):
    maxDiff = None

    def test_meta_strategy_inventory_is_derived_from_strategy_registry(self) -> None:
        self.assertEqual(META_STRATEGY_MODULE_INVENTORY.algorithm_id, "meta_strategy")
        self.assertEqual(module_pairs(META_STRATEGY_MODULE_INVENTORY.directional), meta_pairs(DIRECTIONAL_STRATEGIES))
        self.assertEqual(module_pairs(META_STRATEGY_MODULE_INVENTORY.context), meta_pairs(CONTEXT_STRATEGIES))
        self.assertEqual(module_pairs(META_STRATEGY_MODULE_INVENTORY.regime), meta_pairs(REGIME_STRATEGIES))
        self.assertEqual(module_pairs(META_STRATEGY_MODULE_INVENTORY.safety), meta_pairs(SAFETY_STRATEGIES))
        assert_unique(self, module_ids(META_STRATEGY_MODULE_INVENTORY))

    def test_regime_inventory_is_derived_from_strategy_definitions(self) -> None:
        self.assertEqual(REGIME_MODULE_INVENTORY.algorithm_id, "regime")
        self.assertEqual(module_pairs(REGIME_MODULE_INVENTORY.directional), regime_pairs("directional"))
        self.assertEqual(module_pairs(REGIME_MODULE_INVENTORY.context), regime_pairs("confirmation"))
        self.assertEqual(module_pairs(REGIME_MODULE_INVENTORY.regime), regime_pairs("regime_context"))
        self.assertEqual(module_pairs(REGIME_MODULE_INVENTORY.safety), regime_pairs("safety_gate"))
        self.assertEqual(regime_strategy_inventory()["moduleInventory"], asdict(REGIME_MODULE_INVENTORY))
        assert_unique(self, module_ids(REGIME_MODULE_INVENTORY))

    def test_wca_inventory_is_derived_from_strategy_registry(self) -> None:
        self.assertEqual(WCA_MODULE_INVENTORY.algorithm_id, "wca")
        self.assertEqual(wca_module_inventory(), WCA_MODULE_INVENTORY)
        self.assertEqual(module_pairs(WCA_MODULE_INVENTORY.primary_voters), tuple((entry.slug, "active") for entry in WCA_STRATEGY_REGISTRY))
        self.assertEqual(module_pairs(WCA_MODULE_INVENTORY.modifiers), tuple((entry.slug, "active") for entry in WCA_MODIFIER_REGISTRY))
        self.assertEqual(module_pairs(WCA_MODULE_INVENTORY.hard_filters), tuple((entry.slug, "active") for entry in WCA_HARD_FILTER_REGISTRY))
        assert_unique(
            self,
            (
                *[module.id for module in WCA_MODULE_INVENTORY.primary_voters],
                *[module.id for module in WCA_MODULE_INVENTORY.modifiers],
                *[module.id for module in WCA_MODULE_INVENTORY.hard_filters],
            ),
        )

    def test_weighted_voting_inventory_is_derived_from_dedicated_strategy_inventory(self) -> None:
        dedicated = weighted_voting_dedicated_strategy_inventory()

        self.assertEqual(WEIGHTED_VOTING_MODULE_INVENTORY.algorithm_id, "weighted_voting")
        self.assertEqual(weighted_voting_module_inventory(), WEIGHTED_VOTING_MODULE_INVENTORY)
        self.assertEqual(
            module_pairs(WEIGHTED_VOTING_MODULE_INVENTORY.directional),
            tuple((item.strategy_id, "active" if item.enabled else "shadow") for item in dedicated),
        )
        self.assertEqual(WEIGHTED_VOTING_MODULE_INVENTORY.context, ())
        self.assertEqual(WEIGHTED_VOTING_MODULE_INVENTORY.regime, ())
        self.assertEqual(WEIGHTED_VOTING_MODULE_INVENTORY.safety, ())
        assert_unique(self, module_ids(WEIGHTED_VOTING_MODULE_INVENTORY))


def meta_pairs(entries) -> tuple[tuple[str, str], ...]:
    return tuple((entry.strategy_id, "active" if entry.enabled else "shadow") for entry in entries)


def regime_pairs(role: str) -> tuple[tuple[str, str], ...]:
    return tuple((entry.strategy_id, "active") for entry in REGIME_STRATEGY_DEFINITIONS if entry.role == role)


def module_pairs(modules) -> tuple[tuple[str, str], ...]:
    return tuple((module.id, module.status) for module in modules)


def module_ids(inventory) -> tuple[str, ...]:
    return tuple(
        module.id
        for collection in (inventory.directional, inventory.context, inventory.regime, inventory.safety)
        for module in collection
    )


def assert_unique(test: unittest.TestCase, values: tuple[str, ...]) -> None:
    test.assertEqual(len(values), len(set(values)))


if __name__ == "__main__":
    unittest.main()
