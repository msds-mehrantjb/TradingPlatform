from __future__ import annotations

import sqlite3
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.meta_strategy import (
    META_STRATEGY_INVENTORY_TABLES,
    MetaStrategyApplicationService,
    MetaStrategyRepositoryAttributionError,
    MetaStrategySqliteRepository,
    deterministic_meta_strategy_client_order_id,
)
from backend.tests.test_meta_strategy_step7_market_snapshot import request_with


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)


class MetaStrategyPhase5InventoryTest(unittest.TestCase):
    maxDiff = None

    def test_migration_creates_dedicated_inventory_tables_with_attribution(self) -> None:
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")

        with sqlite3.connect(repository.path) as conn:
            conn.row_factory = sqlite3.Row
            tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue(set(META_STRATEGY_INVENTORY_TABLES).issubset(tables))
            for table in META_STRATEGY_INVENTORY_TABLES:
                with self.subTest(table=table):
                    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                    self.assertTrue({"algorithm_id", "capital_partition_id", "settings_version", "correlation_id", "payload_json"}.issubset(columns))

    def test_order_submission_does_not_create_position_until_fill_arrives(self) -> None:
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")
        repository.record_order_intent(order_intent_payload(quantity=10, reserved_risk=125.0))
        repository.record_submitted_order(order_payload(status="ACCEPTED"))

        snapshot = repository.current_inventory_snapshot()

        self.assertEqual(snapshot.open_positions, ())
        self.assertEqual(snapshot.reserved_risk_dollars, 125.0)
        self.assertEqual(snapshot.daily_trade_count, 0)

    def test_application_service_rejects_caller_supplied_authoritative_trading_state(self) -> None:
        service = MetaStrategyApplicationService()

        result = service.paper_evaluate(
            {
                "snapshotRequest": request_with().model_dump(mode="json"),
                "availableBuyingPower": 1_000_000,
                "brokerQuantity": 25,
                "dailyTradeCount": 99,
                "duplicateOrderIntentIds": ("client-supplied-duplicate",),
                "existingPositionSymbols": ("SPY",),
            }
        )

        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("meta_strategy.service.caller_supplied_trading_state_rejected", result["reasonCodes"])
        self.assertEqual(
            result["payload"]["boundary"],
            "trading_state_must_come_from_meta_strategy_repositories_and_read_only_shared_views",
        )
        self.assertEqual(
            result["payload"]["rejectedFields"],
            ["availableBuyingPower", "brokerQuantity", "dailyTradeCount", "duplicateOrderIntentIds", "existingPositionSymbols"],
        )

    def test_foreign_fill_cannot_alter_meta_strategy_inventory(self) -> None:
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")
        repository.record_order_intent(order_intent_payload(quantity=10))

        with self.assertRaises(MetaStrategyRepositoryAttributionError):
            repository.ingest_broker_fill(fill_payload(algorithm_id="weighted_voting", broker_fill_id="foreign-fill", quantity=10))
        with self.assertRaises(MetaStrategyRepositoryAttributionError):
            repository.ingest_broker_fill(fill_payload(broker_fill_id="foreign-partition-fill", quantity=10, capital_partition_id="meta_strategy.paper.other"))

        self.assertEqual(repository.current_inventory_snapshot().open_positions, ())

    def test_database_constraints_reject_foreign_algorithm_and_partition_rows(self) -> None:
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")

        with sqlite3.connect(repository.path) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                insert_raw_inventory_row(conn, table="meta_strategy_inventory_fills", algorithm_id="weighted_voting", capital_partition_id="meta_strategy.paper.default")
            with self.assertRaises(sqlite3.IntegrityError):
                insert_raw_inventory_row(conn, table="meta_strategy_inventory_fills", algorithm_id="meta_strategy", capital_partition_id="meta_strategy.paper.other")

    def test_meta_strategy_and_sibling_algorithm_same_symbol_remain_separate(self) -> None:
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")
        repository.record_order_intent(order_intent_payload(quantity=10))
        repository.ingest_broker_fill(fill_payload(broker_fill_id="meta-fill-1", quantity=10, price=100.0))
        with self.assertRaises(MetaStrategyRepositoryAttributionError):
            repository.ingest_broker_fill(fill_payload(algorithm_id="regime", broker_fill_id="regime-fill-1", quantity=25, price=101.0))

        position = repository.current_inventory_snapshot().open_positions[0]

        self.assertEqual(position.symbol, "SPY")
        self.assertEqual(position.quantity, 10.0)
        self.assertEqual(position.average_price, 100.0)

    def test_duplicate_and_partial_fills_are_idempotent_and_incremental(self) -> None:
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")
        repository.record_order_intent(order_intent_payload(quantity=10, reserved_risk=100.0))

        first = repository.ingest_broker_fill(fill_payload(broker_fill_id="fill-1", quantity=4, price=100.0))
        duplicate = repository.ingest_broker_fill(fill_payload(broker_fill_id="fill-1", quantity=4, price=100.0))
        second = repository.ingest_broker_fill(fill_payload(broker_fill_id="fill-2", quantity=6, price=101.0))
        snapshot = repository.current_inventory_snapshot(mark_prices={"SPY": 102.0})

        self.assertEqual(first["status"], "INGESTED")
        self.assertEqual(duplicate["status"], "DUPLICATE_IGNORED")
        self.assertEqual(second["status"], "INGESTED")
        self.assertEqual(snapshot.open_positions[0].quantity, 10.0)
        self.assertEqual(snapshot.open_positions[0].average_price, 100.6)
        self.assertEqual(snapshot.unrealised_pnl, 14.0)
        self.assertEqual(snapshot.reserved_risk_dollars, 0.0)

    def test_fees_slippage_allocated_capital_and_exposures_are_rebuilt_from_ledger(self) -> None:
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")
        repository.record_order_intent(order_intent_payload(quantity=10, reserved_risk=100.0, strategy_id="trend_alignment", family="TREND"))
        repository.ingest_broker_fill(fill_payload(broker_fill_id="fill-fee-1", quantity=5, price=100.0, commission=0.25, slippage=0.10, strategy_id="trend_alignment", family="TREND"))
        repository.ingest_broker_fill(fill_payload(broker_fill_id="fill-fee-2", quantity=5, price=101.0, commission=0.25, slippage=0.10, strategy_id="trend_alignment", family="TREND"))

        snapshot = repository.rebuild_inventory_from_ledger(mark_prices={"SPY": 102.0})

        self.assertEqual(snapshot.fees_and_slippage, 0.7)
        self.assertEqual(snapshot.allocated_capital, 1020.0)
        self.assertEqual(snapshot.strategy_exposure["trend_alignment"], 1020.0)
        self.assertEqual(snapshot.family_exposure["TREND"], 1020.0)
        self.assertEqual(snapshot.symbol_exposure["SPY"], 1020.0)

    def test_exits_realise_pnl_fifo_and_release_lots(self) -> None:
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")
        repository.record_order_intent(order_intent_payload(quantity=10))
        repository.ingest_broker_fill(fill_payload(broker_fill_id="entry-1", side="BUY", quantity=4, price=100.0))
        repository.ingest_broker_fill(fill_payload(broker_fill_id="entry-2", side="BUY", quantity=6, price=101.0))
        repository.ingest_broker_fill(fill_payload(broker_fill_id="exit-1", side="SELL", quantity=5, price=103.0))
        snapshot = repository.current_inventory_snapshot(mark_prices={"SPY": 103.0})

        self.assertEqual(snapshot.open_positions[0].quantity, 5.0)
        self.assertEqual(snapshot.realised_pnl, 14.0)
        self.assertEqual(snapshot.unrealised_pnl, 10.0)
        self.assertEqual(snapshot.daily_trade_count, 1)

    def test_cancel_reject_and_correction_preserve_fill_driven_inventory(self) -> None:
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")
        repository.record_order_intent(order_intent_payload(quantity=10, reserved_risk=100.0))
        repository.record_order_status(order_status_payload(status="CANCELED"))
        repository.record_order_status(order_status_payload(status="REJECTED", client_order_id="client-rejected"))
        self.assertEqual(repository.current_inventory_snapshot().open_positions, ())
        self.assertEqual(repository.current_inventory_snapshot().reserved_risk_dollars, 0.0)

        repository.ingest_broker_fill(fill_payload(broker_fill_id="fill-corrected", quantity=10, price=100.0))
        repository.ingest_broker_fill(fill_payload(broker_fill_id="fill-correction", side="SELL", quantity=2, price=100.0, correction_of="fill-corrected"))
        snapshot = repository.current_inventory_snapshot()

        self.assertEqual(snapshot.open_positions[0].quantity, 8.0)
        self.assertEqual(snapshot.realised_pnl, 0.0)

    def test_timeout_unknown_status_is_quarantined_without_releasing_risk(self) -> None:
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")
        repository.record_order_intent(order_intent_payload(quantity=10, reserved_risk=100.0))
        repository.record_order_status(order_status_payload(status="TIMEOUT"))

        snapshot = repository.current_inventory_snapshot()
        quarantine = repository.inventory_records("quarantine")

        self.assertEqual(snapshot.reserved_risk_dollars, 100.0)
        self.assertEqual(quarantine[0]["status"], "QUARANTINED")
        self.assertEqual(quarantine[0]["payload"]["quarantineReason"], "ORDER_TIMEOUT")

    def test_restart_replay_reproduces_inventory_and_consistency_check_passes(self) -> None:
        path = temp_db_path()
        repository = MetaStrategySqliteRepository(f"sqlite:///{path}")
        repository.record_order_intent(order_intent_payload(quantity=10))
        repository.ingest_broker_fill(fill_payload(broker_fill_id="entry-1", side="BUY", quantity=10, price=100.0))
        repository.ingest_broker_fill(fill_payload(broker_fill_id="exit-1", side="SELL", quantity=3, price=105.0))
        before = repository.current_inventory_snapshot(mark_prices={"SPY": 104.0})

        restarted = MetaStrategySqliteRepository(f"sqlite:///{path}")
        replayed = restarted.rebuild_inventory_from_ledger(mark_prices={"SPY": 104.0})
        consistency = restarted.check_inventory_consistency(mark_prices={"SPY": 104.0})

        self.assertEqual(replayed, before)
        self.assertTrue(consistency["consistent"], consistency)
        self.assertEqual(replayed.open_positions[0].quantity, 7.0)
        self.assertEqual(replayed.realised_pnl, 15.0)
        self.assertEqual(replayed.unrealised_pnl, 28.0)

    def test_client_order_id_contains_meta_strategy_prefix_and_partition_identity(self) -> None:
        client_order_id = deterministic_meta_strategy_client_order_id(order_intent_payload(quantity=10))

        self.assertTrue(client_order_id.startswith("meta-strategy-meta-strategy-paper-def"))


