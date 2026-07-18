from __future__ import annotations

import ast
import importlib
import json
import pkgutil
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.algorithms.wca import WCA_PACKAGE_VERSION
from backend.app.algorithms.wca.backtest import WCA_BACKTEST_FILE_INVENTORY, WCA_BACKTEST_INVENTORY, WCA_BACKTEST_RESPONSIBILITY_IDS
from backend.app.algorithms.wca.configuration import WCA_CONFIGURATION_VERSION, validate_baseline_settings
from backend.app.algorithms.wca.contracts import (
    BacktestResult,
    BacktestRunConfiguration,
    WCA_ALGORITHM_ID,
    WCA_BROKER_RECONCILIATION_SCHEMA_VERSION,
    WCA_CONTRACT_VERSION,
    WCA_DEDICATED_COMPONENT_IDS,
    WCA_DEDICATED_COMPONENT_INVENTORY,
    WCA_DEDICATED_COMPONENT_OWNER_MODULES,
    WCA_FEATURE_SNAPSHOT_SCHEMA_VERSION,
    WCA_GLOBAL_RISK_FORBIDDEN_REWRITE_TARGETS,
    WCA_PAPER_STABILITY_VALIDATION_SCHEMA_VERSION,
    WCA_SHADOW_COMPARISON_EVIDENCE_SCHEMA_VERSION,
    WCA_SHARED_PLATFORM_COMPONENT_IDS,
    WCA_SHARED_PLATFORM_COMPONENT_INVENTORY,
    GlobalGateResult,
    ProposedOrder,
    WcaAggregationResult,
    WcaBaselineSettings,
    WcaCandle,
    WcaDecision,
    WcaEvaluationStatus,
    WcaGateStatus,
    WcaLocalGateResult,
    WcaMarketSnapshot,
    WcaMarketStatus,
    WcaOrderValidationContext,
    WcaOrderValidationResult,
    WcaSide,
    WcaSizingResult,
    WcaStrategyEvaluation,
    WcaWeightSnapshot,
)
from backend.app.algorithms.wca.strategy_registry import (
    WCA_HARD_FILTER_REGISTRY,
    WCA_MODIFIER_REGISTRY,
    WCA_STRATEGY_IDS,
    WCA_STRATEGY_REGISTRY,
)
from backend.app.algorithms.wca.market_snapshot import build_wca_market_snapshot, validate_wca_market_snapshot
from backend.app.algorithms.wca.dynamic_profile import WCA_DYNAMIC_PROFILE_VALUE_IDS, WCA_DYNAMIC_PROFILE_VALUE_INVENTORY
from backend.app.algorithms.wca.repository import WCA_PERSISTENCE_RECORD_IDS, WCA_PERSISTENCE_RECORD_INVENTORY, WCA_PERSISTENCE_TABLES
from backend.app.algorithms.wca.sizing import WCA_SIZING_INPUT_IDS, WCA_SIZING_INPUT_INVENTORY
from backend.app.algorithms.wca.test_coverage import (
    WCA_TEST_SUITE_COVERAGE_AREA_IDS,
    WCA_TEST_SUITE_COVERAGE_INVENTORY,
    WCA_TEST_SUITE_PASS_REQUIRES_EXECUTION,
    WCA_VALIDATION_ROLLOUT_FILE_INVENTORY,
    WCA_VALIDATION_ROLLOUT_FILE_NAMES,
)
from backend.app.algorithms.wca.weights import WCA_WEIGHT_SYSTEM_COMPONENT_IDS, WCA_WEIGHT_SYSTEM_INVENTORY
from backend.app.main import app


ROOT = Path(__file__).parents[2]
WCA_PACKAGE = "backend.app.algorithms.wca"
WCA_PATH = ROOT / "backend" / "app" / "algorithms" / "wca"
RISK_PATH = ROOT / "backend" / "app" / "risk"
EXECUTION_PATH = ROOT / "backend" / "app" / "execution"

