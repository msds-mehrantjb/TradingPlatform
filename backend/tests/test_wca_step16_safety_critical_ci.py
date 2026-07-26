from __future__ import annotations

import ast
from importlib import import_module
import unittest
from pathlib import Path

from backend.app.algorithms.wca.final_acceptance import build_wca_final_acceptance_report
from backend.app.algorithms.wca.repository import WCA_PERSISTENCE_RECORD_INVENTORY
from backend.app.algorithms.wca.strategy_registry import WCA_HARD_FILTER_REGISTRY, WCA_MODULE_CATALOG, WCA_MODIFIER_REGISTRY, WCA_STRATEGY_REGISTRY
from backend.app.algorithms.wca.test_coverage import (
    WCA_STEP16_SAFETY_CRITICAL_AREA_IDS,
    WCA_STEP16_SAFETY_CRITICAL_TEST_AREAS,
    WCA_STEP16_SAFETY_CRITICAL_TEST_FILES,
    WCA_TEST_SUITE_PASS_REQUIRES_EXECUTION,
    wca_step16_safety_critical_test_report,
)


ROOT = Path(__file__).resolve().parents[2]
BACKEND_TESTS = ROOT / "backend" / "tests"
CI_SCRIPT = ROOT / "scripts" / "ci_quality_gates.py"
WCA_PACKAGE = ROOT / "backend" / "app" / "algorithms" / "wca"
SIBLING_ALGORITHMS = (
    ROOT / "backend" / "app" / "algorithms" / "weighted_voting",
    ROOT / "backend" / "app" / "algorithms" / "voting_ensemble",
    ROOT / "backend" / "app" / "algorithms" / "regime",
    ROOT / "backend" / "app" / "algorithms" / "session",
    ROOT / "backend" / "app" / "algorithms" / "meta_strategy",
)

EXPECTED_STEP16_AREAS = {
    "exact_inventory_11_11_7",
    "registry_to_class_parity",
    "strategy_isolation",
    "each_primary_strategy",
    "each_modifier",
    "each_hard_filter",
    "canonical_configuration_and_migration",
    "per_strategy_settings",
    "dynamic_settings_and_hard_caps",
    "calibration_leakage",
    "weight_leakage_and_normalisation",
    "family_and_correlation_caps",
    "one_production_pipeline",
    "paper_replay_backtest_parity",
    "duplicate_finalised_bar_events",
    "stale_and_out_of_order_events",
    "queue_backpressure",
    "worker_crash_and_restart",
    "checkpoint_recovery",
    "single_writer_guarantees",
    "global_risk_concurrency",
    "final_validation_after_overrides",
    "atomic_outbox_reservation",
    "broker_timeout_unknown_submission",
    "duplicate_broker_order_prevention",
    "partial_fills",
    "virtual_inventory_attribution",
    "cross_algorithm_inventory_isolation",
    "reconciliation",
    "unprotected_position_recovery",
    "protective_exits_during_entry_pauses",
    "early_market_close",
    "cost_and_net_edge_gates",
    "latency_and_stale_quote_gates",
    "api_presentation_only_boundary",
    "frontend_presentation_only_boundary",
    "wca_no_ml_dependency",
    "paper_only_enforcement",
}

