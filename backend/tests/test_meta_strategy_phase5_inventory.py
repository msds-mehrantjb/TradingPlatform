from __future__ import annotations

import sqlite3
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.meta_strategy import (
    META_STRATEGY_INVENTORY_TABLES,
    MetaStrategyApplicationService,
    MetaStrategyInventoryOwnershipConflict,
    MetaStrategyRepositoryAttributionError,
    MetaStrategySqliteRepository,
    deterministic_meta_strategy_client_order_id,
)
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.local_settings_risk import MetaStrategyLocalSettingsRiskSource
from backend.app.algorithms.meta_strategy.runtime import MetaStrategyRuntimeDependencies, MetaStrategyRuntimeMode, reconstruct_meta_strategy_runtime_state
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore, build_meta_strategy_settings
from backend.tests.test_meta_strategy_step7_market_snapshot import request_with


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)


class FailingMetaStrategyProjectionRepository(MetaStrategySqliteRepository):
    fail_projection = False

    def _store_inventory_projection(self, *args, **kwargs) -> None:
        if self.fail_projection:
            raise RuntimeError("injected_inventory_projection_failure")
        return super()._store_inventory_projection(*args, **kwargs)


class SiblingInventoryFixture:
    def __init__(self, algorithm_id: str, *, quantity: float = 0.0, cash: float = 100_000.0, realised_pnl: float = 0.0) -> None:
        self.algorithm_id = algorithm_id
        self.quantity = float(quantity)
        self.cash = float(cash)
        self.realised_pnl = float(realised_pnl)
        self.trade_count = 0

    def open_spy(self, quantity: float, *, price: float = 100.0) -> None:
        self.quantity += float(quantity)
        self.cash -= float(quantity) * float(price)
        self.trade_count += 1


    def current_inventory_snapshot(self) -> dict[str, object]:
        return {
            "algorithmId": self.algorithm_id,
            "symbol": "SPY",
            "quantity": self.quantity,
            "cash": self.cash,
            "realisedPnl": self.realised_pnl,
            "tradeCount": self.trade_count,
        }


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

    def test_entry_intent_and_reservation_roll_back_atomically_on_projection_failure(self) -> None:
        path = temp_db_path()
        repository = FailingMetaStrategyProjectionRepository(f"sqlite:///{path}")
        repository.fail_projection = True

        with self.assertRaisesRegex(RuntimeError, "injected_inventory_projection_failure"):
            repository.record_order_intent(order_intent_payload(quantity=10, reserved_risk=125.0))

        clean = MetaStrategySqliteRepository(f"sqlite:///{path}")
        self.assertEqual(clean.inventory_records("order_intents"), ())
        self.assertEqual(clean.inventory_records("risk_reservations"), ())
        self.assertEqual(clean.current_inventory_snapshot().reserved_risk_dollars, 0.0)
        self.assertEqual(clean.current_inventory_snapshot().open_positions, ())

    def test_fill_position_pnl_exposure_stats_and_reservation_roll_back_atomically_on_projection_failure(self) -> None:
        path = temp_db_path()
        repository = FailingMetaStrategyProjectionRepository(f"sqlite:///{path}")
        repository.record_order_intent(order_intent_payload(quantity=10, reserved_risk=100.0))
        repository.ingest_broker_fill(fill_payload(broker_fill_id="atomic-entry-fill", side="BUY", quantity=10, price=100.0))
        sell_intent = {
            **order_intent_payload(quantity=5, reserved_risk=50.0),
            "decisionId": "decision-atomic-sell",
            "eventId": "intent-atomic-sell",
            "orderIntentId": "intent-atomic-sell",
            "clientOrderId": "client-atomic-sell",
            "side": "SELL",
        }
        repository.record_order_intent(sell_intent)
        before = repository.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        repository.fail_projection = True

        with self.assertRaisesRegex(RuntimeError, "injected_inventory_projection_failure"):
            repository.ingest_broker_fill(
                {
                    **fill_payload(broker_fill_id="atomic-exit-fill", side="SELL", quantity=5, price=105.0),
                    "decisionId": "decision-atomic-sell",
                    "eventId": "event-atomic-sell",
                    "orderIntentId": "intent-atomic-sell",
                    "clientOrderId": "client-atomic-sell",
                    "brokerOrderId": "broker-atomic-sell",
                }
            )

        clean = MetaStrategySqliteRepository(f"sqlite:///{path}")
        after = clean.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        self.assertEqual(len(clean.inventory_records("fills")), 1)
        self.assertEqual(after.open_positions[0].quantity, before.open_positions[0].quantity)
        self.assertEqual(after.open_lots[0].quantity, before.open_lots[0].quantity)
        self.assertEqual(after.realised_pnl, before.realised_pnl)
        self.assertEqual(after.unrealised_pnl, before.unrealised_pnl)
        self.assertEqual(after.daily_trade_count, before.daily_trade_count)
        self.assertEqual(after.reserved_risk_dollars, before.reserved_risk_dollars)
        self.assertEqual(after.symbol_exposure, before.symbol_exposure)

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

    def test_application_service_recursively_rejects_caller_supplied_local_account_inventory_and_pnl_state(self) -> None:
        service = MetaStrategyApplicationService()
        snapshot_request = request_with().model_dump(mode="json")
        snapshot_request["position"] = {"symbol": "SPY", "quantity": 100}
        snapshot_request["nested"] = {"cash": 999_999, "risk": [{"reservedRisk": 1_000}]}

        result = service.shadow_evaluate(
            {
                "snapshotRequest": snapshot_request,
                "balance": 999_999,
                "cashAvailable": 999_999,
                "pnl": 12_345,
                "submittedOrders": [{"clientOrderId": "client-owned-by-caller"}],
            }
        )

        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("meta_strategy.service.caller_supplied_trading_state_rejected", result["reasonCodes"])
        self.assertEqual(
            set(result["payload"]["rejectedFields"]),
            {
                "balance",
                "cashAvailable",
                "pnl",
                "submittedOrders",
                "snapshotRequest.position",
                "snapshotRequest.nested.cash",
                "snapshotRequest.nested.risk[0].reservedRisk",
            },
        )

    def test_finalised_bar_command_rejects_caller_supplied_authoritative_state_aliases(self) -> None:
        service = MetaStrategyApplicationService()

        result = service.enqueue_finalised_bar(
            {
                "mode": "PAPER",
                "symbol": "SPY",
                "barEnd": NOW.isoformat(),
                "positionLots": [],
                "realisedPnl": 10.0,
                "totalBalance": 200_000.0,
                "reservedRisk": 100.0,
            }
        )

        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("meta_strategy.api.authoritative_fields_rejected", result["reasonCodes"])
        self.assertEqual(
            result["payload"]["rejectedFields"],
            ["positionLots", "realisedPnl", "reservedRisk", "totalBalance"],
        )

    def test_named_foreign_algorithms_are_rejected_with_reason_codes_on_inventory_write_paths(self) -> None:
        foreign_algorithms = ("weighted_voting", "voting_ensemble", "wca", "regime", "session")
        write_paths = (
            ("record_order_intent", lambda repository, payload: repository.record_order_intent(payload)),
            ("adjust_reserved_risk", lambda repository, payload: repository.adjust_reserved_risk(payload, target_reserved_risk=1.0, reason="TEST")),
            ("record_submitted_order", lambda repository, payload: repository.record_submitted_order(payload)),
            ("record_order_status", lambda repository, payload: repository.record_order_status({**payload, "status": "CANCELED", "orderStatus": "CANCELED"})),
            ("ingest_broker_fill", lambda repository, payload: repository.ingest_broker_fill(payload)),
            ("record_allocated_capital", lambda repository, payload: repository.record_allocated_capital({**payload, "allocatedCapital": 10_000.0})),
            ("record_reconciliation_checkpoint", lambda repository, payload: repository.record_reconciliation_checkpoint(payload)),
            ("record_position_lifecycle", lambda repository, payload: repository.record_position_lifecycle(payload)),
            ("record_quarantine", lambda repository, payload: repository.record_quarantine(payload, reason="FOREIGN_TEST")),
        )
        for algorithm_id in foreign_algorithms:
            for write_name, write in write_paths:
                with self.subTest(algorithm_id=algorithm_id, write_path=write_name):
                    repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")
                    payload = fill_payload(
                        algorithm_id=algorithm_id,
                        broker_fill_id=f"foreign-{algorithm_id}-{write_name}",
                        quantity=1,
                    )

                    with self.assertRaises(MetaStrategyRepositoryAttributionError) as error:
                        write(repository, payload)

                    self.assertEqual(error.exception.reason_codes, ("meta_strategy.inventory.foreign_algorithm_rejected",))
                    self.assertEqual(error.exception.observed_algorithm_id, algorithm_id)
                    self.assertEqual(error.exception.observed_capital_partition_id, "meta_strategy.paper.default")
                    self.assertEqual(error.exception.expected_algorithm_id, "meta_strategy")
                    self.assertEqual(error.exception.expected_capital_partition_id, "meta_strategy.paper.default")
                    self.assertEqual(repository.current_inventory_snapshot().open_positions, ())
                    self.assertEqual(repository.inventory_records("fills"), ())

    def test_broker_fill_ingestion_rejects_malformed_required_fill_fields(self) -> None:
        malformed_cases = (
            ("orderIntentId", lambda payload: payload.pop("orderIntentId")),
            ("clientOrderId", lambda payload: payload.pop("clientOrderId")),
            ("brokerOrderId", lambda payload: payload.pop("brokerOrderId")),
            ("brokerFillId", lambda payload: payload.pop("brokerFillId")),
            ("symbol", lambda payload: payload.__setitem__("symbol", "")),
            ("side", lambda payload: payload.__setitem__("side", "HOLD")),
            ("quantity", lambda payload: payload.__setitem__("filledQuantity", 0)),
            ("price", lambda payload: payload.__setitem__("fillPrice", 0)),
            ("timestamp", lambda payload: payload.__setitem__("timestamp", "not-a-timestamp")),
        )
        for field, mutate in malformed_cases:
            with self.subTest(field=field):
                repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")
                repository.record_order_intent(order_intent_payload(quantity=10))
                payload = fill_payload(broker_fill_id=f"malformed-{field}", quantity=1)
                mutate(payload)

                with self.assertRaises(ValueError) as error:
                    repository.ingest_broker_fill(payload)

                self.assertIn("meta_strategy.inventory.fill_malformed", str(error.exception))
                self.assertEqual(repository.inventory_records("fills"), ())
                self.assertEqual(repository.current_inventory_snapshot().open_positions, ())

    def test_duplicate_broker_fill_id_is_idempotent_for_position_pnl_and_trade_count(self) -> None:
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")
        repository.record_order_intent(order_intent_payload(quantity=10))
        repository.ingest_broker_fill(fill_payload(broker_fill_id="entry-fill", quantity=10, price=100.0))
        sell_fill = fill_payload(broker_fill_id="exit-fill", side="SELL", quantity=5, price=110.0)

        first = repository.ingest_broker_fill(sell_fill)
        before = repository.current_inventory_snapshot(mark_prices={"SPY": 110.0})
        duplicate = repository.ingest_broker_fill({**sell_fill, "eventId": "event-exit-fill-replay"})
        after = repository.current_inventory_snapshot(mark_prices={"SPY": 110.0})

        self.assertEqual(first["status"], "INGESTED")
        self.assertEqual(duplicate["status"], "DUPLICATE_IGNORED")
        self.assertEqual(len(repository.inventory_records("fills")), 2)
        self.assertEqual(after.open_positions[0].quantity, before.open_positions[0].quantity)
        self.assertEqual(after.realised_pnl, before.realised_pnl)
        self.assertEqual(after.unrealised_pnl, before.unrealised_pnl)
        self.assertEqual(after.daily_trade_count, before.daily_trade_count)

    def test_foreign_fill_cannot_alter_meta_strategy_inventory(self) -> None:
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")
        repository.record_order_intent(order_intent_payload(quantity=10))

        with self.assertRaises(MetaStrategyRepositoryAttributionError):
            repository.ingest_broker_fill(fill_payload(algorithm_id="weighted_voting", broker_fill_id="foreign-fill", quantity=10))
        with self.assertRaises(MetaStrategyRepositoryAttributionError):
            repository.ingest_broker_fill(fill_payload(broker_fill_id="foreign-partition-fill", quantity=10, capital_partition_id="meta_strategy.paper.other"))
        with self.assertRaises(MetaStrategyRepositoryAttributionError):
            repository.ingest_broker_fill({key: value for key, value in fill_payload(broker_fill_id="missing-partition-fill", quantity=10).items() if key != "capitalPartitionId"})

        self.assertEqual(repository.current_inventory_snapshot().open_positions, ())

    def test_sibling_order_status_cannot_release_meta_strategy_reserved_risk(self) -> None:
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")
        repository.record_order_intent(order_intent_payload(quantity=10, reserved_risk=100.0))

        with self.assertRaises(MetaStrategyRepositoryAttributionError):
            repository.record_order_status({**order_status_payload(status="CANCELED"), "algorithmId": "weighted_voting"})

        self.assertEqual(repository.current_inventory_snapshot().reserved_risk_dollars, 100.0)

    def test_duplicate_owned_order_identity_cannot_replace_existing_records(self) -> None:
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")
        repository.record_order_intent(order_intent_payload(quantity=10))
        repository.record_submitted_order(order_payload(status="ACCEPTED", client_order_id="client-1"))

        with self.assertRaises(MetaStrategyInventoryOwnershipConflict):
            repository.record_order_intent({**order_intent_payload(quantity=5), "decisionId": "decision-2", "eventId": "intent-event-2", "clientOrderId": "client-2"})
        with self.assertRaises(MetaStrategyInventoryOwnershipConflict):
            repository.record_submitted_order({**order_payload(status="ACCEPTED", client_order_id="client-1"), "decisionId": "decision-2", "orderIntentId": "intent-2", "eventId": "order-accepted-2"})

        self.assertEqual(len(repository.inventory_records("order_intents")), 1)
        self.assertEqual(len(repository.inventory_records("orders")), 1)

    def test_foreign_ownership_quarantine_preserves_observed_owner_without_position_change(self) -> None:
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")

        result = repository.record_foreign_ownership_quarantine(
            fill_payload(algorithm_id="weighted_voting", broker_fill_id="foreign-fill", quantity=10),
            reason="BROKER_EVENT_FOREIGN_ALGORITHM",
        )

        quarantine = repository.inventory_records("quarantine")[0]
        self.assertEqual(result["status"], "QUARANTINED")
        self.assertEqual(quarantine["payload"]["observedAlgorithmId"], "weighted_voting")
        self.assertEqual(quarantine["payload"]["quarantineReason"], "BROKER_EVENT_FOREIGN_ALGORITHM")
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

    def test_partial_buy_fills_aggregate_single_position_weighted_average_and_release_reserved_risk(self) -> None:
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")
        repository.record_order_intent(order_intent_payload(quantity=100, reserved_risk=1000.0))

        empty = repository.current_inventory_snapshot(mark_prices={"SPY": 104.0})
        self.assertEqual(empty.open_positions, ())
        self.assertEqual(empty.reserved_risk_dollars, 1000.0)

        repository.ingest_broker_fill(fill_payload(broker_fill_id="fill-30", quantity=30, price=100.0))
        after_30 = repository.current_inventory_snapshot(mark_prices={"SPY": 104.0})
        self.assertEqual(len(after_30.open_positions), 1)
        self.assertEqual(after_30.open_positions[0].quantity, 30.0)
        self.assertEqual(after_30.open_positions[0].average_price, 100.0)
        self.assertEqual(after_30.reserved_risk_dollars, 700.0)

        repository.ingest_broker_fill(fill_payload(broker_fill_id="fill-40", quantity=40, price=101.0))
        after_70 = repository.current_inventory_snapshot(mark_prices={"SPY": 104.0})
        self.assertEqual(len(after_70.open_positions), 1)
        self.assertEqual(after_70.open_positions[0].quantity, 70.0)
        self.assertEqual(after_70.open_positions[0].average_price, round(((30 * 100.0) + (40 * 101.0)) / 70, 10))
        self.assertEqual(after_70.reserved_risk_dollars, 300.0)

        repository.ingest_broker_fill(fill_payload(broker_fill_id="fill-30-final", quantity=30, price=103.0))
        after_100 = repository.current_inventory_snapshot(mark_prices={"SPY": 104.0})
        self.assertEqual(len(after_100.open_positions), 1)
        self.assertEqual(after_100.open_positions[0].quantity, 100.0)
        self.assertEqual(after_100.open_positions[0].average_price, 101.3)
        self.assertEqual(after_100.unrealised_pnl, 270.0)
        self.assertEqual(after_100.reserved_risk_dollars, 0.0)
        self.assertEqual(len(repository.inventory_records("position_lots")), 3)
        self.assertEqual(len(repository.inventory_records("positions")), 1)

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

    def test_exit_fills_update_realised_unrealised_pnl_exposure_stats_and_account_equity(self) -> None:
        database_url = f"sqlite:///{temp_db_path()}"
        repository = MetaStrategySqliteRepository(database_url)
        settings_store = MetaStrategySettingsStore(temp_db_path())
        baseline = settings_store.create_baseline(build_meta_strategy_settings(settings_version="settings-pnl-account"), actor="test")
        settings_store.activate_settings(baseline.settings_version, actor="test")
        repository.record_allocated_capital({**order_intent_payload(quantity=0), "eventId": "capital", "allocatedCapital": 10_000.0})
        repository.record_order_intent(order_intent_payload(quantity=10, reserved_risk=100.0))
        repository.ingest_broker_fill(fill_payload(broker_fill_id="entry-a", side="BUY", quantity=4, price=100.0, commission=0.40, slippage=0.20))
        repository.ingest_broker_fill(fill_payload(broker_fill_id="entry-b", side="BUY", quantity=6, price=102.0, commission=0.60, slippage=0.30))
        sell_intent = {
            **order_intent_payload(quantity=5, reserved_risk=50.0),
            "decisionId": "decision-sell-partial",
            "eventId": "intent-event-sell-partial",
            "orderIntentId": "intent-sell-partial",
            "clientOrderId": "client-sell-partial",
            "side": "SELL",
        }
        repository.record_order_intent(sell_intent)
        repository.ingest_broker_fill(
            {
                **fill_payload(broker_fill_id="exit-partial", side="SELL", quantity=5, price=105.0, commission=0.50, slippage=0.25),
                "decisionId": "decision-sell-partial",
                "eventId": "event-sell-partial",
                "orderIntentId": "intent-sell-partial",
                "clientOrderId": "client-sell-partial",
                "brokerOrderId": "broker-sell-partial",
            }
        )

        partial = repository.current_inventory_snapshot(mark_prices={"SPY": 106.0})
        self.assertEqual(len(partial.open_positions), 1)
        self.assertEqual(len(partial.open_lots), 1)
        self.assertEqual(partial.open_positions[0].quantity, 5.0)
        self.assertEqual(partial.open_positions[0].average_price, 102.0)
        self.assertEqual(partial.realised_pnl, 23.0)
        self.assertEqual(partial.unrealised_pnl, 20.0)
        self.assertEqual(partial.fees_and_slippage, 2.25)
        self.assertEqual(partial.daily_trade_count, 1)
        self.assertEqual(partial.symbol_exposure["SPY"], 530.0)
        self.assertEqual(partial.reserved_risk_dollars, 0.0)

        account = MetaStrategyLocalSettingsRiskSource(settings_store=settings_store, inventory_repository=repository).read_account_snapshot(at=NOW)
        self.assertEqual(account["allocatedCapital"], 10_000.0)
        self.assertEqual(account["realisedPnl"], 23.0)
        self.assertEqual(account["unrealisedPnl"], 20.0)
        self.assertEqual(account["feesAndSlippage"], 2.25)
        self.assertEqual(account["accountEquity"], 10_040.75)
        self.assertEqual(account["buyingPower"], 9_510.75)
        self.assertEqual(account["cashAvailable"], 9_510.75)

        repository.ingest_broker_fill(fill_payload(broker_fill_id="exit-final", side="SELL", quantity=5, price=104.0, commission=0.50, slippage=0.25))
        closed = repository.current_inventory_snapshot(mark_prices={"SPY": 106.0})
        self.assertEqual(closed.open_positions, ())
        self.assertEqual(closed.open_lots, ())
        self.assertEqual(closed.realised_pnl, 33.0)
        self.assertEqual(closed.unrealised_pnl, 0)
        self.assertEqual(closed.fees_and_slippage, 3.0)
        self.assertEqual(closed.daily_trade_count, 2)
        self.assertEqual(closed.symbol_exposure, {})
        self.assertEqual(closed.reserved_risk_dollars, 0.0)
        closed_account = MetaStrategyLocalSettingsRiskSource(settings_store=settings_store, inventory_repository=repository).read_account_snapshot(at=NOW)
        self.assertEqual(closed_account["accountEquity"], 10_030.0)
        self.assertEqual(closed_account["buyingPower"], 10_030.0)

    def test_market_day_change_resets_only_daily_statistics_not_durable_inventory(self) -> None:
        database_url = f"sqlite:///{temp_db_path()}"
        repository = MetaStrategySqliteRepository(database_url)
        settings_store = MetaStrategySettingsStore(temp_db_path())
        baseline = settings_store.create_baseline(build_meta_strategy_settings(settings_version="settings-daily-rollover"), actor="test")
        settings_store.activate_settings(baseline.settings_version, actor="test")
        next_day = datetime(2026, 1, 6, 15, 45, tzinfo=UTC)
        repository.record_allocated_capital({**order_intent_payload(quantity=0), "eventId": "capital-rollover", "allocatedCapital": 10_000.0, "timestamp": NOW.isoformat()})
        repository.ingest_broker_fill(fill_payload(broker_fill_id="rollover-entry", side="BUY", quantity=10, price=100.0))
        repository.ingest_broker_fill(fill_payload(broker_fill_id="rollover-exit", side="SELL", quantity=5, price=105.0))

        same_day = repository.current_inventory_snapshot(mark_prices={"SPY": 106.0}, as_of=NOW)
        next_session = repository.current_inventory_snapshot(mark_prices={"SPY": 107.0}, as_of=next_day)
        account = MetaStrategyLocalSettingsRiskSource(settings_store=settings_store, inventory_repository=repository).read_account_snapshot(at=next_day)

        self.assertEqual(same_day.daily_trade_count, 1)
        self.assertEqual(same_day.daily_realised_pnl, 25.0)
        self.assertEqual(next_session.daily_trade_count, 0)
        self.assertEqual(next_session.daily_realised_pnl, 0.0)
        self.assertEqual(next_session.open_positions[0].quantity, 5.0)
        self.assertEqual(next_session.open_lots[0].quantity, 5.0)
        self.assertEqual(next_session.realised_pnl, 25.0)
        self.assertEqual(next_session.unrealised_pnl, 35.0)
        self.assertEqual(next_session.allocated_capital, 10_000.0)
        self.assertEqual(account["dailyTradeCount"], 0)
        self.assertEqual(account["dailyRealisedPnl"], 0.0)
        self.assertEqual(account["realisedPnl"], 25.0)
        self.assertEqual(account["unrealisedPnl"], 35.0)
        self.assertEqual(account["accountEquity"], 10_060.0)

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

    def test_terminal_no_longer_consuming_order_statuses_release_reserved_risk(self) -> None:
        for status in ("DONE_FOR_DAY", "DEAD_LETTER"):
            with self.subTest(status=status):
                repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")
                repository.record_order_intent(order_intent_payload(quantity=10, reserved_risk=100.0))

                repository.record_order_status(order_status_payload(status=status))

                self.assertEqual(repository.current_inventory_snapshot().reserved_risk_dollars, 0.0)

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

    def test_runtime_restart_reconstructs_full_local_inventory_from_meta_strategy_ledger_only(self) -> None:
        path = temp_db_path()
        database_url = f"sqlite:///{path}"
        repository = MetaStrategySqliteRepository(database_url)
        repository.record_foreign_ownership_quarantine(
            fill_payload(algorithm_id="weighted_voting", broker_fill_id="foreign-restart-fill", quantity=99),
            reason="FOREIGN_RESTART_STATE",
        )
        repository.record_allocated_capital({**order_intent_payload(quantity=0), "eventId": "capital-restart", "allocatedCapital": 25_000.0})
        entry_intent = order_intent_payload(quantity=10, reserved_risk=1000.0, strategy_id="restart_strategy", family="RESTART")
        repository.record_order_intent(entry_intent)
        repository.record_submitted_order({**order_payload(status="ACKNOWLEDGED"), "eventId": "submitted-entry", "orderStatus": "ACKNOWLEDGED", "status": "ACKNOWLEDGED"})
        repository.record_order_status({**order_status_payload(status="PARTIALLY_FILLED"), "eventId": "status-entry-partial", "orderStatus": "PARTIALLY_FILLED", "status": "PARTIALLY_FILLED"})
        repository.ingest_broker_fill(fill_payload(broker_fill_id="restart-entry-fill", side="BUY", quantity=4, price=100.0, strategy_id="restart_strategy", family="RESTART"))
        exit_intent = {
            **order_intent_payload(quantity=1, strategy_id="restart_strategy", family="RESTART"),
            "decisionId": "decision-exit-restart",
            "eventId": "intent-exit-restart",
            "orderIntentId": "intent-exit-restart",
            "clientOrderId": "client-exit-restart",
            "side": "SELL",
        }
        repository.record_order_intent(exit_intent)
        repository.record_submitted_order({**exit_intent, "eventId": "submitted-exit-restart", "brokerOrderId": "broker-exit-restart", "orderStatus": "FILLED", "status": "FILLED"})
        repository.record_order_status({**exit_intent, "eventId": "status-exit-restart", "brokerOrderId": "broker-exit-restart", "orderStatus": "FILLED", "status": "FILLED"})
        repository.ingest_broker_fill(
            {
                **fill_payload(broker_fill_id="restart-exit-fill", side="SELL", quantity=1, price=105.0, strategy_id="restart_strategy", family="RESTART"),
                "decisionId": "decision-exit-restart",
                "eventId": "fill-exit-restart",
                "orderIntentId": "intent-exit-restart",
                "clientOrderId": "client-exit-restart",
                "brokerOrderId": "broker-exit-restart",
            }
        )
        repository.record_position_lifecycle(
            {
                **entry_intent,
                "eventId": "lifecycle-restart",
                "positionId": "meta_strategy.position.meta_strategy.paper.default.SPY",
                "status": "HOLD",
                "payload": {"positionId": "meta_strategy.position.meta_strategy.paper.default.SPY", "protectiveStop": 98.0, "profitTarget": 106.0},
            }
        )
        before = repository.current_inventory_snapshot(mark_prices={"SPY": 106.0})

        restarted_inventory = MetaStrategySqliteRepository(database_url)
        reconstruction = reconstruct_meta_strategy_runtime_state(
            MetaStrategyRuntimeDependencies(
                mode=MetaStrategyRuntimeMode.PAPER,
                inventory_repository=restarted_inventory,
                job_repository=MetaStrategyJobRepository(database_url),
            )
        )
        recovered = reconstruction["recoveredInventory"]

        self.assertEqual(reconstruction["status"], "OK")
        self.assertEqual(reconstruction["authoritativeInventoryApi"], "current_inventory_snapshot")
        self.assertFalse(reconstruction["portfolioImportedFromBroker"])
        self.assertFalse(reconstruction["foreignStateImported"])
        self.assertEqual(recovered["algorithmId"], "meta_strategy")
        self.assertEqual(recovered["capitalPartitionId"], "meta_strategy.paper.default")
        self.assertTrue(recovered["rebuiltFromLedger"])
        self.assertEqual(recovered["allocatedCapital"], 25_000.0)
        self.assertNotEqual(recovered["allocatedCapital"], 100_000.0)
        self.assertEqual(recovered["openPositions"][0]["quantity"], 3.0)
        self.assertEqual(recovered["openLots"][0]["quantity"], 3.0)
        self.assertEqual(recovered["pendingOrderCount"], 1)
        self.assertEqual(recovered["pendingOrders"][0]["status"], "PARTIALLY_FILLED")
        self.assertEqual(recovered["partialFillCount"], 1)
        self.assertEqual(recovered["fillCount"], 2)
        self.assertEqual(recovered["realisedPnl"], 5.0)
        self.assertEqual(recovered["unrealisedPnl"], 18.0)
        self.assertEqual(recovered["reservedRiskDollars"], 600.0)
        self.assertEqual(recovered["dailyTradeCount"], 1)
        self.assertEqual(recovered["symbolExposure"], {"SPY": 318.0})
        self.assertEqual(recovered["strategyExposure"], {"restart_strategy": 318.0})
        self.assertEqual(recovered["familyExposure"], {"RESTART": 318.0})
        self.assertEqual(recovered["latestPositionLifecycle"][0]["positionId"], "meta_strategy.position.meta_strategy.paper.default.SPY")
        self.assertEqual(recovered["recordCounts"]["position_lifecycle"], 1)
        self.assertEqual(recovered["recordCounts"]["fills"], 2)
        self.assertEqual(recovered["recordCounts"]["allocated_capital"], 1)
        self.assertFalse(recovered["foreignStateImported"])
        self.assertEqual(restarted_inventory.current_inventory_snapshot().snapshot_id, before.snapshot_id)

    def test_hard_isolation_a_weighted_voting_spy_position_is_not_meta_strategy_inventory(self) -> None:
        weighted_voting = SiblingInventoryFixture("weighted_voting")
        weighted_voting.open_spy(100, price=100.0)
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")

        snapshot = repository.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        meta_spy_positions = [position for position in snapshot.open_positions if position.symbol == "SPY"]

        self.assertEqual(weighted_voting.current_inventory_snapshot()["quantity"], 100.0)
        self.assertEqual(meta_spy_positions, [])
        self.assertEqual(snapshot.symbol_exposure, {})

    def test_hard_isolation_b_meta_strategy_open_does_not_change_sibling_inventories(self) -> None:
        siblings = {
            "weighted_voting": SiblingInventoryFixture("weighted_voting", quantity=100.0, cash=90_000.0),
            "voting_ensemble": SiblingInventoryFixture("voting_ensemble", quantity=20.0, cash=98_000.0),
            "wca": SiblingInventoryFixture("wca", quantity=35.0, cash=96_500.0),
            "regime": SiblingInventoryFixture("regime", quantity=5.0, cash=99_500.0),
        }
        before = {name: fixture.current_inventory_snapshot().copy() for name, fixture in siblings.items()}
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")

        repository.record_order_intent(order_intent_payload(quantity=50, reserved_risk=500.0))
        repository.ingest_broker_fill(fill_payload(broker_fill_id="hard-isolation-open-50", quantity=50, price=100.0))

        snapshot = repository.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        after = {name: fixture.current_inventory_snapshot().copy() for name, fixture in siblings.items()}

        self.assertEqual(len(snapshot.open_positions), 1)
        self.assertEqual(snapshot.open_positions[0].symbol, "SPY")
        self.assertEqual(snapshot.open_positions[0].quantity, 50.0)
        self.assertEqual(snapshot.symbol_exposure, {"SPY": 5_000.0})
        self.assertEqual(after, before)

    def test_hard_isolation_c_weighted_voting_owner_is_rejected_on_meta_strategy_insert_and_update(self) -> None:
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")
        foreign_payload = {**order_intent_payload(quantity=10, reserved_risk=100.0), "algorithmId": "weighted_voting"}

        with self.assertRaises(MetaStrategyRepositoryAttributionError) as insert_error:
            repository.record_order_intent(foreign_payload)
        with self.assertRaises(MetaStrategyRepositoryAttributionError) as update_error:
            repository.adjust_reserved_risk(foreign_payload, target_reserved_risk=0.0, reason="FOREIGN_UPDATE_ATTEMPT")

        self.assertEqual(insert_error.exception.reason_codes, ("meta_strategy.inventory.foreign_algorithm_rejected",))
        self.assertEqual(update_error.exception.reason_codes, ("meta_strategy.inventory.foreign_algorithm_rejected",))
        self.assertEqual(repository.inventory_records("order_intents"), ())
        self.assertEqual(repository.inventory_records("risk_reservations"), ())
        self.assertEqual(repository.current_inventory_snapshot().open_positions, ())

    def test_hard_isolation_d_voting_ensemble_fill_cannot_change_meta_strategy_account_or_inventory(self) -> None:
        database_url = f"sqlite:///{temp_db_path()}"
        repository = MetaStrategySqliteRepository(database_url)
        settings_store = MetaStrategySettingsStore(temp_db_path())
        baseline = settings_store.create_baseline(build_meta_strategy_settings(settings_version="settings-hard-isolation-d"), actor="test")
        settings_store.activate_settings(baseline.settings_version, actor="test")
        account_source = MetaStrategyLocalSettingsRiskSource(settings_store=settings_store, inventory_repository=repository)
        repository.record_allocated_capital({**order_intent_payload(quantity=0), "eventId": "capital-hard-isolation-d", "allocatedCapital": 10_000.0})
        repository.record_order_intent(order_intent_payload(quantity=10, reserved_risk=100.0))
        before_inventory = repository.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        before_account = account_source.read_account_snapshot(at=NOW)

        with self.assertRaises(MetaStrategyRepositoryAttributionError):
            repository.ingest_broker_fill(
                fill_payload(
                    algorithm_id="voting_ensemble",
                    capital_partition_id="voting_ensemble.paper.default",
                    broker_fill_id="voting-ensemble-foreign-fill",
                    quantity=10,
                    price=100.0,
                )
            )

        after_inventory = repository.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        after_account = account_source.read_account_snapshot(at=NOW)
        self.assertEqual(after_inventory.open_positions, before_inventory.open_positions)
        self.assertEqual(after_inventory.open_lots, before_inventory.open_lots)
        self.assertEqual(after_inventory.realised_pnl, before_inventory.realised_pnl)
        self.assertEqual(after_inventory.unrealised_pnl, before_inventory.unrealised_pnl)
        self.assertEqual(after_inventory.reserved_risk_dollars, before_inventory.reserved_risk_dollars)
        self.assertEqual(after_inventory.daily_trade_count, before_inventory.daily_trade_count)
        for field in (
            "accountEquity",
            "cashAvailable",
            "buyingPower",
            "realisedPnl",
            "unrealisedPnl",
            "reservedRiskDollars",
            "dailyTradeCount",
        ):
            with self.subTest(field=field):
                self.assertEqual(after_account[field], before_account[field])
        self.assertEqual(repository.inventory_records("fills"), ())

    def test_hard_isolation_e_meta_strategy_close_does_not_change_sibling_inventories(self) -> None:
        siblings = {
            "weighted_voting": SiblingInventoryFixture("weighted_voting", quantity=100.0, cash=90_000.0, realised_pnl=250.0),
            "voting_ensemble": SiblingInventoryFixture("voting_ensemble", quantity=20.0, cash=98_000.0, realised_pnl=50.0),
            "wca": SiblingInventoryFixture("wca", quantity=35.0, cash=96_500.0, realised_pnl=-25.0),
            "regime": SiblingInventoryFixture("regime", quantity=5.0, cash=99_500.0, realised_pnl=0.0),
        }
        before = {name: fixture.current_inventory_snapshot().copy() for name, fixture in siblings.items()}
        repository = MetaStrategySqliteRepository(f"sqlite:///{temp_db_path()}")
        repository.record_order_intent(order_intent_payload(quantity=50, reserved_risk=500.0))
        repository.ingest_broker_fill(fill_payload(broker_fill_id="hard-isolation-entry-close", side="BUY", quantity=50, price=100.0))
        sell_intent = {
            **order_intent_payload(quantity=50),
            "decisionId": "decision-hard-isolation-close",
            "eventId": "intent-hard-isolation-close",
            "orderIntentId": "intent-hard-isolation-close",
            "clientOrderId": "client-hard-isolation-close",
            "side": "SELL",
        }
        repository.record_order_intent(sell_intent)
        opened = repository.current_inventory_snapshot(mark_prices={"SPY": 105.0})

        repository.ingest_broker_fill(
            {
                **fill_payload(broker_fill_id="hard-isolation-exit-close", side="SELL", quantity=50, price=105.0),
                "decisionId": "decision-hard-isolation-close",
                "eventId": "fill-hard-isolation-close",
                "orderIntentId": "intent-hard-isolation-close",
                "clientOrderId": "client-hard-isolation-close",
                "brokerOrderId": "broker-hard-isolation-close",
            }
        )

        closed = repository.current_inventory_snapshot(mark_prices={"SPY": 105.0})
        after = {name: fixture.current_inventory_snapshot().copy() for name, fixture in siblings.items()}
        self.assertEqual(opened.open_positions[0].quantity, 50.0)
        self.assertEqual(closed.open_positions, ())
        self.assertEqual(closed.open_lots, ())
        self.assertEqual(closed.realised_pnl, 250.0)
        self.assertEqual(closed.symbol_exposure, {})
        self.assertEqual(after, before)

    def test_local_account_e2e_buy_mark_sell_uses_meta_strategy_cash_pnl_and_isolation(self) -> None:
        siblings = {
            "weighted_voting": SiblingInventoryFixture("weighted_voting", quantity=100.0, cash=50_000.0, realised_pnl=10.0),
            "voting_ensemble": SiblingInventoryFixture("voting_ensemble", quantity=25.0, cash=87_500.0, realised_pnl=-5.0),
            "wca": SiblingInventoryFixture("wca", quantity=0.0, cash=100_000.0, realised_pnl=0.0),
            "regime": SiblingInventoryFixture("regime", quantity=3.0, cash=98_500.0, realised_pnl=2.5),
        }
        sibling_before = {name: fixture.current_inventory_snapshot().copy() for name, fixture in siblings.items()}
        database_url = f"sqlite:///{temp_db_path()}"
        repository = MetaStrategySqliteRepository(database_url)
        settings_store = MetaStrategySettingsStore(temp_db_path())
        baseline = settings_store.create_baseline(build_meta_strategy_settings(settings_version="settings-local-account-e2e"), actor="test")
        settings_store.activate_settings(baseline.settings_version, actor="test")
        account_source = MetaStrategyLocalSettingsRiskSource(settings_store=settings_store, inventory_repository=repository)
        repository.record_allocated_capital({**order_intent_payload(quantity=0), "eventId": "capital-local-account-e2e", "allocatedCapital": 100_000.0})
        entry_intent = {
            **order_intent_payload(quantity=10, reserved_risk=1_000.0, strategy_id="local_account_e2e", family="LOCAL_ACCOUNT"),
            "decisionId": "decision-local-account-entry",
            "eventId": "intent-local-account-entry",
            "orderIntentId": "intent-local-account-entry",
            "clientOrderId": "client-local-account-entry",
        }

        repository.record_order_intent(entry_intent)
        reserved = repository.current_inventory_snapshot(mark_prices={"SPY": 500.0})
        reserved_account = account_source.read_account_snapshot(at=NOW)
        self.assertEqual(reserved.reserved_risk_dollars, 1_000.0)
        self.assertEqual(reserved_account["allocatedCapital"], 100_000.0)
        self.assertEqual(reserved_account["buyingPower"], 99_000.0)

        repository.ingest_broker_fill(
            {
                **fill_payload(
                    broker_fill_id="local-account-entry-fill",
                    side="BUY",
                    quantity=10,
                    price=500.0,
                    commission=1.00,
                    slippage=0.50,
                    strategy_id="local_account_e2e",
                    family="LOCAL_ACCOUNT",
                ),
                "decisionId": "decision-local-account-entry",
                "eventId": "fill-local-account-entry",
                "orderIntentId": "intent-local-account-entry",
                "clientOrderId": "client-local-account-entry",
                "brokerOrderId": "broker-local-account-entry",
            }
        )

        after_buy = repository.current_inventory_snapshot(mark_prices={"SPY": 500.0})
        after_buy_account = account_source.read_account_snapshot(at=NOW)
        self.assertEqual(len(after_buy.open_positions), 1)
        self.assertEqual(after_buy.open_positions[0].symbol, "SPY")
        self.assertEqual(after_buy.open_positions[0].quantity, 10.0)
        self.assertEqual(after_buy.symbol_exposure, {"SPY": 5_000.0})
        self.assertEqual(len(after_buy.open_lots), 1)
        self.assertEqual(after_buy.open_lots[0].quantity, 10.0)
        self.assertEqual(after_buy.open_lots[0].average_price, 500.0)
        self.assertEqual(after_buy.reserved_risk_dollars, 0.0)
        self.assertEqual(after_buy.fees_and_slippage, 1.5)
        self.assertEqual(after_buy_account["accountEquity"], 99_998.5)
        self.assertEqual(after_buy_account["cashAvailable"], 94_998.5)
        self.assertEqual(after_buy_account["buyingPower"], 94_998.5)

        marked = repository.current_inventory_snapshot(mark_prices={"SPY": 505.0})
        marked_account = account_source.read_account_snapshot(at=NOW)
        self.assertEqual(marked.unrealised_pnl, 50.0)
        self.assertEqual(marked.open_positions[0].unrealised_pnl, 50.0)
        self.assertEqual(marked_account["unrealisedPnl"], 50.0)
        self.assertEqual(marked_account["accountEquity"], 100_048.5)

        exit_intent = {
            **order_intent_payload(quantity=10, strategy_id="local_account_e2e", family="LOCAL_ACCOUNT"),
            "decisionId": "decision-local-account-exit",
            "eventId": "intent-local-account-exit",
            "orderIntentId": "intent-local-account-exit",
            "clientOrderId": "client-local-account-exit",
            "side": "SELL",
        }
        repository.record_order_intent(exit_intent)
        repository.ingest_broker_fill(
            {
                **fill_payload(
                    broker_fill_id="local-account-exit-fill",
                    side="SELL",
                    quantity=10,
                    price=505.0,
                    commission=1.00,
                    slippage=0.50,
                    strategy_id="local_account_e2e",
                    family="LOCAL_ACCOUNT",
                ),
                "decisionId": "decision-local-account-exit",
                "eventId": "fill-local-account-exit",
                "orderIntentId": "intent-local-account-exit",
                "clientOrderId": "client-local-account-exit",
                "brokerOrderId": "broker-local-account-exit",
            }
        )

        closed = repository.current_inventory_snapshot(mark_prices={"SPY": 505.0})
        closed_account = account_source.read_account_snapshot(at=NOW)
        trades = repository.inventory_records("trades")
        sibling_after = {name: fixture.current_inventory_snapshot().copy() for name, fixture in siblings.items()}
        self.assertEqual(closed.open_positions, ())
        self.assertEqual(closed.open_lots, ())
        self.assertEqual(closed.symbol_exposure, {})
        self.assertEqual(closed.realised_pnl, 50.0)
        self.assertEqual(closed.unrealised_pnl, 0.0)
        self.assertEqual(closed.fees_and_slippage, 3.0)
        self.assertEqual(closed.reserved_risk_dollars, 0.0)
        self.assertEqual(closed.daily_trade_count, 1)
        self.assertEqual(closed.daily_realised_pnl, 50.0)
        self.assertEqual(closed_account["accountEquity"], 100_047.0)
        self.assertEqual(closed_account["cashAvailable"], 100_047.0)
        self.assertEqual(closed_account["buyingPower"], 100_047.0)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["status"], "CLOSED")
        self.assertEqual(trades[0]["symbol"], "SPY")
        self.assertEqual(trades[0]["quantity"], 10.0)
        self.assertEqual(trades[0]["realisedPnl"], 50.0)
        self.assertEqual(sibling_after, sibling_before)

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
