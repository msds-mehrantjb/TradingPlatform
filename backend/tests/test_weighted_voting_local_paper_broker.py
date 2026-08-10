from __future__ import annotations

import asyncio
import unittest
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.execution_gateway import (
    execution_gateway_status,
    submit_queued_weighted_voting_paper_order,
)
from backend.app.algorithms.weighted_voting.inventory import WeightedVotingInventoryEventType, WeightedVotingInventoryRepository
from backend.app.algorithms.weighted_voting.local_paper_broker import (
    WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE,
    WeightedVotingLocalPaperBroker,
    WeightedVotingLocalPaperRiskPort,
    WeightedVotingLocalPaperRiskService,
)
from backend.app.algorithms.weighted_voting.market_condition import classify_market_condition
from backend.app.algorithms.weighted_voting.market_snapshot import build_weighted_voting_market_snapshot
from backend.app.algorithms.weighted_voting.runtime_supervisor import (
    WeightedVotingEventBus,
    WeightedVotingFinalisedBarEvent,
    WeightedVotingRuntimeConfig,
    WeightedVotingRuntimeSupervisor,
)
from backend.app.algorithms.weighted_voting.service import WeightedVotingService
from backend.app.domain.models import Signal
from backend.app.execution import PaperOrderGateway, PaperOrderIntentRecord
from backend.app.gates import GlobalGateResponse, apply_global_gate_response
from backend.tests.test_weighted_voting_paper_order_gateway import (
    global_application,
    global_proposal,
    local_gate,
    validated_rollout_flags,
    validated_rollout_validation,
)
from backend.app.algorithms.weighted_voting.execution_gateway import enqueue_weighted_voting_execution_order


NOW = datetime(2026, 7, 14, 15, 30, tzinfo=UTC)


