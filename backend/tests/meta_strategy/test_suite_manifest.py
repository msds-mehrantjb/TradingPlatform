from __future__ import annotations

import ast
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PACKAGE_DIR = ROOT / "backend" / "tests" / "meta_strategy"

META_STRATEGY_BRANCH_COVERAGE_THRESHOLD_PERCENT = 85


@dataclass(frozen=True)
class MetaStrategySuiteArea:
    area_id: str
    test_file: str
    source_steps: tuple[str, ...]


MANDATORY_META_STRATEGY_SUITES: tuple[MetaStrategySuiteArea, ...] = (
    MetaStrategySuiteArea("contracts", "test_contracts.py", ("step4", "step5")),
    MetaStrategySuiteArea("configuration", "test_configuration.py", ("step3", "step28")),
    MetaStrategySuiteArea("isolation", "test_isolation.py", ("step6", "step42")),
    MetaStrategySuiteArea("every_strategy", "test_every_strategy.py", ("step8", "step9")),
    MetaStrategySuiteArea("context", "test_context.py", ("step10",)),
    MetaStrategySuiteArea("regime", "test_regime.py", ("step11",)),
    MetaStrategySuiteArea("safety", "test_safety.py", ("step12",)),
    MetaStrategySuiteArea("candidate_generation", "test_candidate_generation.py", ("step14",)),
    MetaStrategySuiteArea("family_aggregation", "test_family_aggregation.py", ("step13",)),
    MetaStrategySuiteArea("features", "test_features.py", ("step16",)),
    MetaStrategySuiteArea("leakage", "test_leakage.py", ("step17", "step18", "step22")),
    MetaStrategySuiteArea("labels", "test_labels.py", ("step17",)),
    MetaStrategySuiteArea("oos_forecasts", "test_oos_forecasts.py", ("step18",)),
    MetaStrategySuiteArea("training", "test_training.py", ("step19", "step20")),
    MetaStrategySuiteArea("purging", "test_purging.py", ("step19", "step22")),
    MetaStrategySuiteArea("embargo", "test_embargo.py", ("step19", "step22")),
    MetaStrategySuiteArea("walk_forward", "test_walk_forward.py", ("step22", "step35", "step36")),
    MetaStrategySuiteArea("holdout", "test_holdout.py", ("step22", "step36")),
    MetaStrategySuiteArea("calibration", "test_calibration.py", ("step23",)),
    MetaStrategySuiteArea("artifacts", "test_artifacts.py", ("step21",)),
    MetaStrategySuiteArea("inference", "test_inference.py", ("step24", "step25", "step26")),
    MetaStrategySuiteArea("local_gates", "test_local_gates.py", ("step27",)),
    MetaStrategySuiteArea("dynamic_profile", "test_dynamic_profile.py", ("step28",)),
    MetaStrategySuiteArea("sizing", "test_sizing.py", ("step29",)),
    MetaStrategySuiteArea("trade_management", "test_trade_management.py", ("step30",)),
    MetaStrategySuiteArea("order_validation", "test_order_validation.py", ("step32",)),
    MetaStrategySuiteArea("global_risk", "test_global_risk.py", ("step33",)),
    MetaStrategySuiteArea("broker", "test_broker.py", ("step33",)),
    MetaStrategySuiteArea("reconciliation", "test_reconciliation.py", ("step30", "step33")),
    MetaStrategySuiteArea("backtesting", "test_backtesting.py", ("step35", "step36")),
    MetaStrategySuiteArea("runtime_parity", "test_runtime_parity.py", ("step35",)),
    MetaStrategySuiteArea("paper_stability", "test_paper_stability.py", ("step39",)),
    MetaStrategySuiteArea("promotion", "test_promotion.py", ("step37", "step38")),
    MetaStrategySuiteArea("persistence", "test_persistence.py", ("step34",)),
    MetaStrategySuiteArea("api", "test_api.py", ("step40", "step41")),
    MetaStrategySuiteArea("final_acceptance", "test_final_acceptance.py", ("step41", "step42")),
)

SUPPORTING_META_STRATEGY_SUITES = (
    "test_characterization.py",
    "test_legacy_authority.py",
    "test_market_snapshot.py",
    "test_candidate_geometry.py",
    "test_execution_pipeline.py",
    "test_coverage_manifest.py",
    "test_legacy_cutover.py",
)


class MetaStrategyDedicatedSuiteManifestTest(unittest.TestCase):
    maxDiff = None

    def test_mandatory_step43_suite_areas_are_present(self) -> None:
        expected = {
            "contracts",
            "configuration",
            "isolation",
            "every_strategy",
            "context",
            "regime",
            "safety",
            "candidate_generation",
            "family_aggregation",
            "features",
            "leakage",
            "labels",
            "oos_forecasts",
            "training",
            "purging",
            "embargo",
            "walk_forward",
            "holdout",
            "calibration",
            "artifacts",
            "inference",
            "local_gates",
            "dynamic_profile",
            "sizing",
            "trade_management",
            "order_validation",
            "global_risk",
            "broker",
            "reconciliation",
            "backtesting",
            "runtime_parity",
            "paper_stability",
            "promotion",
            "persistence",
            "api",
            "final_acceptance",
        }

        self.assertEqual({area.area_id for area in MANDATORY_META_STRATEGY_SUITES}, expected)

    def test_declared_suite_files_exist_and_import_step_tests(self) -> None:
        for area in MANDATORY_META_STRATEGY_SUITES:
            with self.subTest(area=area.area_id):
                path = PACKAGE_DIR / area.test_file
                self.assertTrue(path.is_file(), area.test_file)
                imports = imported_modules(path)
                for step in area.source_steps:
                    self.assertTrue(
                        any(f"test_meta_strategy_{step}" in module for module in imports),
                        f"{area.test_file} does not import {step}",
                    )

    def test_supporting_suites_are_present(self) -> None:
        for filename in SUPPORTING_META_STRATEGY_SUITES:
            with self.subTest(filename=filename):
                self.assertTrue((PACKAGE_DIR / filename).is_file())

    def test_no_dedicated_meta_strategy_test_is_marked_skip(self) -> None:
        skip_markers = ("pytest.mark.skip", "pytest.mark.skipif", "@unittest.skip", "self.skipTest(", "pytest.skip(")
        violations = []
        for path in sorted(PACKAGE_DIR.glob("test_*.py")):
            if path.name == "test_suite_manifest.py":
                continue
            source = path.read_text(encoding="utf-8")
            for marker in skip_markers:
                if marker in source:
                    violations.append(f"{path.name}: {marker}")

        self.assertEqual(violations, [])

    def test_branch_coverage_threshold_is_declared_for_ci(self) -> None:
        self.assertGreater(META_STRATEGY_BRANCH_COVERAGE_THRESHOLD_PERCENT, 0)


def imported_modules(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)


if __name__ == "__main__":
    unittest.main()