EXPECTED_WCA_FILES = {
    "__init__.py",
    "contracts.py",
    "configuration.py",
    "strategy_registry.py",
    "market_snapshot.py",
    "market_status.py",
    "confidence.py",
    "weights.py",
    "aggregation.py",
    "dynamic_profile.py",
    "local_gates.py",
    "execution_pipeline.py",
    "sizing.py",
    "order_validation.py",
    "exits.py",
    "broker_reconciliation.py",
    "engine.py",
    "service.py",
    "repository.py",
    "api.py",
    "strategies/__init__.py",
    "strategies/base.py",
    "modifiers/__init__.py",
    "modifiers/base.py",
    "backtest/__init__.py",
    "backtest/engine.py",
    "backtest/execution.py",
    "backtest/ledger.py",
    "backtest/metrics.py",
    "backtest/walk_forward.py",
    "backtest/reports.py",
}

EXPECTED_RISK_FILES = {
    "__init__.py",
    "global_gate_engine.py",
    "account_risk_ledger.py",
    "gate_contracts.py",
    "exposure.py",
}

EXPECTED_EXECUTION_FILES = {
    "order_contracts.py",
    "order_validator.py",
    "idempotency.py",
    "reconciliation.py",
    "broker_adapter.py",
}

FORBIDDEN_WCA_IMPORTS = (
    "frontend",
    "backend.app.algorithms.weighted_voting",
    "backend.app.ensemble",
    "backend.app.ml",
    "backend.app.market_forecast",
    "backend.app.meta_strategy_training",
    "backend.app.strategies",
    "backend.app.trading_policy",
)

FORBIDDEN_STRATEGY_IMPORT_TERMS = ("broker", "database", "alpaca", "repository")
FORBIDDEN_STRATEGY_STATE_IMPORTS = (
    "backend.app.algorithms.weighted_voting",
    "backend.app.algorithms.voting_ensemble",
    "backend.app.algorithms.regime",
    "backend.app.ensemble",
    "backend.app.ml",
    "backend.app.market_forecast",
    "backend.app.meta_strategy_training",
    "backend.app.database",
)


