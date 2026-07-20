from __future__ import annotations

import ast
import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.app.algorithms.meta_strategy.configuration import MetaStrategyBaselineSettings
from backend.app.algorithms.meta_strategy.dynamic_profile import MetaStrategyEffectiveSettings
from backend.app.algorithms.meta_strategy.local_gates import MetaStrategyLocalGateContext, evaluate_meta_strategy_local_gates
from backend.app.algorithms.meta_strategy.models.artifact_loader import RUNTIME_ARTIFACT_RULE_IDS
from backend.app.algorithms.meta_strategy.sizing import MetaStrategySizingContext, calculate_meta_strategy_position_size
from backend.app.algorithms.meta_strategy.strategy_registry import ALL_META_STRATEGY_STRATEGIES


ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = ROOT / "backend" / "tests" / "meta_strategy" / "coverage_manifest.json"
SKIP_MARKERS = tuple(
    "".join(parts)
    for parts in (
        ("pytest.mark.", "skip"),
        ("pytest.mark.", "skipif"),
        ("@unittest.", "skip"),
        ("self.", "skipTest("),
        ("pytest.", "skip("),
    )
)


class MetaStrategyStep44CoverageManifestTest(unittest.TestCase):
    maxDiff = None

    def test_manifest_is_machine_readable_and_expands_to_required_fields(self) -> None:
        manifest = load_manifest()
        required = tuple(manifest["required_fields"])
        entries = expanded_manifest_entries(manifest)

        self.assertEqual(manifest["manifest_version"], "meta_strategy_coverage_manifest_v1")
        self.assertEqual(manifest["algorithm_id"], "meta_strategy")
        self.assertGreater(len(entries), 0)
        for entry in entries:
            with self.subTest(component_id=entry["component_id"]):
                self.assertTrue(all(field in entry for field in required))
                self.assertTrue(all(str(entry[field]).strip() for field in required))

    def test_manifest_paths_exist_for_every_listed_component(self) -> None:
        for entry in expanded_manifest_entries(load_manifest()):
            with self.subTest(component_id=entry["component_id"]):
                self.assertTrue((ROOT / entry["implementation_path"]).is_file(), entry["implementation_path"])
                self.assertTrue((ROOT / entry["test_path"]).is_file(), entry["test_path"])

    def test_new_strategies_require_manifest_entries_and_focused_tests(self) -> None:
        manifest_entries = entries_by_type("strategy.")
        actual = {
            entry.strategy_id: {
                "component_type": f"strategy.{str(entry.role).split('.')[-1].lower()}",
                "implementation_path": implementation_path(entry.implementation_module),
            }
            for entry in ALL_META_STRATEGY_STRATEGIES
        }

        self.assertEqual(set(manifest_entries), set(actual))
        for strategy_id, expected in actual.items():
            with self.subTest(strategy_id=strategy_id):
                declared = manifest_entries[strategy_id]
                self.assertEqual(declared["component_type"], expected["component_type"])
                self.assertEqual(declared["implementation_path"], expected["implementation_path"])

    def test_new_local_gates_require_manifest_entries_and_focused_tests(self) -> None:
        manifest_ids = set(entries_by_type("local_gate"))
        actual_ids = {gate.gate_id for gate in evaluate_meta_strategy_local_gates(valid_gate_context()).gate_results}

        self.assertEqual(manifest_ids, actual_ids)

    def test_new_sizing_caps_require_manifest_entries_and_focused_tests(self) -> None:
        manifest_ids = set(entries_by_type("sizing_cap"))
        actual_ids = {cap.cap_id for cap in calculate_meta_strategy_position_size(valid_sizing_context()).caps}

        self.assertEqual(manifest_ids, actual_ids)

    def test_new_artifact_rules_require_manifest_entries_and_focused_tests(self) -> None:
        manifest_ids = set(entries_by_type("artifact_rule"))

        self.assertEqual(manifest_ids, set(RUNTIME_ARTIFACT_RULE_IDS))

    def test_manifest_lists_no_skipped_mandatory_tests(self) -> None:
        violations: list[str] = []
        checked_paths: set[Path] = set()
        for entry in expanded_manifest_entries(load_manifest()):
            test_path = ROOT / entry["test_path"]
            for path in (test_path, *imported_test_module_paths(test_path)):
                if path in checked_paths:
                    continue
                checked_paths.add(path)
                if not path.is_file():
                    violations.append(f"{path}: missing")
                    continue
                source = path.read_text(encoding="utf-8")
                for marker in SKIP_MARKERS:
                    if marker in source:
                        violations.append(f"{path.relative_to(ROOT)}: {marker}")

        self.assertEqual(violations, [])


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def expanded_manifest_entries(manifest: dict[str, Any]) -> tuple[dict[str, str], ...]:
    required = tuple(manifest["required_fields"])
    entries: list[dict[str, str]] = []
    for group in manifest["coverage_groups"]:
        inherited = {key: value for key, value in group.items() if key != "components"}
        for component in group["components"]:
            entry = {**inherited, **component}
            missing = tuple(field for field in required if field not in entry)
            if missing:
                raise AssertionError(f"{entry.get('component_id', '<unknown>')} missing manifest fields: {missing}")
            entries.append({field: str(entry[field]) for field in required})
    return tuple(entries)


