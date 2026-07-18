from __future__ import annotations

import ast
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.algorithms.wca.contracts import WcaStrategyPerformanceRecord
from backend.app.algorithms.wca.strategy_registry import WCA_STRATEGY_REGISTRY
from backend.app.algorithms.wca.weights import (
    WCA_WEIGHT_SYSTEM_COMPONENT_IDS,
    WCA_WEIGHT_SYSTEM_INVENTORY,
    WcaWeightEngineConfig,
    baseline_weight_snapshot,
    performance_weight_snapshot,
)


UTC = timezone.utc
ROOT = Path(__file__).parents[2]
APP_PATH = ROOT / "backend" / "app"
WCA_PATH = APP_PATH / "algorithms" / "wca"


class WcaStep6PerformanceWeightsTest(unittest.TestCase):
    def test_weight_system_inventory_is_dedicated_to_wca_responsibilities(self) -> None:
        expected = (
            "baseline_weights",
            "performance_derived_weights",
            "sample_size_reliability",
            "shrinkage_toward_baseline",
            "time_decay",
            "strategy_health",
            "regime_adjustment",
            "correlation_penalties",
            "maximum_strategy_weight",
            "maximum_family_concentration",
            "versioned_weight_snapshots",
        )

        self.assertEqual(tuple(component.component_id for component in WCA_WEIGHT_SYSTEM_INVENTORY), expected)
        self.assertEqual(WCA_WEIGHT_SYSTEM_COMPONENT_IDS, set(expected))
        allowed_prefixes = ("Use WCA", "Derive WCA", "Scale", "Shrink", "Give", "Reduce WCA", "Adjust WCA", "Penalize", "Cap", "Emit")
        self.assertTrue(all(component.responsibility.startswith(allowed_prefixes) for component in WCA_WEIGHT_SYSTEM_INVENTORY))

    def test_replaying_same_cutoff_generates_same_weights(self) -> None:
        cutoff = datetime(2026, 1, 30, 21, 0, tzinfo=UTC)
        records = sample_records(cutoff)
        config = WcaWeightEngineConfig(family_cap=0.34)

        first = performance_weight_snapshot(records=records, cutoff=cutoff, config=config)
        second = performance_weight_snapshot(records=records, cutoff=cutoff, config=config)

        self.assertEqual(first.deterministic_json(), second.deterministic_json())
        self.assertAlmostEqual(sum(first.weights.values()), 1.0, places=8)
        self.assertTrue(first.details)
        self.assertTrue(all(detail.metrics_cutoff_timestamp == cutoff for detail in first.details))

    def test_future_trades_cannot_affect_earlier_weights(self) -> None:
        cutoff = datetime(2026, 1, 30, 21, 0, tzinfo=UTC)
        records = sample_records(cutoff)
        future = tuple(
            performance_record("C1", "trend", 5.0, cutoff + timedelta(days=1, minutes=index))
            for index in range(20)
        )

        baseline = performance_weight_snapshot(records=records, cutoff=cutoff)
        with_future = performance_weight_snapshot(records=(*records, *future), cutoff=cutoff)

        self.assertEqual(with_future.deterministic_json(), baseline.deterministic_json())

    def test_family_caps_are_enforced_and_auditable(self) -> None:
        cutoff = datetime(2026, 1, 30, 21, 0, tzinfo=UTC)
        records = sample_records(cutoff, trend_edge=2.0)
        config = WcaWeightEngineConfig(family_cap=0.30, strategy_cap=0.16, strategy_floor=0.015)

        snapshot = performance_weight_snapshot(records=records, cutoff=cutoff, config=config)

        family_by_strategy = {definition.strategy_id: definition.family for definition in WCA_STRATEGY_REGISTRY}
        family_totals: dict[str, float] = {}
        for strategy_id, weight in snapshot.weights.items():
            family = family_by_strategy[strategy_id]
            family_totals[family] = family_totals.get(family, 0) + weight
            self.assertLessEqual(weight, config.strategy_cap + 1e-8)
            self.assertGreaterEqual(weight, config.strategy_floor - 1e-8)
        self.assertTrue(all(total <= config.family_cap + 1e-8 for total in family_totals.values()))
        self.assertTrue(all(detail.weight_version == config.weight_version for detail in snapshot.details))

    def test_small_losing_sample_does_not_zero_strategy(self) -> None:
        cutoff = datetime(2026, 1, 30, 21, 0, tzinfo=UTC)
        records = (performance_record("C1", "trend", -1.0, cutoff - timedelta(days=1)),)
        config = WcaWeightEngineConfig(strategy_floor=0.02, minimum_trade_count_full_weight=40)

        snapshot = performance_weight_snapshot(records=records, cutoff=cutoff, config=config)

        self.assertGreaterEqual(snapshot.weights["C1"], config.strategy_floor)
        detail = next(row for row in snapshot.details if row.strategy_id == "C1")
        self.assertIn("wca.weights.shrunk_to_baseline", detail.reason_codes)

    def test_static_baseline_weights_remain_available(self) -> None:
        cutoff = datetime(2026, 1, 30, 21, 0, tzinfo=UTC)

        snapshot = baseline_weight_snapshot(cutoff=cutoff)

        expected = {definition.strategy_id: definition.base_weight for definition in WCA_STRATEGY_REGISTRY}
        self.assertEqual(snapshot.weights, expected)
        self.assertEqual(snapshot.metrics_cutoff_timestamp, cutoff)
        self.assertIn("wca.weights.static_baseline", snapshot.reason_codes)

    def test_non_wca_algorithm_modules_cannot_modify_wca_weights(self) -> None:
        violations: list[str] = []
        forbidden_imports = {
            "backend.app.algorithms.wca.weights",
        }
        forbidden_names = {
            "WcaWeightSnapshot",
            "baseline_weight_snapshot",
            "equal_weight_snapshot",
            "performance_weight_snapshot",
            "save_weight_snapshot",
        }

        for path in sorted((APP_PATH / "algorithms").rglob("*.py")):
            if WCA_PATH in path.parents:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imported_names = {alias.name for alias in node.names}
                    if node.module in forbidden_imports or imported_names & forbidden_names:
                        violations.append(f"{path.relative_to(APP_PATH)} imports WCA weight ownership API")
                elif isinstance(node, ast.Import):
                    if any(alias.name in forbidden_imports for alias in node.names):
                        violations.append(f"{path.relative_to(APP_PATH)} imports WCA weight module")
                elif isinstance(node, ast.Call):
                    call_name = _call_name(node.func)
                    if call_name in {"save_weight_snapshot", "performance_weight_snapshot", "baseline_weight_snapshot", "equal_weight_snapshot"}:
                        violations.append(f"{path.relative_to(APP_PATH)} calls {call_name}")

            source = path.read_text(encoding="utf-8").lower()
            if "wca_weight_snapshots" in source:
                violations.append(f"{path.relative_to(APP_PATH)} references WCA weight snapshot storage")

        self.assertEqual(violations, [])


