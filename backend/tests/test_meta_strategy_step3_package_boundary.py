from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = ROOT / "backend" / "app" / "algorithms" / "meta_strategy"
REQUIRED_PACKAGE_FILES = (
    "__init__.py",
    "identity.py",
    "contracts.py",
    "configuration.py",
    "versions.py",
    "validation.py",
    "ownership.py",
)
BOUNDARY_MODULES = (
    "backend.app.algorithms.meta_strategy",
    "backend.app.algorithms.meta_strategy.identity",
    "backend.app.algorithms.meta_strategy.contracts",
    "backend.app.algorithms.meta_strategy.configuration",
    "backend.app.algorithms.meta_strategy.versions",
    "backend.app.algorithms.meta_strategy.validation",
    "backend.app.algorithms.meta_strategy.ownership",
)
SIBLING_ALGORITHM_IDS = {
    "wca": "backend.app.algorithms.wca.contracts.WCA_ALGORITHM_ID",
    "regime": "backend.app.algorithms.regime.contracts.REGIME_ALGORITHM_ID",
    "weighted_voting": "backend.app.algorithms.weighted_voting.identity.WEIGHTED_VOTING_ALGORITHM_ID",
    "voting_ensemble": "backend.app.algorithms.voting_ensemble.position_state.VOTING_ENSEMBLE_ALGORITHM_ID",
}
FORBIDDEN_SIBLING_IMPORT_PREFIXES = (
    "backend.app.algorithms.wca",
    "backend.app.algorithms.regime",
    "backend.app.algorithms.weighted_voting",
    "backend.app.algorithms.voting_ensemble",
)


class MetaStrategyStep3PackageBoundaryTest(unittest.TestCase):
    maxDiff = None

    def test_required_package_files_exist(self) -> None:
        self.assertTrue(PACKAGE_DIR.exists(), "Meta-Strategy package directory is missing.")
        for filename in REQUIRED_PACKAGE_FILES:
            with self.subTest(filename=filename):
                self.assertTrue((PACKAGE_DIR / filename).is_file())

    def test_package_and_boundary_modules_import_successfully(self) -> None:
        for module_name in BOUNDARY_MODULES:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                self.assertIsNotNone(module)

    def test_algorithm_identity_is_exported_from_package(self) -> None:
        package = importlib.import_module("backend.app.algorithms.meta_strategy")

        self.assertEqual(package.ALGORITHM_ID, "meta_strategy")
        self.assertEqual(package.ALGORITHM_NAME, "Meta-Strategy")
        self.assertEqual(package.META_STRATEGY_ALGORITHM_ID, package.ALGORITHM_ID)
        self.assertEqual(package.META_STRATEGY_ALGORITHM_NAME, package.ALGORITHM_NAME)

    def test_algorithm_id_is_unique_against_existing_algorithm_packages(self) -> None:
        from backend.app.algorithms.meta_strategy import ALGORITHM_ID, validate_algorithm_id_unique

        current_algorithm_ids = [ALGORITHM_ID, *resolved_sibling_algorithm_ids().values()]
        validation = validate_algorithm_id_unique(current_algorithm_ids)

        self.assertEqual(current_algorithm_ids.count(ALGORITHM_ID), 1)
        self.assertEqual(len(current_algorithm_ids), len(set(current_algorithm_ids)))
        self.assertTrue(validation["valid"])
        self.assertEqual(validation["duplicates"], [])

    def test_boundary_manifest_configuration_and_ownership_are_passive(self) -> None:
        from backend.app.algorithms.meta_strategy import (
            ALGORITHM_ID,
            meta_strategy_boundary_manifest,
            meta_strategy_configuration,
            meta_strategy_contract_inventory,
            meta_strategy_ownership_boundary,
            meta_strategy_service_boundary,
            validate_boundary_manifest,
            validate_meta_strategy_identity,
        )

        boundary = meta_strategy_service_boundary()
        manifest = meta_strategy_boundary_manifest()
        configuration = meta_strategy_configuration()
        baseline_config = configuration.baseline_configuration()
        ownership = meta_strategy_ownership_boundary()

        self.assertTrue(validate_meta_strategy_identity()["valid"])
        self.assertTrue(validate_boundary_manifest(manifest)["valid"])
        self.assertEqual(boundary.algorithm_id, ALGORITHM_ID)
        self.assertFalse(boundary.production_behavior_changed)
        self.assertFalse(manifest.productionBehaviorChanged)
        self.assertFalse(configuration.enabled)
        self.assertEqual(configuration.operating_mode, "OFF")
        self.assertFalse(configuration.owns_runtime_behavior)
        self.assertFalse(configuration.production_behavior_changed)
        self.assertFalse(baseline_config["enabled"])
        self.assertFalse(baseline_config["productionBehaviorChanged"])
        self.assertFalse(ownership["mayMutateForeignAlgorithmState"])
        self.assertFalse(ownership["mayReadSiblingPrivateState"])
        self.assertEqual(meta_strategy_contract_inventory()["algorithmId"], ALGORITHM_ID)

    def test_ownership_helpers_reject_foreign_algorithm_records(self) -> None:
        from backend.app.algorithms.meta_strategy import assert_meta_strategy_ownership, is_meta_strategy_owned

        self.assertTrue(is_meta_strategy_owned({"algorithmId": "meta_strategy"}))
        assert_meta_strategy_ownership({"algorithm_id": "meta_strategy"})

        self.assertFalse(is_meta_strategy_owned({"algorithmId": "weighted_voting"}))
        with self.assertRaisesRegex(ValueError, "cannot mutate records owned by weighted_voting"):
            assert_meta_strategy_ownership({"algorithmId": "weighted_voting"})

    def test_boundary_modules_do_not_import_sibling_algorithm_private_state(self) -> None:
        for filename in REQUIRED_PACKAGE_FILES:
            path = PACKAGE_DIR / filename
            imported_modules = imported_module_names(path)
            forbidden = sorted(
                module_name
                for module_name in imported_modules
                if module_name.startswith(FORBIDDEN_SIBLING_IMPORT_PREFIXES)
            )
            with self.subTest(filename=filename):
                self.assertEqual(forbidden, [])

    def test_production_api_imports_package_owned_meta_strategy_inference_after_cutover(self) -> None:
        api_v2 = (ROOT / "backend" / "app" / "api" / "v2.py").read_text(encoding="utf-8")

        self.assertIn("backend.app.algorithms.meta_strategy.inference.safe_inference", api_v2)
        self.assertNotIn("backend.app.ml.inference", api_v2)


def resolved_sibling_algorithm_ids() -> dict[str, str]:
    ids: dict[str, str] = {}
    for algorithm_name, dotted_symbol in SIBLING_ALGORITHM_IDS.items():
        module_name, symbol_name = dotted_symbol.rsplit(".", 1)
        module = importlib.import_module(module_name)
        ids[algorithm_name] = str(getattr(module, symbol_name))
    return ids


def imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


if __name__ == "__main__":
    unittest.main()