class WeightedVotingLocalPaperBrokerTest(unittest.TestCase):
    def test_default_runtime_uses_local_paper_without_alpaca_credentials(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)

        supervisor = WeightedVotingRuntimeSupervisor(store=store, inventory_repository=inventory)

        self.assertIsInstance(supervisor.paper_gateway.broker, WeightedVotingLocalPaperBroker)
        self.assertEqual(supervisor.paper_gateway.execution_mode, "LOCAL_PAPER")
        self.assertIsInstance(supervisor.account_port, WeightedVotingLocalPaperBroker)
        self.assertIsInstance(supervisor.global_risk_port, WeightedVotingLocalPaperRiskPort)
        account = supervisor.account_port.account_observation(as_of=NOW)
        self.assertEqual(account.account_equity, 25_000.0)
        self.assertEqual(account.broker_buying_power, 25_000.0)
        self.assertIn("weighted_voting.local_paper.account_from_dedicated_inventory", account.reason_codes)

    def test_default_runtime_seeds_weighted_voting_owned_initial_capital_from_config(self) -> None:
        store = MemoryStore()
        config = WeightedVotingConfig(local_paper_initial_capital=123_456.78)

        supervisor = WeightedVotingRuntimeSupervisor(store=store, weighted_config=config)
        snapshot = supervisor.inventory_repository.current_snapshot(now=NOW)
        account = supervisor.account_port.account_observation(as_of=NOW)

        self.assertEqual(snapshot.capital_partition_id, "weighted_voting.paper.default")
        self.assertEqual(snapshot.initial_capital, 123_456.78)
        self.assertEqual(snapshot.equity, 123_456.78)
        self.assertEqual(account.account_equity, 123_456.78)
        self.assertEqual(account.broker_buying_power, 123_456.78)
        self.assertIn("weighted_voting.inventory.snapshot.current", store.snapshots)
        self.assertFalse(any(key.startswith(("voting_ensemble.", "wca.", "meta_strategy.", "regime_based.")) for key in store.snapshots))

    def test_local_paper_submission_fills_and_updates_dedicated_inventory(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker = WeightedVotingLocalPaperBroker(store, inventory)
        gateway = PaperOrderGateway(
            broker,
            store,
            execution_mode="LOCAL_PAPER",
            account_snapshot_provider=broker.gateway_account_snapshot,
            portfolio_snapshot_provider=broker.gateway_portfolio_snapshot,
        )
        proposal = _proposal_with_quote(global_proposal(quantity=10))
        item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            enqueued_at=NOW,
            idempotency_key="weighted_voting.local_paper.test",
        )

        result = submit_queued_weighted_voting_paper_order(
            gateway=gateway,
            queue_item=item,
            inventory_repository=inventory,
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        snapshot = inventory.current_snapshot(now=NOW)

        self.assertTrue(result.submitted)
        self.assertEqual(result.executionMode, "LOCAL_PAPER")
        self.assertEqual(result.status, "FILLED")
        self.assertEqual(len(snapshot.open_positions), 1)
        self.assertEqual(snapshot.open_positions[0].algorithm_id, "weighted_voting")
        self.assertEqual(snapshot.reserved_buying_power, 0.0)
        self.assertTrue(any(key.startswith(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.") for key in store.snapshots))
        self.assertFalse(any(key.startswith("paper_order_gateway.") for key in store.snapshots))
        self.assertFalse(any("alpaca" in str(value).lower() for value in store.snapshots.values()))

    def test_local_paper_lifecycle_logs_include_ownership_ids_and_snapshot_versions(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker, result = _submit_bracket_entry(store, inventory, order_intent_id="wv-lifecycle")

        self.assertTrue(result.submitted)
        broker.process_market_data(symbol="SPY", market_data=_quote(bid=102.05, ask=102.10, timestamp=NOW + timedelta(minutes=1)), observed_at=NOW + timedelta(minutes=1))
        events = _local_lifecycle_events(store)
        names = {event["eventName"] for event in events}

        self.assertIn("weighted_voting.local_paper.order_created", names)
        self.assertIn("weighted_voting.local_paper.order_open", names)
        self.assertIn("weighted_voting.local_paper.fill_recorded", names)
        self.assertIn("weighted_voting.local_paper.position_updated", names)
        self.assertIn("weighted_voting.local_paper.reservation_released", names)
        self.assertIn("weighted_voting.local_paper.exit_filled", names)
        for event in events:
            self.assertEqual(event["algorithmId"], "weighted_voting")
            self.assertEqual(event["executionMode"], "LOCAL_PAPER")
            self.assertEqual(event["brokerKind"], "weighted_voting_local_paper")
            self.assertEqual(event["decisionId"], "wv-lifecycle.decision")
            self.assertTrue(event["orderIntentId"])
            self.assertTrue(event["clientOrderId"])
            self.assertIsInstance(event["inventorySnapshotVersion"], int)
            self.assertGreaterEqual(event["inventorySnapshotVersion"], 1)
            self.assertTrue(event["lifecycleEventId"])
        positioned_events = [event for event in events if event["eventName"] in {"weighted_voting.local_paper.fill_recorded", "weighted_voting.local_paper.position_updated", "weighted_voting.local_paper.exit_filled"}]
        self.assertTrue(positioned_events)
        self.assertTrue(all(event.get("positionId") == f"weighted_voting.position.SPY.{result.clientOrderId}" for event in positioned_events))

    def test_local_broker_tracks_weighted_voting_stop_loss_and_closes_position(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker = WeightedVotingLocalPaperBroker(store, inventory)
        gateway = PaperOrderGateway(
            broker,
            store,
            execution_mode="LOCAL_PAPER",
            account_snapshot_provider=broker.gateway_account_snapshot,
            portfolio_snapshot_provider=broker.gateway_portfolio_snapshot,
        )
        proposal = _proposal_with_quote(global_proposal(quantity=10, order_intent_id="wv-stop-parent"))
        item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            enqueued_at=NOW,
            idempotency_key="weighted_voting.local_paper.stop-parent",
        )
        result = submit_queued_weighted_voting_paper_order(
            gateway=gateway,
            queue_item=item,
            inventory_repository=inventory,
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        foreign_key = f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.foreign-stop"
        store.write_snapshot(
            foreign_key,
            {
                "algorithmId": "voting_ensemble",
                "clientOrderId": "foreign-stop",
                "parentClientOrderId": result.clientOrderId,
                "symbol": "SPY",
                "status": "OPEN",
            },
        )

        fills = broker.process_market_data(symbol="SPY", market_data=_quote(bid=98.9, ask=99.0, timestamp=NOW + timedelta(minutes=1)), observed_at=NOW + timedelta(minutes=1))
        snapshot = inventory.current_snapshot(now=NOW + timedelta(minutes=1))
        stop_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.{result.clientOrderId}-stop"]
        target_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.{result.clientOrderId}-target"]

        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].side, Signal.SELL)
        self.assertEqual(fills[0].filledQuantity, 10)
        self.assertEqual(stop_order["status"], "FILLED")
        self.assertEqual(stop_order["parentClientOrderId"], result.clientOrderId)
        self.assertEqual(stop_order["parentPositionId"], f"weighted_voting.position.SPY.{result.clientOrderId}")
        self.assertEqual(target_order["status"], "CANCELED")
        self.assertEqual(store.snapshots[foreign_key]["status"], "OPEN")
        self.assertEqual(snapshot.open_positions, ())
        self.assertLess(snapshot.realised_pnl, 0.0)

    def test_local_broker_tracks_weighted_voting_profit_target_and_cancels_stop_sibling(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker = WeightedVotingLocalPaperBroker(store, inventory)
        gateway = PaperOrderGateway(
            broker,
            store,
            execution_mode="LOCAL_PAPER",
            account_snapshot_provider=broker.gateway_account_snapshot,
            portfolio_snapshot_provider=broker.gateway_portfolio_snapshot,
        )
        proposal = _proposal_with_quote(global_proposal(quantity=10, order_intent_id="wv-target-parent"))
        item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            enqueued_at=NOW,
            idempotency_key="weighted_voting.local_paper.target-parent",
        )
        result = submit_queued_weighted_voting_paper_order(
            gateway=gateway,
            queue_item=item,
            inventory_repository=inventory,
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )

        fills = broker.process_market_data(symbol="SPY", market_data=_quote(bid=102.05, ask=102.10, timestamp=NOW + timedelta(minutes=1)), observed_at=NOW + timedelta(minutes=1))
        snapshot = inventory.current_snapshot(now=NOW + timedelta(minutes=1))
        stop_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.{result.clientOrderId}-stop"]
        target_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.{result.clientOrderId}-target"]

        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].side, Signal.SELL)
        self.assertEqual(target_order["status"], "FILLED")
        self.assertEqual(target_order["protectiveKind"], "profit_target")
        self.assertEqual(stop_order["status"], "CANCELED")
        self.assertEqual(snapshot.open_positions, ())
        self.assertGreater(snapshot.realised_pnl, 0.0)

    def test_local_bracket_oco_target_fill_prevents_later_stop_double_exit(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker, result = _submit_bracket_entry(store, inventory, order_intent_id="wv-oco-target-first")
        entry_snapshot = inventory.current_snapshot(now=NOW)
        initial_stop_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.{result.clientOrderId}-stop"]
        initial_target_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.{result.clientOrderId}-target"]

        self.assertTrue(result.submitted)
        self.assertEqual(result.status, "FILLED")
        self.assertIsNotNone(result.fill)
        self.assertEqual(result.fill.filledQuantity, 10)
        self.assertEqual(entry_snapshot.position_quantity, 10)
        self.assertEqual(initial_stop_order["status"], "OPEN")
        self.assertEqual(initial_target_order["status"], "OPEN")
        self.assertEqual(initial_stop_order["parentClientOrderId"], result.clientOrderId)
        self.assertEqual(initial_target_order["parentClientOrderId"], result.clientOrderId)

        target_fills = broker.process_market_data(symbol="SPY", market_data=_quote(bid=102.05, ask=102.10, timestamp=NOW + timedelta(minutes=1)), observed_at=NOW + timedelta(minutes=1))
        stop_fills = broker.process_market_data(symbol="SPY", market_data=_quote(bid=98.9, ask=99.0, timestamp=NOW + timedelta(minutes=2)), observed_at=NOW + timedelta(minutes=2))
        snapshot = inventory.current_snapshot(now=NOW + timedelta(minutes=2))
        stop_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.{result.clientOrderId}-stop"]
        target_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.{result.clientOrderId}-target"]
        closing_fills = (*target_fills, *stop_fills)

        self.assertEqual(len(target_fills), 1)
        self.assertEqual(stop_fills, ())
        self.assertEqual(sum(fill.filledQuantity for fill in closing_fills), result.fill.filledQuantity)
        self.assertEqual(len(closing_fills), 1)
        self.assertEqual(closing_fills[0].clientOrderId, f"{result.clientOrderId}-target")
        self.assertEqual(closing_fills[0].side, Signal.SELL)
        self.assertEqual(target_order["status"], "FILLED")
        self.assertEqual(stop_order["status"], "CANCELED")
        self.assertEqual(stop_order["reasonCodes"][-1], "weighted_voting.local_paper.protective_sibling_canceled_after_exit_fill")
        self.assertEqual(snapshot.open_positions, ())
        self.assertFalse(any(position.quantity < 0 for position in snapshot.open_positions))

    def test_local_bracket_oco_stop_fill_prevents_later_target_double_exit(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker, result = _submit_bracket_entry(store, inventory, order_intent_id="wv-oco-stop-first")
        entry_snapshot = inventory.current_snapshot(now=NOW)
        initial_stop_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.{result.clientOrderId}-stop"]
        initial_target_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.{result.clientOrderId}-target"]

        self.assertTrue(result.submitted)
        self.assertEqual(result.status, "FILLED")
        self.assertIsNotNone(result.fill)
        self.assertEqual(result.fill.filledQuantity, 10)
        self.assertEqual(entry_snapshot.position_quantity, 10)
        self.assertEqual(initial_stop_order["status"], "OPEN")
        self.assertEqual(initial_target_order["status"], "OPEN")
        self.assertEqual(initial_stop_order["parentClientOrderId"], result.clientOrderId)
        self.assertEqual(initial_target_order["parentClientOrderId"], result.clientOrderId)

        stop_fills = broker.process_market_data(symbol="SPY", market_data=_quote(bid=98.9, ask=99.0, timestamp=NOW + timedelta(minutes=1)), observed_at=NOW + timedelta(minutes=1))
        target_fills = broker.process_market_data(symbol="SPY", market_data=_quote(bid=102.05, ask=102.10, timestamp=NOW + timedelta(minutes=2)), observed_at=NOW + timedelta(minutes=2))
        snapshot = inventory.current_snapshot(now=NOW + timedelta(minutes=2))
        stop_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.{result.clientOrderId}-stop"]
        target_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.{result.clientOrderId}-target"]
        closing_fills = (*stop_fills, *target_fills)

        self.assertEqual(len(stop_fills), 1)
        self.assertEqual(target_fills, ())
        self.assertEqual(sum(fill.filledQuantity for fill in closing_fills), result.fill.filledQuantity)
        self.assertEqual(len(closing_fills), 1)
        self.assertEqual(closing_fills[0].clientOrderId, f"{result.clientOrderId}-stop")
        self.assertEqual(closing_fills[0].side, Signal.SELL)
        self.assertEqual(stop_order["status"], "FILLED")
        self.assertEqual(target_order["status"], "CANCELED")
        self.assertEqual(target_order["reasonCodes"][-1], "weighted_voting.local_paper.protective_sibling_canceled_after_exit_fill")
        self.assertEqual(snapshot.open_positions, ())
        self.assertFalse(any(position.quantity < 0 for position in snapshot.open_positions))

    def test_local_broker_accepts_weighted_voting_limit_stop_and_stop_limit_orders(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker = WeightedVotingLocalPaperBroker(store, inventory)

        cases = (("limit", "LIMIT"), ("STOP", "STOP"), ("stop-limit", "STOP_LIMIT"))
        for raw_order_type, expected_order_type in cases:
            intent = _local_intent(order_type=raw_order_type, order_intent_id=f"wv-{expected_order_type.lower()}")

            ack = broker.submit_bracket_order(intent)
            fill = broker.refresh_order(intent.clientOrderId)
            order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{intent.clientOrderId}"]

            self.assertEqual(ack.status, "ACCEPTED")
            self.assertIsNotNone(fill)
            self.assertEqual(fill.status, "FILLED")
            self.assertEqual(order["orderType"], expected_order_type)
            self.assertEqual(order["status"], "FILLED")
            self.assertEqual(order["lifecycleStatuses"], ["PENDING", "ACCEPTED", "OPEN", "FILLED"])

    def test_replaying_same_client_order_id_is_pure_idempotent_noop(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker = WeightedVotingLocalPaperBroker(store, inventory)
        intent = _local_intent(order_type="LIMIT", order_intent_id="wv-idempotent-local-order")

        first = broker.submit_bracket_order(intent)
        order_key = f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{intent.clientOrderId}"
        fill_key = f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.fills.{intent.clientOrderId}"
        order_before = deepcopy(store.snapshots[order_key])
        fill_before = deepcopy(store.snapshots[fill_key])
        snapshot_before = deepcopy(dict(store.snapshots))

        second = broker.submit_bracket_order(intent)

        self.assertEqual(first.status, "ACCEPTED")
        self.assertEqual(second.status, "ACCEPTED")
        self.assertEqual(store.snapshots[order_key], order_before)
        self.assertEqual(store.snapshots[fill_key], fill_before)
        self.assertEqual(store.snapshots, snapshot_before)

    def test_local_broker_records_partial_open_expired_replaced_and_canceled_states(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker = WeightedVotingLocalPaperBroker(store, inventory)

        partial = _local_intent(order_type="STOP_LIMIT", order_intent_id="wv-partial", filled_quantity=3)
        broker.submit_bracket_order(partial)
        partial_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{partial.clientOrderId}"]
        partial_fill = broker.refresh_order(partial.clientOrderId)
        self.assertEqual(partial_order["status"], "PARTIALLY_FILLED")
        self.assertEqual(partial_order["filledQuantity"], 3)
        self.assertEqual(partial_order["remainingQuantity"], 7)
        self.assertEqual(partial_order["lifecycleStatuses"], ["PENDING", "ACCEPTED", "OPEN", "PARTIALLY_FILLED"])
        self.assertIsNotNone(partial_fill)
        self.assertEqual(partial_fill.status, "PARTIALLY_FILLED")

        all_or_none = _local_intent(
            order_type="STOP_LIMIT",
            order_intent_id="wv-all-or-none",
            filled_quantity=3,
            settings_extra={"weighted_voting.local_paper.partial_fill_mode": "ALL_OR_NONE"},
        )
        broker.submit_bracket_order(all_or_none)
        all_or_none_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{all_or_none.clientOrderId}"]
        self.assertEqual(all_or_none_order["status"], "OPEN")
        self.assertEqual(all_or_none_order["filledQuantity"], 0)
        self.assertEqual(all_or_none_order["remainingQuantity"], 10)

        open_intent = _local_intent(order_type="LIMIT", order_intent_id="wv-open", quote=_quote(bid=100.5, ask=100.5))
        broker.submit_bracket_order(open_intent)
        open_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{open_intent.clientOrderId}"]
        self.assertEqual(open_order["status"], "OPEN")
        self.assertEqual(open_order["lifecycleStatuses"], ["PENDING", "ACCEPTED", "OPEN"])

        cancel_intent = _local_intent(order_type="STOP", order_intent_id="wv-cancel", stop_price=101.0, quote=_quote(bid=99.0, ask=100.0))
        broker.submit_bracket_order(cancel_intent)
        self.assertTrue(broker.cancel_order(cancel_intent.clientOrderId))
        canceled_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{cancel_intent.clientOrderId}"]
        self.assertEqual(canceled_order["status"], "CANCELED")
        self.assertIn("CANCELED", canceled_order["lifecycleStatuses"])

        expire_intent = _local_intent(order_type="LIMIT", order_intent_id="wv-expire", quote=_quote(bid=100.5, ask=100.5))
        broker.submit_bracket_order(expire_intent)
        self.assertTrue(broker.expire_order(expire_intent.clientOrderId, expired_at=NOW))
        expired_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{expire_intent.clientOrderId}"]
        self.assertEqual(expired_order["status"], "EXPIRED")
        self.assertIn("EXPIRED", expired_order["lifecycleStatuses"])

        replace_intent = _local_intent(order_type="LIMIT", order_intent_id="wv-replace", quote=_quote(bid=100.5, ask=100.5))
        broker.submit_bracket_order(replace_intent)
        self.assertTrue(broker.replace_order(replace_intent.clientOrderId, replacement_client_order_id="wv-replacement.client", replaced_at=NOW))
        replaced_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{replace_intent.clientOrderId}"]
        self.assertEqual(replaced_order["status"], "REPLACED")
        self.assertEqual(replaced_order["replacementClientOrderId"], "wv-replacement.client")
        self.assertIn("REPLACED", replaced_order["lifecycleStatuses"])

    def test_local_fill_engine_uses_nbbo_side_specific_limit_executability(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker = WeightedVotingLocalPaperBroker(store, inventory)

        buy = _local_intent(order_type="LIMIT", order_intent_id="wv-buy-limit", quote=_quote(bid=99.95, ask=100.0))
        broker.submit_bracket_order(buy)
        self.assertEqual(broker.refresh_order(buy.clientOrderId).averageFillPrice, 100.0)

        buy_open = _local_intent(order_type="LIMIT", order_intent_id="wv-buy-limit-open", quote=_quote(bid=100.25, ask=100.25))
        broker.submit_bracket_order(buy_open)
        buy_open_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{buy_open.clientOrderId}"]
        self.assertEqual(buy_open_order["status"], "OPEN")
        self.assertIsNone(broker.refresh_order(buy_open.clientOrderId))

        _seed_long_position(inventory, quantity=10, price=100.0, event_id="seed-long-for-sell-limit")
        sell = _local_intent(order_type="LIMIT", order_intent_id="wv-sell-limit", side=Signal.SELL, quote=_quote(bid=100.0, ask=100.05))
        broker.submit_bracket_order(sell)
        sell_fill = broker.refresh_order(sell.clientOrderId)
        self.assertEqual(sell_fill.side, Signal.SELL)
        self.assertEqual(sell_fill.averageFillPrice, 100.0)

    def test_local_fill_engine_respects_stop_and_stop_limit_triggering(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker = WeightedVotingLocalPaperBroker(store, inventory)

        untriggered_stop = _local_intent(order_type="STOP", order_intent_id="wv-stop-open", stop_price=101.0, quote=_quote(bid=99.95, ask=100.0))
        broker.submit_bracket_order(untriggered_stop)
        untriggered_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{untriggered_stop.clientOrderId}"]
        self.assertEqual(untriggered_order["status"], "OPEN")
        self.assertFalse(untriggered_order["fillEvaluation"]["triggered"])

        triggered_stop = _local_intent(order_type="STOP", order_intent_id="wv-stop-filled", stop_price=99.5, quote=_quote(bid=99.95, ask=100.0))
        broker.submit_bracket_order(triggered_stop)
        self.assertEqual(broker.refresh_order(triggered_stop.clientOrderId).status, "FILLED")

        stop_limit_open = _local_intent(order_type="STOP_LIMIT", order_intent_id="wv-stop-limit-open", stop_price=99.5, limit_price=99.75, quote=_quote(bid=99.95, ask=100.0))
        broker.submit_bracket_order(stop_limit_open)
        stop_limit_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{stop_limit_open.clientOrderId}"]
        self.assertTrue(stop_limit_order["fillEvaluation"]["triggered"])
        self.assertFalse(stop_limit_order["fillEvaluation"]["executable"])
        self.assertEqual(stop_limit_order["status"], "OPEN")

    def test_fill_quality_is_deterministic_and_never_favorable_against_nbbo(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker = WeightedVotingLocalPaperBroker(store, inventory)

        buy_below_ask = _local_intent(order_type="LIMIT", order_intent_id="wv-quality-buy-open", limit_price=99.99, quote=_quote(bid=99.50, ask=100.00))
        broker.submit_bracket_order(buy_below_ask)
        buy_below_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{buy_below_ask.clientOrderId}"]
        self.assertEqual(buy_below_order["status"], "OPEN")
        self.assertFalse(buy_below_order["fillEvaluation"]["executable"])
        self.assertEqual(buy_below_order["fillEvaluation"]["reasonCode"], "weighted_voting.local_paper.order_not_executable_at_market_price")
        self.assertIsNone(broker.refresh_order(buy_below_ask.clientOrderId))

        buy_reaches_ask = _local_intent(order_type="LIMIT", order_intent_id="wv-quality-buy-filled", limit_price=100.00, quote=_quote(bid=99.50, ask=100.00))
        broker.submit_bracket_order(buy_reaches_ask)
        buy_fill = broker.refresh_order(buy_reaches_ask.clientOrderId)
        buy_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{buy_reaches_ask.clientOrderId}"]
        self.assertEqual(buy_order["status"], "FILLED")
        self.assertEqual(buy_order["lifecycleStatuses"], ["PENDING", "ACCEPTED", "OPEN", "FILLED"])
        self.assertEqual(buy_fill.averageFillPrice, 100.00)
        self.assertEqual(buy_order["fillEvaluation"]["reference"]["field"], "ask")
        self.assertEqual(buy_order["fillEvaluation"]["marketReferencePrice"], 100.00)
        self.assertGreaterEqual(buy_fill.averageFillPrice, buy_order["fillEvaluation"]["marketReferencePrice"])

        _seed_long_position(inventory, quantity=20, price=100.0, event_id="seed-long-for-fill-quality-sells")
        sell_above_bid = _local_intent(order_type="LIMIT", order_intent_id="wv-quality-sell-open", side=Signal.SELL, limit_price=100.01, quote=_quote(bid=100.00, ask=100.50))
        broker.submit_bracket_order(sell_above_bid)
        sell_above_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{sell_above_bid.clientOrderId}"]
        self.assertEqual(sell_above_order["status"], "OPEN")
        self.assertFalse(sell_above_order["fillEvaluation"]["executable"])
        self.assertEqual(sell_above_order["fillEvaluation"]["reasonCode"], "weighted_voting.local_paper.order_not_executable_at_market_price")
        self.assertIsNone(broker.refresh_order(sell_above_bid.clientOrderId))

        sell_reaches_bid = _local_intent(order_type="LIMIT", order_intent_id="wv-quality-sell-filled", side=Signal.SELL, limit_price=100.00, quote=_quote(bid=100.00, ask=100.50))
        broker.submit_bracket_order(sell_reaches_bid)
        sell_fill = broker.refresh_order(sell_reaches_bid.clientOrderId)
        sell_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{sell_reaches_bid.clientOrderId}"]
        self.assertEqual(sell_order["status"], "FILLED")
        self.assertEqual(sell_order["lifecycleStatuses"], ["PENDING", "ACCEPTED", "OPEN", "FILLED"])
        self.assertEqual(sell_fill.averageFillPrice, 100.00)
        self.assertEqual(sell_order["fillEvaluation"]["reference"]["field"], "bid")
        self.assertEqual(sell_order["fillEvaluation"]["marketReferencePrice"], 100.00)
        self.assertLessEqual(sell_fill.averageFillPrice, sell_order["fillEvaluation"]["marketReferencePrice"])

        stop_not_triggered = _local_intent(order_type="STOP", order_intent_id="wv-quality-stop-open", stop_price=101.00, quote=_quote(bid=99.95, ask=100.00))
        broker.submit_bracket_order(stop_not_triggered)
        stop_open_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{stop_not_triggered.clientOrderId}"]
        self.assertEqual(stop_open_order["status"], "OPEN")
        self.assertFalse(stop_open_order["fillEvaluation"]["triggered"])
        self.assertFalse(stop_open_order["fillEvaluation"]["executable"])
        self.assertEqual(stop_open_order["fillEvaluation"]["reasonCode"], "weighted_voting.local_paper.stop_not_triggered")
        self.assertIsNone(broker.refresh_order(stop_not_triggered.clientOrderId))

        stop_triggered = _local_intent(order_type="STOP", order_intent_id="wv-quality-stop-filled", stop_price=100.00, quote=_quote(bid=99.95, ask=100.00))
        broker.submit_bracket_order(stop_triggered)
        stop_fill = broker.refresh_order(stop_triggered.clientOrderId)
        stop_filled_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{stop_triggered.clientOrderId}"]
        self.assertEqual(stop_filled_order["status"], "FILLED")
        self.assertTrue(stop_filled_order["fillEvaluation"]["triggered"])
        self.assertTrue(stop_filled_order["fillEvaluation"]["executable"])
        self.assertEqual(stop_filled_order["lifecycleStatuses"], ["PENDING", "ACCEPTED", "OPEN", "FILLED"])
        self.assertEqual(stop_fill.averageFillPrice, 100.00)

        stop_limit_triggered_not_executable = _local_intent(
            order_type="STOP_LIMIT",
            order_intent_id="wv-quality-stop-limit-open",
            stop_price=100.00,
            limit_price=99.99,
            quote=_quote(bid=99.95, ask=100.00),
        )
        broker.submit_bracket_order(stop_limit_triggered_not_executable)
        stop_limit_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{stop_limit_triggered_not_executable.clientOrderId}"]
        self.assertEqual(stop_limit_order["status"], "OPEN")
        self.assertTrue(stop_limit_order["fillEvaluation"]["triggered"])
        self.assertFalse(stop_limit_order["fillEvaluation"]["executable"])
        self.assertEqual(stop_limit_order["fillEvaluation"]["reasonCode"], "weighted_voting.local_paper.order_not_executable_at_market_price")
        self.assertIsNone(broker.refresh_order(stop_limit_triggered_not_executable.clientOrderId))

    def test_local_fill_engine_uses_completed_bar_close_without_lookahead(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker = WeightedVotingLocalPaperBroker(store, inventory)

        completed_bar = _bar(close=99.9, timestamp=NOW - timedelta(minutes=1), bar_end=NOW)
        filled = _local_intent(order_type="LIMIT", order_intent_id="wv-bar-filled", bar=completed_bar)
        broker.submit_bracket_order(filled)
        filled_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{filled.clientOrderId}"]
        self.assertEqual(filled_order["status"], "FILLED")
        self.assertEqual(filled_order["fillEvaluation"]["reference"]["source"], "completed_bar_close")

        future_bar = _bar(close=99.5, timestamp=NOW, bar_end=NOW + timedelta(minutes=1))
        open_order_intent = _local_intent(order_type="LIMIT", order_intent_id="wv-bar-open", bar=future_bar, quote=None)
        broker.submit_bracket_order(open_order_intent)
        open_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{open_order_intent.clientOrderId}"]
        self.assertEqual(open_order["status"], "OPEN")
        self.assertIn("point_in_time_market_data", open_order["fillEvaluation"]["reasonCode"])

    def test_local_fill_engine_prefers_quote_over_completed_bar(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker = WeightedVotingLocalPaperBroker(store, inventory)
        intent = _local_intent(
            order_type="LIMIT",
            order_intent_id="wv-quote-over-bar",
            quote=_quote(bid=100.25, ask=100.25),
            bar=_bar(close=99.5, timestamp=NOW - timedelta(minutes=1), bar_end=NOW),
        )

        broker.submit_bracket_order(intent)

        order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{intent.clientOrderId}"]
        self.assertEqual(order["status"], "OPEN")
        self.assertEqual(order["fillEvaluation"]["reference"]["source"], "quote")

    def test_local_fill_engine_applies_deterministic_slippage_spread_and_fees(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker = WeightedVotingLocalPaperBroker(store, inventory)
        costs = {
            "buySlippagePerShare": 0.02,
            "sellSlippagePerShare": 0.02,
            "commissionPerShare": 0.01,
            "regulatoryFeePerShare": 0.001,
            "spreadImpactPerShare": 0.005,
            "spreadImpactPercent": 0.0001,
        }

        buy = _local_intent(order_type="LIMIT", order_intent_id="wv-buy-costs", quote=_quote(bid=99.95, ask=100.0), execution_costs=costs)
        broker.submit_bracket_order(buy)
        buy_fill = broker.refresh_order(buy.clientOrderId)
        buy_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{buy.clientOrderId}"]

        self.assertEqual(buy_fill.marketReferencePrice, 100.0)
        self.assertAlmostEqual(buy_fill.averageFillPrice, 100.035)
        self.assertAlmostEqual(buy_fill.slippagePerShare, 0.02)
        self.assertAlmostEqual(buy_fill.spreadImpactPerShare, 0.015)
        self.assertAlmostEqual(buy_fill.commission, 0.10)
        self.assertAlmostEqual(buy_fill.regulatoryFees, 0.01)
        self.assertAlmostEqual(buy_fill.totalExecutionCost, 0.46)
        self.assertEqual(buy_order["fillEvaluation"]["executionCosts"]["reasonCode"], "weighted_voting.local_paper.execution_costs_applied")

        _seed_long_position(inventory, quantity=10, price=100.0, event_id="seed-long-for-sell-costs")
        sell = _local_intent(order_type="LIMIT", order_intent_id="wv-sell-costs", side=Signal.SELL, quote=_quote(bid=100.0, ask=100.05), execution_costs=costs)
        broker.submit_bracket_order(sell)
        sell_fill = broker.refresh_order(sell.clientOrderId)
        self.assertEqual(sell_fill.marketReferencePrice, 100.0)
        self.assertAlmostEqual(sell_fill.averageFillPrice, 99.965)
        self.assertAlmostEqual(sell_fill.totalExecutionCost, 0.46)

    def test_local_execution_costs_reduce_weighted_voting_cash_and_realized_pnl_only(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker = WeightedVotingLocalPaperBroker(store, inventory)
        gateway = PaperOrderGateway(
            broker,
            store,
            execution_mode="LOCAL_PAPER",
            account_snapshot_provider=broker.gateway_account_snapshot,
            portfolio_snapshot_provider=broker.gateway_portfolio_snapshot,
        )
        costs = {
            "buySlippagePerShare": 0.02,
            "commissionPerShare": 0.01,
            "regulatoryFeePerShare": 0.001,
            "spreadImpactPerShare": 0.005,
            "spreadImpactPercent": 0.0001,
        }
        proposal = _proposal_with_quote(global_proposal(quantity=10, order_intent_id="wv-costed-inventory"), execution_costs=costs)
        item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            enqueued_at=NOW,
            idempotency_key="weighted_voting.local_paper.costed-inventory",
        )

        result = submit_queued_weighted_voting_paper_order(
            gateway=gateway,
            queue_item=item,
            inventory_repository=inventory,
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )

        snapshot = inventory.current_snapshot(now=NOW)
        self.assertTrue(result.submitted)
        self.assertAlmostEqual(result.fill.averageFillPrice, 100.035)
        self.assertAlmostEqual(result.fill.totalExecutionCost, 0.46)
        self.assertAlmostEqual(snapshot.realised_pnl, -0.46)
        self.assertAlmostEqual(snapshot.daily_realised_pnl, -0.46)
        self.assertAlmostEqual(snapshot.cash_available, 23_999.19)
        self.assertFalse(any(key.startswith(("voting_ensemble.", "wca.", "meta_strategy.", "regime_based.")) for key in store.snapshots))

    def test_automatic_buy_order_reserves_cash_and_risk_before_activation_without_double_spend(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store, allocated_capital=10_000.0)
        broker = WeightedVotingLocalPaperBroker(store, inventory)
        gateway = PaperOrderGateway(
            broker,
            store,
            execution_mode="LOCAL_PAPER",
            account_snapshot_provider=broker.gateway_account_snapshot,
            portfolio_snapshot_provider=broker.gateway_portfolio_snapshot,
        )
        proposal_a = _proposal_with_quote(
            global_proposal(quantity=60, order_intent_id="wv-reserve-a").model_copy(update={"plannedRiskDollars": 600.0}),
            quote=_quote(bid=100.5, ask=100.5),
        )
        proposal_b = _proposal_with_quote(
            global_proposal(quantity=50, order_intent_id="wv-reserve-b").model_copy(update={"plannedRiskDollars": 500.0}),
            quote=_quote(bid=100.0, ask=100.0),
        )
        item_a = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=proposal_a,
            global_application=global_application(proposal_a),
            local_gate_result=local_gate(True),
            enqueued_at=NOW,
            idempotency_key="weighted_voting.local_paper.reserve-a",
        )
        item_b = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=proposal_b,
            global_application=global_application(proposal_b),
            local_gate_result=local_gate(True),
            enqueued_at=NOW,
            idempotency_key="weighted_voting.local_paper.reserve-b",
        )

        result_a = submit_queued_weighted_voting_paper_order(
            gateway=gateway,
            queue_item=item_a,
            inventory_repository=inventory,
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        after_a = inventory.current_snapshot(now=NOW)
        result_b = submit_queued_weighted_voting_paper_order(
            gateway=gateway,
            queue_item=item_b,
            inventory_repository=inventory,
            evaluated_at=NOW + timedelta(seconds=1),
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        after_b = inventory.current_snapshot(now=NOW)

        self.assertTrue(result_a.submitted)
        self.assertEqual(result_a.status, "ACCEPTED")
        self.assertEqual(len(after_a.pending_orders), 1)
        self.assertEqual(after_a.reserved_buying_power, 6_000.0)
        self.assertEqual(after_a.cash_available, 10_000.0)
        self.assertEqual(after_a.available_cash, 4_000.0)
        self.assertEqual(after_a.available_cash, after_a.cash - after_a.reserved_cash)
        self.assertEqual(after_a.remaining_capital_partition, 4_000.0)
        self.assertEqual(after_a.daily_risk_used, 600.0)
        self.assertEqual(after_a.remaining_daily_risk, 9_400.0)
        self.assertFalse(result_b.submitted)
        self.assertIn("weighted_voting.execution.stale_inventory_version", result_b.reasonCodes)
        self.assertEqual(after_b.reserved_buying_power, 6_000.0)
        self.assertEqual(after_b.cash_available, 10_000.0)
        self.assertEqual(after_b.available_cash, 4_000.0)
        self.assertEqual(after_b.pending_orders[0].client_order_id, item_a.command.client_order_id)

    def test_risk_reservation_conflict_fails_closed_without_second_order(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store, allocated_capital=10_000.0)
        broker = WeightedVotingLocalPaperBroker(store, inventory)
        gateway = PaperOrderGateway(
            broker,
            store,
            execution_mode="LOCAL_PAPER",
            account_snapshot_provider=broker.gateway_account_snapshot,
            portfolio_snapshot_provider=broker.gateway_portfolio_snapshot,
        )
        first = _proposal_with_quote(global_proposal(quantity=60, order_intent_id="wv-conflict-a"), quote=_quote(bid=100.5, ask=100.5))
        first_item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=first,
            global_application=global_application(first),
            local_gate_result=local_gate(True),
            enqueued_at=NOW,
            idempotency_key="weighted_voting.local_paper.conflict-a",
        )
        first_result = submit_queued_weighted_voting_paper_order(
            gateway=gateway,
            queue_item=first_item,
            inventory_repository=inventory,
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        after_first = inventory.current_snapshot(now=NOW)
        second = _proposal_with_quote(global_proposal(quantity=50, order_intent_id="wv-conflict-b"), quote=_quote(bid=100.5, ask=100.5))
        second_item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=second,
            global_application=global_application(second),
            local_gate_result=local_gate(True),
            enqueued_at=NOW + timedelta(seconds=1),
            idempotency_key="weighted_voting.local_paper.conflict-b",
            inventory_snapshot_version=after_first.snapshot_version,
        )

        second_result = submit_queued_weighted_voting_paper_order(
            gateway=gateway,
            queue_item=second_item,
            inventory_repository=inventory,
            evaluated_at=NOW + timedelta(seconds=1),
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        after_second = inventory.current_snapshot(now=NOW + timedelta(seconds=1))

        self.assertTrue(first_result.submitted)
        self.assertFalse(second_result.submitted)
        self.assertEqual(second_result.status, "NOT_SUBMITTED")
        self.assertIn("weighted_voting.execution.inventory_reservation_failed", second_result.reasonCodes)
        self.assertIn("weighted_voting.execution.insufficient_local_buying_power", second_result.reasonCodes)
        self.assertEqual(after_second.snapshot_version, after_first.snapshot_version)
        self.assertEqual(after_second.reserved_buying_power, 6_000.0)
        self.assertEqual(len(after_second.pending_orders), 1)
        self.assertNotIn(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{second_item.command.client_order_id}", store.snapshots)

    def test_duplicate_automatic_order_is_idempotent_and_does_not_reserve_twice(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store, allocated_capital=10_000.0)
        broker = WeightedVotingLocalPaperBroker(store, inventory)
        gateway = PaperOrderGateway(
            broker,
            store,
            execution_mode="LOCAL_PAPER",
            account_snapshot_provider=broker.gateway_account_snapshot,
            portfolio_snapshot_provider=broker.gateway_portfolio_snapshot,
        )
        proposal = _proposal_with_quote(global_proposal(quantity=10, order_intent_id="wv-duplicate-local"))
        item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            enqueued_at=NOW,
            idempotency_key="weighted_voting.local_paper.duplicate-local",
        )

        first = submit_queued_weighted_voting_paper_order(
            gateway=gateway,
            queue_item=item,
            inventory_repository=inventory,
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        first_snapshot = inventory.current_snapshot(now=NOW)
        second = submit_queued_weighted_voting_paper_order(
            gateway=gateway,
            queue_item=item,
            inventory_repository=inventory,
            evaluated_at=NOW + timedelta(seconds=1),
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        second_snapshot = inventory.current_snapshot(now=NOW + timedelta(seconds=1))
        reserve_events = [
            value
            for key, value in store.snapshots.items()
            if key.startswith("weighted_voting.inventory.events.") and isinstance(value, dict) and value.get("event_type") == WeightedVotingInventoryEventType.ORDER_RESERVED.value
        ]

        self.assertTrue(first.submitted)
        self.assertEqual(first.clientOrderId, second.clientOrderId)
        self.assertEqual(first_snapshot.as_dict(), second_snapshot.as_dict())
        self.assertEqual(len(reserve_events), 1)
        self.assertTrue(any(key.endswith(".RECONCILED") for key in store.snapshots if key.startswith("weighted_voting.execution_gateway.lifecycle.")))

    def test_local_broker_rejects_market_and_unsupported_order_types(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker = WeightedVotingLocalPaperBroker(store, inventory)

        market = _local_intent(order_type="MARKET", order_intent_id="wv-market")
        market_ack = broker.submit_bracket_order(market)
        market_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{market.clientOrderId}"]
        self.assertEqual(market_ack.status, "REJECTED")
        self.assertEqual(market_ack.rejectedReason, "weighted_voting.local_paper.market_orders_disabled")
        self.assertEqual(market_order["status"], "REJECTED")
        self.assertEqual(market_order["filledQuantity"], 0)

        unsupported = _local_intent(order_type="PEGGED", order_intent_id="wv-unsupported")
        unsupported_ack = broker.submit_bracket_order(unsupported)
        unsupported_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{unsupported.clientOrderId}"]
        self.assertEqual(unsupported_ack.status, "REJECTED")
        self.assertEqual(unsupported_ack.rejectedReason, "weighted_voting.local_paper.unsupported_order_type_rejected")
        self.assertEqual(unsupported_order["status"], "REJECTED")

    def test_local_broker_rejects_sell_that_would_open_short_inventory(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker = WeightedVotingLocalPaperBroker(store, inventory)

        pure_short = _local_intent(order_type="LIMIT", order_intent_id="wv-open-short", side=Signal.SELL, quote=_quote(bid=100.0, ask=100.05))
        pure_short_ack = broker.submit_bracket_order(pure_short)
        pure_short_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{pure_short.clientOrderId}"]

        self.assertEqual(pure_short_ack.status, "REJECTED")
        self.assertEqual(pure_short_ack.rejectedReason, "weighted_voting.local_paper.open_short_not_supported")
        self.assertEqual(pure_short_order["status"], "REJECTED")
        self.assertEqual(pure_short_order["filledQuantity"], 0)
        self.assertIsNone(broker.refresh_order(pure_short.clientOrderId))

        _seed_long_position(inventory, quantity=5, price=100.0, event_id="seed-long-for-oversell")
        oversell = _local_intent(order_type="LIMIT", order_intent_id="wv-oversell-short", side=Signal.SELL, quote=_quote(bid=100.0, ask=100.05))
        oversell_ack = broker.submit_bracket_order(oversell)
        oversell_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{oversell.clientOrderId}"]

        self.assertEqual(oversell_ack.status, "REJECTED")
        self.assertEqual(oversell_ack.rejectedReason, "weighted_voting.local_paper.open_short_not_supported")
        self.assertEqual(oversell_order["filledQuantity"], 0)

    def test_automatic_sell_that_would_open_short_is_rejected_before_reservation(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store, allocated_capital=10_000.0)
        broker = WeightedVotingLocalPaperBroker(store, inventory)
        gateway = PaperOrderGateway(
            broker,
            store,
            execution_mode="LOCAL_PAPER",
            account_snapshot_provider=broker.gateway_account_snapshot,
            portfolio_snapshot_provider=broker.gateway_portfolio_snapshot,
        )
        proposal = _proposal_with_quote(global_proposal(quantity=10, order_intent_id="wv-automatic-open-short").model_copy(update={"side": "SELL"}))
        item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            enqueued_at=NOW,
            idempotency_key="weighted_voting.local_paper.open-short",
        )

        result = submit_queued_weighted_voting_paper_order(
            gateway=gateway,
            queue_item=item,
            inventory_repository=inventory,
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        snapshot = inventory.current_snapshot(now=NOW)

        self.assertFalse(result.submitted)
        self.assertEqual(result.status, "NOT_SUBMITTED")
        self.assertIn("weighted_voting.execution.open_short_not_supported", result.reasonCodes)
        self.assertEqual(snapshot.reserved_buying_power, 0.0)
        self.assertEqual(snapshot.pending_orders, ())
        self.assertFalse(any(key.startswith(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.") for key in store.snapshots))

    def test_automatic_market_order_remains_rejected_before_local_submission(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker = WeightedVotingLocalPaperBroker(store, inventory)
        gateway = PaperOrderGateway(
            broker,
            store,
            execution_mode="LOCAL_PAPER",
            account_snapshot_provider=broker.gateway_account_snapshot,
            portfolio_snapshot_provider=broker.gateway_portfolio_snapshot,
        )
        proposal = global_proposal(order_intent_id="wv-market-intent").model_copy(update={"entryFormula": {"kind": "market"}})
        item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            enqueued_at=NOW,
            idempotency_key="weighted_voting.local_paper.market",
        )

        result = submit_queued_weighted_voting_paper_order(
            gateway=gateway,
            queue_item=item,
            inventory_repository=inventory,
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )

        self.assertFalse(result.submitted)
        self.assertEqual(result.status, "NOT_SUBMITTED")
        self.assertIn("weighted_voting.execution.market_entry_rejected", result.reasonCodes)
        self.assertFalse(any(key.startswith(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.") for key in store.snapshots))

    def test_automatic_local_paper_trading_works_without_alpaca_credentials_or_network(self) -> None:
        store = MemoryStore()
        config = WeightedVotingConfig(local_paper_initial_capital=100_000.0)
        alpaca_guard = AssertionError("Alpaca must not be called in LOCAL_PAPER")
        with patch.dict("os.environ", {}, clear=True), patch("httpx.Client", side_effect=alpaca_guard), patch(
            "httpx.AsyncClient", side_effect=alpaca_guard
        ), patch("socket.create_connection", side_effect=alpaca_guard), patch(
            "backend.app.algorithms.weighted_voting.alpaca_paper_broker.build_weighted_voting_paper_gateway_dependencies",
            side_effect=alpaca_guard,
        ), patch(
            "backend.app.algorithms.weighted_voting.runtime_supervisor._now",
            return_value=NOW,
        ):
            supervisor = WeightedVotingRuntimeSupervisor(
                store=store,
                service=NoAlpacaAutomaticLocalPaperService(store=store),
                weighted_config=config,
                config=WeightedVotingRuntimeConfig(heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
                event_bus=WeightedVotingEventBus(maxsize=8),
                rollout_flags=validated_rollout_flags(),
                rollout_validation=validated_rollout_validation(),
            )
            _enable_local_automatic_entries(supervisor)

            decision_record = asyncio.run(supervisor.process_finalised_bar_event(_finalized_event(close=100.0)))
            queue_item = supervisor.execution_queue.get_nowait()
            execution_record = supervisor.process_execution_queue_item(queue_item)
            entry_snapshot = supervisor.inventory_repository.current_snapshot(now=NOW)
            entry_at = queue_item.command.created_at
            marked_snapshot = supervisor.inventory_repository.mark_to_market(
                symbol="SPY",
                price=101.0,
                occurred_at=entry_at + timedelta(minutes=1),
                market_event_id="no-alpaca-local-paper-mark",
            )
            exit_fills = supervisor.paper_gateway.broker.process_market_data(
                symbol="SPY",
                market_data=_quote(bid=102.05, ask=102.10, timestamp=entry_at + timedelta(minutes=2)),
                observed_at=entry_at + timedelta(minutes=2),
            )
            exit_snapshot = supervisor.inventory_repository.current_snapshot(now=entry_at + timedelta(minutes=2))

        inventory_events = [
            value["event_type"]
            for key, value in store.snapshots.items()
            if key.startswith("weighted_voting.inventory.events.") and isinstance(value, dict) and value.get("event_type")
        ]
        order_records = [value for key, value in store.snapshots.items() if key.startswith(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.")]

        self.assertEqual(decision_record["status"], "decision_persisted")
        self.assertTrue(any(key.startswith("weighted_voting.execution_gateway.decision_command.") for key in store.snapshots))
        self.assertEqual(queue_item.command.as_shared_broker_command()["executionMode"], "LOCAL_PAPER")
        self.assertEqual(queue_item.command.quantity, 10)
        self.assertEqual(execution_record["status"], "submitted")
        self.assertEqual(execution_record["result"]["executionMode"], "LOCAL_PAPER")
        self.assertEqual(execution_record["result"]["status"], "FILLED")
        self.assertIn(WeightedVotingInventoryEventType.ORDER_RESERVED.value, inventory_events)
        self.assertIn(WeightedVotingInventoryEventType.FILL_RECORDED.value, inventory_events)
        self.assertLess(inventory_events.index(WeightedVotingInventoryEventType.ORDER_RESERVED.value), inventory_events.index(WeightedVotingInventoryEventType.FILL_RECORDED.value))
        self.assertTrue(order_records)
        self.assertEqual(order_records[0]["executionMode"], "LOCAL_PAPER")
        self.assertEqual(entry_snapshot.initial_capital, 100_000.0)
        self.assertEqual(entry_snapshot.position_quantity, 10)
        self.assertEqual(entry_snapshot.average_entry_price, 100.0)
        self.assertEqual(entry_snapshot.reserved_cash, 0.0)
        self.assertGreater(marked_snapshot.unrealized_pnl, 0.0)
        self.assertEqual(marked_snapshot.equity, 100_010.0)
        self.assertEqual(len(exit_fills), 1)
        self.assertEqual(exit_snapshot.position_quantity, 0)
        self.assertGreater(exit_snapshot.realized_pnl, 0.0)
        self.assertEqual(exit_snapshot.unrealized_pnl, 0.0)
        self.assertGreater(exit_snapshot.equity, 100_000.0)
        self.assertEqual(exit_snapshot.cash, exit_snapshot.equity)
        self.assertFalse(any(key.startswith("paper_order_gateway.") for key in store.snapshots))
        self.assertFalse(any(key.lower().startswith("alpaca.") for key in store.snapshots))
        self.assertFalse(any("alpaca_paper" in str(value).lower() for value in store.snapshots.values()))

    def test_deterministic_local_paper_e2e_uses_weighted_inventory_as_next_risk_basis(self) -> None:
        store = MemoryStore()
        _seed_foreign_algorithm_inventory_records(store)
        foreign_before = _foreign_algorithm_records(store)
        voting_ensemble_before = _voting_ensemble_metrics(store)
        service = LocalInventorySizingAcceptanceService(store=store)
        config = WeightedVotingConfig(local_paper_initial_capital=100_000.0)
        alpaca_guard = AssertionError("Alpaca must not be called in LOCAL_PAPER")
        with patch.dict("os.environ", {}, clear=True), patch("httpx.Client", side_effect=alpaca_guard), patch(
            "httpx.AsyncClient", side_effect=alpaca_guard
        ), patch("socket.create_connection", side_effect=alpaca_guard), patch(
            "backend.app.algorithms.weighted_voting.alpaca_paper_broker.build_weighted_voting_paper_gateway_dependencies",
            side_effect=alpaca_guard,
        ), patch(
            "backend.app.algorithms.weighted_voting.runtime_supervisor._now",
            return_value=NOW,
        ):
            supervisor = WeightedVotingRuntimeSupervisor(
                store=store,
                service=service,
                weighted_config=config,
                config=WeightedVotingRuntimeConfig(heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
                event_bus=WeightedVotingEventBus(maxsize=8),
                rollout_flags=validated_rollout_flags(),
                rollout_validation=validated_rollout_validation(),
            )
            _enable_local_automatic_entries(supervisor)

            initial_snapshot = supervisor.inventory_repository.current_snapshot(now=NOW)
            decision_record = asyncio.run(supervisor.process_finalised_bar_event(_finalized_event(close=100.0)))
            queue_item = supervisor.execution_queue.get_nowait()
            execution_record = supervisor.process_execution_queue_item(queue_item)
            accepted_snapshot = supervisor.inventory_repository.current_snapshot(now=NOW)
            open_order = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{queue_item.command.client_order_id}"]

            entry_fills = supervisor.paper_gateway.broker.process_market_data(
                symbol="SPY",
                market_data=_quote(bid=99.95, ask=100.0, timestamp=NOW + timedelta(seconds=30)),
                observed_at=NOW + timedelta(seconds=30),
            )
            entry_snapshot = supervisor.inventory_repository.current_snapshot(now=NOW + timedelta(seconds=30))
            entry_position = entry_snapshot.open_positions[0]
            marked_snapshot = supervisor.inventory_repository.mark_to_market(
                symbol="SPY",
                price=101.0,
                occurred_at=NOW + timedelta(minutes=1),
                market_event_id="weighted-voting-e2e-spy-101",
            )
            exit_fills = supervisor.paper_gateway.broker.process_market_data(
                symbol="SPY",
                market_data=_quote(bid=102.05, ask=102.10, timestamp=NOW + timedelta(minutes=2)),
                observed_at=NOW + timedelta(minutes=2),
            )
            exit_snapshot = supervisor.inventory_repository.current_snapshot(now=NOW + timedelta(minutes=2))
            next_event = _finalized_event(close=100.0, observed_at=NOW + timedelta(minutes=4), source_sequence=2, source="weighted_voting.local_paper.e2e_next")
            next_market_snapshot = build_weighted_voting_market_snapshot(next_event.market_payload)
            next_context = supervisor.build_runtime_context_from_finalised_bar(
                snapshot=next_market_snapshot,
                active_weight_state=service.active_weight_state(),
                effective_settings=supervisor._active_effective_settings(),
                market_condition=classify_market_condition(next_market_snapshot, config=supervisor.weighted_config),
                observed_at=next_market_snapshot.data_timestamp,
                session_evidence=supervisor._authoritative_session_evidence(next_market_snapshot.data_timestamp),
            )
            service.evaluate_context(next_context)

        first_sizing, next_sizing = service.sizing_observations
        foreign_after = _foreign_algorithm_records(store)
        voting_ensemble_after = _voting_ensemble_metrics(store)
        protective_stop = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.{queue_item.command.client_order_id}-stop"]
        protective_target = store.snapshots[f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.{queue_item.command.client_order_id}-target"]

        self.assertEqual(initial_snapshot.initial_capital, 100_000.0)
        self.assertEqual(initial_snapshot.cash, 100_000.0)
        self.assertIsInstance(supervisor.paper_gateway.broker, WeightedVotingLocalPaperBroker)
        self.assertIs(supervisor.account_port, supervisor.paper_gateway.broker)
        self.assertEqual(decision_record["status"], "decision_persisted")
        self.assertEqual(first_sizing["source"], "weighted_voting.local_inventory")
        self.assertEqual(first_sizing["equity"], 100_000.0)
        self.assertEqual(first_sizing["cash"], 100_000.0)
        self.assertEqual(first_sizing["quantity"], 1000)
        self.assertEqual(queue_item.command.quantity, 1000)
        self.assertEqual(queue_item.command.as_shared_broker_command()["executionMode"], "LOCAL_PAPER")
        self.assertEqual(execution_record["status"], "submitted")
        self.assertEqual(execution_record["result"]["status"], "ACCEPTED")
        self.assertEqual(open_order["status"], "OPEN")
        self.assertEqual(accepted_snapshot.position_quantity, 0)
        self.assertEqual(accepted_snapshot.reserved_cash, 100_000.0)

        self.assertEqual(len(entry_fills), 1)
        self.assertEqual(entry_fills[0].filledQuantity, 1000)
        self.assertEqual(entry_fills[0].averageFillPrice, 100.0)
        self.assertEqual(entry_snapshot.position_quantity, 1000)
        self.assertEqual(entry_snapshot.average_entry_price, 100.0)
        self.assertEqual(entry_position.algorithm_id, "weighted_voting")
        self.assertEqual(entry_position.symbol, "SPY")
        self.assertEqual(entry_snapshot.cash, 0.0)
        self.assertEqual(entry_snapshot.reserved_cash, 0.0)
        self.assertLess(entry_snapshot.cash, initial_snapshot.cash)
        self.assertEqual(protective_stop["status"], "CANCELED")
        self.assertEqual(protective_target["status"], "FILLED")

        self.assertEqual(marked_snapshot.unrealized_pnl, 1_000.0)
        self.assertEqual(marked_snapshot.equity, 101_000.0)
        self.assertEqual(len(exit_fills), 1)
        self.assertEqual(exit_fills[0].side, Signal.SELL)
        self.assertEqual(exit_fills[0].filledQuantity, 1000)
        self.assertEqual(exit_snapshot.position_quantity, 0)
        self.assertEqual(exit_snapshot.open_positions, ())
        self.assertEqual(exit_snapshot.realized_pnl, 2_050.0)
        self.assertEqual(exit_snapshot.unrealized_pnl, 0.0)
        self.assertEqual(exit_snapshot.cash, 102_050.0)
        self.assertEqual(exit_snapshot.equity, 102_050.0)
        self.assertEqual(next_sizing["source"], "weighted_voting.local_inventory")
        self.assertEqual(next_sizing["equity"], exit_snapshot.equity)
        self.assertEqual(next_sizing["cash"], exit_snapshot.cash)
        self.assertEqual(next_sizing["buying_power"], exit_snapshot.buying_power)
        self.assertEqual(next_sizing["quantity"], 1020)
        self.assertGreater(next_sizing["equity"], first_sizing["equity"])
        self.assertEqual(voting_ensemble_before, {"cash": 88_888.0, "position": 7, "pnl": 321.45})
        self.assertEqual(voting_ensemble_after, voting_ensemble_before)
        self.assertEqual(foreign_after, foreign_before)
        self.assertTrue(any(key.startswith("wca.") for key in foreign_after))
        self.assertTrue(any(key.startswith("regime.") for key in foreign_after))
        self.assertTrue(any(key.startswith("meta_strategy.") for key in foreign_after))
        self.assertFalse(any(key.lower().startswith("alpaca.") for key in store.snapshots))
        self.assertFalse(any("alpaca_paper" in str(value).lower() for value in store.snapshots.values()))
        self.assertFalse(any(key.startswith("paper_order_gateway.") for key in store.snapshots))

    def test_local_risk_service_caps_from_weighted_inventory_only(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store, allocated_capital=500.0)
        service = WeightedVotingLocalPaperRiskService(inventory)
        proposal = global_proposal(quantity=10)
        request = _risk_request(proposal)

        response = service.evaluate(request)

        self.assertEqual(response.algorithm_id, "weighted_voting")
        self.assertLessEqual(response.maximum_quantity, 5)
        self.assertIn(response.action, {"ALLOW", "REDUCE", "REJECT"})
        self.assertIn("weighted_voting.local_paper", response.reason_codes[0])

    def test_local_risk_service_rejects_insufficient_local_buying_power(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store, allocated_capital=50.0)
        service = WeightedVotingLocalPaperRiskService(inventory)
        proposal = global_proposal(quantity=10)

        response = service.evaluate(_risk_request(proposal))

        self.assertEqual(response.action, "REJECT")
        self.assertEqual(response.maximum_quantity, 0)
        self.assertEqual(response.maximum_additional_risk, 0.0)
        self.assertIn("weighted_voting.local_paper.local_risk_rejected", response.reason_codes)

    def test_global_risk_rejection_and_reduction_are_fail_closed_or_one_way(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store, allocated_capital=10_000.0)
        broker = WeightedVotingLocalPaperBroker(store, inventory)
        gateway = PaperOrderGateway(
            broker,
            store,
            execution_mode="LOCAL_PAPER",
            account_snapshot_provider=broker.gateway_account_snapshot,
            portfolio_snapshot_provider=broker.gateway_portfolio_snapshot,
        )
        rejected = _proposal_with_quote(global_proposal(quantity=10, order_intent_id="wv-global-reject"))
        rejected_item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=rejected,
            global_application=global_application(rejected, action="REJECT_NEW_ENTRY"),
            local_gate_result=local_gate(True),
            enqueued_at=NOW,
            idempotency_key="weighted_voting.local_paper.global-reject",
        )
        reduced = _proposal_with_quote(global_proposal(quantity=10, order_intent_id="wv-global-reduce"))
        reduced_application = apply_global_gate_response(
            reduced,
            GlobalGateResponse(
                action="REDUCE_QUANTITY",
                maximumAllowedQuantity=4,
                maximumAdditionalRiskDollars=40.0,
                evaluatedAt=NOW,
                configurationHash="weighted_voting.local_paper.global_reduce",
            ),
        )
        reduced_item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=reduced,
            global_application=reduced_application,
            local_gate_result=local_gate(True),
            enqueued_at=NOW,
            idempotency_key="weighted_voting.local_paper.global-reduce",
        )

        reduced_result = submit_queued_weighted_voting_paper_order(
            gateway=gateway,
            queue_item=reduced_item,
            inventory_repository=inventory,
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        snapshot = inventory.current_snapshot(now=NOW)

        self.assertIsNone(rejected_item)
        self.assertFalse(any(key.startswith(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.") and "wv-global-reject" in str(value) for key, value in store.snapshots.items()))
        self.assertEqual(reduced_item.command.quantity, 4)
        self.assertTrue(reduced_result.submitted)
        self.assertEqual(snapshot.position_quantity, 4)
        self.assertLessEqual(reduced_item.command.quantity, reduced.quantity)

    def test_execution_status_declares_local_paper_not_shared_broker(self) -> None:
        status = execution_gateway_status()

        self.assertEqual(status["executionMode"], "LOCAL_PAPER")
        self.assertEqual(status["sharedServices"], [])
        self.assertEqual(status["brokerConnectionBoundary"], "weighted_voting_local_paper_broker")


class WeightedVotingLocalPaperAccountingTest(unittest.TestCase):
    def test_initial_local_account_uses_dedicated_one_hundred_thousand_capital(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store, allocated_capital=100_000.0)

        snapshot = inventory.current_snapshot(now=NOW)

        self.assertEqual(snapshot.initial_capital, 100_000.0)
        self.assertEqual(snapshot.cash, 100_000.0)
        self.assertEqual(snapshot.equity, 100_000.0)
        self.assertEqual(snapshot.position_quantity, 0)
        self.assertEqual(snapshot.reserved_cash, 0.0)
        self.assertEqual(snapshot.realized_pnl, 0.0)
        self.assertEqual(snapshot.unrealized_pnl, 0.0)

    def test_buy_mark_to_market_and_full_profitable_exit_accounting_before_costs(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store, allocated_capital=100_000.0)

        _reserve_order(inventory, event_id="buy-100-reserve", client_order_id="buy-100", quantity=100, price=600.0)
        after_buy = _record_fill(inventory, event_id="buy-100-fill", fill_id="buy-100-fill", client_order_id="buy-100", quantity=100, price=600.0)

        self.assertEqual(after_buy.position_quantity, 100)
        self.assertEqual(after_buy.average_entry_price, 600.0)
        self.assertEqual(after_buy.cash, 40_000.0)
        self.assertEqual(after_buy.reserved_cash, 0.0)
        self.assertEqual(after_buy.pending_orders, ())
        self.assertEqual(after_buy.equity, 100_000.0)

        marked = inventory.mark_to_market(symbol="SPY", price=603.0, occurred_at=NOW + timedelta(minutes=1), market_event_id="spy-603")

        self.assertEqual(marked.unrealized_pnl, 300.0)
        self.assertEqual(marked.equity, 100_300.0)

        after_exit = _record_fill(
            inventory,
            event_id="sell-100-fill",
            fill_id="sell-100-fill",
            client_order_id="sell-100",
            quantity=-100,
            price=603.0,
            occurred_at=NOW + timedelta(minutes=2),
        )

        self.assertEqual(after_exit.position_quantity, 0)
        self.assertEqual(after_exit.open_positions, ())
        self.assertEqual(after_exit.realized_pnl, 300.0)
        self.assertEqual(after_exit.unrealized_pnl, 0.0)
        self.assertEqual(after_exit.equity, 100_300.0)
        self.assertEqual(after_exit.cash, 100_300.0)

    def test_weighted_voting_buy_and_sell_do_not_mutate_other_algorithm_inventories(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store, allocated_capital=100_000.0)
        _seed_foreign_algorithm_inventory_records(store)
        foreign_before = _foreign_algorithm_records(store)
        voting_ensemble_before = _voting_ensemble_metrics(store)

        _reserve_order(inventory, event_id="isolated-buy-reserve", client_order_id="isolated-buy", quantity=100, price=600.0)
        _record_fill(inventory, event_id="isolated-buy-fill", fill_id="isolated-buy-fill", client_order_id="isolated-buy", quantity=100, price=600.0)
        _record_fill(
            inventory,
            event_id="isolated-sell-fill",
            fill_id="isolated-sell-fill",
            client_order_id="isolated-sell",
            quantity=-100,
            price=603.0,
            occurred_at=NOW + timedelta(minutes=1),
        )

        foreign_after = _foreign_algorithm_records(store)
        voting_ensemble_after = _voting_ensemble_metrics(store)

        self.assertEqual(voting_ensemble_before, {"cash": 88_888.0, "position": 7, "pnl": 321.45})
        self.assertEqual(voting_ensemble_after, voting_ensemble_before)
        self.assertEqual(foreign_after, foreign_before)
        self.assertFalse(any(key.startswith("weighted_voting.") for key in foreign_after))
        self.assertTrue(any(key.startswith("weighted_voting.inventory.") for key in store.snapshots))
        self.assertEqual(inventory.current_snapshot(now=NOW + timedelta(minutes=1)).realized_pnl, 300.0)

    def test_weighted_voting_local_paper_reset_does_not_mutate_sibling_algorithm_state(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store, allocated_capital=100_000.0)
        broker = WeightedVotingLocalPaperBroker(store, inventory)
        _seed_foreign_algorithm_inventory_records(store)
        foreign_before = _foreign_algorithm_records(store)

        broker.submit_bracket_order(_local_intent(order_type="LIMIT", order_intent_id="wv-reset-archived-order"))
        _reserve_order(inventory, event_id="reset-buy-reserve", client_order_id="reset-buy", quantity=100, price=600.0)
        _record_fill(inventory, event_id="reset-buy-fill", fill_id="reset-buy-fill", client_order_id="reset-buy", quantity=100, price=600.0)
        _record_fill(
            inventory,
            event_id="reset-sell-fill",
            fill_id="reset-sell-fill",
            client_order_id="reset-sell",
            quantity=-40,
            price=603.0,
            occurred_at=NOW + timedelta(minutes=1),
        )

        reset = broker.reset_local_paper_account(reset_at=NOW + timedelta(minutes=2), reason="weighted_voting.local_paper.test_reset")
        snapshot = inventory.current_snapshot(now=NOW + timedelta(minutes=2))
        archived_fill = broker.refresh_order("wv-reset-archived-order.client")

        self.assertEqual(_foreign_algorithm_records(store), foreign_before)
        self.assertEqual(snapshot.cash, 100_000.0)
        self.assertEqual(snapshot.equity, 100_000.0)
        self.assertEqual(snapshot.open_positions, ())
        self.assertEqual(snapshot.pending_orders, ())
        self.assertEqual(snapshot.realized_pnl, 0.0)
        self.assertEqual(snapshot.unrealized_pnl, 0.0)
        self.assertEqual(snapshot.daily_loss, 0.0)
        self.assertEqual(snapshot.daily_trade_count, 0)
        self.assertEqual(reset["algorithmId"], "weighted_voting")
        self.assertFalse(reset["siblingAlgorithmMutationAllowed"])
        self.assertGreater(reset["archivedWeightedVotingLocalPaperRecords"]["orders"], 0)
        self.assertGreater(reset["archivedWeightedVotingLocalPaperRecords"]["fills"], 0)
        self.assertIsNone(archived_fill)

    def test_losing_trade_declines_cash_and_equity_before_costs(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store, allocated_capital=100_000.0)

        _reserve_order(inventory, event_id="loss-buy-reserve", client_order_id="loss-buy", quantity=100, price=600.0)
        _record_fill(inventory, event_id="loss-buy-fill", fill_id="loss-buy-fill", client_order_id="loss-buy", quantity=100, price=600.0)
        after_loss = _record_fill(
            inventory,
            event_id="loss-sell-fill",
            fill_id="loss-sell-fill",
            client_order_id="loss-sell",
            quantity=-100,
            price=597.0,
            occurred_at=NOW + timedelta(minutes=1),
        )

        self.assertEqual(after_loss.position_quantity, 0)
        self.assertEqual(after_loss.realized_pnl, -300.0)
        self.assertEqual(after_loss.unrealized_pnl, 0.0)
        self.assertEqual(after_loss.cash, 99_700.0)
        self.assertEqual(after_loss.equity, 99_700.0)

    def test_partial_entry_fill_adjusts_remaining_order_and_reservation(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store, allocated_capital=100_000.0)

        _reserve_order(inventory, event_id="partial-buy-reserve", client_order_id="partial-buy", quantity=100, price=600.0)
        snapshot = _record_fill(inventory, event_id="partial-buy-fill", fill_id="partial-buy-fill", client_order_id="partial-buy", quantity=40, price=600.0)

        self.assertEqual(snapshot.position_quantity, 40)
        self.assertEqual(snapshot.average_entry_price, 600.0)
        self.assertEqual(snapshot.cash, 76_000.0)
        self.assertEqual(snapshot.reserved_cash, 36_000.0)
        self.assertEqual(snapshot.available_cash, 40_000.0)
        self.assertEqual(len(snapshot.pending_orders), 1)
        self.assertEqual(snapshot.pending_orders[0].filled_quantity, 40)
        self.assertEqual(snapshot.pending_orders[0].remaining_quantity, 60)
        self.assertEqual(snapshot.pending_orders[0].reserved_cash, 36_000.0)

    def test_partial_exit_realizes_only_closed_quantity_and_keeps_remaining_unrealized_pnl(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store, allocated_capital=100_000.0)

        _reserve_order(inventory, event_id="partial-exit-buy-reserve", client_order_id="partial-exit-buy", quantity=100, price=600.0)
        _record_fill(inventory, event_id="partial-exit-buy-fill", fill_id="partial-exit-buy-fill", client_order_id="partial-exit-buy", quantity=100, price=600.0)
        inventory.mark_to_market(symbol="SPY", price=603.0, occurred_at=NOW + timedelta(minutes=1), market_event_id="partial-exit-spy-603")
        snapshot = _record_fill(
            inventory,
            event_id="partial-exit-sell-fill",
            fill_id="partial-exit-sell-fill",
            client_order_id="partial-exit-sell",
            quantity=-40,
            price=603.0,
            occurred_at=NOW + timedelta(minutes=2),
        )

        self.assertEqual(snapshot.position_quantity, 60)
        self.assertEqual(snapshot.average_entry_price, 600.0)
        self.assertEqual(snapshot.realized_pnl, 120.0)
        self.assertEqual(snapshot.unrealized_pnl, 180.0)
        self.assertEqual(snapshot.equity, 100_300.0)

    def test_rejection_cancellation_and_expiry_release_reservations_exactly_once(self) -> None:
        for terminal_status in ("REJECTED", "CANCELED", "EXPIRED"):
            with self.subTest(status=terminal_status):
                store = MemoryStore()
                inventory = seeded_inventory(store, allocated_capital=100_000.0)
                client_order_id = f"{terminal_status.lower()}-buy"

                reserved = _reserve_order(inventory, event_id=f"{client_order_id}-reserve", client_order_id=client_order_id, quantity=100, price=600.0)
                released = _release_order(inventory, event_id=f"{client_order_id}-release", client_order_id=client_order_id, status=terminal_status)
                duplicate_same_event = _release_order(inventory, event_id=f"{client_order_id}-release", client_order_id=client_order_id, status=terminal_status)
                duplicate_new_event = _release_order(inventory, event_id=f"{client_order_id}-release-duplicate", client_order_id=client_order_id, status=terminal_status)

                self.assertEqual(reserved.reserved_cash, 60_000.0)
                self.assertEqual(released.reserved_cash, 0.0)
                self.assertEqual(released.cash, 100_000.0)
                self.assertEqual(released.available_cash, 100_000.0)
                self.assertEqual(released.pending_orders, ())
                self.assertEqual(duplicate_same_event.reserved_cash, 0.0)
                self.assertEqual(duplicate_new_event.reserved_cash, 0.0)
                self.assertEqual(duplicate_new_event.cash, 100_000.0)
                self.assertEqual(duplicate_new_event.available_cash, 100_000.0)

    def test_duplicate_fill_id_changes_inventory_exactly_once(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store, allocated_capital=100_000.0)

        _reserve_order(inventory, event_id="duplicate-buy-reserve", client_order_id="duplicate-buy", quantity=100, price=600.0)
        first = _record_fill(inventory, event_id="duplicate-buy-fill-event-1", fill_id="duplicate-buy-fill", client_order_id="duplicate-buy", quantity=100, price=600.0)
        replay = _record_fill(inventory, event_id="duplicate-buy-fill-event-2", fill_id="duplicate-buy-fill", client_order_id="duplicate-buy", quantity=100, price=600.0)

        self.assertEqual(first.position_quantity, 100)
        self.assertEqual(replay.position_quantity, 100)
        self.assertEqual(replay.cash, first.cash)
        self.assertEqual(replay.equity, first.equity)
        self.assertEqual(replay.realized_pnl, first.realized_pnl)
        self.assertEqual(replay.reserved_cash, first.reserved_cash)
        self.assertEqual(replay.processed_fill_ids.count("duplicate-buy-fill"), 1)

    def test_restart_reloads_exact_cash_positions_pnl_pending_orders_and_reservations(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store, allocated_capital=100_000.0)

        _reserve_order(inventory, event_id="restart-buy-reserve", client_order_id="restart-buy", quantity=100, price=600.0)
        _record_fill(inventory, event_id="restart-buy-fill", fill_id="restart-buy-fill", client_order_id="restart-buy", quantity=40, price=600.0)
        before = inventory.mark_to_market(symbol="SPY", price=603.0, occurred_at=NOW + timedelta(minutes=1), market_event_id="restart-spy-603")

        reconstructed = WeightedVotingInventoryRepository(store, symbol="SPY", allocated_capital=100_000.0)
        loaded = reconstructed.recover_current_snapshot()

        self.assertEqual(loaded.cash, before.cash)
        self.assertEqual(loaded.equity, before.equity)
        self.assertEqual(loaded.realized_pnl, before.realized_pnl)
        self.assertEqual(loaded.unrealized_pnl, before.unrealized_pnl)
        self.assertEqual(loaded.position_quantity, before.position_quantity)
        self.assertEqual(loaded.average_entry_price, before.average_entry_price)
        self.assertEqual(loaded.reserved_cash, before.reserved_cash)
        self.assertEqual(loaded.available_cash, before.available_cash)
        self.assertEqual(len(loaded.positions), 1)
        self.assertEqual(len(loaded.pending_orders), 1)
        self.assertEqual(loaded.pending_orders[0].remaining_quantity, 60)
        self.assertEqual(loaded.pending_orders[0].reserved_cash, 36_000.0)
        self.assertEqual(loaded.processed_fill_ids, before.processed_fill_ids)


class NoAlpacaAutomaticLocalPaperService(WeightedVotingService):
    def evaluate_context(self, context, **_kwargs) -> dict:
        evaluated_at = NOW
        proposal = _proposal_with_quote(
            global_proposal(quantity=10, proposed_at=evaluated_at, order_intent_id="no-alpaca-auto-local-paper"),
            quote=_quote(bid=99.95, ask=100.0, timestamp=evaluated_at),
            execution_costs={
                "buySlippagePerShare": 0.0,
                "sellSlippagePerShare": 0.0,
                "commissionPerShare": 0.0,
                "regulatoryFeePerShare": 0.0,
                "spreadImpactPerShare": 0.0,
                "spreadImpactPercent": 0.0,
            },
        )
        response = GlobalGateResponse(
            action="ALLOW",
            maximumAllowedQuantity=proposal.quantity,
            maximumAdditionalRiskDollars=proposal.plannedRiskDollars,
            evaluatedAt=evaluated_at,
            configurationHash="weighted_voting.no_alpaca.local_global_gate",
        )
        application = apply_global_gate_response(proposal, response)
        return {
            "decision": {
                "decision_id": proposal.decisionId,
                "algorithm_id": "weighted_voting",
                "side": "BUY",
                "weightedScore": 1.0,
                "sizing": {
                    "requestedQuantity": proposal.quantity,
                    "entryPrice": proposal.limitPrice,
                    "plannedRiskDollars": proposal.plannedRiskDollars,
                    "source": "weighted_voting.no_alpaca.local_paper_test",
                },
                "reason_codes": ("weighted_voting.no_alpaca.decision_generated",),
            },
            "gateResult": {
                "permission_granted": True,
                "mode": "automatic",
                "reason_codes": ("weighted_voting.no_alpaca.local_risk_passed",),
                "explanation": "No-Alpaca local paper automatic path generated a deterministic trade.",
            },
            "globalOrderProposal": proposal.model_dump(mode="json"),
            "globalRiskResponse": {
                "action": "ALLOW",
                "maximumAllowedQuantity": proposal.quantity,
                "maximumAdditionalRiskDollars": proposal.plannedRiskDollars,
                "configurationHash": "weighted_voting.no_alpaca.local_global_risk",
                "configurationVersion": "weighted_voting.no_alpaca.local_global_risk_v1",
                "evaluatedAt": evaluated_at.isoformat(),
                "expiresAt": (evaluated_at + timedelta(seconds=30)).isoformat(),
                "reasonCodes": ("weighted_voting.no_alpaca.global_risk_allowed",),
            },
            "globalGateApplication": application.model_dump(mode="json"),
            "signals": (
                {"strategyId": "S2", "shadowRecordsOnly": False, "side": "BUY"},
                {"strategyId": "S3", "shadowRecordsOnly": True, "side": "HOLD"},
            ),
        }


class LocalInventorySizingAcceptanceService(WeightedVotingService):
    def __init__(self, *, store: "MemoryStore") -> None:
        super().__init__(store=store)
        self.sizing_observations: list[dict[str, float | int | str]] = []

    def evaluate_context(self, context, **_kwargs) -> dict:
        evaluated_at = context.finalised_one_minute_market_snapshot.data_timestamp
        inventory = context.inventory_snapshot
        entry_price = float(context.finalised_one_minute_market_snapshot.ask or context.finalised_one_minute_market_snapshot.one_minute_candles[-1].close)
        stop_distance = 1.0
        risk_dollars = round(float(inventory.equity) * 0.01, 10)
        risk_quantity = int(risk_dollars // stop_distance)
        local_cash_quantity = int(float(inventory.cash) // entry_price)
        quantity = max(0, min(risk_quantity, local_cash_quantity))
        observation = {
            "source": "weighted_voting.local_inventory",
            "equity": float(inventory.equity),
            "cash": float(inventory.cash),
            "buying_power": float(inventory.buying_power),
            "local_cash_quantity": local_cash_quantity,
            "risk_dollars": risk_dollars,
            "entry_price": entry_price,
            "quantity": quantity,
            "inventory_snapshot_version": inventory.snapshot_version,
        }
        self.sizing_observations.append(observation)
        order_intent_id = f"weighted-voting-e2e-local-{len(self.sizing_observations)}"
        proposal = _proposal_with_quote(
            global_proposal(quantity=quantity, proposed_at=evaluated_at, order_intent_id=order_intent_id).model_copy(
                update={
                    "plannedRiskDollars": risk_dollars,
                    "limitPrice": entry_price,
                    "triggerPrice": entry_price,
                    "stopPrice": entry_price - stop_distance,
                    "targetPrice": entry_price + (2.0 * stop_distance),
                    "settingsSnapshot": {
                        "settings_version": "weighted-voting-e2e",
                        "localPaperQuote": _quote(bid=entry_price + 0.45, ask=entry_price + 0.50, timestamp=evaluated_at),
                        "localPaperExecutionCosts": {
                            "buySlippagePerShare": 0.0,
                            "sellSlippagePerShare": 0.0,
                            "commissionPerShare": 0.0,
                            "regulatoryFeePerShare": 0.0,
                            "spreadImpactPerShare": 0.0,
                            "spreadImpactPercent": 0.0,
                        },
                        "sizingSource": "weighted_voting.local_inventory",
                        "inventorySnapshotVersion": inventory.snapshot_version,
                    },
                }
            ),
            quote=_quote(bid=entry_price + 0.45, ask=entry_price + 0.50, timestamp=evaluated_at),
            execution_costs={
                "buySlippagePerShare": 0.0,
                "sellSlippagePerShare": 0.0,
                "commissionPerShare": 0.0,
                "regulatoryFeePerShare": 0.0,
                "spreadImpactPerShare": 0.0,
                "spreadImpactPercent": 0.0,
            },
        )
        response = GlobalGateResponse(
            action="ALLOW",
            maximumAllowedQuantity=proposal.quantity,
            maximumAdditionalRiskDollars=proposal.plannedRiskDollars,
            evaluatedAt=evaluated_at,
            configurationHash="weighted_voting.e2e.local_inventory_global_gate",
        )
        application = apply_global_gate_response(proposal, response)
        return {
            "decision": {
                "decision_id": proposal.decisionId,
                "algorithm_id": "weighted_voting",
                "side": "BUY",
                "weightedScore": 1.0,
                "sizing": observation,
                "reason_codes": ("weighted_voting.e2e.buy_generated_from_local_inventory",),
            },
            "gateResult": {
                "permission_granted": quantity > 0,
                "mode": "automatic",
                "reason_codes": ("weighted_voting.e2e.local_inventory_risk_sized",),
                "explanation": "Deterministic E2E service sized from Weighted Voting local inventory only.",
            },
            "globalOrderProposal": proposal.model_dump(mode="json"),
            "globalRiskResponse": {
                "action": "ALLOW",
                "maximumAllowedQuantity": proposal.quantity,
                "maximumAdditionalRiskDollars": proposal.plannedRiskDollars,
                "configurationHash": "weighted_voting.e2e.local_inventory_global_risk",
                "configurationVersion": "weighted_voting.e2e.local_inventory_global_risk_v1",
                "evaluatedAt": evaluated_at.isoformat(),
                "expiresAt": (evaluated_at + timedelta(seconds=30)).isoformat(),
                "reasonCodes": ("weighted_voting.e2e.global_risk_allowed",),
            },
            "globalGateApplication": application.model_dump(mode="json"),
            "signals": (
                {"strategyId": "S2", "shadowRecordsOnly": False, "side": "BUY"},
                {"strategyId": "S3", "shadowRecordsOnly": True, "side": "HOLD"},
            ),
        }


def seeded_inventory(store: "MemoryStore", *, allocated_capital: float = 25_000.0) -> WeightedVotingInventoryRepository:
    inventory = WeightedVotingInventoryRepository(store, symbol="SPY", allocated_capital=allocated_capital)
    inventory.initialize_session(
        session_date=date(2026, 7, 14),
        allocated_capital=allocated_capital,
        cash_available=allocated_capital,
        occurred_at=NOW,
        expected_snapshot_version=0,
        event_id=f"weighted-voting-local-paper-session-{allocated_capital}",
    )
    return inventory


def _seed_long_position(inventory: WeightedVotingInventoryRepository, *, quantity: int, price: float, event_id: str) -> None:
    snapshot = inventory.current_snapshot(now=NOW)
    inventory.append_event(
        event_id=event_id,
        event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
        payload={
            "algorithm_id": "weighted_voting",
            "fill_id": f"{event_id}.fill",
            "position_id": f"weighted_voting.position.SPY.{event_id}",
            "symbol": "SPY",
            "side": "LONG",
            "quantity": quantity,
            "average_entry_price": price,
            "opened_at": NOW.isoformat(),
            "decision_id": f"{event_id}.decision",
            "order_intent_id": f"{event_id}.intent",
            "client_order_id": f"{event_id}.client",
            "source": "weighted_voting.local_paper.test_seed",
        },
        occurred_at=NOW,
        expected_snapshot_version=snapshot.snapshot_version,
    )


def _reserve_order(
    inventory: WeightedVotingInventoryRepository,
    *,
    event_id: str,
    client_order_id: str,
    quantity: int,
    price: float,
    status: str = "OPEN",
):
    snapshot = inventory.current_snapshot(now=NOW)
    return inventory.append_event(
        event_id=event_id,
        event_type=WeightedVotingInventoryEventType.ORDER_RESERVED,
        payload={
            "algorithm_id": "weighted_voting",
            "order_id": f"{client_order_id}.order",
            "client_order_id": client_order_id,
            "decision_id": f"{client_order_id}.decision",
            "order_intent_id": f"{client_order_id}.intent",
            "symbol": "SPY",
            "side": "BUY",
            "quantity": quantity,
            "filled_quantity": 0,
            "remaining_quantity": quantity,
            "order_type": "LIMIT",
            "limit_price": price,
            "stop_price": None,
            "status": status,
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
            "expiration": (NOW + timedelta(minutes=15)).isoformat(),
            "reserved_cash": round(quantity * price, 10),
            "reserved_buying_power": round(quantity * price, 10),
            "planned_risk_dollars": 1_000.0,
        },
        occurred_at=NOW,
        expected_snapshot_version=snapshot.snapshot_version,
    )


def _release_order(
    inventory: WeightedVotingInventoryRepository,
    *,
    event_id: str,
    client_order_id: str,
    status: str,
):
    snapshot = inventory.current_snapshot(now=NOW)
    return inventory.append_event(
        event_id=event_id,
        event_type=WeightedVotingInventoryEventType.ORDER_RELEASED,
        payload={
            "algorithm_id": "weighted_voting",
            "order_id": f"{client_order_id}.order",
            "client_order_id": client_order_id,
            "status": status,
            "reason": f"weighted_voting.local_paper.{status.lower()}_release_test",
        },
        occurred_at=NOW,
        expected_snapshot_version=snapshot.snapshot_version,
    )


def _record_fill(
    inventory: WeightedVotingInventoryRepository,
    *,
    event_id: str,
    fill_id: str,
    client_order_id: str,
    quantity: int,
    price: float,
    occurred_at: datetime = NOW,
):
    snapshot = inventory.current_snapshot(now=occurred_at)
    side = "SHORT" if quantity < 0 else "LONG"
    return inventory.append_event(
        event_id=event_id,
        event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
        payload={
            "algorithm_id": "weighted_voting",
            "fill_id": fill_id,
            "position_id": f"weighted_voting.position.SPY.{client_order_id}",
            "symbol": "SPY",
            "side": side,
            "quantity": quantity,
            "average_entry_price": price,
            "mark_price": price,
            "opened_at": occurred_at.isoformat(),
            "decision_id": f"{client_order_id}.decision",
            "order_intent_id": f"{client_order_id}.intent",
            "client_order_id": client_order_id,
            "total_execution_cost": 0.0,
            "source": "weighted_voting.local_paper.accounting_test",
        },
        occurred_at=occurred_at,
        expected_snapshot_version=snapshot.snapshot_version,
    )


def _seed_foreign_algorithm_inventory_records(store: "MemoryStore") -> None:
    records = {
        "voting_ensemble.local_paper.account.current": {
            "algorithmId": "voting_ensemble",
            "cash": 88_888.0,
            "equity": 89_209.45,
            "realizedPnl": 123.45,
            "unrealizedPnl": 198.0,
            "reservedCash": 456.0,
            "updatedAt": NOW.isoformat(),
        },
        "voting_ensemble.local_paper.positions.SPY": {
            "algorithmId": "voting_ensemble",
            "symbol": "SPY",
            "quantity": 7,
            "averageEntryPrice": 581.0,
            "realizedPnl": 123.45,
            "unrealizedPnl": 198.0,
            "updatedAt": NOW.isoformat(),
        },
        "voting_ensemble.local_paper.orders.pending": {
            "algorithmId": "voting_ensemble",
            "clientOrderId": "ve-pending-1",
            "symbol": "SPY",
            "quantity": 3,
            "status": "OPEN",
            "reservedCash": 1_800.0,
        },
        "wca.inventory_projection.SPY": {
            "algorithmId": "wca",
            "cash": 77_777.0,
            "quantity": 5,
            "realizedPnl": 42.0,
            "unrealizedPnl": -11.0,
            "pendingOrderQuantity": 2,
        },
        "wca.positions.SPY": {
            "algorithmId": "wca",
            "symbol": "SPY",
            "quantity": 5,
            "averageEntryPrice": 590.0,
            "realizedPnl": 42.0,
            "unrealizedPnl": -11.0,
        },
        "regime.inventory.snapshot.current": {
            "algorithmId": "regime",
            "cash": 66_666.0,
            "quantity": 4,
            "realizedPnl": -22.0,
            "unrealizedPnl": 33.0,
            "reservedCash": 700.0,
        },
        "regime.positions.SPY": {
            "algorithmId": "regime",
            "symbol": "SPY",
            "quantity": 4,
            "averageEntryPrice": 601.0,
            "realizedPnl": -22.0,
            "unrealizedPnl": 33.0,
        },
        "meta_strategy.local_paper.account.current": {
            "algorithmId": "meta_strategy",
            "cash": 55_555.0,
            "quantity": 2,
            "realizedPnl": 12.0,
            "unrealizedPnl": 13.0,
            "reservedCash": 300.0,
        },
        "meta_strategy.local_paper.positions.SPY": {
            "algorithmId": "meta_strategy",
            "symbol": "SPY",
            "quantity": 2,
            "averageEntryPrice": 599.0,
            "realizedPnl": 12.0,
            "unrealizedPnl": 13.0,
        },
    }
    for key, payload in records.items():
        store.write_snapshot(key, deepcopy(payload))


def _foreign_algorithm_records(store: "MemoryStore") -> dict[str, dict]:
    foreign_prefixes = ("voting_ensemble.", "wca.", "regime.", "meta_strategy.")
    return {key: deepcopy(value) for key, value in store.snapshots.items() if key.startswith(foreign_prefixes)}


def _voting_ensemble_metrics(store: "MemoryStore") -> dict[str, float | int]:
    account = store.read_snapshot("voting_ensemble.local_paper.account.current")
    position = store.read_snapshot("voting_ensemble.local_paper.positions.SPY")
    return {
        "cash": float(account["cash"]),
        "position": int(position["quantity"]),
        "pnl": round(float(account["realizedPnl"]) + float(account["unrealizedPnl"]), 10),
    }


def _enable_local_automatic_entries(supervisor: WeightedVotingRuntimeSupervisor) -> None:
    observed_at = NOW
    supervisor.metrics.supervisor_started = True
    supervisor.metrics.inventory_reconciled = True
    supervisor.metrics.entry_creation_paused_for_reconciliation = False
    supervisor.metrics.processing_lag_seconds = 0.0
    supervisor.metrics.last_global_risk_response = {
        "action": "ALLOW",
        "maximumAllowedQuantity": 10,
        "maximumAdditionalRiskDollars": 100.0,
    }
    supervisor.metrics.last_reconciliation = {
        "algorithmId": "weighted_voting",
        "status": "reconciled",
        "inventoryReconciled": True,
        "entriesPaused": False,
        "riskReducingExitsAllowed": True,
        "trigger": "weighted_voting.no_alpaca.local_paper_test",
        "reasonCodes": ("weighted_voting.local_paper.reconciled",),
        "recordedAt": observed_at.isoformat(),
    }
    supervisor.metrics.last_finalised_bar_received = {
        "symbol": "SPY",
        "source": "weighted_voting.no_alpaca.local_paper_test",
        "finalisedCandleTimestamp": observed_at.isoformat(),
        "dataFreshnessSeconds": 0.0,
        "sessionPhase": "morning",
    }
    supervisor.metrics.last_bar_processed = dict(supervisor.metrics.last_finalised_bar_received)
    control = supervisor.update_runtime_control(
        paper_trading_enabled=True,
        automatic_entries_enabled=True,
        updated_by="weighted_voting.no_alpaca.local_paper_test",
        reason="weighted_voting.no_alpaca.enable_local_automatic_paper",
    )
    assert control["automatic_entries_enabled"] is True


def _finalized_event(*, close: float, observed_at: datetime = NOW, source_sequence: int = 1, source: str = "weighted_voting.no_alpaca.local_test") -> WeightedVotingFinalisedBarEvent:
    candles = []
    start = observed_at - timedelta(minutes=94)
    for index in range(95):
        timestamp = start + timedelta(minutes=index)
        candles.append(
            {
                "timestamp": timestamp.isoformat(),
                "open": close,
                "high": close + 0.45,
                "low": close - 0.18,
                "close": close,
                "volume": 200_000,
            }
        )
    return WeightedVotingFinalisedBarEvent(
        algorithm_id="weighted_voting",
        symbol="SPY",
        finalised_candle_timestamp=observed_at,
        data_manifest_hash=f"weighted_voting.no_alpaca.local_manifest.{source_sequence}",
        market_payload={
            "symbol": "SPY",
            "data_timestamp": observed_at.isoformat(),
            "candles": candles,
            "bid": close - 0.05,
            "ask": close,
            "session_phase": "morning",
            "data_freshness_seconds": 0.0,
            "source": "weighted_voting.no_alpaca.local_payload",
            "quote": _quote(bid=close - 0.05, ask=close, timestamp=observed_at),
        },
        published_at=observed_at,
        bar_start=observed_at,
        bar_end=observed_at + timedelta(minutes=1),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100_000,
        data_source=source,
        source_sequence=source_sequence,
    )


def _submit_bracket_entry(store: "MemoryStore", inventory: WeightedVotingInventoryRepository, *, order_intent_id: str):
    broker = WeightedVotingLocalPaperBroker(store, inventory)
    gateway = PaperOrderGateway(
        broker,
        store,
        execution_mode="LOCAL_PAPER",
        account_snapshot_provider=broker.gateway_account_snapshot,
        portfolio_snapshot_provider=broker.gateway_portfolio_snapshot,
    )
    proposal = _proposal_with_quote(global_proposal(quantity=10, order_intent_id=order_intent_id))
    item = enqueue_weighted_voting_execution_order(
        store=store,
        proposal=proposal,
        global_application=global_application(proposal),
        local_gate_result=local_gate(True),
        enqueued_at=NOW,
        idempotency_key=f"weighted_voting.local_paper.{order_intent_id}",
    )
    result = submit_queued_weighted_voting_paper_order(
        gateway=gateway,
        queue_item=item,
        inventory_repository=inventory,
        evaluated_at=NOW,
        rollout_flags=validated_rollout_flags(),
        rollout_validation=validated_rollout_validation(),
    )
    return broker, result


def _local_intent(
    *,
    order_type: str,
    order_intent_id: str,
    filled_quantity: int | None = None,
    side: Signal = Signal.BUY,
    limit_price: float = 100.0,
    stop_price: float = 99.0,
    quote: dict | None = None,
    bar: dict | None = None,
    execution_costs: dict | None = None,
    settings_extra: dict | None = None,
) -> PaperOrderIntentRecord:
    settings = {"settings_version": "test"}
    if quote is None and bar is None:
        quote = _quote(bid=99.95, ask=100.0)
    if quote is not None:
        settings["localPaperQuote"] = quote
    if bar is not None:
        settings["localPaperBar"] = bar
    if filled_quantity is not None:
        settings["localPaperAvailableQuantity"] = filled_quantity
    if execution_costs is not None:
        settings["localPaperExecutionCosts"] = execution_costs
    if settings_extra is not None:
        settings.update(settings_extra)
    return PaperOrderIntentRecord(
        executionMode="LOCAL_PAPER",
        algorithmId="weighted_voting",
        capitalPartitionId="weighted_voting.paper.default",
        decisionId=f"{order_intent_id}.decision",
        orderIntentId=order_intent_id,
        clientOrderId=f"{order_intent_id}.client",
        mode="automatic",
        symbol="SPY",
        side=side,
        proposedQuantity=10,
        globallyAllowedQuantity=10,
        submittedQuantity=10,
        triggerPrice=100.0,
        orderType=order_type,
        limitPrice=limit_price,
        stopPrice=stop_price,
        targetPrice=102.0,
        plannedRiskDollars=100.0,
        globalAction="ALLOW",
        localGatePassed=True,
        globalGatePassed=True,
        paperAccountVerified=True,
        createdAt=NOW,
        decisionTimestamp=NOW,
        settingsSnapshot=settings,
    )


def _quote(*, bid: float, ask: float, timestamp: datetime = NOW) -> dict:
    return {"bid": bid, "ask": ask, "timestamp": timestamp.isoformat()}


def _local_lifecycle_events(store: "MemoryStore") -> list[dict]:
    prefix = f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.lifecycle."
    return [
        dict(payload)
        for key, payload in sorted(store.snapshots.items())
        if key.startswith(prefix) and not key.endswith(".index") and isinstance(payload, dict)
    ]


def _bar(*, close: float, timestamp: datetime, bar_end: datetime) -> dict:
    return {
        "timestamp": timestamp.isoformat(),
        "barEndTimestamp": bar_end.isoformat(),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "timeframe": "1Min",
    }


def _risk_request(proposal):
    from backend.app.algorithms.weighted_voting.global_interface import build_weighted_voting_global_risk_request

    return build_weighted_voting_global_risk_request(
        proposal=proposal,
        inventory_version=0,
        current_algorithm_exposure=0.0,
        current_account_exposure=0.0,
        daily_algorithm_pnl=0.0,
        account_level_risk_observations={"source": "weighted_voting.local_paper.test"},
        settings_version="test",
        requested_at=NOW,
    )


def _proposal_with_quote(proposal, *, execution_costs: dict | None = None, quote: dict | None = None):
    settings = dict(proposal.settingsSnapshot or {})
    settings["localPaperQuote"] = quote or _quote(bid=99.95, ask=100.0)
    if execution_costs is not None:
        settings["localPaperExecutionCosts"] = execution_costs
    return proposal.model_copy(update={"settingsSnapshot": settings})


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