def sample_records(cutoff: datetime, *, trend_edge: float = 1.0) -> tuple[WcaStrategyPerformanceRecord, ...]:
    family_by_strategy = {definition.strategy_id: definition.family for definition in WCA_STRATEGY_REGISTRY}
    records: list[WcaStrategyPerformanceRecord] = []
    for definition in WCA_STRATEGY_REGISTRY:
        for index in range(45):
            if definition.family == "trend":
                value = trend_edge if index % 3 != 0 else -0.35
            elif definition.family == "mean_reversion":
                value = 0.45 if index % 2 == 0 else -0.30
            elif definition.family == "breakout":
                value = 0.55 if index % 2 == 0 else -0.45
            else:
                value = 0.25 if index % 2 == 0 else -0.20
            records.append(performance_record(definition.strategy_id, family_by_strategy[definition.strategy_id], value, cutoff - timedelta(days=45 - index)))
    return tuple(records)


def performance_record(strategy_id: str, family: str, r_multiple: float, available_at: datetime) -> WcaStrategyPerformanceRecord:
    return WcaStrategyPerformanceRecord(
        strategy_id=strategy_id,
        strategy_version=f"wca_{strategy_id.lower()}_test_v1",
        family=family,
        decision_timestamp=available_at - timedelta(hours=1),
        outcome_available_at=available_at,
        r_multiple=r_multiple,
        pnl=r_multiple * 100,
        success=r_multiple > 0,
        regime="default",
    )


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


if __name__ == "__main__":
    unittest.main()
