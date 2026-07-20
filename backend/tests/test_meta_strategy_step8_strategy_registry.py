from __future__ import annotations

import ast
import importlib
import unittest
from datetime import UTC, datetime
from pathlib import Path

from backend.app.algorithms.meta_strategy import (
    ALL_META_STRATEGY_STRATEGIES,
    DIRECTIONAL_STRATEGIES,
    META_STRATEGY_STRATEGY_PACKAGE,
    MetaStrategyMarketSnapshot,
    directional_strategy_input_ids,
    influence_strategy_ids,
    meta_strategy_strategy_catalog,
    resolve_strategy,
    resolve_strategy_list,
    validate_meta_strategy_registry,
)


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = ROOT / "backend" / "app" / "algorithms" / "meta_strategy"
PROHIBITED_STRATEGY_IMPORT_PREFIXES = (
    "requests",
    "httpx",
    "urllib",
    "sqlite3",
    "backend.app.api",
    "backend.app.broker",
    "backend.app.brokers",
    "backend.app.database",
    "backend.app.db",
    "backend.app.persistence",
    "backend.app.strategies",
)


class MetaStrategyStep8StrategyRegistryTest(unittest.TestCase):
    maxDiff = None

    def test_requested_registry_structure_exists(self) -> None:
        expected_paths = (
            "strategy_registry.py",
            "strategies/__init__.py",
            "strategies/base.py",
            "strategies/directional/__init__.py",
            "strategies/context/__init__.py",
            "strategies/regime/__init__.py",
            "strategies/safety/__init__.py",
        )

        for relative in expected_paths:
            with self.subTest(path=relative):
                self.assertTrue((PACKAGE_DIR / relative).is_file())

    def test_registry_entries_have_required_metadata(self) -> None:
        catalog = meta_strategy_strategy_catalog()
        validation = validate_meta_strategy_registry(catalog)

        self.assertEqual(catalog, ALL_META_STRATEGY_STRATEGIES)
        self.assertTrue(validation["valid"])
        self.assertGreaterEqual(len(catalog), 19)
        self.assertEqual(len(directional_strategy_input_ids()), len(DIRECTIONAL_STRATEGIES))
        for entry in catalog:
            with self.subTest(strategy=entry.strategy_id):
                self.assertEqual(entry.algorithm_id, "meta_strategy")
                self.assertTrue(entry.strategy_id)
                self.assertTrue(entry.strategy_version)
                self.assertTrue(entry.role)
                self.assertTrue(entry.family)
                self.assertGreater(len(entry.required_inputs), 0)
                self.assertGreaterEqual(entry.minimum_warmup, 0)
                self.assertIsInstance(entry.enabled, bool)
                self.assertGreater(len(entry.supported_directions), 0)
                self.assertTrue(entry.configuration_schema.schema_id)
                self.assertTrue(entry.implementation_module.startswith(META_STRATEGY_STRATEGY_PACKAGE))
                self.assertEqual(entry.canonical_influence_id, entry.strategy_id)

    def test_registry_contains_only_meta_strategy_owned_implementations(self) -> None:
        snapshot = snapshot_fixture()
        for entry in meta_strategy_strategy_catalog():
            module = importlib.import_module(entry.implementation_module)
            strategy_class = getattr(module, entry.implementation_class)
            strategy = strategy_class()
            result = strategy.evaluate(snapshot)
            with self.subTest(strategy=entry.strategy_id):
                self.assertEqual(strategy.strategy_id, entry.strategy_id)
                self.assertEqual(result.strategy_id, entry.strategy_id)
                self.assertIn(result.signal, {"BUY", "SELL", "HOLD"})
                self.assertGreaterEqual(result.confidence, 0.0)
                self.assertLessEqual(result.confidence, 1.0)

    def test_duplicate_strategy_ids_fail_validation(self) -> None:
        catalog = meta_strategy_strategy_catalog()
        duplicate = catalog[0].model_copy()
        validation = validate_meta_strategy_registry((*catalog, duplicate))

        self.assertFalse(validation["valid"])
        self.assertEqual(validation["duplicateStrategyIds"], (catalog[0].strategy_id,))

    def test_alias_strategies_cannot_receive_duplicate_influence(self) -> None:
        bollinger = resolve_strategy_list(
            [
                "Bollinger Band Reversion",
                "ATR Overextension Reversion",
                "Bollinger/ATR Reversion",
                "bollinger_atr_reversion",
            ]
        )
        failed_breakout = resolve_strategy_list(["Failed Breakout Strategy", "Failed Breakout Reversal", "failed_breakout_reversal"])

        self.assertEqual(len(bollinger), 1)
        self.assertEqual(bollinger[0].strategy_id, "bollinger_atr_reversion")
        self.assertEqual(influence_strategy_ids(["Bollinger Band Reversion", "ATR Overextension Reversion"]), ("bollinger_atr_reversion",))
        self.assertEqual(len(failed_breakout), 1)
        self.assertEqual(failed_breakout[0].strategy_id, "failed_breakout_reversal")
        self.assertEqual(resolve_strategy("VWAP Position Strategy").strategy_id, "vwap_position_context")

    def test_strategy_modules_do_not_import_databases_brokers_apis_or_legacy_strategies(self) -> None:
        violations = []
        for path in sorted((PACKAGE_DIR / "strategies").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for module_name in imported_module_names(tree):
                if starts_with_any(module_name, PROHIBITED_STRATEGY_IMPORT_PREFIXES):
                    violations.append(f"{path.relative_to(PACKAGE_DIR)} imports {module_name}")

        self.assertEqual(violations, [])


def snapshot_fixture() -> MetaStrategyMarketSnapshot:
    timestamp = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)
    return MetaStrategyMarketSnapshot(
        algorithm_id="meta_strategy",
        algorithm_version="meta_strategy_algorithm_v1",
        configuration_version="meta_strategy_config_v1",
        strategy_catalog_version="meta_strategy_strategy_catalog_v1",
        decision_id="decision-1",
        snapshot_id="snapshot-1",
        timestamp=timestamp,
        symbol="SPY",
        last_price=101.0,
        bid_price=100.99,
        ask_price=101.01,
        spread_bps=1.98,
        volume=100_000,
        source_cutoff_timestamp=timestamp,
        point_in_time=True,
        vwap=100.5,
        adx={"1m": 24.0, "5m": 20.0, "15m": 18.0},
        liquidity={"level": "good", "score": 1.0},
        breadth={"averageReturn": 0.001, "componentCount": 2},
    )


def imported_module_names(tree: ast.AST) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)


def starts_with_any(module_name: str, prefixes: tuple[str, ...]) -> bool:
    return any(module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in prefixes)


if __name__ == "__main__":
    unittest.main()
