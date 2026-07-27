from __future__ import annotations

import ast
import sqlite3
import unittest
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID, META_STRATEGY_ALLOWED_SHARED_SERVICES
from backend.app.algorithms.meta_strategy.interfaces import (
    META_STRATEGY_APPROVED_SHARED_INTERFACE_IDS,
    META_STRATEGY_APPROVED_SHARED_INTERFACES,
    MetaStrategyAccountDataReader,
    MetaStrategyBrokerGateway,
    MetaStrategyClock,
    MetaStrategyGlobalRiskClient,
    MetaStrategyLogger,
    MetaStrategyMarketCalendar,
    MetaStrategyMarketDataReader,
    MetaStrategyMetrics,
)
from backend.app.algorithms.meta_strategy.ownership import (
    META_STRATEGY_AUTHORITATIVE_DOMAIN_IDS,
    META_STRATEGY_REQUIRED_IDENTITY_FIELDS,
    meta_strategy_ownership_contract,
)
from backend.app.algorithms.meta_strategy.repository import (
    META_STRATEGY_REPOSITORY_IDENTITY_COLUMNS,
    MetaStrategyRepositoryAttributionError,
    MetaStrategySqliteRepository,
    apply_meta_strategy_persistence_migrations,
)
from backend.tests.test_meta_strategy_step34_repository import sample_payload


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_PATH = ROOT / "backend" / "app" / "algorithms" / "meta_strategy"
PRIVATE_IMPORT_SUFFIXES = (
    ".repository",
    ".configuration",
    ".strategy_registry",
    ".strategies",
    ".runtime_state",
    ".position_state",
    ".settings",
    ".contracts",
)
SIBLING_PREFIXES = (
    "backend.app.algorithms.wca",
    "backend.app.algorithms.regime",
    "backend.app.algorithms.weighted_voting",
    "backend.app.algorithms.voting_ensemble",
)


class MetaStrategyPhase1ArchitectureContractsTest(unittest.TestCase):
    maxDiff = None

    def test_ownership_contract_covers_authoritative_domains_and_identities(self) -> None:
        expected_domains = {
            "settings",
            "inventory",
            "strategy_state",
            "decisions",
            "order_intents",
            "orders",
            "fills",
            "trades",
            "model_artifacts",
            "training",
            "replay",
            "backtesting",
            "promotion_evidence",
        }
        expected_identity_fields = {
            "algorithm_id",
            "capital_partition_id",
            "decision_id",
            "job_id",
            "event_id",
            "order_intent_id",
            "client_order_id",
            "broker_order_id",
            "settings_version",
            "model_version",
        }

        contract = meta_strategy_ownership_contract()

        self.assertEqual(contract["algorithmId"], ALGORITHM_ID)
        self.assertTrue(expected_domains.issubset(set(META_STRATEGY_AUTHORITATIVE_DOMAIN_IDS)))
        self.assertTrue(expected_identity_fields.issubset(set(META_STRATEGY_REQUIRED_IDENTITY_FIELDS)))
        self.assertFalse(contract["mayMutateForeignAlgorithmState"])
        self.assertFalse(contract["mayReadSiblingPrivateState"])
        for domain in contract["authoritativeDomains"]:
            with self.subTest(domain=domain["domain_id"]):
                self.assertTrue(domain["mutable_by_meta_strategy_only"])
                self.assertFalse(domain["may_read_sibling_private_state"])
                self.assertFalse(domain["may_mutate_foreign_algorithm_state"])

    def test_shared_services_are_explicit_protocol_interfaces(self) -> None:
        protocol_types = (
            MetaStrategyMarketDataReader,
            MetaStrategyAccountDataReader,
            MetaStrategyGlobalRiskClient,
            MetaStrategyBrokerGateway,
            MetaStrategyLogger,
            MetaStrategyMetrics,
            MetaStrategyClock,
            MetaStrategyMarketCalendar,
        )

        self.assertEqual(META_STRATEGY_APPROVED_SHARED_INTERFACE_IDS, META_STRATEGY_ALLOWED_SHARED_SERVICES)
        self.assertEqual(
            {contract.service_id for contract in META_STRATEGY_APPROVED_SHARED_INTERFACES},
            set(META_STRATEGY_ALLOWED_SHARED_SERVICES),
        )
        for protocol_type in protocol_types:
            with self.subTest(protocol=protocol_type.__name__):
                self.assertTrue(issubclass(protocol_type, Protocol))
                self.assertTrue(getattr(protocol_type, "_is_protocol", False))

    def test_cross_algorithm_private_imports_are_rejected_by_static_boundary(self) -> None:
        violations = []
        for path in sorted(PACKAGE_PATH.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for imported_module in imported_module_names(tree):
                if is_sibling_private_import(imported_module):
                    violations.append(f"{path.relative_to(PACKAGE_PATH).as_posix()} imports {imported_module}")
            for imported_module in dynamic_import_targets(tree):
                if is_sibling_private_import(imported_module):
                    violations.append(f"{path.relative_to(PACKAGE_PATH).as_posix()} dynamically imports {imported_module}")

        self.assertEqual(violations, [])

    def test_repository_schema_standardizes_identity_columns(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            apply_meta_strategy_persistence_migrations(conn)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(meta_strategy_decisions)").fetchall()}

        self.assertTrue(set(META_STRATEGY_REPOSITORY_IDENTITY_COLUMNS).issubset(columns))

    def test_repository_rejects_foreign_write_update_load_and_reconcile_records(self) -> None:
        path = temp_db_path()
        repository = MetaStrategySqliteRepository(f"sqlite:///{path}")

        with self.assertRaises(MetaStrategyRepositoryAttributionError):
            repository.persist("decisions", {**sample_payload("decisions"), "algorithmId": "weighted_voting"})
        with self.assertRaises(MetaStrategyRepositoryAttributionError):
            repository.persist("decisions", {**sample_payload("decisions"), "algorithmId": "regime"}, record_id="existing-record")
        with self.assertRaises(MetaStrategyRepositoryAttributionError):
            repository.persist("trades", {**sample_payload("trades"), "algorithmId": "wca"})

        insert_legacy_foreign_row(path)
        with self.assertRaises(MetaStrategyRepositoryAttributionError):
            repository.load("decisions", "foreign-record")
        with self.assertRaises(MetaStrategyRepositoryAttributionError):
            repository.latest_for_decision("decisions", "foreign-decision")

    def test_package_documentation_records_ownership_rules(self) -> None:
        readme = (PACKAGE_PATH / "README.md").read_text(encoding="utf-8")

        self.assertIn('algorithm_id="meta_strategy"', readme)
        self.assertIn("must never read or mutate sibling algorithm private repositories", readme)
        for domain_id in META_STRATEGY_AUTHORITATIVE_DOMAIN_IDS:
            with self.subTest(domain=domain_id):
                self.assertIn(domain_id, readme)
        for service_id in META_STRATEGY_ALLOWED_SHARED_SERVICES:
            with self.subTest(service=service_id):
                self.assertIn(service_id.replace("_", "-"), readme.replace(" ", "-"))


def imported_module_names(tree: ast.AST) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)


def dynamic_import_targets(tree: ast.AST) -> tuple[str, ...]:
    targets: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "importlib"
        ) or (isinstance(node.func, ast.Name) and node.func.id == "__import__"):
            for arg in node.args[:1]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    targets.append(arg.value)
    return tuple(targets)


