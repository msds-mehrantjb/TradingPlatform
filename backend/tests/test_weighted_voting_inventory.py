from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from backend.app.algorithms.weighted_voting.execution_gateway import (
    WeightedVotingBrokerCommand,
    record_weighted_voting_fill,
)
from backend.app.algorithms.weighted_voting.inventory import (
    CURRENT_SNAPSHOT_KEY,
    WEIGHTED_VOTING_INVENTORY_NAMESPACE,
    WeightedVotingInventoryEventType,
    WeightedVotingInventoryRepository,
    inventory_status,
)


NOW = datetime(2026, 7, 14, 15, 30, tzinfo=UTC)
SESSION_DATE = date(2026, 7, 14)


class WeightedVotingInventoryTest(unittest.TestCase):
    def test_inventory_rebuilds_from_append_only_events_after_restart(self) -> None:
        store = MemoryStore()
        repo = WeightedVotingInventoryRepository(store, allocated_capital=25_000.0)
        snapshot = repo.initialize_session(session_date=SESSION_DATE, allocated_capital=25_000.0, cash_available=None, occurred_at=NOW, expected_snapshot_version=0)
        snapshot = repo.append_event(
            event_id="reserve-1",
            event_type=WeightedVotingInventoryEventType.ORDER_RESERVED,
            payload=pending_order_payload(reserved_buying_power=1_000.0),
            occurred_at=NOW + timedelta(seconds=1),
            expected_snapshot_version=snapshot.snapshot_version,
        )
        snapshot = repo.append_event(
            event_id="fill-1",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(quantity=10, average_entry_price=100.0),
            occurred_at=NOW + timedelta(seconds=2),
            expected_snapshot_version=snapshot.snapshot_version,
        )
        snapshot = repo.append_event(
            event_id="mark-1",
            event_type=WeightedVotingInventoryEventType.POSITION_MARKED,
            payload={"algorithm_id": "weighted_voting", "position_id": "wv-position-1", "mark_price": 101.5},
            occurred_at=NOW + timedelta(seconds=3),
            expected_snapshot_version=snapshot.snapshot_version,
        )

        del store.snapshots[CURRENT_SNAPSHOT_KEY]
        recovered = WeightedVotingInventoryRepository(store, allocated_capital=25_000.0).recover_current_snapshot()

        self.assertEqual(recovered.snapshot_version, snapshot.snapshot_version)
        self.assertEqual(recovered.gross_exposure, 1015.0)
        self.assertEqual(recovered.unrealised_pnl, 15.0)
        self.assertEqual(recovered.last_event_sequence, 4)
        self.assertEqual(len([key for key in store.snapshots if key.startswith(f"{WEIGHTED_VOTING_INVENTORY_NAMESPACE}.events.000")]), 4)

    def test_duplicate_fill_events_do_not_duplicate_positions_or_pnl(self) -> None:
        store = MemoryStore()
        repo = initialized_repo(store)
        snapshot = repo.current_snapshot()
        first = repo.append_event(
            event_id="fill-duplicate",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(quantity=10, average_entry_price=100.0),
            occurred_at=NOW + timedelta(seconds=1),
            expected_snapshot_version=snapshot.snapshot_version,
        )
        repeated = repo.append_event(
            event_id="fill-duplicate",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(quantity=10, average_entry_price=100.0),
            occurred_at=NOW + timedelta(seconds=2),
            expected_snapshot_version=first.snapshot_version,
        )
        second_id_same_position = repo.append_event(
            event_id="fill-duplicate-broker-redelivery",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(quantity=10, average_entry_price=100.0),
            occurred_at=NOW + timedelta(seconds=3),
            expected_snapshot_version=repeated.snapshot_version,
        )

        self.assertEqual(len(second_id_same_position.open_positions), 1)
        self.assertEqual(second_id_same_position.gross_exposure, 1000.0)
        self.assertEqual(second_id_same_position.realised_pnl, 0.0)
        self.assertEqual(first.as_dict(), repeated.as_dict())

    def test_daily_trade_count_and_loss_are_calculated_from_weighted_records(self) -> None:
        store = MemoryStore()
        repo = initialized_repo(store)
        snapshot = repo.current_snapshot()
        snapshot = repo.append_event(
            event_id="fill-loss",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(position_id="wv-loss", quantity=10, average_entry_price=100.0),
            occurred_at=NOW + timedelta(seconds=1),
            expected_snapshot_version=snapshot.snapshot_version,
        )
        closed = repo.append_event(
            event_id="close-loss",
            event_type=WeightedVotingInventoryEventType.POSITION_CLOSED,
            payload={"algorithm_id": "weighted_voting", "position_id": "wv-loss", "exit_price": 97.0, "realised_pnl": 999_999.0},
            occurred_at=NOW + timedelta(minutes=1),
            expected_snapshot_version=snapshot.snapshot_version,
        )

        self.assertEqual(closed.daily_trade_count, 1)
        self.assertEqual(closed.daily_realised_pnl, -30.0)
        self.assertEqual(closed.daily_loss_percent, 0.12)
        self.assertEqual(closed.daily_risk_used, 30.0)
        self.assertEqual(closed.remaining_daily_risk, 24_970.0)

    def test_position_lookup_returns_execution_created_positions_after_migration(self) -> None:
        store = MemoryStore()
        command = weighted_command()
        _, execution_position = record_weighted_voting_fill(
            store=store,
            command=command,
            filled_quantity=7,
            average_fill_price=100.05,
            filled_at=NOW,
            broker_order_id="broker-1",
            broker_fill_id="fill-1",
        )
        repo = initialized_repo(store)
        migrated = repo.migrate_legacy_positions(migrated_at=NOW + timedelta(seconds=1), expected_snapshot_version=repo.current_snapshot().snapshot_version)

        looked_up = repo.position_by_id(execution_position.position_id)

        self.assertEqual(len(migrated.open_positions), 1)
        self.assertIsNotNone(looked_up)
        self.assertEqual(looked_up.client_order_id, command.client_order_id)
        self.assertIn(f"weighted_voting.execution_gateway.position.{execution_position.position_id}", store.snapshots)
        self.assertIn(f"{WEIGHTED_VOTING_INVENTORY_NAMESPACE}.positions.{execution_position.position_id}", store.snapshots)

    def test_cross_algorithm_writes_and_lost_updates_fail_closed(self) -> None:
        store = MemoryStore()
        repo = initialized_repo(store)
        snapshot = repo.current_snapshot()

        with self.assertRaises(ValueError):
            repo.append_event(
                event_id="foreign-fill",
                event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
                payload={**position_payload(), "algorithm_id": "meta_strategy"},
                occurred_at=NOW,
                expected_snapshot_version=snapshot.snapshot_version,
            )
        with self.assertRaises(RuntimeError):
            repo.append_event(
                event_id="stale-version",
                event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
                payload=position_payload(),
                occurred_at=NOW,
                expected_snapshot_version=999,
            )

    def test_session_start_initializes_daily_ledger_and_date_rollover(self) -> None:
        store = MemoryStore()
        repo = initialized_repo(store)
        snapshot = repo.current_snapshot()
        snapshot = repo.append_event(
            event_id="fill-before-rollover",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(position_id="rollover-position", quantity=5, average_entry_price=100.0),
            occurred_at=NOW + timedelta(seconds=1),
            expected_snapshot_version=snapshot.snapshot_version,
        )
        snapshot = repo.append_event(
            event_id="close-before-rollover",
            event_type=WeightedVotingInventoryEventType.POSITION_CLOSED,
            payload={"algorithm_id": "weighted_voting", "position_id": "rollover-position", "exit_price": 99.0},
            occurred_at=NOW + timedelta(seconds=2),
            expected_snapshot_version=snapshot.snapshot_version,
        )
        rolled = repo.initialize_session(
            session_date=date(2026, 7, 15),
            allocated_capital=25_000.0,
            cash_available=25_000.0,
            occurred_at=NOW + timedelta(days=1),
            expected_snapshot_version=snapshot.snapshot_version,
        )

        self.assertEqual(rolled.session_date, date(2026, 7, 15))
        self.assertEqual(rolled.daily_trade_count, 0)
        self.assertEqual(rolled.daily_realised_pnl, 0.0)
        self.assertIn("weighted_voting.inventory.daily_ledgers.2026-07-15", store.snapshots)

    def test_migration_consolidates_legacy_position_keys_without_deleting_history(self) -> None:
        store = MemoryStore()
        store.write_snapshot("weighted_voting.position_trade_state.position.legacy-1", legacy_position("legacy-1", "client-legacy-1"))
        store.write_snapshot("weighted_voting.positions.legacy-2", legacy_position("legacy-2", "client-legacy-2"))
        store.write_snapshot("weighted_voting.execution_gateway.position.foreign", {**legacy_position("foreign", "client-foreign"), "algorithmId": "regime"})
        repo = initialized_repo(store)

        migrated = repo.migrate_legacy_positions(migrated_at=NOW + timedelta(seconds=1), expected_snapshot_version=repo.current_snapshot().snapshot_version)

        self.assertEqual({position.position_id for position in migrated.open_positions}, {"legacy-1", "legacy-2"})
        self.assertIn("weighted_voting.position_trade_state.position.legacy-1", store.snapshots)
        self.assertIn("weighted_voting.positions.legacy-2", store.snapshots)
        self.assertIn(f"{WEIGHTED_VOTING_INVENTORY_NAMESPACE}.positions.legacy-1", store.snapshots)
        self.assertIn(f"{WEIGHTED_VOTING_INVENTORY_NAMESPACE}.positions.legacy-2", store.snapshots)

    def test_api_supplied_inventory_values_are_not_authoritative(self) -> None:
        store = MemoryStore()
        repo = initialized_repo(store)
        snapshot = repo.current_snapshot()

        updated = repo.append_event(
            event_id="api-contaminated-fill",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload={
                **position_payload(quantity=3, average_entry_price=100.0),
                "cash_available": 1_000_000.0,
                "gross_exposure": 1_000_000.0,
                "daily_trade_count": 99,
            },
            occurred_at=NOW + timedelta(seconds=1),
            expected_snapshot_version=snapshot.snapshot_version,
        )

        self.assertEqual(updated.cash_available, 25_000.0)
        self.assertEqual(updated.gross_exposure, 300.0)
        self.assertEqual(updated.daily_trade_count, 0)

    def test_inventory_status_declares_boundary_controls(self) -> None:
        status = inventory_status()

        self.assertEqual(status["algorithmId"], "weighted_voting")
        self.assertTrue(status["optimisticVersionChecks"])
        self.assertEqual(status["idempotency"], "event_id")
        self.assertEqual(status["brokerAccountRole"], "read_only_reconciliation_source")


