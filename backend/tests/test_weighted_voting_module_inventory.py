from __future__ import annotations

import unittest

from backend.app.algorithms.weighted_voting.catalog import (
    WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS,
    WEIGHTED_VOTING_SHADOW_STRATEGY_IDS,
    WEIGHTED_VOTING_STRATEGY_CATALOG,
)
from backend.app.algorithms.weighted_voting.decision_gates import (
    WEIGHTED_VOTING_LOCAL_GATE_IDS,
    WEIGHTED_VOTING_LOCAL_GATE_INVENTORY,
)
from backend.app.algorithms.weighted_voting.module_inventory import (
    WEIGHTED_VOTING_SAFETY_MODULES,
    weighted_voting_full_inventory,
    weighted_voting_module_groups,
    weighted_voting_strategy_counts,
)


class WeightedVotingGateInventoryTest(unittest.TestCase):
    def test_declared_gate_inventory_matches_the_gates_the_pipeline_runs(self) -> None:
        """The declared list is only trustworthy if it is what actually executes.

        Building a neutral gate input means building a whole decision, so the inventory is
        declared rather than derived. This is the check that stops it drifting: it reads the
        gate ids straight out of the pipeline source and compares them, in order.
        """
        import ast
        import inspect

        from backend.app.algorithms.weighted_voting import decision_gates

        tree = ast.parse(inspect.getsource(decision_gates))
        emitted: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "_gate" and len(call.args) >= 2:
                    first, second = call.args[0], call.args[1]
                    if isinstance(first, ast.Constant) and isinstance(second, ast.Constant):
                        emitted.setdefault(node.name, first.value)

        order: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(getattr(target, "id", "") == "mandatory_gates" for target in node.targets):
                for element in getattr(node.value, "elts", []):
                    name = getattr(element.func, "id", None) if isinstance(element, ast.Call) else None
                    if name:
                        order.append(name)

        pipeline_ids = tuple(emitted[name] for name in order) + (emitted["_final_local_acceptance"],)

        self.assertEqual(WEIGHTED_VOTING_LOCAL_GATE_IDS, pipeline_ids)
        self.assertEqual(len(set(WEIGHTED_VOTING_LOCAL_GATE_IDS)), len(WEIGHTED_VOTING_LOCAL_GATE_IDS))

    def test_every_gate_is_reported_as_a_blocking_safety_module(self) -> None:
        self.assertTrue(WEIGHTED_VOTING_LOCAL_GATE_INVENTORY)
        for module in WEIGHTED_VOTING_LOCAL_GATE_INVENTORY:
            with self.subTest(gate_id=module.gate_id):
                self.assertTrue(module.gate_name)
                self.assertTrue(module.blocks_order)


class WeightedVotingFullInventoryTest(unittest.TestCase):
    def test_inventory_reports_every_role_the_algorithm_actually_runs(self) -> None:
        """Safety, regime and aggregator were hardcoded empty; they are real modules."""
        groups = weighted_voting_module_groups()

        self.assertEqual(len(groups["directional"]), len(WEIGHTED_VOTING_STRATEGY_CATALOG))
        self.assertEqual(len(groups["safety"]), len(WEIGHTED_VOTING_LOCAL_GATE_IDS))
        self.assertEqual([module["id"] for module in groups["safety"]], list(WEIGHTED_VOTING_LOCAL_GATE_IDS))
        self.assertEqual([module["id"] for module in groups["regime"]], ["market_condition_classifier"])
        self.assertEqual([module["id"] for module in groups["aggregator"]], ["weighted_signal_aggregator"])

    def test_context_is_empty_because_the_algorithm_has_no_context_voters(self) -> None:
        """Reported as an empty group, not omitted, so "none" is distinguishable from "unpublished"."""
        groups = weighted_voting_module_groups()

        self.assertIn("context", groups)
        self.assertEqual(groups["context"], [])

    def test_only_directional_strategies_can_carry_voting_influence(self) -> None:
        groups = weighted_voting_module_groups()

        for collection in ("regime", "safety", "aggregator"):
            for module in groups[collection]:
                with self.subTest(collection=collection, module=module["id"]):
                    self.assertEqual(module["votingInfluence"], 0.0)

        voting = sum(module["votingInfluence"] for module in groups["directional"])
        self.assertAlmostEqual(voting, 1.0, places=10)

    def test_module_ids_are_unique_across_every_group(self) -> None:
        groups = weighted_voting_module_groups()
        ids = [module["id"] for collection in groups.values() for module in collection]

        self.assertEqual(len(ids), len(set(ids)))

    def test_counts_follow_the_lifecycle_roster(self) -> None:
        counts = weighted_voting_strategy_counts()

        self.assertEqual(counts["directional"], len(WEIGHTED_VOTING_STRATEGY_CATALOG))
        self.assertEqual(counts["active"], len(WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS))
        self.assertEqual(counts["shadow"], len(WEIGHTED_VOTING_SHADOW_STRATEGY_IDS))
        self.assertEqual(counts["safety"], len(WEIGHTED_VOTING_SAFETY_MODULES))

    def test_payload_declares_its_contract_and_isolated_namespaces(self) -> None:
        inventory = weighted_voting_full_inventory()

        self.assertEqual(inventory["algorithmId"], "weighted_voting")
        self.assertTrue(inventory["contractVersion"])
        self.assertTrue(inventory["inventoryVersion"])
        isolated = inventory["isolatedInventory"]
        self.assertTrue(isolated["algorithmOwnsInventory"])
        self.assertEqual(isolated["capitalPartitionId"], "weighted_voting.paper.default")
        for namespace in ("settingsNamespace", "stateNamespace", "persistenceNamespace"):
            with self.subTest(namespace=namespace):
                self.assertTrue(isolated[namespace].startswith("weighted_voting."))


if __name__ == "__main__":
    unittest.main()