def is_sibling_private_import(module_name: str) -> bool:
    if not any(module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in SIBLING_PREFIXES):
        return False
    return any(module_name.endswith(suffix) or f"{suffix}." in module_name for suffix in PRIVATE_IMPORT_SUFFIXES)


def insert_legacy_foreign_row(path: Path) -> None:
    payload = sample_payload("decisions")
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA ignore_check_constraints=ON")
        conn.execute(
            """
            INSERT INTO meta_strategy_decisions (
                record_id, artifact_type, algorithm_id, capital_partition_id, algorithm_version,
                configuration_version, settings_version, strategy_catalog_version, feature_schema_version,
                label_specification_version, model_version, model_artifact_version, dynamic_profile_version,
                position_sizing_version, exit_policy_version, backtest_engine_version, timestamp, symbol,
                bar_end, decision_id, idempotency_key, job_id, event_id, snapshot_id, order_intent_id,
                client_order_id, broker_order_id, trade_id, run_id, artifact_id, status, payload_json,
                created_at, updated_at
            )
            VALUES (
                'foreign-record', 'decisions', 'weighted_voting', 'weighted_voting.paper.default',
                'weighted_voting_algorithm_v1', 'weighted_voting_config_v1', 'weighted_voting_config_v1',
                'weighted_voting_catalog_v1', 'meta_strategy_feature_schema_v1',
                'meta_strategy_label_specification_v1', 'meta_strategy_model_v1',
                'meta_strategy_model_artifact_v1', 'meta_strategy_dynamic_profile_v1',
                'meta_strategy_position_sizing_v1', 'meta_strategy_exit_policy_v1',
                'meta_strategy_backtest_engine_v1', ?, 'SPY', ?, 'foreign-decision',
                'foreign-idempotency', 'foreign-job', 'foreign-event', 'foreign-snapshot',
                '', '', '', '', '', '', 'PERSISTED', ?, ?, ?
            )
            """,
            (payload["timestamp"], payload["timestamp"], "{}", payload["timestamp"], payload["timestamp"]),
        )


def temp_db_path() -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"meta-strategy-phase1-{uuid4().hex}.sqlite"


if __name__ == "__main__":
    unittest.main()