def order_intent_payload(*, quantity: float, reserved_risk: float = 0.0, strategy_id: str = "meta_strategy", family: str = "UNKNOWN") -> dict:
    return {
        "algorithmId": "meta_strategy",
        "capitalPartitionId": "meta_strategy.paper.default",
        "settingsVersion": "settings-v1",
        "decisionId": "decision-1",
        "jobId": "job-1",
        "eventId": "intent-event-1",
        "orderIntentId": "intent-1",
        "clientOrderId": "client-1",
        "correlationId": "corr-1",
        "symbol": "SPY",
        "side": "BUY",
        "quantity": quantity,
        "reservedRiskDollars": reserved_risk,
        "strategyId": strategy_id,
        "family": family,
        "timestamp": NOW.isoformat(),
    }


def order_payload(*, status: str, client_order_id: str = "client-1") -> dict:
    return {
        **order_intent_payload(quantity=10),
        "eventId": f"order-{status}-{client_order_id}",
        "clientOrderId": client_order_id,
        "brokerOrderId": f"broker-{client_order_id}",
        "orderStatus": status,
    }


def order_status_payload(*, status: str, client_order_id: str = "client-1") -> dict:
    return {
        **order_payload(status=status, client_order_id=client_order_id),
        "eventId": f"status-{status}-{client_order_id}",
    }


