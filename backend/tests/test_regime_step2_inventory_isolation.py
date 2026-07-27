from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime.persistence import (
    REGIME_OWNED_TABLES,
    REGIME_OWNERSHIP_KEY_COLUMNS,
    REGIME_SHARED_ATTRIBUTED_TABLES,
    RegimeSqliteRepository,
    migrate_regime_sqlite_database,
)


EXPECTED_STEP2_TABLES = (
    "regime_settings_versions",
    "regime_active_settings",
    "regime_strategy_settings",
    "regime_runtime_instances",
    "regime_runtime_commands",
    "regime_runtime_events",
    "regime_runtime_checkpoints",
    "regime_hysteresis_state",
    "regime_daily_counters",
    "regime_strategy_performance",
    "regime_decisions",
    "regime_classifications",
    "regime_transitions",
    "regime_strategy_outputs",
    "regime_context_outputs",
    "regime_confirmation_outputs",
    "regime_safety_results",
    "regime_family_scores",
    "regime_effective_profiles",
    "regime_local_risk_results",
    "regime_order_intents",
    "regime_execution_outbox",
    "regime_orders",
    "regime_fills",
    "regime_positions",
    "regime_trades",
    "regime_reconciliation_events",
    "regime_backtest_jobs",
    "regime_backtest_runs",
    "regime_backtest_trades",
    "regime_rollout_evidence",
)


