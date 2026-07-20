from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.meta_strategy import (
    META_STRATEGY_PERSISTENCE_MIGRATION_VERSION,
    META_STRATEGY_PERSISTENCE_RECORD_IDS,
    META_STRATEGY_PERSISTENCE_RECORD_INVENTORY,
    META_STRATEGY_PERSISTENCE_TABLES,
    META_STRATEGY_REQUIRED_ATTRIBUTION_COLUMNS,
    META_STRATEGY_VERSION_COLUMNS,
    MetaStrategyRepositoryAttributionError,
    MetaStrategyRepositoryPersistenceAdapter,
    MetaStrategySqliteRepository,
    apply_meta_strategy_persistence_migrations,
    run_meta_strategy_execution_pipeline,
)
from backend.app.algorithms.meta_strategy.execution_pipeline import MetaStrategyExecutionPipelineRequest
from backend.tests.test_meta_strategy_step7_market_snapshot import DECISION_TIMESTAMP, request_with


EXPECTED_RECORD_IDS = (
    "configurations",
    "market_snapshots",
    "strategy_outputs",
    "family_scores",
    "candidates",
    "feature_sets",
    "labels",
    "training_runs",
    "validation_folds",
    "model_artifacts",
    "calibration_reports",
    "predictions",
    "decisions",
    "effective_profiles",
    "sizing_results",
    "order_intents",
    "trades",
    "backtests",
    "shadow_comparisons",
    "paper_stability",
    "promotions",
    "rollbacks",
)


class MetaStrategyStep34RepositoryTest(unittest.TestCase):
    def test_inventory_covers_all_authoritative_meta_strategy_state(self) -> None:
        self.assertEqual(tuple(record.record_id for record in META_STRATEGY_PERSISTENCE_RECORD_INVENTORY), EXPECTED_RECORD_IDS)
        self.assertEqual(META_STRATEGY_PERSISTENCE_RECORD_IDS, set(EXPECTED_RECORD_IDS))
        self.assertEqual(len(META_STRATEGY_PERSISTENCE_TABLES), len(EXPECTED_RECORD_IDS))
        self.assertTrue(all(table.startswith("meta_strategy_") for table in META_STRATEGY_PERSISTENCE_TABLES))

    def test_migration_creates_all_tables_with_attribution_and_version_columns_idempotently(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            apply_meta_strategy_persistence_migrations(conn)
            apply_meta_strategy_persistence_migrations(conn)

            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
            versions = {row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}

            self.assertTrue(set(META_STRATEGY_PERSISTENCE_TABLES).issubset(tables))
            self.assertIn(META_STRATEGY_PERSISTENCE_MIGRATION_VERSION, versions)
            for table in META_STRATEGY_PERSISTENCE_TABLES:
                with self.subTest(table=table):
                    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                    self.assertTrue(set(META_STRATEGY_REQUIRED_ATTRIBUTION_COLUMNS).issubset(columns))
                    self.assertTrue(set(META_STRATEGY_VERSION_COLUMNS).issubset(columns))
                    self.assertIn("payload_json", columns)

    def test_repository_rejects_missing_or_cross_algorithm_attribution(self) -> None:
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")

        with self.assertRaises(MetaStrategyRepositoryAttributionError):
            repository.persist("decisions", {"decisionId": "decision-1"})

        with self.assertRaises(MetaStrategyRepositoryAttributionError):
            repository.persist("decisions", {**sample_payload("decisions"), "algorithmId": "wca"})

    def test_repository_persists_every_artifact_type_and_survives_restart(self) -> None:
        path = temp_db_path()
        repository = MetaStrategySqliteRepository(f"sqlite:///{path}")

        persisted = {
            artifact_type: repository.persist(artifact_type, sample_payload(artifact_type))
            for artifact_type in EXPECTED_RECORD_IDS
        }

        reloaded = MetaStrategySqliteRepository(f"sqlite:///{path}")
        counts = reloaded.table_counts().table_counts
        inventory = reloaded.persistence_inventory()

        self.assertTrue(inventory["passed"])
        for artifact_type, record in persisted.items():
            with self.subTest(artifact_type=artifact_type):
                self.assertEqual(counts[record.table_name], 1)
                restored = reloaded.load(artifact_type, record.record_id)
                self.assertIsNotNone(restored)
                assert restored is not None
                self.assertEqual(restored.algorithm_id, "meta_strategy")
                self.assertEqual(restored.payload["artifactType"], artifact_type)

        with sqlite3.connect(path) as conn:
            for table in META_STRATEGY_PERSISTENCE_TABLES:
                owners = {row[0] for row in conn.execute(f"SELECT DISTINCT algorithm_id FROM {table}").fetchall()}
                self.assertEqual(owners, {"meta_strategy"}, table)

    def test_pipeline_persistence_adapter_recovers_decision_after_restart(self) -> None:
        path = temp_db_path()
        repository = MetaStrategySqliteRepository(f"sqlite:///{path}")

        result = run_meta_strategy_execution_pipeline(
            MetaStrategyExecutionPipelineRequest(mode="DIAGNOSTICS", snapshot_request=request_with()),
            persistence_adapter=MetaStrategyRepositoryPersistenceAdapter(repository),
        )

        restarted = MetaStrategySqliteRepository(f"sqlite:///{path}")
        recovered = restarted.latest_for_decision("decisions", result.snapshot.decision_id)

        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual(recovered.algorithm_id, "meta_strategy")
        self.assertEqual(recovered.payload["decisionId"], result.snapshot.decision_id)
        self.assertEqual(tuple(recovered.payload["stageSequence"]), result.stage_sequence)


def sample_payload(artifact_type: str) -> dict:
    return {
        "algorithmId": "meta_strategy",
        "algorithmVersion": "meta_strategy_algorithm_v1",
        "configurationVersion": "meta_strategy_config_v1",
        "strategyCatalogVersion": "meta_strategy_strategy_catalog_v1",
        "timestamp": DECISION_TIMESTAMP.isoformat(),
        "symbol": "SPY",
        "decisionId": "decision-1",
        "snapshotId": "snapshot-1",
        "orderIntentId": "meta_strategy.order_intent.decision-1" if artifact_type in {"order_intents", "trades"} else "",
        "tradeId": "meta_strategy.trade.decision-1" if artifact_type == "trades" else "",
        "runId": "meta_strategy.run.1" if artifact_type in {"training_runs", "validation_folds", "backtests"} else "",
        "artifactId": "meta_strategy.artifact.1" if artifact_type == "model_artifacts" else "",
        "artifactType": artifact_type,
        "payload": {"value": artifact_type},
    }


def temp_db_path() -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"meta-strategy-step34-{uuid4().hex}.sqlite"


if __name__ == "__main__":
    unittest.main()