FORBIDDEN_WCA_IMPORT_SYMBOLS = {
    "WCA_PERSISTENCE_RECORD_INVENTORY",
    "WCA_PERSISTENCE_TABLES",
    "WcaConfiguration",
    "WcaDecisionSettings",
    "WcaTradingSettings",
    "WcaWeightSnapshot",
    "WcaConfidenceCalibrationTable",
    "WcaInventoryOwnershipDecision",
    "WcaManagedPosition",
    "WcaPositionManagementRepository",
    "WcaPositionManagementSettings",
    "WcaRepository",
    "WcaSqliteRepository",
    "WcaRuntimeRepository",
    "WcaResearchRepository",
    "authorize_wca_lot_reduction",
    "build_managed_position",
    "manage_wca_position",
    "write_position_management_snapshot",
    "WCA_STRATEGY_REGISTRY",
    "WCA_MODIFIER_REGISTRY",
    "WCA_HARD_FILTER_REGISTRY",
}
FORBIDDEN_WCA_IMPORT_MODULE_FRAGMENTS = (
    ".wca.configuration",
    ".wca.strategy_registry",
    ".wca.strategies",
    ".wca.weights",
    ".wca.confidence",
    ".wca.repository",
    ".wca.runtime_repository",
    ".wca.research_repository",
    ".wca.service",
    ".wca.position_management",
)
FORBIDDEN_ML_IMPORT_ROOTS = {"sklearn", "tensorflow", "torch", "keras", "xgboost", "lightgbm", "catboost"}
FORBIDDEN_WCA_INVENTORY_MUTATION_FRAGMENTS = {
    "authorize_wca_lot_reduction",
    "apply_fill_and_update_position",
    "write_position_management_snapshot",
    "wca_owned_lots",
    "wca_positions",
    "wca_virtual_positions",
}


