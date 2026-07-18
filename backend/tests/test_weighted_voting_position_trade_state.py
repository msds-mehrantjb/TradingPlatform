from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.weighted_voting.position_trade_state import (
    WEIGHTED_VOTING_POSITION_TRADE_STATE_NAMESPACE,
    WeightedVotingOrderLifecycle,
    WeightedVotingPositionLifecycle,
    assert_weighted_voting_position_ownership,
    close_weighted_voting_position,
    create_weighted_voting_order_state,
    mark_weighted_voting_order_cancelled,
    mark_weighted_voting_order_filled,
    mark_weighted_voting_order_open,
    mark_weighted_voting_order_partial_fill,
    mark_weighted_voting_order_rejected,
    open_weighted_voting_position_from_order,
    persist_weighted_voting_order_state,
    persist_weighted_voting_position_state,
    position_trade_state_status,
    update_weighted_voting_position_mark,
)


NOW = datetime(2026, 7, 14, 15, 30, tzinfo=UTC)


class WeightedVotingPositionTradeStateTest(unittest.TestCase):
    def test_order_and_position_lifecycle_own_full_trade_state(self) -> None:
        store = MemoryStore()
        order = create_weighted_voting_order_state(
            decision_id="decision-1",
            client_order_id="client-1",
            symbol="spy",
            side="BUY",
            requested_quantity=10,
            stop=99.0,
            target=102.0,
            owning_strategy_ids=("wv_orb", "wv_vwap_trend"),
            weight_version="weights-v1",
            settings_version="settings-v1",
            created_at=NOW,
        )
        opened_order = mark_weighted_voting_order_open(order, broker_order_id="broker-1", opened_at=NOW + timedelta(seconds=1))
        partial = mark_weighted_voting_order_partial_fill(opened_order, filled_quantity=4, average_fill_price=100.0, filled_at=NOW + timedelta(seconds=2))
        filled = mark_weighted_voting_order_filled(partial, filled_quantity=10, average_fill_price=100.0, filled_at=NOW + timedelta(seconds=3))
        position = open_weighted_voting_position_from_order(filled, opened_at=NOW + timedelta(seconds=3))
        marked = update_weighted_voting_position_mark(position, mark_price=101.25, marked_at=NOW + timedelta(minutes=2))
        adverse = update_weighted_voting_position_mark(marked, mark_price=98.75, marked_at=NOW + timedelta(minutes=3))
        closed = close_weighted_voting_position(adverse, exit_price=101.0, exit_time=NOW + timedelta(minutes=5), exit_reason="target_hit")

        persist_weighted_voting_order_state(store, filled)
        persist_weighted_voting_position_state(store, closed)

        self.assertEqual(order.order_lifecycle, WeightedVotingOrderLifecycle.PENDING_ORDER)
        self.assertEqual(opened_order.order_lifecycle, WeightedVotingOrderLifecycle.OPEN_ORDER)
        self.assertEqual(partial.order_lifecycle, WeightedVotingOrderLifecycle.PARTIAL_FILL)
        self.assertEqual(filled.order_lifecycle, WeightedVotingOrderLifecycle.FILLED_ORDER)
        self.assertEqual(filled.remaining_quantity, 0)
        self.assertEqual(position.position_lifecycle, WeightedVotingPositionLifecycle.OPEN_POSITION)
        self.assertEqual(closed.position_lifecycle, WeightedVotingPositionLifecycle.CLOSED_POSITION)
        self.assertEqual(closed.realized_pnl, 10.0)
        self.assertEqual(closed.unrealized_pnl, 0.0)
        self.assertEqual(closed.maximum_favorable_excursion, 12.5)
        self.assertEqual(closed.maximum_adverse_excursion, -12.5)
        self.assertEqual(closed.exit_reason, "target_hit")
        self.assertEqual(closed.owning_decision_id, "decision-1")
        self.assertEqual(closed.owning_strategy_ids, ("wv_orb", "wv_vwap_trend"))
        self.assertEqual(closed.weight_version, "weights-v1")
        self.assertEqual(closed.settings_version, "settings-v1")
        self.assertIn(f"{WEIGHTED_VOTING_POSITION_TRADE_STATE_NAMESPACE}.order.client-1", store.snapshots)
        self.assertIn(f"{WEIGHTED_VOTING_POSITION_TRADE_STATE_NAMESPACE}.position.client-1", store.snapshots)

    def test_cancelled_and_rejected_orders_are_owned_states_without_positions(self) -> None:
        store = MemoryStore()
        order = create_weighted_voting_order_state(
            decision_id="decision-2",
            client_order_id="client-2",
            symbol="SPY",
            side="SELL",
            requested_quantity=8,
            stop=101.0,
            target=98.0,
            owning_strategy_ids=("wv_failed_breakout",),
            weight_version="weights-v1",
            settings_version="settings-v1",
            created_at=NOW,
        )

        cancelled = mark_weighted_voting_order_cancelled(order, cancelled_at=NOW + timedelta(seconds=5), cancellation_reason="entry_expired")
        rejected = mark_weighted_voting_order_rejected(order, rejected_at=NOW + timedelta(seconds=6), rejection_reason="broker_rejected")
        persist_weighted_voting_order_state(store, cancelled)
        persist_weighted_voting_order_state(store, rejected)

        self.assertEqual(cancelled.order_lifecycle, WeightedVotingOrderLifecycle.CANCELLED_ORDER)
        self.assertEqual(cancelled.remaining_quantity, 0)
        self.assertEqual(rejected.order_lifecycle, WeightedVotingOrderLifecycle.REJECTED_ORDER)
        self.assertEqual(rejected.rejection_reason, "broker_rejected")
        self.assertFalse(any(key.startswith(f"{WEIGHTED_VOTING_POSITION_TRADE_STATE_NAMESPACE}.position.") for key in store.snapshots))

    def test_position_from_another_algorithm_is_never_managed_as_weighted_voting(self) -> None:
        order = create_weighted_voting_order_state(
            decision_id="decision-3",
            client_order_id="client-3",
            symbol="SPY",
            side="BUY",
            requested_quantity=5,
            stop=99.0,
            target=103.0,
            owning_strategy_ids=("wv_liquidity_sweep",),
            weight_version="weights-v1",
            settings_version="settings-v1",
            created_at=NOW,
        )
        filled = mark_weighted_voting_order_filled(order, filled_quantity=5, average_fill_price=100.0, filled_at=NOW)
        position = open_weighted_voting_position_from_order(filled, opened_at=NOW)
        with self.assertRaises(ValueError):
            replace(position, algorithm_id="regime")
        with self.assertRaises(ValueError):
            assert_weighted_voting_position_ownership({"algorithmId": "wca", "positionId": "foreign"})

    def test_state_status_inventory_declares_required_records_and_fields(self) -> None:
        status = position_trade_state_status()

        self.assertEqual(status["algorithmId"], "weighted_voting")
        for expected in ("pending_order", "open_order", "partial_fill", "filled_order", "cancelled_order", "rejected_order"):
            self.assertIn(expected, status["ownedOrderRecords"])
        for expected in ("open_position", "closed_position"):
            self.assertIn(expected, status["ownedPositionRecords"])
        for expected in ("realized_pnl", "unrealized_pnl", "entry_time", "exit_time", "stop", "target", "maximum_favorable_excursion", "maximum_adverse_excursion", "exit_reason", "owning_decision", "owning_strategies", "weight_version", "settings_version"):
            self.assertIn(expected, status["ownedFields"])
        self.assertEqual(status["ownershipRule"], "positions_from_other_algorithms_are_rejected")


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