class RegimeStep2InventoryIsolationTest(unittest.TestCase):
    def test_inventory_contains_complete_regime_owned_tables_and_indexes(self) -> None:
        path = temp_db_path()
        repository = RegimeSqliteRepository(f"sqlite:///{path}")

        self.assertEqual(REGIME_OWNED_TABLES[: len(EXPECTED_STEP2_TABLES)], EXPECTED_STEP2_TABLES)
        inventory = repository.persistence_inventory()
        self.assertEqual(tuple(inventory["ownershipKeyColumns"]), REGIME_OWNERSHIP_KEY_COLUMNS)
        self.assertEqual(tuple(inventory["ownershipKeyColumns"]), ("algorithm_id", "algorithm_instance_id", "account_id", "runtime_mode", "symbol"))
        self.assertTrue(inventory["passed"])

        with sqlite3.connect(path) as conn:
            for table in EXPECTED_STEP2_TABLES:
                columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                self.assertTrue(set(REGIME_OWNERSHIP_KEY_COLUMNS).issubset(columns), table)
                self.assertIn("sequence_version", columns, table)
                self.assertIn("processing_status", columns, table)
                index_names = {row[1] for row in conn.execute(f"PRAGMA index_list({table})")}
                for suffix in (
                    "instance_symbol",
                    "event_timestamp",
                    "decision",
                    "order_intent",
                    "broker_order",
                    "position",
                    "trade",
                    "settings_version",
                    "processing_status",
                ):
                    self.assertIn(f"idx_{table}_{suffix}", index_names, f"{table}:{suffix}")

    def test_migration_is_idempotent_and_backward_compatible(self) -> None:
        path = temp_db_path()
        migrate_regime_sqlite_database(path)
        migrate_regime_sqlite_database(path)
        repository = RegimeSqliteRepository(f"sqlite:///{path}")

        with sqlite3.connect(path) as conn:
            versions = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        self.assertIn("regime_persistence_step2_003", versions)
        self.assertTrue(repository.persistence_inventory()["passed"])

    def test_cross_algorithm_writes_and_reads_fail_safely(self) -> None:
        repository = RegimeSqliteRepository(f"sqlite:///{temp_db_path()}")

        with self.assertRaises(ValueError):
            repository.record_runtime_event({"algorithmId": "weighted_voting", "symbol": "SPY", "eventId": "bad-write"})

        with self.assertRaises(ValueError):
            repository.read_owned_records("broker_orders", identity=identity())

        with self.assertRaises(ValueError):
            repository.read_owned_records("regime_runtime_events", identity={**identity(), "algorithmId": "wca"})

    def test_duplicate_order_intent_insertion_fails_closed(self) -> None:
        repository = RegimeSqliteRepository(f"sqlite:///{temp_db_path()}")
        intent = {**identity(), "decisionId": "decision-1", "orderIntentId": "intent-1", "side": "Buy", "quantity": 10}

        first = repository.insert_order_intent(intent)
        second = repository.insert_order_intent(intent)

        self.assertTrue(first["inserted"])
        self.assertFalse(second["inserted"])
        self.assertEqual(second["reason"], "duplicate_order_intent")
        self.assertEqual(repository.table_counts()["regime_execution_outbox"], 1)

    def test_invalid_algorithm_id_is_rejected_by_schema_and_repository(self) -> None:
        path = temp_db_path()
        repository = RegimeSqliteRepository(f"sqlite:///{path}")

        with self.assertRaises(ValueError):
            repository.insert_order_intent({**identity(), "algorithm_id": "meta_strategy", "orderIntentId": "intent-2"})

        with sqlite3.connect(path) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO regime_runtime_events (
                        record_id, algorithm_id, algorithm_instance_id, account_id, runtime_mode,
                        algorithm_version, settings_version, strategy_version, profile_version,
                        timestamp, event_timestamp, symbol, data_timestamp, decision_id,
                        processing_status, sequence_version, payload_json
                    )
                    VALUES ('bad', 'wca', 'regime-default', 'default', 'shadow',
                            'v', 's', 'c', 'p', '', '', 'SPY', '', 'd', 'recorded', 1, '{}')
                    """
                )

    def test_stale_state_version_update_fails_and_runtime_instances_are_isolated(self) -> None:
        repository = RegimeSqliteRepository(f"sqlite:///{temp_db_path()}")
        first = repository.write_runtime_checkpoint({**identity("instance-a"), "decisionId": "checkpoint-a", "payload": {"last": 1}}, expected_sequence_version=0)
        stale = repository.write_runtime_checkpoint({**identity("instance-a"), "decisionId": "checkpoint-a", "payload": {"last": 2}}, expected_sequence_version=0)
        second_instance = repository.write_runtime_checkpoint({**identity("instance-b"), "decisionId": "checkpoint-b", "payload": {"last": 99}}, expected_sequence_version=0)

        self.assertTrue(first["updated"])
        self.assertFalse(stale["updated"])
        self.assertEqual(stale["reason"], "stale_state_version")
        self.assertTrue(second_instance["updated"])
        self.assertEqual(repository.read_runtime_checkpoint(identity("instance-a"))["payload"], {"last": 1})
        self.assertEqual(repository.read_runtime_checkpoint(identity("instance-b"))["payload"], {"last": 99})

    def test_broker_observations_copy_into_regime_owned_ledgers_with_secret_redaction(self) -> None:
        path = temp_db_path()
        repository = RegimeSqliteRepository(f"sqlite:///{path}")
        result = repository.copy_broker_observation(
            {
                **identity(),
                "type": "fill",
                "decisionId": "decision-fill",
                "orderIntentId": "intent-fill",
                "brokerOrderId": "broker-123",
                "api_key": "secret-value",
                "filledQuantity": 5,
            }
        )

        self.assertEqual(result["table"], "regime_fills")
        with sqlite3.connect(path) as conn:
            shared_rows = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
            owned = conn.execute("SELECT algorithm_id, broker_order_id, order_intent_id, payload_json FROM regime_fills").fetchone()
        self.assertEqual(shared_rows, 0)
        self.assertEqual(owned[0], "regime")
        self.assertEqual(owned[1], "broker-123")
        self.assertEqual(owned[2], "intent-fill")
        self.assertIn("[REDACTED]", owned[3])
        self.assertNotIn("secret-value", owned[3])


def identity(instance: str = "regime-default") -> dict[str, str]:
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": instance,
        "accountId": "paper-account",
        "runtimeMode": "paper",
        "symbol": "SPY",
    }


def temp_db_path() -> Path:
    root = Path(__file__).resolve().parent / "tmp" / "regime_step2"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{uuid4().hex}.sqlite"


if __name__ == "__main__":
    unittest.main()