def fill_payload(
    *,
    broker_fill_id: str,
    algorithm_id: str = "meta_strategy",
    side: str = "BUY",
    quantity: float,
    price: float = 100.0,
    correction_of: str | None = None,
    capital_partition_id: str = "meta_strategy.paper.default",
    commission: float = 0.0,
    slippage: float = 0.0,
    strategy_id: str = "meta_strategy",
    family: str = "UNKNOWN",
) -> dict:
    payload = {
        **order_intent_payload(quantity=10, strategy_id=strategy_id, family=family),
        "algorithmId": algorithm_id,
        "capitalPartitionId": capital_partition_id,
        "eventId": f"event-{broker_fill_id}",
        "brokerOrderId": "broker-client-1",
        "brokerFillId": broker_fill_id,
        "side": side,
        "filledQuantity": quantity,
        "fillPrice": price,
        "commission": commission,
        "estimatedSlippage": slippage,
        "timestamp": NOW.isoformat(),
    }
    if correction_of:
        payload["correctionOfBrokerFillId"] = correction_of
    return payload


def insert_raw_inventory_row(conn: sqlite3.Connection, *, table: str, algorithm_id: str, capital_partition_id: str) -> None:
    conn.execute(
        f"""
        INSERT INTO {table} (
            record_id, algorithm_id, capital_partition_id, settings_version, correlation_id,
            decision_id, job_id, event_id, order_intent_id, client_order_id, broker_order_id,
            broker_fill_id, symbol, side, quantity, price, status, realised_pnl, timestamp, payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"raw-{algorithm_id}-{capital_partition_id}",
            algorithm_id,
            capital_partition_id,
            "settings-v1",
            "corr-raw",
            "decision-raw",
            "job-raw",
            "event-raw",
            "intent-raw",
            "client-raw",
            "broker-raw",
            "fill-raw",
            "SPY",
            "BUY",
            1.0,
            100.0,
            "FILLED",
            0.0,
            NOW.isoformat(),
            "{}",
        ),
    )


def temp_db_path() -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"meta-strategy-phase5-{uuid4().hex}.sqlite"


if __name__ == "__main__":
    unittest.main()
