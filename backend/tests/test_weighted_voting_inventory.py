from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
import threading
import time

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

    def test_inventory_snapshot_exposes_authoritative_subledger_fields(self) -> None:
        store = MemoryStore()
        repo = initialized_repo(store)
        snapshot = repo.current_snapshot()
        snapshot = repo.append_event(
            event_id="subledger-reserve",
            event_type=WeightedVotingInventoryEventType.ORDER_RESERVED,
            payload=pending_order_payload(reserved_buying_power=1_000.0),
            occurred_at=NOW + timedelta(seconds=1),
            expected_snapshot_version=snapshot.snapshot_version,
        )
        snapshot = repo.append_event(
            event_id="subledger-partial-fill",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(quantity=4, average_entry_price=100.0),
            occurred_at=NOW + timedelta(seconds=2),
            expected_snapshot_version=snapshot.snapshot_version,
        )

        self.assertEqual(snapshot.algorithm_id, "weighted_voting")
        self.assertEqual(snapshot.allocated_capital, 25_000.0)
        self.assertEqual(snapshot.initial_capital, 25_000.0)
        self.assertEqual(snapshot.daily_starting_equity, 25_000.0)
        self.assertEqual(snapshot.cash, snapshot.cash_available)
        self.assertEqual(snapshot.reserved_cash, snapshot.reserved_buying_power)
        self.assertEqual(snapshot.available_cash, snapshot.remaining_capital_partition)
        self.assertEqual(snapshot.buying_power, snapshot.remaining_capital_partition)
        self.assertEqual(snapshot.reserved_buying_power, 600.0)
        self.assertEqual(snapshot.consumed_capital, 400.0)
        self.assertEqual(snapshot.remaining_capital_partition, 24_000.0)
        self.assertEqual(snapshot.daily_loss, 0.0)
        self.assertEqual(snapshot.as_dict()["daily_starting_equity"], 25_000.0)
        self.assertEqual(snapshot.daily_risk_used, 60.0)
        self.assertEqual(snapshot.remaining_daily_risk, 24_940.0)
        self.assertEqual(snapshot.gross_exposure, 400.0)
        self.assertEqual(snapshot.net_exposure, 400.0)
        self.assertEqual(snapshot.market_value, 400.0)
        self.assertEqual(len(snapshot.open_positions), 1)
        self.assertEqual(snapshot.positions, snapshot.open_positions)
        self.assertEqual(snapshot.position_quantity, 4)
        self.assertEqual(snapshot.open_positions[0].average_entry_price, 100.0)
        self.assertEqual(snapshot.average_entry_price, 100.0)
        self.assertEqual(len(snapshot.individual_lots), 1)
        self.assertEqual(snapshot.individual_lots[0].algorithm_id, "weighted_voting")
        self.assertEqual(snapshot.individual_lots[0].remaining_quantity, 4)
        self.assertEqual(len(snapshot.pending_orders), 1)
        pending = snapshot.pending_orders[0]
        self.assertEqual(pending.algorithm_id, "weighted_voting")
        self.assertEqual(pending.order_id, "wv-order-1")
        self.assertEqual(pending.client_order_id, "client-1")
        self.assertEqual(pending.decision_id, "decision-1")
        self.assertEqual(pending.order_intent_id, "intent-1")
        self.assertEqual(pending.symbol, "SPY")
        self.assertEqual(pending.side, "BUY")
        self.assertEqual(pending.quantity, 10)
        self.assertEqual(pending.filled_quantity, 4)
        self.assertEqual(pending.remaining_quantity, 6)
        self.assertEqual(pending.order_type, "LIMIT")
        self.assertEqual(pending.limit_price, 100.0)
        self.assertEqual(pending.stop_price, 99.0)
        self.assertEqual(pending.status, "PARTIALLY_FILLED")
        self.assertEqual(pending.created_at, NOW)
        self.assertEqual(pending.updated_at, NOW + timedelta(seconds=2))
        self.assertEqual(pending.expiration, NOW + timedelta(minutes=5))
        self.assertEqual(pending.reserved_cash, 600.0)
        self.assertEqual(pending.reserved_buying_power, 600.0)
        self.assertEqual(pending.planned_risk_dollars, 60.0)
        self.assertEqual(snapshot.reserved_position_quantity, 6)
        self.assertEqual(len(snapshot.working_orders), 1)
        self.assertEqual(len(snapshot.partially_filled_orders), 1)
        self.assertEqual(snapshot.partially_filled_orders[0].filled_quantity, 4)
        self.assertEqual(snapshot.partially_filled_orders[0].status, "PARTIALLY_FILLED")
        self.assertEqual(snapshot.realized_pnl, snapshot.realised_pnl)
        self.assertEqual(snapshot.unrealized_pnl, snapshot.unrealised_pnl)
        self.assertEqual(snapshot.total_pnl, 0.0)
        self.assertEqual(snapshot.equity, 25_000.0)
        self.assertEqual(snapshot.daily_realized_pnl, snapshot.daily_realised_pnl)
        self.assertEqual(snapshot.daily_unrealized_pnl, snapshot.daily_unrealised_pnl)
        self.assertEqual(snapshot.risk_used, snapshot.daily_risk_used)
        self.assertEqual(snapshot.risk_remaining, snapshot.remaining_daily_risk)
        self.assertEqual(snapshot.last_updated_at, snapshot.updated_at)
        persisted = snapshot.as_dict()
        for key in (
            "initial_capital",
            "cash",
            "reserved_cash",
            "available_cash",
            "buying_power",
            "positions",
            "position_quantity",
            "average_entry_price",
            "market_value",
            "realized_pnl",
            "unrealized_pnl",
            "total_pnl",
            "equity",
            "reserved_position_quantity",
            "daily_realized_pnl",
            "daily_unrealized_pnl",
            "risk_used",
            "risk_remaining",
            "snapshot_version",
            "last_updated_at",
        ):
            self.assertIn(key, persisted)

    def test_accounting_invariants_validate_cash_reservation_and_equity_relationships_on_load(self) -> None:
        store = MemoryStore()
        repo = initialized_repo(store)
        snapshot = repo.current_snapshot()
        reserved = repo.append_event(
            event_id="invariant-reserve",
            event_type=WeightedVotingInventoryEventType.ORDER_RESERVED,
            payload=pending_order_payload(reserved_buying_power=6_000.0, planned_risk_dollars=600.0, quantity=60),
            occurred_at=NOW + timedelta(seconds=1),
            expected_snapshot_version=snapshot.snapshot_version,
        )

        self.assertEqual(reserved.cash, 25_000.0)
        self.assertEqual(reserved.reserved_cash, 6_000.0)
        self.assertEqual(reserved.available_cash, 19_000.0)
        self.assertEqual(reserved.available_cash, reserved.cash - reserved.reserved_cash)
        self.assertEqual(reserved.equity, reserved.cash + reserved.market_value)

        corrupted = replace(reserved, cash_available=5_000.0).as_dict()
        store.write_snapshot(CURRENT_SNAPSHOT_KEY, corrupted)

        with self.assertRaisesRegex(RuntimeError, "available cash cannot be negative"):
            repo.current_snapshot(now=NOW + timedelta(seconds=2))

    def test_accounting_invariants_validate_position_quantity_against_remaining_lots_on_load(self) -> None:
        store = MemoryStore()
        repo = initialized_repo(store)
        snapshot = repo.current_snapshot()
        filled = repo.append_event(
            event_id="invariant-fill",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(quantity=10, average_entry_price=100.0, fill_id="invariant-fill-1"),
            occurred_at=NOW + timedelta(seconds=1),
            expected_snapshot_version=snapshot.snapshot_version,
        )
        persisted = filled.as_dict()
        persisted["open_positions"][0]["lots"][0]["remaining_quantity"] = 9
        store.write_snapshot(CURRENT_SNAPSHOT_KEY, persisted)

        with self.assertRaisesRegex(RuntimeError, "position quantity must equal its remaining lots"):
            repo.current_snapshot(now=NOW + timedelta(seconds=2))

    def test_pending_order_reserves_buying_power_and_risk_before_fill(self) -> None:
        store = MemoryStore()
        repo = WeightedVotingInventoryRepository(store, allocated_capital=10_000.0)
        snapshot = repo.initialize_session(session_date=SESSION_DATE, allocated_capital=10_000.0, cash_available=10_000.0, occurred_at=NOW, expected_snapshot_version=0)

        reserved = repo.append_event(
            event_id="reserve-before-submit",
            event_type=WeightedVotingInventoryEventType.ORDER_RESERVED,
            payload=pending_order_payload(reserved_buying_power=6_000.0, planned_risk_dollars=600.0, quantity=60),
            occurred_at=NOW + timedelta(seconds=1),
            expected_snapshot_version=snapshot.snapshot_version,
        )

        self.assertEqual(reserved.cash_available, 10_000.0)
        self.assertEqual(reserved.available_cash, 4_000.0)
        self.assertEqual(reserved.buying_power, 4_000.0)
        self.assertEqual(reserved.reserved_cash, 6_000.0)
        self.assertEqual(reserved.available_cash, reserved.cash - reserved.reserved_cash)
        self.assertEqual(reserved.pending_orders[0].reserved_cash, 6_000.0)
        self.assertEqual(reserved.daily_risk_used, 600.0)
        self.assertEqual(reserved.remaining_daily_risk, 9_400.0)

    def test_terminal_order_release_clears_reservation_idempotently(self) -> None:
        for terminal_status in ("REJECTED", "CANCELED", "EXPIRED"):
            with self.subTest(terminal_status=terminal_status):
                store = MemoryStore()
                repo = WeightedVotingInventoryRepository(store, allocated_capital=10_000.0)
                snapshot = repo.initialize_session(session_date=SESSION_DATE, allocated_capital=10_000.0, cash_available=10_000.0, occurred_at=NOW, expected_snapshot_version=0)
                reserved = repo.append_event(
                    event_id=f"reserve-{terminal_status}",
                    event_type=WeightedVotingInventoryEventType.ORDER_RESERVED,
                    payload=pending_order_payload(reserved_buying_power=6_000.0, planned_risk_dollars=600.0, quantity=60),
                    occurred_at=NOW + timedelta(seconds=1),
                    expected_snapshot_version=snapshot.snapshot_version,
                )

                released = repo.append_event(
                    event_id=f"release-{terminal_status}",
                    event_type=WeightedVotingInventoryEventType.ORDER_RELEASED,
                    payload={
                        "algorithm_id": "weighted_voting",
                        "order_id": "wv-order-1",
                        "client_order_id": "client-1",
                        "decision_id": "decision-1",
                        "status": terminal_status,
                    },
                    occurred_at=NOW + timedelta(seconds=2),
                    expected_snapshot_version=reserved.snapshot_version,
                )
                repeated = repo.append_event(
                    event_id=f"release-{terminal_status}-redelivery",
                    event_type=WeightedVotingInventoryEventType.ORDER_RELEASED,
                    payload={
                        "algorithm_id": "weighted_voting",
                        "order_id": "wv-order-1",
                        "client_order_id": "client-1",
                        "decision_id": "decision-1",
                        "status": terminal_status,
                    },
                    occurred_at=NOW + timedelta(seconds=3),
                    expected_snapshot_version=released.snapshot_version,
                )

                self.assertEqual(released.pending_orders, ())
                self.assertEqual(released.reserved_buying_power, 0.0)
                self.assertEqual(released.cash_available, 10_000.0)
                self.assertEqual(released.daily_risk_used, 0.0)
                self.assertEqual(repeated.pending_orders, ())
                self.assertEqual(repeated.reserved_buying_power, 0.0)
                self.assertEqual(repeated.cash_available, 10_000.0)
                self.assertEqual(repeated.daily_risk_used, 0.0)
                self.assertEqual(repeated.snapshot_version, released.snapshot_version)

    def test_partial_and_complete_fills_recalculate_or_convert_reservation_idempotently(self) -> None:
        store = MemoryStore()
        repo = WeightedVotingInventoryRepository(store, allocated_capital=10_000.0)
        snapshot = repo.initialize_session(session_date=SESSION_DATE, allocated_capital=10_000.0, cash_available=10_000.0, occurred_at=NOW, expected_snapshot_version=0)
        reserved = repo.append_event(
            event_id="reserve-fill-lifecycle",
            event_type=WeightedVotingInventoryEventType.ORDER_RESERVED,
            payload=pending_order_payload(reserved_buying_power=1_000.0, planned_risk_dollars=100.0, quantity=10),
            occurred_at=NOW + timedelta(seconds=1),
            expected_snapshot_version=snapshot.snapshot_version,
        )

        partial = repo.append_event(
            event_id="partial-fill-lifecycle",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(quantity=4, average_entry_price=100.0, fill_id="partial-fill-1"),
            occurred_at=NOW + timedelta(seconds=2),
            expected_snapshot_version=reserved.snapshot_version,
        )
        repeated_partial = repo.append_event(
            event_id="partial-fill-lifecycle-redelivery",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(quantity=4, average_entry_price=100.0, fill_id="partial-fill-1"),
            occurred_at=NOW + timedelta(seconds=3),
            expected_snapshot_version=partial.snapshot_version,
        )

        self.assertEqual(len(partial.pending_orders), 1)
        self.assertEqual(partial.pending_orders[0].filled_quantity, 4)
        self.assertEqual(partial.pending_orders[0].remaining_quantity, 6)
        self.assertEqual(partial.reserved_buying_power, 600.0)
        self.assertEqual(partial.daily_risk_used, 60.0)
        self.assertEqual(partial.cash_available, 9_600.0)
        self.assertEqual(partial.available_cash, 9_000.0)
        self.assertEqual(partial.available_cash, partial.cash - partial.reserved_cash)
        self.assertEqual(repeated_partial.reserved_buying_power, partial.reserved_buying_power)
        self.assertEqual(repeated_partial.daily_risk_used, partial.daily_risk_used)
        self.assertEqual(repeated_partial.cash_available, partial.cash_available)

        complete = repo.append_event(
            event_id="complete-fill-lifecycle",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(quantity=6, average_entry_price=100.0, fill_id="complete-fill-1"),
            occurred_at=NOW + timedelta(seconds=4),
            expected_snapshot_version=repeated_partial.snapshot_version,
        )
        repeated_complete = repo.append_event(
            event_id="complete-fill-lifecycle-redelivery",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(quantity=6, average_entry_price=100.0, fill_id="complete-fill-1"),
            occurred_at=NOW + timedelta(seconds=5),
            expected_snapshot_version=complete.snapshot_version,
        )

        self.assertEqual(complete.pending_orders, ())
        self.assertEqual(complete.reserved_buying_power, 0.0)
        self.assertEqual(complete.open_positions[0].quantity, 10)
        self.assertEqual(complete.cash_available, 9_000.0)
        self.assertEqual(complete.daily_risk_used, 0.0)
        self.assertEqual(repeated_complete.pending_orders, complete.pending_orders)
        self.assertEqual(repeated_complete.reserved_buying_power, complete.reserved_buying_power)
        self.assertEqual(repeated_complete.open_positions[0].quantity, complete.open_positions[0].quantity)
        self.assertEqual(repeated_complete.cash_available, complete.cash_available)

    def test_buy_fill_converts_reserved_cash_into_position_and_equity_uses_market_value(self) -> None:
        store = MemoryStore()
        repo = WeightedVotingInventoryRepository(store, allocated_capital=100_000.0)
        snapshot = repo.initialize_session(session_date=SESSION_DATE, allocated_capital=100_000.0, cash_available=100_000.0, occurred_at=NOW, expected_snapshot_version=0)
        reserved = repo.append_event(
            event_id="reserve-buy-100-spy",
            event_type=WeightedVotingInventoryEventType.ORDER_RESERVED,
            payload=pending_order_payload(reserved_buying_power=60_000.0, planned_risk_dollars=1_000.0, quantity=100),
            occurred_at=NOW + timedelta(seconds=1),
            expected_snapshot_version=snapshot.snapshot_version,
        )

        filled = repo.append_event(
            event_id="fill-buy-100-spy",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(quantity=100, average_entry_price=600.0, fill_id="buy-100-spy-fill"),
            occurred_at=NOW + timedelta(seconds=2),
            expected_snapshot_version=reserved.snapshot_version,
        )
        marked = repo.append_event(
            event_id="mark-buy-100-spy",
            event_type=WeightedVotingInventoryEventType.POSITION_MARKED,
            payload={"algorithm_id": "weighted_voting", "position_id": "wv-position-1", "mark_price": 610.0},
            occurred_at=NOW + timedelta(seconds=3),
            expected_snapshot_version=filled.snapshot_version,
        )

        self.assertEqual(reserved.cash_available, 100_000.0)
        self.assertEqual(reserved.reserved_cash, 60_000.0)
        self.assertEqual(reserved.available_cash, 40_000.0)
        self.assertEqual(reserved.available_cash, reserved.cash - reserved.reserved_cash)
        self.assertEqual(filled.cash_available, 40_000.0)
        self.assertEqual(filled.reserved_cash, 0.0)
        self.assertEqual(filled.reserved_buying_power, 0.0)
        self.assertEqual(filled.pending_orders, ())
        self.assertEqual(filled.open_positions[0].quantity, 100)
        self.assertEqual(filled.open_positions[0].average_entry_price, 600.0)
        self.assertEqual(filled.market_value, 60_000.0)
        self.assertEqual(filled.equity, 100_000.0)
        self.assertEqual(marked.cash_available, 40_000.0)
        self.assertEqual(marked.market_value, 61_000.0)
        self.assertEqual(marked.unrealised_pnl, 1_000.0)
        self.assertEqual(marked.equity, 101_000.0)
        self.assertEqual(marked.equity, marked.cash_available + marked.market_value)

    def test_sell_fill_closes_long_position_updates_cash_and_realized_pnl(self) -> None:
        store = MemoryStore()
        repo = WeightedVotingInventoryRepository(store, allocated_capital=100_000.0)
        snapshot = repo.initialize_session(session_date=SESSION_DATE, allocated_capital=100_000.0, cash_available=100_000.0, occurred_at=NOW, expected_snapshot_version=0)
        reserved = repo.append_event(
            event_id="reserve-buy-before-sell",
            event_type=WeightedVotingInventoryEventType.ORDER_RESERVED,
            payload=pending_order_payload(reserved_buying_power=60_000.0, planned_risk_dollars=1_000.0, quantity=100),
            occurred_at=NOW + timedelta(seconds=1),
            expected_snapshot_version=snapshot.snapshot_version,
        )
        bought = repo.append_event(
            event_id="buy-100-at-600",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(quantity=100, average_entry_price=600.0, fill_id="buy-100-at-600"),
            occurred_at=NOW + timedelta(seconds=2),
            expected_snapshot_version=reserved.snapshot_version,
        )

        sold = repo.append_event(
            event_id="sell-100-at-603",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(quantity=-100, average_entry_price=603.0, fill_id="sell-100-at-603"),
            occurred_at=NOW + timedelta(seconds=3),
            expected_snapshot_version=bought.snapshot_version,
        )
        repeated_sell = repo.append_event(
            event_id="sell-100-at-603-redelivery",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(quantity=-100, average_entry_price=603.0, fill_id="sell-100-at-603"),
            occurred_at=NOW + timedelta(seconds=4),
            expected_snapshot_version=sold.snapshot_version,
        )

        self.assertEqual(bought.cash_available, 40_000.0)
        self.assertEqual(sold.open_positions, ())
        self.assertEqual(sold.position_quantity, 0)
        self.assertEqual(sold.reserved_cash, 0.0)
        self.assertEqual(sold.market_value, 0.0)
        self.assertEqual(sold.realised_pnl, 300.0)
        self.assertEqual(sold.daily_realised_pnl, 300.0)
        self.assertEqual(sold.cash_available, 100_300.0)
        self.assertEqual(sold.equity, sold.cash_available)
        self.assertEqual(repeated_sell.open_positions, ())
        self.assertEqual(repeated_sell.realised_pnl, 300.0)
        self.assertEqual(repeated_sell.cash_available, 100_300.0)
        self.assertEqual(repeated_sell.snapshot_version, sold.snapshot_version)

    def test_partial_sell_reduces_long_and_splits_realized_and_unrealized_pnl(self) -> None:
        store = MemoryStore()
        repo = WeightedVotingInventoryRepository(store, allocated_capital=100_000.0)
        snapshot = repo.initialize_session(session_date=SESSION_DATE, allocated_capital=100_000.0, cash_available=100_000.0, occurred_at=NOW, expected_snapshot_version=0)
        reserved = repo.append_event(
            event_id="reserve-buy-before-partial-sell",
            event_type=WeightedVotingInventoryEventType.ORDER_RESERVED,
            payload=pending_order_payload(reserved_buying_power=60_000.0, planned_risk_dollars=1_000.0, quantity=100),
            occurred_at=NOW + timedelta(seconds=1),
            expected_snapshot_version=snapshot.snapshot_version,
        )
        bought = repo.append_event(
            event_id="buy-100-before-partial-sell",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(quantity=100, average_entry_price=600.0, fill_id="buy-100-before-partial-sell"),
            occurred_at=NOW + timedelta(seconds=2),
            expected_snapshot_version=reserved.snapshot_version,
        )
        marked = repo.append_event(
            event_id="mark-before-partial-sell",
            event_type=WeightedVotingInventoryEventType.POSITION_MARKED,
            payload={"algorithm_id": "weighted_voting", "position_id": "wv-position-1", "mark_price": 610.0},
            occurred_at=NOW + timedelta(seconds=3),
            expected_snapshot_version=bought.snapshot_version,
        )

        sold = repo.append_event(
            event_id="sell-40-at-603",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(quantity=-40, average_entry_price=603.0, fill_id="sell-40-at-603"),
            occurred_at=NOW + timedelta(seconds=4),
            expected_snapshot_version=marked.snapshot_version,
        )

        self.assertEqual(len(sold.open_positions), 1)
        self.assertEqual(sold.open_positions[0].quantity, 60)
        self.assertEqual(sold.open_positions[0].average_entry_price, 600.0)
        self.assertEqual(sold.open_positions[0].mark_price, 610.0)
        self.assertEqual(sold.realised_pnl, 120.0)
        self.assertEqual(sold.unrealised_pnl, 600.0)
        self.assertEqual(sold.market_value, 36_600.0)
        self.assertEqual(sold.cash_available, 64_120.0)
        self.assertEqual(sold.equity, 100_720.0)
        self.assertEqual(sold.equity, sold.cash_available + sold.market_value)

    def test_pyramided_buy_fills_use_quantity_weighted_average_cost(self) -> None:
        store = MemoryStore()
        repo = WeightedVotingInventoryRepository(store, allocated_capital=100_000.0)
        snapshot = repo.initialize_session(session_date=SESSION_DATE, allocated_capital=100_000.0, cash_available=100_000.0, occurred_at=NOW, expected_snapshot_version=0)
        first = repo.append_event(
            event_id="pyramid-buy-50-at-600",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(position_id="wv-pyramid-1", client_order_id="pyramid-client-1", order_intent_id="pyramid-intent-1", quantity=50, average_entry_price=600.0, fill_id="pyramid-buy-50-at-600"),
            occurred_at=NOW + timedelta(seconds=1),
            expected_snapshot_version=snapshot.snapshot_version,
        )
        second = repo.append_event(
            event_id="pyramid-buy-50-at-602",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(position_id="wv-pyramid-2", client_order_id="pyramid-client-2", order_intent_id="pyramid-intent-2", quantity=50, average_entry_price=602.0, fill_id="pyramid-buy-50-at-602"),
            occurred_at=NOW + timedelta(seconds=2),
            expected_snapshot_version=first.snapshot_version,
        )

        self.assertEqual(second.position_quantity, 100)
        self.assertEqual(second.average_entry_price, 601.0)
        self.assertEqual(sum(abs(position.quantity) for position in second.open_positions), 100)
        self.assertEqual(second.cash_available, 39_900.0)

    def test_mark_to_market_updates_local_valuation_and_risk_metrics(self) -> None:
        store = MemoryStore()
        repo = WeightedVotingInventoryRepository(store, allocated_capital=100_000.0)
        snapshot = repo.initialize_session(session_date=SESSION_DATE, allocated_capital=100_000.0, cash_available=100_000.0, occurred_at=NOW, expected_snapshot_version=0)
        bought = repo.append_event(
            event_id="mark-to-market-buy",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(quantity=100, average_entry_price=600.0, fill_id="mark-to-market-buy-fill"),
            occurred_at=NOW + timedelta(seconds=1),
            expected_snapshot_version=snapshot.snapshot_version,
        )

        marked = repo.mark_to_market(
            symbol="SPY",
            price=610.0,
            occurred_at=NOW + timedelta(seconds=2),
            market_event_id="market-event-610",
            expected_snapshot_version=bought.snapshot_version,
        )
        repeated = repo.mark_to_market(
            symbol="SPY",
            price=610.0,
            occurred_at=NOW + timedelta(seconds=3),
            market_event_id="market-event-610",
            expected_snapshot_version=marked.snapshot_version,
        )

        self.assertEqual(marked.last_price, 610.0)
        self.assertEqual(marked.open_positions[0].mark_price, 610.0)
        self.assertEqual(marked.open_positions[0].unrealised_pnl, 1_000.0)
        self.assertEqual(marked.market_value, 61_000.0)
        self.assertEqual(marked.gross_exposure, 61_000.0)
        self.assertEqual(marked.net_exposure, 61_000.0)
        self.assertEqual(marked.unrealised_pnl, 1_000.0)
        self.assertEqual(marked.equity, 101_000.0)
        self.assertEqual(marked.daily_loss, 0.0)
        self.assertEqual(marked.daily_risk_used, 0.0)
        self.assertEqual(marked.risk_remaining, 100_000.0)
        self.assertEqual(marked.as_dict()["last_price"], 610.0)
        self.assertEqual(repeated.snapshot_version, marked.snapshot_version)

    def test_inventory_rejects_unsupported_short_opening_fill(self) -> None:
        store = MemoryStore()
        repo = initialized_repo(store)
        snapshot = repo.current_snapshot()

        with self.assertRaisesRegex(ValueError, "unsupported opening short"):
            repo.append_event(
                event_id="unsupported-open-short",
                event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
                payload=position_payload(quantity=-10, average_entry_price=100.0, fill_id="unsupported-open-short-fill"),
                occurred_at=NOW + timedelta(seconds=1),
                expected_snapshot_version=snapshot.snapshot_version,
            )

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
        self.assertEqual(closed.daily_starting_equity, 25_000.0)
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
        with self.assertRaises(ValueError):
            repo.append_event(
                event_id="foreign-pending-order",
                event_type=WeightedVotingInventoryEventType.ORDER_RESERVED,
                payload={**pending_order_payload(), "algorithm_id": "meta_strategy"},
                occurred_at=NOW,
                expected_snapshot_version=snapshot.snapshot_version,
            )
        with self.assertRaises(ValueError):
            payload = pending_order_payload()
            del payload["algorithm_id"]
            repo.append_event(
                event_id="missing-owner-pending-order",
                event_type=WeightedVotingInventoryEventType.ORDER_RESERVED,
                payload=payload,
                occurred_at=NOW,
                expected_snapshot_version=snapshot.snapshot_version,
            )
        with self.assertRaises(ValueError):
            repo.append_event(
                event_id="foreign-nested-risk-reservation",
                event_type=WeightedVotingInventoryEventType.ORDER_RESERVED,
                payload={
                    **pending_order_payload(),
                    "risk_reservation": {
                        "algorithm_id": "voting_ensemble",
                        "reservation_id": "foreign-risk-reservation",
                    },
                },
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

    def test_wrong_owner_fill_api_rejects_voting_ensemble_without_state_mutation(self) -> None:
        store = MemoryStore()
        repo = initialized_repo(store)
        snapshot = repo.current_snapshot()
        snapshot_before = snapshot.as_dict()
        persisted_before = deepcopy(store.snapshots)

        with self.assertRaisesRegex(ValueError, "cross-algorithm writes"):
            repo.append_event(
                event_id="wrong-owner-voting-ensemble-fill",
                event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
                payload={
                    **position_payload(quantity=10, average_entry_price=100.0, fill_id="wrong-owner-fill"),
                    "algorithm_id": "voting_ensemble",
                },
                occurred_at=NOW + timedelta(seconds=1),
                expected_snapshot_version=snapshot.snapshot_version,
            )

        snapshot_after = repo.current_snapshot(now=NOW + timedelta(seconds=2))

        self.assertEqual(snapshot_after.as_dict(), snapshot_before)
        self.assertEqual(store.snapshots, persisted_before)
        self.assertFalse(any("wrong-owner-voting-ensemble-fill" in key for key in store.snapshots))

    def test_concurrent_reservations_use_optimistic_snapshot_version_without_double_spend(self) -> None:
        store = SlowCurrentSnapshotWriteStore()
        repo = WeightedVotingInventoryRepository(store, allocated_capital=10_000.0)
        snapshot = repo.initialize_session(session_date=SESSION_DATE, allocated_capital=10_000.0, cash_available=10_000.0, occurred_at=NOW, expected_snapshot_version=0)
        started = threading.Barrier(2)

        def reserve(order_id: str):
            worker_repo = WeightedVotingInventoryRepository(store, allocated_capital=10_000.0)
            started.wait(timeout=5)
            try:
                payload = pending_order_payload(reserved_buying_power=8_000.0)
                payload.update({"order_id": order_id, "client_order_id": f"{order_id}-client"})
                return worker_repo.append_event(
                    event_id=f"reserve-{order_id}",
                    event_type=WeightedVotingInventoryEventType.ORDER_RESERVED,
                    payload=payload,
                    occurred_at=NOW + timedelta(seconds=1),
                    expected_snapshot_version=snapshot.snapshot_version,
                )
            except RuntimeError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result() for future in (pool.submit(reserve, "a"), pool.submit(reserve, "b"))]

        successes = [result for result in results if not isinstance(result, RuntimeError)]
        failures = [result for result in results if isinstance(result, RuntimeError)]
        final = repo.current_snapshot(now=NOW)

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIn("optimistic version check failed", str(failures[0]))
        self.assertEqual(final.reserved_buying_power, 8_000.0)
        self.assertEqual(final.remaining_capital_partition, 2_000.0)
        self.assertEqual(len(final.pending_orders), 1)

    def test_oversized_reservation_fails_closed_before_persisting_negative_buying_power(self) -> None:
        store = MemoryStore()
        repo = WeightedVotingInventoryRepository(store, allocated_capital=10_000.0)
        snapshot = repo.initialize_session(session_date=SESSION_DATE, allocated_capital=10_000.0, cash_available=10_000.0, occurred_at=NOW, expected_snapshot_version=0)

        with self.assertRaises(RuntimeError):
            payload = pending_order_payload(reserved_buying_power=16_000.0)
            payload.update({"order_id": "too-large", "client_order_id": "too-large-client"})
            repo.append_event(
                event_id="reserve-too-large",
                event_type=WeightedVotingInventoryEventType.ORDER_RESERVED,
                payload=payload,
                occurred_at=NOW + timedelta(seconds=1),
                expected_snapshot_version=snapshot.snapshot_version,
            )

        final = repo.current_snapshot(now=NOW)
        self.assertEqual(final.reserved_buying_power, 0.0)
        self.assertEqual(final.remaining_capital_partition, 10_000.0)
        self.assertEqual(final.pending_orders, ())

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

        self.assertEqual(updated.cash_available, 24_700.0)
        self.assertEqual(updated.gross_exposure, 300.0)
        self.assertEqual(updated.daily_trade_count, 0)

    def test_inventory_status_declares_boundary_controls(self) -> None:
        status = inventory_status()

        self.assertEqual(status["algorithmId"], "weighted_voting")
        self.assertTrue(status["optimisticVersionChecks"])
        self.assertEqual(status["idempotency"], "event_id")
        self.assertEqual(status["brokerAccountRole"], "authoritative_local_paper_state_source")
        self.assertIn("buying_power", status["authoritativeFields"])
        self.assertIn("equity", status["authoritativeFields"])
        self.assertIn("daily_starting_equity", status["authoritativeFields"])