class WcaStep1BackendStructureTest(unittest.TestCase):
    def test_requested_module_structure_exists(self) -> None:
        missing_wca = sorted(path for path in EXPECTED_WCA_FILES if not (WCA_PATH / path).is_file())
        missing_risk = sorted(path for path in EXPECTED_RISK_FILES if not (RISK_PATH / path).is_file())
        missing_execution = sorted(path for path in EXPECTED_EXECUTION_FILES if not (EXECUTION_PATH / path).is_file())

        self.assertEqual(missing_wca, [])
        self.assertEqual(missing_risk, [])
        self.assertEqual(missing_execution, [])

    def test_wca_modules_import_without_cycles(self) -> None:
        package = importlib.import_module(WCA_PACKAGE)
        imported = {package.__name__}

        for module in pkgutil.walk_packages(package.__path__, f"{WCA_PACKAGE}."):
            imported.add(importlib.import_module(module.name).__name__)

        self.assertIn(f"{WCA_PACKAGE}.contracts", imported)
        self.assertIn(f"{WCA_PACKAGE}.strategies.base", imported)
        self.assertIn(f"{WCA_PACKAGE}.backtest.engine", imported)

    def test_wca_modules_do_not_import_frontend_or_other_algorithms(self) -> None:
        violations: list[str] = []
        for path in sorted(WCA_PATH.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module_names: list[str] = []
                if isinstance(node, ast.Import):
                    module_names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    module_names = [node.module]
                for module_name in module_names:
                    if _matches_prefix(module_name, FORBIDDEN_WCA_IMPORTS):
                        violations.append(f"{path.relative_to(WCA_PATH)} imports {module_name}")
                    if module_name.startswith("backend.app.algorithms.") and not module_name.startswith(WCA_PACKAGE):
                        violations.append(f"{path.relative_to(WCA_PATH)} imports sibling algorithm {module_name}")

        self.assertEqual(violations, [])

    def test_wca_identity_and_contract_inventory_is_dedicated(self) -> None:
        self.assertEqual(WCA_ALGORITHM_ID, "wca")
        self.assertEqual(WCA_PACKAGE_VERSION, "wca_backend_structure_v1")
        self.assertEqual(WCA_CONTRACT_VERSION, "wca_contracts_v1")
        self.assertEqual(WCA_CONFIGURATION_VERSION, "wca_legacy_configuration_v1")
        self.assertEqual(WCA_FEATURE_SNAPSHOT_SCHEMA_VERSION, "wca_read_only_feature_snapshot_v1")
        self.assertEqual(WCA_BROKER_RECONCILIATION_SCHEMA_VERSION, "wca_broker_reconciliation_v1")
        self.assertEqual(WCA_SHADOW_COMPARISON_EVIDENCE_SCHEMA_VERSION, "wca_shadow_comparison_evidence_v1")
        self.assertEqual(WCA_PAPER_STABILITY_VALIDATION_SCHEMA_VERSION, "wca_paper_stability_validation_v1")
        self.assertEqual(len(WCA_SHARED_PLATFORM_COMPONENT_INVENTORY), 12)
        self.assertIn("global_account_risk_engine", WCA_SHARED_PLATFORM_COMPONENT_IDS)
        self.assertIn("wca_backtest_results", WCA_GLOBAL_RISK_FORBIDDEN_REWRITE_TARGETS)
        self.assertEqual(len(WCA_DEDICATED_COMPONENT_INVENTORY), 19)
        self.assertIn("wca_strategies", WCA_DEDICATED_COMPONENT_IDS)
        self.assertIn("backend.app.algorithms.wca.backtest", WCA_DEDICATED_COMPONENT_OWNER_MODULES)
        self.assertEqual(WCA_STRATEGY_IDS, {f"C{index}" for index in range(1, 12)})
        self.assertEqual(len(WCA_STRATEGY_REGISTRY), 11)
        self.assertGreater(len(WCA_MODIFIER_REGISTRY), 0)
        self.assertGreater(len(WCA_HARD_FILTER_REGISTRY), 0)
        self.assertEqual(len(WCA_WEIGHT_SYSTEM_INVENTORY), 11)
        self.assertIn("baseline_weights", WCA_WEIGHT_SYSTEM_COMPONENT_IDS)
        self.assertEqual(len(WCA_DYNAMIC_PROFILE_VALUE_INVENTORY), 13)
        self.assertIn("entry_score_threshold", WCA_DYNAMIC_PROFILE_VALUE_IDS)
        self.assertEqual(len(WCA_SIZING_INPUT_INVENTORY), 10)
        self.assertIn("global_gate_quantity_cap", WCA_SIZING_INPUT_IDS)
        self.assertEqual(len(WCA_PERSISTENCE_RECORD_INVENTORY), 16)
        self.assertIn("rollout_status", WCA_PERSISTENCE_RECORD_IDS)
        self.assertIn("wca_rollout_status", WCA_PERSISTENCE_TABLES)
        self.assertEqual(len(WCA_BACKTEST_FILE_INVENTORY), 7)
        self.assertEqual(len(WCA_BACKTEST_INVENTORY), 15)
        self.assertIn("next_bar_execution", WCA_BACKTEST_RESPONSIBILITY_IDS)
        self.assertEqual(len(WCA_VALIDATION_ROLLOUT_FILE_INVENTORY), 5)
        self.assertIn("rollout.py", WCA_VALIDATION_ROLLOUT_FILE_NAMES)
        self.assertEqual(len(WCA_TEST_SUITE_COVERAGE_INVENTORY), 16)
        self.assertIn("final_acceptance", WCA_TEST_SUITE_COVERAGE_AREA_IDS)
        self.assertTrue(WCA_TEST_SUITE_PASS_REQUIRES_EXECUTION)
        self.assertEqual(validate_baseline_settings({"minimum_score": 0.4}).minimum_score, 0.4)
        self.assertTrue(WcaOrderValidationResult(valid=True, reason_codes=("wca.order_validation.passed",)).valid)
        self.assertTrue(WcaOrderValidationContext(evaluation_timestamp=datetime.now(timezone.utc)).paper_only_mode)

        expected_owners = {
            "WCA_PACKAGE_VERSION": Path("__init__.py"),
            "WCA_ALGORITHM_ID": Path("contracts.py"),
            "WCA_CONTRACT_VERSION": Path("contracts.py"),
            "WCA_FEATURE_SNAPSHOT_SCHEMA_VERSION": Path("contracts.py"),
            "WCA_BROKER_RECONCILIATION_SCHEMA_VERSION": Path("contracts.py"),
            "WCA_SHADOW_COMPARISON_EVIDENCE_SCHEMA_VERSION": Path("contracts.py"),
            "WCA_PAPER_STABILITY_VALIDATION_SCHEMA_VERSION": Path("contracts.py"),
            "WCA_SHARED_PLATFORM_COMPONENT_INVENTORY": Path("contracts.py"),
            "WCA_SHARED_PLATFORM_COMPONENT_IDS": Path("contracts.py"),
            "WCA_GLOBAL_RISK_FORBIDDEN_REWRITE_TARGETS": Path("contracts.py"),
            "WCA_GLOBAL_RISK_ALLOWED_CONSTRAINTS": Path("contracts.py"),
            "WCA_DEDICATED_COMPONENT_INVENTORY": Path("contracts.py"),
            "WCA_DEDICATED_COMPONENT_IDS": Path("contracts.py"),
            "WCA_DEDICATED_COMPONENT_OWNER_MODULES": Path("contracts.py"),
            "WCA_CONFIGURATION_VERSION": Path("configuration.py"),
            "WCA_STRATEGY_REGISTRY": Path("strategy_registry.py"),
            "WCA_MODIFIER_REGISTRY": Path("strategy_registry.py"),
            "WCA_HARD_FILTER_REGISTRY": Path("strategy_registry.py"),
            "WCA_STRATEGY_IDS": Path("strategy_registry.py"),
            "WCA_PRIMARY_VOTER_SLUGS": Path("strategy_registry.py"),
            "WCA_MODIFIER_SLUGS": Path("strategy_registry.py"),
            "WCA_HARD_FILTER_SLUGS": Path("strategy_registry.py"),
            "WCA_WEIGHT_SYSTEM_INVENTORY": Path("weights.py"),
            "WCA_WEIGHT_SYSTEM_COMPONENT_IDS": Path("weights.py"),
            "WCA_DYNAMIC_PROFILE_VALUE_INVENTORY": Path("dynamic_profile.py"),
            "WCA_DYNAMIC_PROFILE_VALUE_IDS": Path("dynamic_profile.py"),
            "WCA_SIZING_INPUT_INVENTORY": Path("sizing.py"),
            "WCA_SIZING_INPUT_IDS": Path("sizing.py"),
            "WCA_PERSISTENCE_RECORD_INVENTORY": Path("repository.py"),
            "WCA_PERSISTENCE_RECORD_IDS": Path("repository.py"),
            "WCA_PERSISTENCE_TABLES": Path("repository.py"),
        }
        violations = []
        for path in sorted(WCA_PATH.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for name in _assigned_names(tree):
                owner = expected_owners.get(name)
                if owner is not None and path.relative_to(WCA_PATH) != owner:
                    violations.append(f"{name} assigned in {path.relative_to(WCA_PATH)}; expected {owner}")

        self.assertEqual(violations, [])

        class_owners = {
            "WcaOrderValidationContext": Path("contracts.py"),
            "WcaOrderValidationResult": Path("contracts.py"),
        }
        class_violations = []
        for path in sorted(WCA_PATH.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    owner = class_owners.get(node.name)
                    if owner is not None and path.relative_to(WCA_PATH) != owner:
                        class_violations.append(f"{node.name} defined in {path.relative_to(WCA_PATH)}; expected {owner}")

        self.assertEqual(class_violations, [])

    def test_strategy_interfaces_contain_no_broker_or_database_calls(self) -> None:
        violations: list[str] = []
        for path in sorted((WCA_PATH / "strategies").rglob("*.py")):
            source = path.read_text(encoding="utf-8").lower()
            for term in FORBIDDEN_STRATEGY_IMPORT_TERMS:
                if term in source:
                    violations.append(f"{path.relative_to(WCA_PATH)} contains {term}")

        self.assertEqual(violations, [])

    def test_wca_market_input_inventory_is_dedicated_and_immutable(self) -> None:
        timestamp = datetime(2026, 7, 15, 14, 30, tzinfo=timezone.utc)
        snapshot = build_wca_market_snapshot(
            symbol="SPY",
            data_timestamp=timestamp,
            decision_timestamp=timestamp,
            candles=(
                {
                    "timestamp": timestamp,
                    "open": 500,
                    "high": 501,
                    "low": 499,
                    "close": 500.5,
                    "volume": 100000,
                },
            ),
            quote={"timestamp": timestamp, "bid": 500.45, "ask": 500.55},
            reason_codes=("wca.market_snapshot.test",),
        )

        self.assertEqual(snapshot.algorithm_id, WCA_ALGORITHM_ID)
        self.assertEqual(validate_wca_market_snapshot(snapshot), snapshot)
        self.assertEqual(snapshot.candles[0].close, 500.5)
        with self.assertRaises(ValidationError):
            snapshot.symbol = "QQQ"
        with self.assertRaises(ValidationError):
            snapshot.candles[0].close = 1

    def test_wca_strategies_read_only_wca_snapshots_not_foreign_state(self) -> None:
        violations: list[str] = []
        for path in sorted((WCA_PATH / "strategies").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = _imported_modules(tree)
            for module_name in imports:
                if _matches_prefix(module_name, FORBIDDEN_STRATEGY_STATE_IMPORTS):
                    violations.append(f"{path.relative_to(WCA_PATH)} imports {module_name}")

        self.assertEqual(violations, [])

    def test_non_wca_algorithm_modules_do_not_share_wca_dedicated_components(self) -> None:
        violations: list[str] = []
        forbidden_storage_refs = {
            "wca_weight_snapshots",
            "wca_order_intents",
            "wca_positions",
            "wca_trade_ledger",
            "wca_backtest_runs",
            "wca_backtest_results",
            "wca_rollout_status",
        }
        for path in sorted(WCA_PATH.parent.rglob("*.py")):
            if WCA_PATH in path.parents or path == WCA_PATH:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = _imported_modules(tree)
            for module_name in imports:
                if _matches_prefix(module_name, tuple(WCA_DEDICATED_COMPONENT_OWNER_MODULES)):
                    violations.append(f"{path.relative_to(WCA_PATH.parent)} imports WCA dedicated component {module_name}")
            source = path.read_text(encoding="utf-8").lower()
            for table_name in forbidden_storage_refs:
                if table_name in source:
                    violations.append(f"{path.relative_to(WCA_PATH.parent)} references WCA-owned storage {table_name}")

        self.assertEqual(violations, [])

    def test_contracts_serialize_deterministically_and_validate_statuses(self) -> None:
        snapshot = sample_market_snapshot()
        strategy = WcaStrategyEvaluation(
            strategy_id="C1",
            name="Moving Average Trend",
            status=WcaEvaluationStatus.ACTIVE,
            signal=WcaSide.BUY,
            confidence=0.75,
            base_weight=0.11,
            effective_weight=0.11,
            contribution=0.0825,
            reason_codes=("wca.strategy.fixture",),
        )
        aggregation = WcaAggregationResult(
            signal=WcaSide.BUY,
            decision_label="Buy",
            buy_score=0.0825,
            sell_score=0,
            net_score=0.0825,
            active_weight=0.11,
            normalized_net_score=0.75,
            active_strategy_count=1,
            buy_agreement=1,
            sell_agreement=0,
            buy_average_confidence=0.75,
            sell_average_confidence=0,
            strategy_evaluations=(strategy,),
        )
        sizing = WcaSizingResult(
            final_quantity=10,
            risk_dollars=25,
            stop_distance=1,
            shares_by_risk=25,
            shares_by_order=10,
            shares_by_capital=10,
            shares_by_buying_power=10,
            shares_by_liquidity=100,
            limiting_factor="order limit",
        )
        order = ProposedOrder(
            decision_id="decision-1",
            order_intent_id="intent-1",
            symbol="SPY",
            side=WcaSide.BUY,
            quantity=10,
            limit_price=501,
        )
        decision = WcaDecision(
            decision_id="decision-1",
            configuration_version="config-v1",
            weight_version="weights-v1",
            data_timestamp=snapshot.data_timestamp,
            decision_timestamp=snapshot.decision_timestamp,
            market_snapshot=snapshot,
            market_status=WcaMarketStatus(status=WcaEvaluationStatus.ACTIVE),
            aggregation=aggregation,
            local_gates=(WcaLocalGateResult(gate_id="fixture", status=WcaGateStatus.PASS, blocks_entry=False),),
            sizing=sizing,
            proposed_order=order,
        )

        first = decision.deterministic_json()
        second = decision.deterministic_json()

        self.assertEqual(first, second)
        self.assertEqual(json.loads(first)["algorithm_id"], "wca")
        baseline = WcaBaselineSettings()
        self.assertEqual(baseline.deterministic_json(), baseline.deterministic_json())
        with self.assertRaises(ValidationError):
            WcaStrategyEvaluation(
                strategy_id="C0",
                name="Not applicable directional",
                status=WcaEvaluationStatus.NOT_APPLICABLE,
                signal=WcaSide.BUY,
                confidence=0,
                base_weight=0,
                effective_weight=0,
                contribution=0,
            )
        with self.assertRaises(ValidationError):
            WcaWeightSnapshot(weights={"C1": 0.6, "C2": 0.3})
        with self.assertRaises(ValidationError):
            GlobalGateResult(status=WcaGateStatus.PASS, proposed_quantity=1, allowed_quantity=2)

    def test_openapi_schemas_are_generated_successfully(self) -> None:
        client = TestClient(app)
        response = client.get("/openapi.json")

        self.assertEqual(response.status_code, 200, response.text)
        schemas = response.json()["components"]["schemas"]
        paths = response.json()["paths"]
        self.assertIn("WcaDecision", schemas)
        self.assertIn("WcaMarketSnapshot", schemas)
        self.assertIn("WcaStrategyEvaluation", schemas)
        self.assertIn("/api/wca/status", paths)
        self.assertIn("/api/wca/config/baseline", paths)


def sample_market_snapshot() -> WcaMarketSnapshot:
    timestamp = datetime(2026, 7, 15, 14, 30, tzinfo=timezone.utc)
    return WcaMarketSnapshot(
        symbol="SPY",
        data_timestamp=timestamp,
        decision_timestamp=timestamp,
        candles=(
            WcaCandle(
                timestamp=timestamp,
                open=500,
                high=501,
                low=499,
                close=500.5,
                volume=100000,
            ),
        ),
    )


def _matches_prefix(module_name: str, prefixes: tuple[str, ...]) -> bool:
    return any(module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in prefixes)


def _imported_modules(tree: ast.AST) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _assigned_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(_target_names(target))
        elif isinstance(node, ast.AnnAssign):
            names.update(_target_names(node.target))
    return names


def _target_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return {name for item in target.elts for name in _target_names(item)}
    return set()


if __name__ == "__main__":
    unittest.main()
