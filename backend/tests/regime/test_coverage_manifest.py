from __future__ import annotations

import json
import unittest
from pathlib import Path

from backend.app.algorithms.regime.contracts import CANONICAL_MARKET_REGIMES
from backend.app.algorithms.regime.strategy_registry import REGIME_STRATEGY_DEFINITIONS
from backend.tests.regime.fixtures.expected_results import CONFIRMATION_MODULES, CONTEXT_MODULES, DIRECTIONAL_STRATEGIES, SAFETY_GATES


ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "backend" / "tests" / "regime" / "coverage_manifest.json"
CASE_FIELDS = {
    "buy_case",
    "sell_case",
    "hold_case",
    "boundary_case",
    "missing_input_case",
    "determinism_case",
    "isolation_case",
}


def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class RegimeCoverageManifestTest(unittest.TestCase):
    def test_manifest_declares_backend_authority_and_ci_command(self):
        data = manifest()

        self.assertEqual(data["algorithmId"], "regime")
        self.assertEqual(data["runtime"], "backend.app.algorithms.regime.execution_pipeline")
        self.assertEqual(data["backtesting"], "backend.app.algorithms.regime.backtest.engine")
        self.assertIn("pytest backend/tests/regime", data["ciCommand"])
        self.assertTrue(data["branchCoverageRequired"])

    def test_manifest_covers_every_registered_strategy_once(self):
        data = manifest()
        strategy_entries = {
            component["component_id"]: component
            for component in data["components"]
            if component["component_type"] in {"directional_strategy", "confirmation_module", "context_module", "safety_gate"}
        }
        registry_ids = {definition.strategy_id for definition in REGIME_STRATEGY_DEFINITIONS}

        self.assertEqual(set(strategy_entries), registry_ids)
        self.assertEqual({item for item in DIRECTIONAL_STRATEGIES}, {key for key, value in strategy_entries.items() if value["component_type"] == "directional_strategy"})
        self.assertEqual(set(CONFIRMATION_MODULES), {key for key, value in strategy_entries.items() if value["component_type"] == "confirmation_module"})
        self.assertEqual(set(CONTEXT_MODULES), {key for key, value in strategy_entries.items() if value["component_type"] == "context_module"})
        self.assertEqual(set(SAFETY_GATES), {key for key, value in strategy_entries.items() if value["component_type"] == "safety_gate"})

    def test_manifest_covers_canonical_regime_inventory(self):
        data = manifest()

        self.assertEqual(len(CANONICAL_MARKET_REGIMES), 16)
        self.assertEqual(len(set(CANONICAL_MARKET_REGIMES)), 16)
        self.assertIn("composite_regimes", {component["component_id"] for component in data["components"]})

    def test_every_manifest_path_exists_and_case_fields_are_explicit(self):
        for component in manifest()["components"]:
            with self.subTest(component=component["component_id"]):
                self.assertTrue((ROOT / component["implementation_path"]).exists(), component["implementation_path"])
                self.assertTrue((ROOT / component["focused_test_path"]).exists(), component["focused_test_path"])
                self.assertTrue(CASE_FIELDS.issubset(component), component["component_id"])
                self.assertTrue(component["determinism_case"], component["component_id"])
                self.assertTrue(component["isolation_case"], component["component_id"])

    def test_focused_suite_has_no_disabled_markers(self):
        forbidden = ("unittest." + "skip", "@pytest.mark." + "skip", "pytest." + "skip", "x" + "fail")
        for path in (ROOT / "backend" / "tests" / "regime").rglob("test_*.py"):
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                self.assertNotIn(marker, text, str(path))


if __name__ == "__main__":
    unittest.main()