def initialized_repo(store: "MemoryStore") -> WeightedVotingInventoryRepository:
    repo = WeightedVotingInventoryRepository(store, allocated_capital=25_000.0)
    repo.initialize_session(session_date=SESSION_DATE, allocated_capital=25_000.0, cash_available=25_000.0, occurred_at=NOW, expected_snapshot_version=0)
    return repo


def pending_order_payload(*, reserved_buying_power: float = 1_000.0) -> dict:
    return {
        "algorithm_id": "weighted_voting",
        "order_id": "wv-order-1",
        "symbol": "SPY",
        "side": "BUY",
        "quantity": 10,
        "reserved_buying_power": reserved_buying_power,
        "planned_risk_dollars": 100.0,
        "decision_id": "decision-1",
        "order_intent_id": "intent-1",
        "client_order_id": "client-1",
        "created_at": NOW.isoformat(),
    }


def position_payload(*, position_id: str = "wv-position-1", quantity: int = 10, average_entry_price: float = 100.0) -> dict:
    return {
        "algorithm_id": "weighted_voting",
        "position_id": position_id,
        "symbol": "SPY",
        "side": "LONG" if quantity > 0 else "SHORT",
        "quantity": quantity,
        "average_entry_price": average_entry_price,
        "opened_at": NOW.isoformat(),
        "decision_id": "decision-1",
        "order_intent_id": "intent-1",
        "client_order_id": "client-1",
        "owning_strategy_ids": ("S1",),
    }


