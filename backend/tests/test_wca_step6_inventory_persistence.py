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
    WcaOrderValidationContext,
    WcaRuntimeMode,
    WcaSide,
)
from backend.app.algorithms.wca.execution_pipeline import WcaExecutionPipelineInput, run_wca_paper_pipeline_adapter
from backend.app.algorithms.wca.repository import (
    WCA_PERSISTENCE_RECORD_INVENTORY,
    WCA_PERSISTENCE_TABLES,
    WCA_PERSISTENCE_MIGRATION_VERSION,
    WcaInventoryLedgerEvent,
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
            self.assertEqual(len(WCA_PERSISTENCE_RECORD_INVENTORY), 49)
            self.assertIn("wca_runtime_control", WCA_PERSISTENCE_TABLES)
            self.assert_primary_key(conn, "wca_finalized_bar_event_receipts", "event_id")
            self.assert_primary_key(conn, "wca_decisions", "decision_id")
            self.assert_primary_key(conn, "wca_broker_orders", "broker_order_id")
            self.assert_primary_key(conn, "wca_attributed_fills", "fill_id")
            self.assert_primary_key(conn, "wca_inventory_ledger", "inventory_event_id")
            self.assert_primary_key(conn, "wca_broker_account_snapshots", "broker_snapshot_id")
            self.assert_index_exists(conn, "wca_order_intents", "idx_wca_order_intents_idempotency")
            self.assert_index_exists(conn, "wca_execution_outbox", "idx_wca_execution_outbox_idempotency")
            self.assert_index_exists(conn, "wca_broker_orders", "idx_wca_broker_orders_idempotency")
            self.assert_index_exists(conn, "wca_inventory_ledger", "idx_wca_inventory_ledger_fill_id")
            self.assert_index_exists(conn, "wca_inventory_ledger", "idx_wca_inventory_ledger_client_order_submission")

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
        # The outbox record carries the order's own idempotency key. Production keeps the
        # two identical -- service.py derives the outbox key as
        # "proposed.idempotency_key or <generated>" and writes it back onto the order -- and
        # applying a fill rejects any outbox row whose key disagrees with the order it claims
        # to belong to. A different literal here builds a state the system does not produce.
        outbox_key = decision.proposed_order.idempotency_key
        self.assertTrue(repository.create_execution_outbox_record(decision, account_id="paper", idempotency_key=outbox_key, final_validation_context=validation_context(decision)))
        self.assertFalse(repository.create_execution_outbox_record(decision, account_id="paper", idempotency_key=outbox_key, final_validation_context=validation_context(decision)))
        self.assertTrue(repository.record_broker_order(decision, broker_order_id="broker-order-1", account_id="paper", idempotency_key="broker-key", status="accepted"))
        self.assertFalse(repository.record_broker_order(decision, broker_order_id="broker-order-1", account_id="paper", idempotency_key="broker-key", status="accepted"))
        self.assertTrue(repository.apply_fill_and_update_position(decision, fill_id="fill-1", account_id="paper", quantity=5, broker_order_id="broker-order-1"))
        self.assertFalse(repository.apply_fill_and_update_position(decision, fill_id="fill-1", account_id="paper", quantity=5, broker_order_id="broker-order-1"))

        with sqlite3.connect(repository.path) as conn:
            conn.row_factory = sqlite3.Row
            lot = conn.execute("SELECT * FROM wca_owned_lots WHERE lot_id = ?", ("wca-lot-fill-1",)).fetchone()
            fill_count = conn.execute("SELECT COUNT(*) FROM wca_attributed_fills WHERE fill_id = ?", ("fill-1",)).fetchone()[0]
            inventory_event_count = conn.execute("SELECT COUNT(*) FROM wca_inventory_ledger WHERE fill_id = ?", ("fill-1",)).fetchone()[0]
            self.assertEqual(fill_count, 1)
            self.assertEqual(inventory_event_count, 1)
            self.assertEqual(lot["algorithm_id"], "wca")
            self.assertEqual(lot["account_id"], "paper")
            self.assertEqual(lot["symbol"], "SPY")

    def test_inventory_ledger_events_are_idempotent_and_projection_rebuild_is_deterministic(self) -> None:
        repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
        self.assertTrue(
            repository.record_inventory_event(
                WcaInventoryLedgerEvent(
                    inventory_event_id="ledger-order-1",
                    event_type="ORDER_INTENT_RESERVED",
                    broker_account_id="paper-ledger",
                    symbol="SPY",
                    event_timestamp="2026-01-06T14:30:00+00:00",
                    trade_date="2026-01-06",
                    order_intent_id="intent-ledger-1",
                    side="BUY",
                    quantity=5,
                    remaining_quantity=5,
                    configuration_version="cfg-ledger",
                    decision_id="decision-ledger",
                    run_id="run-ledger",
                )
            )
        )
        self.assertFalse(
            repository.record_inventory_event(
                WcaInventoryLedgerEvent(
                    inventory_event_id="ledger-order-1",
                    event_type="ORDER_INTENT_RESERVED",
                    broker_account_id="paper-ledger",
                    symbol="SPY",
                    event_timestamp="2026-01-06T14:30:00+00:00",
                    trade_date="2026-01-06",
                )
            )
        )
        repository.record_inventory_event(
            WcaInventoryLedgerEvent(
                inventory_event_id="ledger-risk-1",
                event_type="RISK_RESERVED",
                broker_account_id="paper-ledger",
                symbol="SPY",
                event_timestamp="2026-01-06T14:30:01+00:00",
                trade_date="2026-01-06",
                reserved_risk=25.0,
            )
        )
        repository.record_inventory_event(
            WcaInventoryLedgerEvent(
                inventory_event_id="ledger-fill-buy",
                event_type="FILL_RECEIVED",
                broker_account_id="paper-ledger",
                symbol="SPY",
                event_timestamp="2026-01-06T14:31:00+00:00",
                trade_date="2026-01-06",
                fill_id="ledger-fill-buy",
                side="BUY",
                quantity=5,
                filled_quantity=5,
                fill_price=100.0,
            )
        )
        repository.record_inventory_event(
            WcaInventoryLedgerEvent(
                inventory_event_id="ledger-fill-sell",
                event_type="FILL_RECEIVED",
                broker_account_id="paper-ledger",
                symbol="SPY",
                event_timestamp="2026-01-06T15:00:00+00:00",
                trade_date="2026-01-06",
                fill_id="ledger-fill-sell",
                side="SELL",
                quantity=2,
                filled_quantity=2,
                fill_price=104.0,
                reserved_risk=10.0,
            )
        )

        before = repository.read_inventory_projection(algorithm_id="wca", broker_account_id="paper-ledger", symbol="SPY")
        daily_before = repository.read_daily_state_projection(algorithm_id="wca", broker_account_id="paper-ledger", symbol="SPY", session_date="2026-01-06")

        with sqlite3.connect(repository.path) as conn:
            conn.execute("DELETE FROM wca_inventory_projection")
            conn.execute("DELETE FROM wca_daily_state")
        repository.rebuild_inventory_projections(algorithm_id="wca", broker_account_id="paper-ledger", symbol="SPY")

        after = repository.read_inventory_projection(algorithm_id="wca", broker_account_id="paper-ledger", symbol="SPY")
        daily_after = repository.read_daily_state_projection(algorithm_id="wca", broker_account_id="paper-ledger", symbol="SPY", session_date="2026-01-06")

        self.assertEqual(before, after)
        self.assertEqual(daily_before, daily_after)
        self.assertEqual(after.open_quantity, 3)
        self.assertEqual(after.average_entry_price, 100.0)
        self.assertEqual(after.realized_pnl, 8.0)
        self.assertEqual(after.reserved_risk, 25.0)
        self.assertEqual(daily_after.entries_attempted_today, 1)
        self.assertEqual(daily_after.realized_pnl_today, 8.0)
        self.assertEqual(daily_after.maximum_intraday_exposure, 500.0)

    def test_inventory_ledger_rejects_cross_algorithm_writes_and_scopes_reads_by_account(self) -> None:
        repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
        repository.record_inventory_event(
            WcaInventoryLedgerEvent(
                inventory_event_id="account-a-fill",
                event_type="FILL_RECEIVED",
                broker_account_id="paper-a",
                symbol="SPY",
                event_timestamp="2026-01-06T14:31:00+00:00",
                trade_date="2026-01-06",
                fill_id="account-a-fill",
                side="BUY",
                quantity=5,
                filled_quantity=5,
                fill_price=100.0,
            )
        )
        repository.record_inventory_event(
            WcaInventoryLedgerEvent(
                inventory_event_id="account-b-fill",
                event_type="FILL_RECEIVED",
                broker_account_id="paper-b",
                symbol="SPY",
                event_timestamp="2026-01-06T14:31:00+00:00",
                trade_date="2026-01-06",
                fill_id="account-b-fill",
                side="BUY",
                quantity=7,
                filled_quantity=7,
                fill_price=100.0,
            )
        )

        account_a = repository.read_inventory_projection(algorithm_id="wca", broker_account_id="paper-a", symbol="SPY")
        account_b = repository.read_inventory_projection(algorithm_id="wca", broker_account_id="paper-b", symbol="SPY")
        self.assertEqual(account_a.open_quantity, 5)
        self.assertEqual(account_b.open_quantity, 7)
        self.assertEqual(len(repository.list_inventory_ledger_events(algorithm_id="wca", broker_account_id="paper-a", symbol="SPY")), 1)

        with self.assertRaises(ValueError):
            repository.record_inventory_event(
                WcaInventoryLedgerEvent(
                    inventory_event_id="foreign-fill",
                    event_type="FILL_RECEIVED",
                    algorithm_id="mean_reversion",
                    broker_account_id="paper-a",
                    symbol="SPY",
                    event_timestamp="2026-01-06T14:31:00+00:00",
                    trade_date="2026-01-06",
                )
            )
        with self.assertRaises(ValueError):
            repository.read_inventory_projection(algorithm_id="mean_reversion", broker_account_id="paper-a", symbol="SPY")

    def test_inventory_ledger_database_constraints_reject_duplicate_keys_and_impossible_reductions(self) -> None:
        repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
        repository.record_inventory_event(
            WcaInventoryLedgerEvent(
                inventory_event_id="duplicate-fill-1",
                event_type="FILL_RECEIVED",
                broker_account_id="paper",
                symbol="SPY",
                event_timestamp="2026-01-06T14:31:00+00:00",
                trade_date="2026-01-06",
                fill_id="same-fill",
                side="BUY",
                quantity=1,
                filled_quantity=1,
                fill_price=100.0,
            )
        )
        with self.assertRaises(sqlite3.IntegrityError):
            repository.record_inventory_event(
                WcaInventoryLedgerEvent(
                    inventory_event_id="duplicate-fill-2",
                    event_type="FILL_RECEIVED",
                    broker_account_id="paper",
                    symbol="SPY",
                    event_timestamp="2026-01-06T14:32:00+00:00",
                    trade_date="2026-01-06",
                    fill_id="same-fill",
                    side="BUY",
                    quantity=1,
                    filled_quantity=1,
                    fill_price=100.0,
                )
            )
        repository.record_inventory_event(
            WcaInventoryLedgerEvent(
                inventory_event_id="submitted-client-1",
                event_type="ORDER_SUBMITTED",
                broker_account_id="paper",
                symbol="SPY",
                event_timestamp="2026-01-06T14:33:00+00:00",
                trade_date="2026-01-06",
                client_order_id="same-client-order",
            )
        )
        with self.assertRaises(sqlite3.IntegrityError):
            repository.record_inventory_event(
                WcaInventoryLedgerEvent(
                    inventory_event_id="submitted-client-2",
                    event_type="ORDER_SUBMITTED",
                    broker_account_id="paper",
                    symbol="SPY",
                    event_timestamp="2026-01-06T14:34:00+00:00",
                    trade_date="2026-01-06",
                    client_order_id="same-client-order",
                )
            )
        with self.assertRaises(ValueError):
            repository.record_inventory_event(
                WcaInventoryLedgerEvent(
                    inventory_event_id="impossible-sell",
                    event_type="FILL_RECEIVED",
                    broker_account_id="paper-empty",
                    symbol="SPY",
                    event_timestamp="2026-01-06T15:00:00+00:00",
                    trade_date="2026-01-06",
                    fill_id="impossible-sell",
                    side="SELL",
                    quantity=1,
                    filled_quantity=1,
                    fill_price=100.0,
                )
            )

    def test_inventory_migration_preserves_existing_records_and_adds_new_tables(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            conn.execute(
                """
                CREATE TABLE wca_attributed_fills (
                    fill_id TEXT PRIMARY KEY,
                    algorithm_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    configuration_version TEXT NOT NULL,
                    engine_version TEXT NOT NULL,
                    market_snapshot_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                INSERT INTO wca_attributed_fills (
                    fill_id, algorithm_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id, side, quantity, payload_json
                )
                VALUES ('legacy-fill', 'wca', 'SPY', '2026-01-05T15:00:00+00:00', 'cfg',
                        'engine', 'market', 'decision', 'run', 'BUY', 1, '{}')
                """
            )
            apply_wca_persistence_migrations(conn)

            row = conn.execute("SELECT fill_id, account_id FROM wca_attributed_fills WHERE fill_id = 'legacy-fill'").fetchone()
            tables = {record[0] for record in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
            self.assertEqual(row, ("legacy-fill", "paper"))
            self.assertIn("wca_inventory_ledger", tables)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO wca_inventory_ledger (
                        inventory_event_id, event_type, algorithm_id, broker_account_id, symbol,
                        trade_date, event_timestamp, source_authority
                    )
                    VALUES ('bad-algo', 'ORDER_SUBMITTED', 'other', 'paper', 'SPY',
                            '2026-01-06', '2026-01-06T14:30:00+00:00', 'test')
                    """
                )

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
        self.assertTrue(repository.reconciliation_blocks_new_entries(account_id="paper", symbol="SPY"))

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
    entry_price = decision.market_snapshot.candles[-1].close
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
        trigger_price=entry_price,
        limit_price=entry_price,
        stop_price=entry_price - 1,
        target_price=entry_price + 2,
    )
    sizing = decision.sizing.model_copy(
        update={
            "final_quantity": 5,
            "risk_dollars": 5,
            "stop_distance": 1,
            "shares_by_risk": 5,
            "shares_by_order": 5,
            "shares_by_capital": 5,
            "shares_by_buying_power": 5,
            "shares_by_liquidity": 5,
            "limiting_factor": "test_fixture",
            "blocked_reason": "",
            "side": WcaSide.BUY,
            "entry_price": entry_price,
            "stop_price": entry_price - 1,
            "target_price": entry_price + 2,
            "minimum_reward_risk": 1.5,
            "reward_risk_ratio": 2,
            "approved_risk_budget": 1000,
            "stop_risk_dollars": 5,
            "shares_by_global_gate": 5,
        }
    )
    return decision.model_copy(update={"sizing": sizing, "proposed_order": proposed})


def validation_context(decision) -> WcaOrderValidationContext:
    return WcaOrderValidationContext(
        evaluation_timestamp=decision.decision_timestamp,
        account_id="paper",
        broker_endpoint="paper",
        runtime_mode=WcaRuntimeMode.MANUAL_PAPER,
        requires_executable_paper_stage=True,
        data_ready=decision.market_snapshot.data_ready,
        quote_freshness_seconds=15,
        candle_freshness_seconds=120,
        available_buying_power=100_000,
        account_equity=100_000,
        max_position_value=100_000,
        max_spread_percent=decision.effective_settings.final_max_spread_percent,
        average_one_minute_volume=100_000,
        max_participation_percent=decision.effective_settings.final_max_participation_percent,
        expected_net_edge=1,
        minimum_net_edge=0,
        idempotency_required=True,
        max_approved_quantity=1000,
        order_type="LIMIT",
        time_in_force="DAY",
        protective_exit_plan_present=True,
    )


def temp_db_path() -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"wca-step6-{uuid4().hex}.sqlite"


if __name__ == "__main__":
    unittest.main()
