from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from backend.app.algorithms.weighted_voting.broker_reconciliation import (
    CHECKPOINT_KEY,
    WeightedVotingBrokerFillObservation,
    WeightedVotingBrokerOrderObservation,
    WeightedVotingBrokerPositionObservation,
    reconcile_weighted_voting_broker_observations,
    reconciliation_status,
)
from backend.app.algorithms.weighted_voting.execution_gateway import build_weighted_voting_broker_command, persist_weighted_voting_broker_command
from backend.app.algorithms.weighted_voting.inventory import WeightedVotingInventoryEventType, WeightedVotingInventoryRepository
from backend.app.gates import GlobalGateResponse, GlobalOrderProposal, apply_global_gate_response


NOW = datetime(2026, 7, 14, 15, 30, tzinfo=UTC)
SESSION_DATE = date(2026, 7, 14)


class WeightedVotingBrokerReconciliationTest(unittest.TestCase):
    def test_multiple_partial_fills_create_weighted_average_position_and_release_reservation(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        command = seeded_command(store, inventory)

        result = reconcile_weighted_voting_broker_observations(
            store=store,
            inventory_repository=inventory,
            fills=(
                fill("fill-1", command.client_order_id, quantity=4, price=100.0, filled_at=NOW),
                fill("fill-2", command.client_order_id, quantity=6, price=102.0, filled_at=NOW + timedelta(seconds=10)),
            ),
            positions=(position(command.client_order_id, quantity=10, price=101.2),),
            reconciled_at=NOW + timedelta(seconds=20),
        )

        snapshot = inventory.current_snapshot(now=NOW)
        self.assertTrue(result.inventory_reconciled)
        self.assertFalse(result.entries_paused)
        self.assertEqual(result.applied_fill_ids, ("fill-1", "fill-2"))
        self.assertEqual(snapshot.reserved_buying_power, 0.0)
        self.assertEqual(snapshot.pending_orders, ())
        self.assertEqual(len(snapshot.open_positions), 1)
        self.assertEqual(snapshot.open_positions[0].quantity, 10)
        self.assertAlmostEqual(snapshot.open_positions[0].average_entry_price, 101.2)
        self.assertIn(CHECKPOINT_KEY, store.snapshots)

    def test_duplicate_fill_does_not_change_quantity_or_pnl_twice(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        command = seeded_command(store, inventory)
        first_fill = fill("fill-dup", command.client_order_id, quantity=4, price=100.0, filled_at=NOW)

        first = reconcile_weighted_voting_broker_observations(
            store=store,
            inventory_repository=inventory,
            fills=(first_fill,),
            positions=(position(command.client_order_id, quantity=4, price=100.0),),
            reconciled_at=NOW + timedelta(seconds=1),
        )
        second = reconcile_weighted_voting_broker_observations(
            store=store,
            inventory_repository=inventory,
            fills=(first_fill,),
            positions=(position(command.client_order_id, quantity=4, price=100.0),),
            reconciled_at=NOW + timedelta(seconds=2),
        )

        snapshot = inventory.current_snapshot(now=NOW)
        self.assertEqual(first.applied_fill_ids, ("fill-dup",))
        self.assertEqual(second.duplicate_fill_ids, ("fill-dup",))
        self.assertEqual(snapshot.open_positions[0].quantity, 4)
        self.assertEqual(snapshot.realised_pnl, 0.0)

    def test_restart_during_partial_fill_recovers_from_events(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        command = seeded_command(store, inventory)
        reconcile_weighted_voting_broker_observations(
            store=store,
            inventory_repository=inventory,
            fills=(fill("restart-1", command.client_order_id, quantity=4, price=100.0, filled_at=NOW),),
            positions=(position(command.client_order_id, quantity=4, price=100.0),),
            reconciled_at=NOW + timedelta(seconds=1),
        )

        recovered_inventory = WeightedVotingInventoryRepository(store, symbol="SPY", allocated_capital=25_000.0)
        recovered_inventory.recover_current_snapshot()
        reconcile_weighted_voting_broker_observations(
            store=store,
            inventory_repository=recovered_inventory,
            fills=(fill("restart-2", command.client_order_id, quantity=6, price=102.0, filled_at=NOW + timedelta(seconds=5)),),
            positions=(position(command.client_order_id, quantity=10, price=101.2),),
            reconciled_at=NOW + timedelta(seconds=6),
        )

        snapshot = recovered_inventory.current_snapshot(now=NOW)
        self.assertEqual(snapshot.open_positions[0].quantity, 10)
        self.assertAlmostEqual(snapshot.open_positions[0].average_entry_price, 101.2)

    def test_foreign_and_unattributed_broker_positions_are_excluded_and_flagged(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        seeded_command(store, inventory)

        result = reconcile_weighted_voting_broker_observations(
            store=store,
            inventory_repository=inventory,
            positions=(
                WeightedVotingBrokerPositionObservation(
                    client_order_id="foreign-client",
                    algorithm_id="voting_ensemble",
                    symbol="SPY",
                    quantity=7,
                    average_entry_price=99.0,
                    observed_at=NOW,
                    broker_position_id="foreign-position",
                ),
                WeightedVotingBrokerPositionObservation(
                    client_order_id=None,
                    algorithm_id=None,
                    symbol="SPY",
                    quantity=3,
                    average_entry_price=98.0,
                    observed_at=NOW,
                    broker_position_id="unattributed-position",
                ),
            ),
            reconciled_at=NOW + timedelta(seconds=1),
        )

        self.assertIn("foreign-position", result.excluded_broker_position_ids)
        self.assertIn("unattributed-position", result.excluded_broker_position_ids)
        self.assertEqual(inventory.current_snapshot(now=NOW).open_positions, ())
        self.assertTrue(any(item.reason_code == "weighted_voting.broker_reconciliation.broker_position_unattributed" for item in result.discrepancies))

    def test_unreconciled_broker_weighted_voting_position_pauses_entries(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)

        result = reconcile_weighted_voting_broker_observations(
            store=store,
            inventory_repository=inventory,
            positions=(position("unknown-weighted-client", quantity=5, price=100.0),),
            reconciled_at=NOW,
        )

        self.assertFalse(result.inventory_reconciled)
        self.assertTrue(result.entries_paused)
        self.assertTrue(result.risk_reducing_exits_allowed)

    def test_unknown_or_unattributed_fills_pause_entries_without_adopting_position(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)

        result = reconcile_weighted_voting_broker_observations(
            store=store,
            inventory_repository=inventory,
            fills=(
                WeightedVotingBrokerFillObservation(
                    fill_id="unknown-fill",
                    client_order_id="unknown-client",
                    algorithm_id=None,
                    symbol="SPY",
                    side="BUY",
                    quantity=5,
                    average_fill_price=100.0,
                    filled_at=NOW,
                ),
            ),
            reconciled_at=NOW + timedelta(seconds=1),
        )

        snapshot = inventory.current_snapshot(now=NOW)
        self.assertFalse(result.inventory_reconciled)
        self.assertTrue(result.entries_paused)
        self.assertEqual(snapshot.open_positions, ())
        self.assertEqual(snapshot.last_broker_reconciliation_checkpoint["entries_paused"], True)
        self.assertTrue(any(item.reason_code == "weighted_voting.broker_reconciliation.broker_fill_unattributed_or_foreign" for item in result.discrepancies))

    def test_unknown_broker_order_is_quarantined_and_pauses_entries(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)

        result = reconcile_weighted_voting_broker_observations(
            store=store,
            inventory_repository=inventory,
            orders=(
                WeightedVotingBrokerOrderObservation(
                    client_order_id="wv-unknown-order",
                    algorithm_id="weighted_voting",
                    symbol="SPY",
                    side="BUY",
                    status="ACCEPTED",
                    quantity=5,
                    filled_quantity=0,
                    average_fill_price=None,
                    observed_at=NOW,
                    broker_order_id="broker-unknown",
                ),
            ),
            reconciled_at=NOW + timedelta(seconds=1),
        )

        self.assertFalse(result.inventory_reconciled)
        self.assertTrue(result.entries_paused)
        self.assertTrue(any(item.reason_code == "weighted_voting.broker_reconciliation.broker_order_missing_locally" for item in result.discrepancies))
        self.assertTrue(any(key.startswith("weighted_voting.broker_reconciliation.quarantine.orders.wv-unknown-order") for key in store.snapshots))

    def test_pnl_and_protective_mismatches_pause_entries_and_prioritize_risk_reduction(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        command = seeded_command(store, inventory)
        reconcile_weighted_voting_broker_observations(
            store=store,
            inventory_repository=inventory,
            fills=(fill("fill-protected", command.client_order_id, quantity=4, price=100.0, filled_at=NOW),),
            positions=(position(command.client_order_id, quantity=4, price=100.0),),
            reconciled_at=NOW + timedelta(seconds=1),
        )

        result = reconcile_weighted_voting_broker_observations(
            store=store,
            inventory_repository=inventory,
            orders=(
                WeightedVotingBrokerOrderObservation(
                    client_order_id=command.client_order_id,
                    algorithm_id="weighted_voting",
                    symbol="SPY",
                    side="SELL",
                    status="ACCEPTED",
                    quantity=5,
                    filled_quantity=0,
                    average_fill_price=None,
                    observed_at=NOW + timedelta(seconds=2),
                    protective=True,
                ),
            ),
            positions=(
                WeightedVotingBrokerPositionObservation(
                    client_order_id=command.client_order_id,
                    algorithm_id="weighted_voting",
                    symbol="SPY",
                    quantity=4,
                    average_entry_price=100.0,
                    observed_at=NOW + timedelta(seconds=2),
                    unrealised_pnl=25.0,
                ),
            ),
            reconciled_at=NOW + timedelta(seconds=2),
        )

        reason_codes = {item.reason_code for item in result.discrepancies}
        self.assertTrue(result.entries_paused)
        self.assertIn("weighted_voting.broker_reconciliation.protective_order_quantity_mismatch", reason_codes)
        self.assertIn("weighted_voting.broker_reconciliation.broker_position_pnl_mismatch", reason_codes)
        checkpoint = inventory.current_snapshot(now=NOW).last_broker_reconciliation_checkpoint
        self.assertTrue(checkpoint["risk_reduction_priority"])

    def test_status_documents_daily_trade_count_definition(self) -> None:
        status = reconciliation_status()

        self.assertIn("client_order_id", status["matchesBy"])
        self.assertIn("position is closed", status["dailyTradeCountDefinition"])


def seeded_inventory(store: "MemoryStore") -> WeightedVotingInventoryRepository:
    inventory = WeightedVotingInventoryRepository(store, symbol="SPY", allocated_capital=25_000.0)
    inventory.initialize_session(
        session_date=SESSION_DATE,
        allocated_capital=25_000.0,
        cash_available=25_000.0,
        occurred_at=NOW,
        expected_snapshot_version=0,
        event_id="session-start",
    )
    return inventory


def seeded_command(store: "MemoryStore", inventory: WeightedVotingInventoryRepository):
    proposal = proposal_for()
    command = build_weighted_voting_broker_command(
        proposal=proposal,
        global_application=apply_global_gate_response(
            proposal,
            GlobalGateResponse(
                action="ALLOW",
                maximumAllowedQuantity=10,
                maximumAdditionalRiskDollars=100.0,
                evaluatedAt=NOW,
                configurationHash="global-risk",
            ),
        ),
        accepted_at=NOW,
    )
    persist_weighted_voting_broker_command(store, command)
    snapshot = inventory.current_snapshot(now=NOW)
    inventory.append_event(
        event_id=f"{command.client_order_id}.reserve",
        event_type=WeightedVotingInventoryEventType.ORDER_RESERVED,
        payload={
            "algorithm_id": "weighted_voting",
            "order_id": command.order_intent_id,
            "symbol": command.symbol,
            "side": command.side,
            "quantity": 10,
            "reserved_buying_power": 1000.0,
            "planned_risk_dollars": 100.0,
            "decision_id": command.decision_id,
            "order_intent_id": command.order_intent_id,
            "client_order_id": command.client_order_id,
            "created_at": NOW.isoformat(),
        },
        occurred_at=NOW,
        expected_snapshot_version=snapshot.snapshot_version,
    )
    return command


def proposal_for() -> GlobalOrderProposal:
    return GlobalOrderProposal(
        algorithmId="weighted_voting",
        capitalPartitionId="weighted_voting.paper.default",
        decisionId="decision-1",
        orderIntentId="intent-1",
        intent="new_entry",
        symbol="SPY",
        side="BUY",
        quantity=10,
        triggerPrice=100.0,
        limitPrice=100.0,
        stopPrice=99.0,
        targetPrice=102.0,
        plannedRiskDollars=100.0,
        settingsSnapshot={"settings_version": "test"},
        entryFormula={"kind": "limit"},
        stopFormula={"kind": "structural"},
        targetFormula={"kind": "r_multiple"},
        strategyStateHash="strategy-state",
        proposedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="proposal-config",
    )


def fill(fill_id: str, client_order_id: str, *, quantity: int, price: float, filled_at: datetime) -> WeightedVotingBrokerFillObservation:
    return WeightedVotingBrokerFillObservation(
        fill_id=fill_id,
        client_order_id=client_order_id,
        algorithm_id="weighted_voting",
        symbol="SPY",
        side="BUY",
        quantity=quantity,
        average_fill_price=price,
        filled_at=filled_at,
    )


def position(client_order_id: str, *, quantity: int, price: float) -> WeightedVotingBrokerPositionObservation:
    return WeightedVotingBrokerPositionObservation(
        client_order_id=client_order_id,
        algorithm_id="weighted_voting",
        symbol="SPY",
        quantity=quantity,
        average_entry_price=price,
        observed_at=NOW,
        broker_position_id=f"broker-position-{client_order_id}",
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