def legacy_position(position_id: str, client_order_id: str) -> dict:
    return {
        "algorithmId": "weighted_voting",
        "positionId": position_id,
        "symbol": "SPY",
        "side": "LONG",
        "quantity": 4,
        "averageEntryPrice": 100.25,
        "openedAt": NOW.isoformat(),
        "decisionId": "legacy-decision",
        "orderIntentId": "legacy-intent",
        "clientOrderId": client_order_id,
    }


def weighted_command() -> WeightedVotingBrokerCommand:
    return WeightedVotingBrokerCommand(
        algorithm_id="weighted_voting",
        command_id="weighted_voting.execution_gateway.command.client-exec-1",
        decision_id="decision-exec-1",
        order_intent_id="intent-exec-1",
        client_order_id="client-exec-1",
        symbol="SPY",
        side="BUY",
        quantity=7,
        order_type="limit",
        trigger_price=100.0,
        limit_price=100.0,
        stop_price=99.0,
        target_price=102.0,
        time_in_force="day",
        capital_partition_id="weighted_voting.paper.default",
        planned_risk_dollars=70.0,
        strategy_versions={"S1": "weighted_strategy_S1_v1"},
        weight_version="weights-v1",
        settings_version="settings-v1",
        risk_profile_version="risk-v1",
        market_snapshot_hash="snapshot-hash",
        configuration_hash="config-hash",
        accepted_global_action="ALLOW",
        global_proposal_hash="proposal-hash",
        global_response_hash="response-hash",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        reason_codes=("weighted_voting.test.command",),
    )


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot


if __name__ == "__main__":
    unittest.main()