def entries_by_type(component_type: str) -> dict[str, dict[str, str]]:
    entries = expanded_manifest_entries(load_manifest())
    if component_type.endswith("."):
        return {entry["component_id"]: entry for entry in entries if entry["component_type"].startswith(component_type)}
    return {entry["component_id"]: entry for entry in entries if entry["component_type"] == component_type}


def implementation_path(module_name: str) -> str:
    package = "backend.app.algorithms.meta_strategy."
    if not module_name.startswith(package):
        raise AssertionError(f"unexpected Meta-Strategy implementation module: {module_name}")
    return "backend/app/algorithms/meta_strategy/" + module_name[len(package) :].replace(".", "/") + ".py"


def valid_gate_context() -> MetaStrategyLocalGateContext:
    now = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    return MetaStrategyLocalGateContext(
        timestamp=now,
        proposed_quantity=12,
        active_strategy_count=3,
        independent_family_count=3,
        deterministic_score=0.70,
        deterministic_edge=0.12,
        calibrated_success_probability=0.64,
        uncertainty=0.25,
        missingness=0.05,
        ood_score=0.10,
        model_health_score=0.95,
        reward_risk_after_costs=1.60,
        spread_bps=4.0,
        liquidity=250_000.0,
        realized_daily_pnl=-100.0,
        daily_trade_count=1,
        last_trade_at=now - timedelta(minutes=15),
        event_blackout=False,
        session_phase="regular",
        execution_mode="PAPER",
        paper_trading_permission=True,
        live_trading_permission=False,
    )


def valid_sizing_context() -> MetaStrategySizingContext:
    baseline = MetaStrategyBaselineSettings(risk_percentage=0.10, position_cap=0.50)
    effective = MetaStrategyEffectiveSettings(
        baseline_configuration_version=baseline.configuration_version,
        baseline_settings_hash=baseline.settings_hash,
        entry_threshold=baseline.entry_threshold,
        model_probability_threshold=baseline.model_probability_threshold,
        risk_percentage=baseline.risk_percentage,
        position_cap=baseline.position_cap,
        stop_multiplier=baseline.stop_multiplier,
        target_multiplier=baseline.target_multiplier,
        maximum_holding_minutes=baseline.maximum_holding_minutes,
        spread_limit_bps=baseline.spread_limit_bps,
        liquidity_requirement=baseline.liquidity_requirement,
        trade_count_limit=baseline.trade_count_limit,
        allow_long=baseline.allow_long,
        allow_short=baseline.allow_short,
    )
    return MetaStrategySizingContext(
        side="BUY",
        candidate_accepted=True,
        local_gates_passed=True,
        baseline_settings=baseline,
        effective_settings=effective,
        model_risk_multiplier=1.0,
        account_equity=100_000.0,
        available_buying_power=100_000.0,
        entry_price=100.0,
        stop_distance=1.0,
        market_liquidity=10_000.0,
        remaining_algorithm_risk=100_000.0,
        global_available_risk=100_000.0,
        global_quantity_cap=100_000,
    )


def imported_test_module_paths(path: Path) -> tuple[Path, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    paths: list[Path] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if not node.module.startswith("backend.tests.test_meta_strategy_"):
            continue
        paths.append(ROOT / Path(*node.module.split(".")).with_suffix(".py"))
    return tuple(paths)


if __name__ == "__main__":
    unittest.main()
