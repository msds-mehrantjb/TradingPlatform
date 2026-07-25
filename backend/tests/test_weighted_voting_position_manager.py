from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from backend.app.algorithms.weighted_voting.dynamic_settings import resolve_effective_settings
from backend.app.algorithms.weighted_voting.inventory import WeightedVotingInventoryEventType, WeightedVotingInventoryRepository, WeightedVotingPosition
from backend.app.algorithms.weighted_voting.position_manager import (
    TRADE_PREFIX,
    WeightedVotingPositionManagerService,
    assert_weighted_voting_position_manager_ownership,
    position_manager_status,
)
from backend.app.algorithms.weighted_voting.runtime_supervisor import WeightedVotingEventBus, WeightedVotingRuntimeConfig, WeightedVotingRuntimeSupervisor
from backend.app.algorithms.weighted_voting.service import WeightedVotingService


NOW = datetime(2026, 7, 14, 15, 30, tzinfo=UTC)
SESSION_DATE = date(2026, 7, 14)


class WeightedVotingPositionManagerTest(unittest.TestCase):
    def test_entry_fill_creates_authoritative_position_protection_and_linkage(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        position = seeded_position(inventory)
        broker = FakeProtectionBroker()
        manager = WeightedVotingPositionManagerService(store=store, inventory_repository=inventory, broker=broker)

        instruction = manager.protect_position_on_entry_fill(
            position=position,
            effective_settings=settings(),
            entry_order_id=position.client_order_id,
            supporting_strategy_ids=("trend_follow",),
            protected_at=NOW,
        )

        self.assertEqual(instruction.algorithm_id, "weighted_voting")
        self.assertTrue(instruction.broker_held_preferred)
        self.assertEqual(broker.protective_count, 1)
        self.assertIn("weighted_voting.position_manager.linkage.client-1", store.snapshots)

    def test_restart_restores_orphaned_protective_order_management(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        seeded_position(inventory)
        restarted = WeightedVotingPositionManagerService(store=store, inventory_repository=inventory, broker=FakeProtectionBroker())

        restored = restarted.restore_protective_management(effective_settings_by_version={"settings-test": settings()}, restored_at=NOW + timedelta(seconds=5))

        self.assertEqual(len(restored), 1)
        self.assertIn("weighted_voting.position_manager.protection.client-1", store.snapshots)

    def test_end_of_day_liquidation_closes_position_and_writes_one_trade(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        position = seeded_position(inventory)
        broker = FakeProtectionBroker()
        manager = WeightedVotingPositionManagerService(store=store, inventory_repository=inventory, broker=broker)
        manager.protect_position_on_entry_fill(position=position, effective_settings=settings(), entry_order_id=position.client_order_id, protected_at=NOW)

        trade = manager.monitor_position(
            position=position,
            current_price=100.1,
            observed_at=NOW + timedelta(minutes=30),
            end_of_day=True,
            realised_exit_costs=0.5,
        )

        self.assertIsNotNone(trade)
        self.assertEqual(trade.exit_reason, "end_of_day")
        self.assertEqual(broker.exit_count, 1)
        self.assertEqual(len([key for key in store.snapshots if key.startswith(TRADE_PREFIX)]), 1)
        self.assertEqual(inventory.current_snapshot(now=NOW).daily_trade_count, 1)

    def test_exit_management_continues_when_new_entries_are_paused(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        position = seeded_position(inventory)
        manager = WeightedVotingPositionManagerService(store=store, inventory_repository=inventory, broker=FakeProtectionBroker())
        manager.protect_position_on_entry_fill(position=position, effective_settings=settings(), entry_order_id=position.client_order_id, protected_at=NOW)
        supervisor = WeightedVotingRuntimeSupervisor(
            service=WeightedVotingService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            inventory_repository=inventory,
            position_manager=manager,
        )
        supervisor.metrics.entry_creation_paused_for_reconciliation = True

        trade = manager.monitor_position(position=position, current_price=99.0, observed_at=NOW + timedelta(minutes=5))

        self.assertIsNotNone(trade)
        self.assertTrue(supervisor.health()["entryCreationPausedForReconciliation"])

    def test_other_algorithms_cannot_modify_weighted_voting_position_manager(self) -> None:
        foreign = WeightedVotingPosition(
            algorithm_id="weighted_voting",
            position_id="foreign",
            symbol="SPY",
            side="LONG",
            quantity=1,
            average_entry_price=100.0,
            opened_at=NOW,
            decision_id="decision",
            order_intent_id="intent",
            client_order_id="client",
        )
        self.assertIsNone(assert_weighted_voting_position_manager_ownership(foreign))
        payload = foreign.__dict__.copy()
        payload["algorithmId"] = "voting_ensemble"
        payload.pop("algorithm_id", None)
        with self.assertRaises(ValueError):
            assert_weighted_voting_position_manager_ownership(payload)

    def test_status_declares_dashboard_independent_exit_coverage(self) -> None:
        status = position_manager_status()

        self.assertFalse(status["dashboardRequired"])
        self.assertTrue(status["exitManagementContinuesWhenEntriesPaused"])
        self.assertIn("end_of_day", status["ownedExitReasons"])


def settings():
    return resolve_effective_settings(timestamp=NOW)


def seeded_inventory(store: "MemoryStore") -> WeightedVotingInventoryRepository:
    inventory = WeightedVotingInventoryRepository(store, symbol="SPY", allocated_capital=25_000.0)
    inventory.initialize_session(session_date=SESSION_DATE, allocated_capital=25_000.0, cash_available=25_000.0, occurred_at=NOW, expected_snapshot_version=0, event_id="session-start")
    return inventory


def seeded_position(inventory: WeightedVotingInventoryRepository):
    snapshot = inventory.current_snapshot(now=NOW)
    inventory.append_event(
        event_id="entry-fill-1",
        event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
        payload={
            "algorithm_id": "weighted_voting",
            "fill_id": "fill-1",
            "position_id": "weighted_voting.position.SPY.client-1",
            "symbol": "SPY",
            "side": "LONG",
            "quantity": 10,
            "average_entry_price": 100.0,
            "opened_at": NOW.isoformat(),
            "decision_id": "decision-1",
            "order_intent_id": "intent-1",
            "client_order_id": "client-1",
            "source": "test",
        },
        occurred_at=NOW,
        expected_snapshot_version=snapshot.snapshot_version,
    )
    return inventory.current_snapshot(now=NOW).open_positions[0]


class FakeProtectionBroker:
    def __init__(self) -> None:
        self.protective_count = 0
        self.exit_count = 0

    def submit_protective_order(self, instruction) -> str:
        self.protective_count += 1
        return f"broker-stop-{instruction.client_order_id}"

    def submit_exit_order(self, instruction) -> str:
        self.exit_count += 1
        return f"broker-exit-{instruction.client_order_id}"


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