def initialized_repo(store: "MemoryStore") -> WeightedVotingInventoryRepository:
    repo = WeightedVotingInventoryRepository(store, allocated_capital=25_000.0)
    repo.initialize_session(session_date=SESSION_DATE, allocated_capital=25_000.0, cash_available=25_000.0, occurred_at=NOW, expected_snapshot_version=0)
    return repo


def pending_order_payload(*, reserved_buying_power: float = 1_000.0, planned_risk_dollars: float = 100.0, quantity: int = 10) -> dict:
    return {
        "algorithm_id": "weighted_voting",
        "order_id": "wv-order-1",
        "symbol": "SPY",
        "side": "BUY",
        "quantity": quantity,
        "filled_quantity": 0,
        "remaining_quantity": quantity,
        "order_type": "LIMIT",
        "limit_price": 100.0,
        "stop_price": 99.0,
        "reserved_buying_power": reserved_buying_power,
        "reserved_cash": reserved_buying_power,
        "planned_risk_dollars": planned_risk_dollars,
        "decision_id": "decision-1",
        "order_intent_id": "intent-1",
        "client_order_id": "client-1",
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "expiration": (NOW + timedelta(minutes=5)).isoformat(),
    }


def position_payload(
    *,
    position_id: str = "wv-position-1",
    quantity: int = 10,
    average_entry_price: float = 100.0,
    fill_id: str | None = None,
    client_order_id: str = "client-1",
    order_intent_id: str = "intent-1",
) -> dict:
    return {
        "algorithm_id": "weighted_voting",
        **({"fill_id": fill_id} if fill_id else {}),
        "position_id": position_id,
        "symbol": "SPY",
        "side": "LONG" if quantity > 0 else "SHORT",
        "quantity": quantity,
        "average_entry_price": average_entry_price,
        "opened_at": NOW.isoformat(),
        "decision_id": "decision-1",
        "order_intent_id": order_intent_id,
        "client_order_id": client_order_id,
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


class SlowCurrentSnapshotWriteStore(MemoryStore):
    def write_snapshot(self, key: str, snapshot: dict) -> None:
        if key == CURRENT_SNAPSHOT_KEY:
            time.sleep(0.01)
        super().write_snapshot(key, snapshot)


if __name__ == "__main__":
    unittest.main()