class WcaStep16SafetyCriticalCiTests(unittest.TestCase):
    def test_inventory_counts_remain_exactly_11_11_7(self) -> None:
        self.assertEqual(len(WCA_STRATEGY_REGISTRY), 11)
        self.assertEqual(len(WCA_MODIFIER_REGISTRY), 11)
        self.assertEqual(len(WCA_HARD_FILTER_REGISTRY), 7)

    def test_registry_to_class_parity_resolves_executable_modules_and_settings(self) -> None:
        for entry in WCA_MODULE_CATALOG:
            with self.subTest(module=entry.slug):
                implementation = _load_registry_target(entry.implementation_import_path)
                settings_model = _load_registry_target(entry.settings_model)
                self.assertIsNotNone(implementation, entry.implementation_import_path)
                self.assertIsNotNone(settings_model, entry.settings_model)

                if entry in WCA_STRATEGY_REGISTRY:
                    instance = implementation()
                    self.assertEqual(instance.strategy_id, entry.strategy_id)
                    self.assertEqual(instance.slug, entry.slug)
                    self.assertEqual(instance.name, entry.name)
                    self.assertEqual(instance.family, entry.family)
                    self.assertEqual(instance.version, entry.strategy_version)
                    self.assertEqual(instance.base_weight, entry.base_weight)
                elif entry in WCA_MODIFIER_REGISTRY:
                    instance = implementation()
                    self.assertEqual(instance.modifier_id, entry.slug)
                    self.assertEqual(instance.name, entry.name)
                    self.assertEqual(instance.family, entry.family)

    def test_step16_manifest_names_every_requested_safety_critical_area(self) -> None:
        self.assertEqual(WCA_STEP16_SAFETY_CRITICAL_AREA_IDS, EXPECTED_STEP16_AREAS)
        self.assertTrue(WCA_TEST_SUITE_PASS_REQUIRES_EXECUTION)
        report = wca_step16_safety_critical_test_report()
        self.assertFalse(report["testPresenceProvesPassing"])
        self.assertTrue(report["passingRequiresPytestExecution"])

    def test_every_step16_manifest_test_file_exists_and_is_registered_in_ci(self) -> None:
        ci_source = CI_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("pytest", ci_source)
        self.assertIn("backend/tests", ci_source)
        self.assertIn("typescript-type-check", ci_source)
        self.assertIn("frontend-tests", ci_source)
        self.assertIn("frontend-build", ci_source)
        self.assertIn("wca-safety-critical-tests", ci_source)

        for test_file in WCA_STEP16_SAFETY_CRITICAL_TEST_FILES:
            self.assertTrue((BACKEND_TESTS / test_file).exists(), test_file)
            self.assertIn(f"backend/tests/{test_file}", ci_source)

    def test_ci_gate_fails_on_required_safety_criteria(self) -> None:
        ci_source = CI_SCRIPT.read_text(encoding="utf-8")
        required_fragments = (
            "test_wca_step2_configuration_system.py",
            "test_wca_step3_strategy_catalog.py",
            "test_wca_step4_strategy_isolation.py",
            "test_wca_step15_api_frontend_control_surface.py",
            "test_wca_step16_safety_critical_ci.py",
            "test_wca_step17_persistence.py",
            "test_wca_step20_rollout.py",
            "test_wca_step21_final_acceptance.py",
        )
        for fragment in required_fragments:
            self.assertIn(fragment, ci_source)

    def test_acceptance_evidence_is_present_for_every_wca_item(self) -> None:
        report = build_wca_final_acceptance_report()
        items = report["items"]
        self.assertTrue(items)
        missing_evidence = [item["statement"] for item in items if not item["evidence"]]
        self.assertEqual(missing_evidence, [])
        self.assertIn("Critical tests pass.", {item["statement"] for item in items})

        for item in items:
            for evidence in item["evidence"]:
                if evidence.startswith(("backend/", "docs/", "frontend/", "scripts/")):
                    self.assertTrue((ROOT / evidence).exists(), f"{item['statement']}: {evidence}")

    def test_sibling_algorithm_packages_do_not_import_wca_private_state(self) -> None:
        violations: list[str] = []
        persistence_table_names = {row.table_name for row in WCA_PERSISTENCE_RECORD_INVENTORY}

        for package in SIBLING_ALGORITHMS:
            for path in package.rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        module = node.module or ""
                        if module.startswith("backend.app.algorithms.wca") or module.startswith("app.algorithms.wca"):
                            imported_names = {alias.name for alias in node.names}
                            if imported_names & FORBIDDEN_WCA_IMPORT_SYMBOLS or any(fragment in f".{module}" for fragment in FORBIDDEN_WCA_IMPORT_MODULE_FRAGMENTS):
                                violations.append(f"{path}: imports WCA private module {module}")
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name.startswith("backend.app.algorithms.wca") or alias.name.startswith("app.algorithms.wca"):
                                violations.append(f"{path}: imports WCA package {alias.name}")

                source = path.read_text(encoding="utf-8")
                for table_name in persistence_table_names:
                    if table_name in source:
                        violations.append(f"{path}: references WCA persistence table {table_name}")
                for fragment in FORBIDDEN_WCA_INVENTORY_MUTATION_FRAGMENTS:
                    if fragment in source:
                        violations.append(f"{path}: references WCA inventory mutation surface {fragment}")

        self.assertEqual(violations, [])

    def test_wca_package_has_no_ml_dependency_imports(self) -> None:
        violations: list[str] = []
        for path in WCA_PACKAGE.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".", 1)[0] in FORBIDDEN_ML_IMPORT_ROOTS:
                            violations.append(f"{path}: imports {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    root = (node.module or "").split(".", 1)[0]
                    if root in FORBIDDEN_ML_IMPORT_ROOTS:
                        violations.append(f"{path}: imports {node.module}")
        self.assertEqual(violations, [])

    def test_wca_package_does_not_enable_real_money_execution(self) -> None:
        violations: list[str] = []
        forbidden_fragments = ("paper_only=False", "paperOnly: false", "real_money_enabled=True", "submit_live_order", "live_trading_enabled=True")
        for path in WCA_PACKAGE.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for fragment in forbidden_fragments:
                if fragment in source:
                    violations.append(f"{path}: contains {fragment}")
        frontend = (ROOT / "frontend" / "src" / "main.ts").read_text(encoding="utf-8")
        self.assertNotIn("paperOnly: false", frontend)
        self.assertEqual(violations, [])

    def test_each_manifest_area_has_focused_backend_tests(self) -> None:
        for area in WCA_STEP16_SAFETY_CRITICAL_TEST_AREAS:
            self.assertTrue(area.mandatory_ci, area.area_id)
            self.assertTrue(area.test_files, area.area_id)
            for test_file in area.test_files:
                source = (BACKEND_TESTS / test_file).read_text(encoding="utf-8")
                self.assertIn("test_", source, test_file)


def _load_registry_target(import_path: str):
    module_name, separator, symbol_name = import_path.rpartition(".")
    if not separator:
        return import_module(import_path)
    try:
        module = import_module(module_name)
        return getattr(module, symbol_name)
    except AttributeError:
        return import_module(import_path)


if __name__ == "__main__":
    unittest.main()
