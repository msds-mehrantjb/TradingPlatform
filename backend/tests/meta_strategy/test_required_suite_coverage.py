from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from backend.app.algorithms.meta_strategy import DIRECTIONAL_STRATEGIES


ROOT = Path(__file__).resolve().parents[3]
TEST_ROOT = ROOT / "backend" / "tests"
META_TEST_ROOT = TEST_ROOT / "meta_strategy"
COVERAGE_MANIFEST = META_TEST_ROOT / "coverage_manifest.json"


REQUIRED_STEP16_AREAS: dict[str, tuple[str, ...]] = {
    "directional_strategy": ("test_every_strategy.py", "test_shadow_directional_required_cases.py"),
    "context_module": ("test_context.py",),
    "regime_state": ("test_regime.py",),
    "safety_gate": ("test_safety.py", "test_local_gates.py"),
    "correlation_cap": ("test_family_aggregation.py",),
    "aggregation_rule": ("test_candidate_generation.py", "test_family_aggregation.py"),
    "ml_policy_mode": ("test_inference.py",),
    "sizing_rule": ("test_sizing.py",),
    "settings_validator": ("test_configuration.py",),
    "order_construction_rule": ("test_order_validation.py", "test_execution_pipeline.py"),
    "exit_rule": ("test_trade_management.py",),
    "inventory_transition": ("test_persistence.py", "test_reconciliation.py"),
    "worker": ("test_required_worker_resilience.py",),
    "execution": ("test_required_execution_edges.py",),
    "parity": ("test_required_parity_modes.py",),
    "paper_e2e": ("test_required_paper_e2e.py",),
}


class MetaStrategyRequiredSuiteCoverageTest(unittest.TestCase):
    def test_step16_required_areas_are_split_across_focused_files(self) -> None:
        for area, filenames in REQUIRED_STEP16_AREAS.items():
            with self.subTest(area=area):
                self.assertGreaterEqual(len(filenames), 1)
                for filename in filenames:
                    self.assertTrue((META_TEST_ROOT / filename).is_file(), filename)

        all_files = {filename for filenames in REQUIRED_STEP16_AREAS.values() for filename in filenames}
        self.assertGreater(len(all_files), 8)

    def test_directional_strategy_manifest_covers_every_registered_strategy(self) -> None:
        manifest = json.loads(COVERAGE_MANIFEST.read_text(encoding="utf-8"))
        directional_group = next(group for group in manifest["coverage_groups"] if group["component_type"] == "strategy.directional")

        manifest_ids = {component["component_id"] for component in directional_group["components"]}
        registry_ids = {entry.strategy_id for entry in DIRECTIONAL_STRATEGIES}

        self.assertEqual(manifest_ids, registry_ids)
        for key in ("positive_case", "negative_case", "boundary_case", "missing_input_case", "determinism_case", "isolation_case"):
            self.assertTrue(directional_group[key])

    def test_new_step16_files_are_not_oversized_or_skip_based(self) -> None:
        for filename in sorted({filename for files in REQUIRED_STEP16_AREAS.values() for filename in files if filename.startswith("test_required_")}):
            with self.subTest(filename=filename):
                path = META_TEST_ROOT / filename
                source = path.read_text(encoding="utf-8")
                self.assertLessEqual(len(source.splitlines()), 260)
                self.assertNotIn("pytest.mark." + "skip", source)
                self.assertNotIn("@unittest." + "skip", source)

    def test_required_files_contain_real_tests_not_only_import_wrappers(self) -> None:
        for filename in sorted({filename for files in REQUIRED_STEP16_AREAS.values() for filename in files if filename.startswith("test_required_")}):
            with self.subTest(filename=filename):
                tree = ast.parse((META_TEST_ROOT / filename).read_text(encoding="utf-8"), filename=filename)
                tests = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]
                self.assertGreaterEqual(len(tests), 1)


if __name__ == "__main__":
    unittest.main()
