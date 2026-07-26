from __future__ import annotations

import ast
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.wca.configuration import default_wca_configuration
from backend.app.algorithms.wca.contracts import (
    ProposedOrder,
    WcaBrokerReconciliationResult,
    WcaSide,
)
from backend.app.algorithms.wca.execution_pipeline import WcaExecutionPipelineInput, run_wca_paper_pipeline_adapter
from backend.app.algorithms.wca.repository import (
    WCA_PERSISTENCE_RECORD_INVENTORY,
    WCA_PERSISTENCE_TABLES,
    WCA_PERSISTENCE_MIGRATION_VERSION,
    WcaSqliteRepository,
    apply_wca_persistence_migrations,
    classify_wca_local_storage_key,
)
from backend.app.algorithms.wca.weights import baseline_weight_snapshot
from backend.tests.test_wca_step5_production_pipeline import fake_voters, market_snapshot


UTC = timezone.utc


class WcaStep6InventoryPersistenceTests(unittest.TestCase):
    def test_migration_creates_all_authoritative_step6_records_and_uniqueness_guards(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            apply_wca_persistence_migrations(conn)
            apply_wca_persistence_migrations(conn)
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
            migration_count = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
                (WCA_PERSISTENCE_MIGRATION_VERSION,),
            ).fetchone()[0]

            self.assertEqual(migration_count, 1)
            self.assertTrue(set(WCA_PERSISTENCE_TABLES).issubset(tables))
            self.assertEqual(len(WCA_PERSISTENCE_RECORD_INVENTORY), 37)
            self.assert_primary_key(conn, "wca_finalized_bar_event_receipts", "event_id")
            self.assert_primary_key(conn, "wca_decisions", "decision_id")
            self.assert_primary_key(conn, "wca_broker_orders", "broker_order_id")
            self.assert_primary_key(conn, "wca_attributed_fills", "fill_id")
            self.assert_index_exists(conn, "wca_order_intents", "idx_wca_order_intents_idempotency")
            self.assert_index_exists(conn, "wca_execution_outbox", "idx_wca_execution_outbox_idempotency")
            self.assert_index_exists(conn, "wca_broker_orders", "idx_wca_broker_orders_idempotency")

    def test_event_claim_and_runtime_checkpoint_are_atomic_and_idempotent(self) -> None:
        repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
        event_time = datetime(2026, 1, 6, 17, 0, tzinfo=UTC)

        first_claim = repository.claim_finalized_bar_event(
            event_id="bar-SPY-20260106-1700",
            account_id="paper-step6",
            symbol="SPY",
            event_timestamp=event_time,
            configuration_version="cfg-step6",
            payload={"bar_status": "finalized"},
        )
        duplicate_claim = repository.claim_finalized_bar_event(
            event_id="bar-SPY-20260106-1700",
            account_id="paper-step6",
            symbol="SPY",
            event_timestamp=event_time,
            configuration_version="cfg-step6",
            payload={"bar_status": "finalized"},
        )

        self.assertTrue(first_claim)
        self.assertFalse(duplicate_claim)
        self.assertTrue(repository.compare_and_swap_runtime_checkpoint(checkpoint_key="wca-runtime", expected_version=None, payload={"offset": 1}))
        self.assertFalse(repository.compare_and_swap_runtime_checkpoint(checkpoint_key="wca-runtime", expected_version=None, payload={"offset": 2}))
        self.assertTrue(repository.compare_and_swap_runtime_checkpoint(checkpoint_key="wca-runtime", expected_version=1, payload={"offset": 2}))
        self.assertFalse(repository.compare_and_swap_runtime_checkpoint(checkpoint_key="wca-runtime", expected_version=1, payload={"offset": 3}))

    def test_decision_order_outbox_broker_fill_and_inventory_are_wca_attributed(self) -> None:
        repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
        decision = decision_with_order("step6-decision", "step6-intent", "step6-idempotency")

        repository.write_decision_snapshot(decision, run_id="step6-run")
        counts = repository.table_counts().table_counts

        self.assertEqual(counts["wca_decisions"], 1)
        self.assertGreater(counts["wca_strategy_settings_versions"], 0)
        self.assertGreater(counts["wca_modifier_evaluations"], 0)
        self.assertGreater(counts["wca_global_risk_responses"], 0)
        self.assertGreater(counts["wca_order_intents"], 0)
        self.assertTrue(repository.create_execution_outbox_record(decision, account_id="paper", idempotency_key="outbox-key"))
        self.assertFalse(repository.create_execution_outbox_record(decision, account_id="paper", idempotency_key="outbox-key"))
        self.assertTrue(repository.record_broker_order(decision, broker_order_id="broker-order-1", account_id="paper", idempotency_key="broker-key", status="accepted"))
        self.assertFalse(repository.record_broker_order(decision, broker_order_id="broker-order-1", account_id="paper", idempotency_key="broker-key", status="accepted"))
        self.assertTrue(repository.apply_fill_and_update_position(decision, fill_id="fill-1", account_id="paper", quantity=5, broker_order_id="broker-order-1"))
        self.assertFalse(repository.apply_fill_and_update_position(decision, fill_id="fill-1", account_id="paper", quantity=5, broker_order_id="broker-order-1"))

        with sqlite3.connect(repository.path) as conn:
            conn.row_factory = sqlite3.Row
            lot = conn.execute("SELECT * FROM wca_owned_lots WHERE lot_id = ?", ("wca-lot-fill-1",)).fetchone()
            fill_count = conn.execute("SELECT COUNT(*) FROM wca_attributed_fills WHERE fill_id = ?", ("fill-1",)).fetchone()[0]
            self.assertEqual(fill_count, 1)
            self.assertEqual(lot["algorithm_id"], "wca")
            self.assertEqual(lot["account_id"], "paper")
            self.assertEqual(lot["symbol"], "SPY")

    def test_wca_lot_reduction_requires_wca_owned_lot_not_broker_net_position(self) -> None:
        repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
        decision = decision_with_order("lot-decision", "lot-intent", "lot-idempotency")
        self.assertTrue(repository.apply_fill_and_update_position(decision, fill_id="lot-fill", account_id="paper", quantity=5))

        allowed = repository.authorize_wca_lot_reduction(lot_id="wca-lot-lot-fill", account_id="paper", symbol="SPY", quantity=3)
        too_large = repository.authorize_wca_lot_reduction(lot_id="wca-lot-lot-fill", account_id="paper", symbol="SPY", quantity=6)
        missing = repository.authorize_wca_lot_reduction(lot_id="broker-net-spy", account_id="paper", symbol="SPY", quantity=1)

        self.assertTrue(allowed.allowed)
        self.assertFalse(too_large.allowed)
        self.assertIn("wca.lot_quantity_exceeded", too_large.reason_codes)
        self.assertFalse(missing.allowed)
        self.assertIn("wca.lot_not_owned", missing.reason_codes)

    def test_unexplained_reconciliation_discrepancy_blocks_new_entries_fail_closed(self) -> None:
        repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
        self.assertFalse(repository.reconciliation_blocks_new_entries(account_id="paper", symbol="SPY"))

        repository.write_broker_reconciliation(
            WcaBrokerReconciliationResult(
                reconciliation_id="recon-step6",
                account_id="paper",
                evaluated_at=datetime(2026, 1, 6, 17, 5, tzinfo=UTC),
                intents_checked=1,
                broker_open_orders_checked=1,
                broker_positions_checked=1,
                hard_operational_warning=True,
                reason_codes=("wca.reconciliation.unexplained_broker_discrepancy",),
            )
        )

        self.assertTrue(repository.reconciliation_blocks_new_entries(account_id="paper", symbol="SPY"))

    def test_frontend_local_storage_is_not_authoritative_wca_state(self) -> None:
        self.assertEqual(classify_wca_local_storage_key("confidence-trade-history-v1"), "ignored_authoritative_backend_state")
        self.assertEqual(classify_wca_local_storage_key("wca-arbitrary-state"), "ignored_unknown_wca_local_storage")
        self.assertEqual(classify_wca_local_storage_key("ui-selected-tab"), "allowed_visual_preference")

    def test_sibling_algorithm_packages_do_not_import_wca_persistence_or_tables(self) -> None:
        root = Path("backend/app/algorithms")
        forbidden_modules = {
            "backend.app.algorithms.wca.repository",
            "backend.app.algorithms.wca.configuration",
        }
        forbidden_tables = {row.table_name for row in WCA_PERSISTENCE_RECORD_INVENTORY}
        violations: list[str] = []
        for path in root.rglob("*.py"):
            if "\\wca\\" in str(path) or "/wca/" in str(path):
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
                    violations.append(f"{path}:{node.lineno}: imports {node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in forbidden_modules:
                            violations.append(f"{path}:{node.lineno}: imports {alias.name}")
            for table in forbidden_tables:
                if table in source:
                    violations.append(f"{path}: references WCA table {table}")

        self.assertEqual(violations, [])

    def assert_primary_key(self, conn: sqlite3.Connection, table: str, column: str) -> None:
        columns = {row[1]: row[5] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        self.assertGreater(columns.get(column, 0), 0, f"{table}.{column} must be a primary key")

    def assert_index_exists(self, conn: sqlite3.Connection, table: str, index_name: str) -> None:
        indexes = {row[1] for row in conn.execute(f"PRAGMA index_list({table})").fetchall()}
        self.assertIn(index_name, indexes)


def decision_with_order(decision_id: str, order_intent_id: str, idempotency_key: str):
    configuration = default_wca_configuration()
    snapshot = market_snapshot()
    weight_snapshot = baseline_weight_snapshot(cutoff=snapshot.decision_timestamp, weight_version=f"{decision_id}.weights.v1")
    decision = run_wca_paper_pipeline_adapter(
        WcaExecutionPipelineInput(
            run_id=f"{decision_id}-run",
            decision_id=decision_id,
            order_intent_id=order_intent_id,
            snapshot=snapshot,
            configuration_version=configuration.configuration_version,
            configuration=configuration,
            weight_snapshot=weight_snapshot,
            global_gate_quantity_cap=1000,
            approved_risk_budget=1000,
        ),
        voters=fake_voters(WcaSide.BUY),
    ).decision
    proposed = ProposedOrder(
        decision_id=decision.decision_id,
        configuration_version=decision.configuration_version,
        configuration_hash=decision.configuration_hash,
        order_intent_id=order_intent_id,
        idempotency_key=idempotency_key,
        account_id=getattr(decision.market_snapshot, "account_id", "paper"),
        symbol=decision.market_snapshot.symbol,
        side=WcaSide.BUY,
        quantity=5,
        trigger_price=decision.market_snapshot.candles[-1].close,
        stop_price=decision.market_snapshot.candles[-1].close - 1,
        target_price=decision.market_snapshot.candles[-1].close + 2,
    )
    return decision.model_copy(update={"proposed_order": proposed})


def temp_db_path() -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"wca-step6-{uuid4().hex}.sqlite"


if __name__ == "__main__":
    unittest.main()
