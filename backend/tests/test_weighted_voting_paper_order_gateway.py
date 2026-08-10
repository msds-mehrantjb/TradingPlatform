from __future__ import annotations

import unittest
import shutil
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from backend.app.algorithms.weighted_voting.decision_gates import WeightedGateEvaluationMode, WeightedVotingGatePipelineResult
from backend.app.algorithms.weighted_voting.execution_gateway import (
    WEIGHTED_VOTING_BROKER_CONNECTION_BOUNDARY,
    WEIGHTED_VOTING_EXECUTION_NAMESPACE,
    WeightedVotingExecutionReconciliation,
    build_weighted_voting_broker_command,
    enqueue_weighted_voting_execution_order,
    execution_gateway_status,
    reconcile_weighted_voting_broker_result,
    record_weighted_voting_rejection,
    submit_queued_weighted_voting_paper_order,
    submit_weighted_voting_paper_order,
)
from backend.app.algorithms.weighted_voting.inventory import WeightedVotingInventoryEventType, WeightedVotingInventoryRepository
from backend.app.algorithms.weighted_voting.local_paper_broker import WeightedVotingLocalPaperBroker
from backend.app.algorithms.weighted_voting.models import WeightedGateResult, WeightedGateStatus
from backend.app.algorithms.weighted_voting.rollout import WEIGHTED_VOTING_AUTO_SUBMIT_ENABLED, WeightedVotingRolloutFlags, WeightedVotingRolloutValidation
from backend.app.algorithms.weighted_voting.rollout import ROLLOUT_STATE_KEY, persist_rollout_validation_record
from backend.app.domain.models import Signal
from backend.app.execution import cost_model
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderGateway, PaperOrderGatewayResult
from backend.app.gates import AppliedGlobalGateDecision, GlobalGateResponse, GlobalOrderProposal, apply_global_gate_response


NOW = datetime(2026, 7, 14, 15, 30, tzinfo=UTC)
SESSION_DATE = date(2026, 7, 14)


class WeightedVotingPaperOrderGatewayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = Path("backend/.test_artifacts") / f"weighted_gateway_execution_cost_{uuid.uuid4().hex}"
        shutil.rmtree(self.scratch, ignore_errors=True)
        self.previous_dirs = (
            cost_model.EXECUTION_COST_LEDGER_DIR,
            cost_model.EXECUTION_COST_CANDIDATE_DIR,
            cost_model.EXECUTION_COST_ACTIVE_DIR,
            cost_model.EXECUTION_COST_ACTIVE_HISTORY_DIR,
        )
        cost_model.EXECUTION_COST_LEDGER_DIR = self.scratch / "ledger"
        cost_model.EXECUTION_COST_CANDIDATE_DIR = self.scratch / "artifacts" / "candidates"
        cost_model.EXECUTION_COST_ACTIVE_DIR = self.scratch / "artifacts" / "active"
        cost_model.EXECUTION_COST_ACTIVE_HISTORY_DIR = self.scratch / "artifacts" / "active_history"

    def tearDown(self) -> None:
        (
            cost_model.EXECUTION_COST_LEDGER_DIR,
            cost_model.EXECUTION_COST_CANDIDATE_DIR,
            cost_model.EXECUTION_COST_ACTIVE_DIR,
            cost_model.EXECUTION_COST_ACTIVE_HISTORY_DIR,
        ) = self.previous_dirs
        shutil.rmtree(self.scratch, ignore_errors=True)

    def test_execution_reconciliation_rejects_foreign_algorithm_ownership(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be assigned to another algorithm"):
            WeightedVotingExecutionReconciliation(
                algorithm_id="voting_ensemble",
                command_id="cmd-1",
                decision_id="decision-1",
                order_intent_id="intent-1",
                client_order_id="client-1",
                status="REJECTED",
                reconciled_at=NOW,
                submitted=False,
                broker_order_id=None,
                fill=None,
                position=None,
                reason_codes=("weighted_voting.test.foreign_reconciliation",),
            )

    def test_global_application_cannot_increase_weighted_voting_command_quantity(self) -> None:
        proposal = global_proposal(quantity=10)
        application = global_application(proposal)
        malformed_application = AppliedGlobalGateDecision.model_construct(
            **{
                **application.model_dump(),
                "globallyAllowedQuantity": 11,
                "quantityReduced": False,
            }
        )

        with self.assertRaisesRegex(ValueError, "cannot increase proposed quantity"):
            build_weighted_voting_broker_command(
                proposal=proposal,
                global_application=malformed_application,
                accepted_at=NOW,
            )

    def test_duplicate_submissions_are_prevented_and_intent_is_persisted_before_submit(self) -> None:
        broker = FakePaperBroker()
        store = MemoryStore()
        gateway = weighted_voting_local_gateway(broker, store)
        proposal = global_proposal()
        application = global_application(proposal)

        first = submit_weighted_voting_paper_order(
            gateway=gateway,
            proposal=proposal,
            global_application=application,
            local_gate_result=local_gate(True),
            mode="automatic",
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        second = submit_weighted_voting_paper_order(
            gateway=gateway,
            proposal=proposal,
            global_application=application,
            local_gate_result=local_gate(True),
            mode="automatic",
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )

        self.assertTrue(first.submitted)
        self.assertFalse(first.duplicate)
        self.assertFalse(second.submitted)
        self.assertTrue(second.duplicate)
        self.assertEqual(broker.submit_count, 1)
        intent = store.snapshots[f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.local_paper.intent.{proposal.orderIntentId}"]
        self.assertTrue(intent["persistedBeforeSubmission"])
        self.assertTrue(broker.intent_existed_before_submit)
        self.assertFalse(any(key.startswith("paper_order_gateway.") for key in store.snapshots))

    def test_order_is_not_submitted_when_quantity_zero_local_gate_global_gate_or_stale(self) -> None:
        cases = [
            ("zero", global_proposal(quantity=0), None, local_gate(True), "paper_gateway.zero_quantity", NOW),
            ("local", global_proposal(), None, local_gate(False), "paper_gateway.local_gate_failed", NOW),
            ("global", global_proposal(), "REJECT_NEW_ENTRY", local_gate(True), "paper_gateway.global_gate_rejected", NOW),
            ("stale", global_proposal(proposed_at=NOW - timedelta(minutes=10)), None, local_gate(True), "paper_gateway.stale_decision", NOW),
        ]

        for name, proposal, action, local, reason, evaluated_at in cases:
            with self.subTest(name=name):
                broker = FakePaperBroker()
                store = MemoryStore()
                gateway = weighted_voting_local_gateway(broker, store, max_decision_age_seconds=300)
                application = global_application(proposal, action=action or "ALLOW")
                result = submit_weighted_voting_paper_order(gateway=gateway, proposal=proposal, global_application=application, local_gate_result=local, mode="manual", evaluated_at=evaluated_at)

                self.assertFalse(result.submitted)
                self.assertEqual(broker.submit_count, 0)
                self.assertIn(reason, result.reasonCodes)

    def test_partial_fill_maps_to_weighted_voting_intent_and_creates_protective_order(self) -> None:
        broker = FakePaperBroker(fill_status="PARTIALLY_FILLED", filled_quantity=4)
        store = MemoryStore()
        gateway = weighted_voting_local_gateway(broker, store)
        proposal = global_proposal(quantity=10)
        result = submit_weighted_voting_paper_order(
            gateway=gateway,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            mode="automatic",
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )

        self.assertTrue(result.submitted)
        self.assertEqual(result.status, "PARTIALLY_FILLED")
        self.assertEqual(result.fill.orderIntentId, proposal.orderIntentId)
        self.assertEqual(result.fill.algorithmId, "weighted_voting")
        self.assertEqual(result.protectiveOrder.quantity, 4)
        self.assertEqual(result.protectiveOrder.orderIntentId, proposal.orderIntentId)
        self.assertIn("paper_gateway.partial_fill_mapped_to_intent", result.reasonCodes)

    def test_automatic_submission_is_blocked_until_rollout_validation_passes(self) -> None:
        broker = FakePaperBroker()
        store = MemoryStore()
        gateway = weighted_voting_local_gateway(broker, store)
        proposal = global_proposal()

        result = submit_weighted_voting_paper_order(
            gateway=gateway,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            mode="automatic",
            evaluated_at=NOW,
        )

        self.assertFalse(result.submitted)
        self.assertEqual(broker.submit_count, 0)
        self.assertIn("weighted_voting.rollout.auto_submit_blocked", result.reasonCodes)

    def test_rejection_handling_records_broker_rejection_without_local_position(self) -> None:
        broker = FakePaperBroker(ack_status="REJECTED")
        store = MemoryStore()
        gateway = weighted_voting_local_gateway(broker, store)
        proposal = global_proposal()

        result = submit_weighted_voting_paper_order(
            gateway=gateway,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            mode="automatic",
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )

        self.assertFalse(result.submitted)
        self.assertEqual(result.status, "REJECTED")
        self.assertIn("paper_gateway.broker_rejected", result.reasonCodes)

    def test_paper_account_must_be_verified_before_submission(self) -> None:
        broker = FakePaperBroker(paper_account_verified=False)
        store = MemoryStore()
        gateway = weighted_voting_local_gateway(broker, store)
        proposal = global_proposal()

        result = submit_weighted_voting_paper_order(
            gateway=gateway,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            mode="automatic",
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )

        self.assertFalse(result.submitted)
        self.assertEqual(broker.submit_count, 0)
        self.assertIn("weighted_voting.local_paper.account_unverified", result.reasonCodes)

    def test_weighted_voting_local_paper_fails_closed_when_authoritative_inventory_unavailable(self) -> None:
        class BrokenInventory:
            def current_snapshot(self, **_kwargs):
                raise RuntimeError("inventory unavailable")

        store = MemoryStore()
        broker = WeightedVotingLocalPaperBroker(store, BrokenInventory())
        gateway = PaperOrderGateway(
            broker,
            store,
            execution_mode="LOCAL_PAPER",
            account_snapshot_provider=broker.gateway_account_snapshot,
            portfolio_snapshot_provider=broker.gateway_portfolio_snapshot,
        )
        proposal = global_proposal()

        self.assertEqual(gateway.execution_mode, "LOCAL_PAPER")
        self.assertIs(gateway.broker, broker)
        self.assertIsNotNone(gateway.account_snapshot_provider)
        self.assertIsNotNone(gateway.portfolio_snapshot_provider)
        result = submit_weighted_voting_paper_order(
            gateway=gateway,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            mode="automatic",
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )

        self.assertFalse(result.submitted)
        self.assertEqual(result.status, "NOT_SUBMITTED")
        self.assertIn("weighted_voting.local_paper.authoritative_inventory_unavailable", result.reasonCodes)
        self.assertFalse(any(key.startswith("weighted_voting.local_paper.orders.") for key in store.snapshots))

    def test_shared_gateway_voting_ensemble_authoritative_snapshot_rule_is_unchanged(self) -> None:
        gateway = PaperOrderGateway(FakePaperBroker(), MemoryStore(), execution_mode="LOCAL_PAPER")
        voting_proposal = global_proposal().model_copy(
            update={
                "algorithmId": "voting_ensemble",
                "capitalPartitionId": "voting_ensemble.paper.default",
                "decisionId": "voting-ensemble-decision",
                "orderIntentId": "voting-ensemble-intent",
            }
        )
        other_proposal = global_proposal().model_copy(
            update={
                "algorithmId": "other_algorithm",
                "capitalPartitionId": "other_algorithm.paper.default",
                "decisionId": "other-decision",
                "orderIntentId": "other-intent",
            }
        )

        self.assertIsNone(gateway._authoritative_account_snapshot(voting_proposal, NOW))
        self.assertIsNone(gateway._authoritative_portfolio_snapshot(voting_proposal, NOW))
        self.assertIsNotNone(gateway._authoritative_account_snapshot(other_proposal, NOW))
        self.assertIsNotNone(gateway._authoritative_portfolio_snapshot(other_proposal, NOW))

    def test_paper_endpoint_must_be_verified_before_submission(self) -> None:
        broker = FakePaperBroker(base_url="https://api.alpaca.markets/v2")
        store = MemoryStore()
        gateway = PaperOrderGateway(broker, store)
        proposal = global_proposal()

        result = submit_weighted_voting_paper_order(
            gateway=gateway,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            mode="automatic",
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )

        self.assertFalse(result.submitted)
        self.assertEqual(broker.submit_count, 0)
        self.assertIn("weighted_voting.execution.paper_endpoint_unverified", result.reasonCodes)
        self.assertIn("paper_gateway.paper_endpoint_unverified", result.reasonCodes)

    def test_shared_gateway_rejects_weighted_voting_before_generic_storage(self) -> None:
        broker = FakePaperBroker(fill_status=None)
        store = MemoryStore()
        gateway = PaperOrderGateway(broker, store, max_decision_age_seconds=300)
        proposal = global_proposal()
        result = submit_weighted_voting_paper_order(
            gateway=gateway,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            mode="automatic",
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )

        self.assertFalse(result.submitted)
        self.assertEqual(broker.submit_count, 0)
        self.assertIn("weighted_voting.execution.paper_endpoint_unverified", result.reasonCodes)
        self.assertFalse(any(key.startswith("paper_order_gateway.") for key in store.snapshots))

    def test_local_submission_preserves_known_intents_without_generic_restart_state(self) -> None:
        broker = FakePaperBroker()
        store = MemoryStore()
        gateway = weighted_voting_local_gateway(broker, store)
        proposal = global_proposal()
        result = submit_weighted_voting_paper_order(
            gateway=gateway,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            mode="automatic",
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        broker.positions.append({"positionId": "orphan-position", "clientOrderId": "unknown-client", "algorithmId": "weighted_voting"})

        self.assertTrue(result.submitted)
        self.assertIn(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.local_paper.intent.{proposal.orderIntentId}", store.snapshots)
        self.assertFalse(any(key.startswith("paper_order_gateway.") for key in store.snapshots))

    def test_manual_and_automatic_modes_use_identical_gate_path(self) -> None:
        results = []
        for mode in ("manual", "automatic"):
            broker = FakePaperBroker()
            store = MemoryStore()
            gateway = weighted_voting_local_gateway(broker, store)
            proposal = global_proposal(order_intent_id=f"wv-{mode}-intent")
            results.append(
                submit_weighted_voting_paper_order(
                    gateway=gateway,
                    proposal=proposal,
                    global_application=global_application(proposal),
                    local_gate_result=local_gate(True),
                    mode=mode,
                    evaluated_at=NOW,
                    rollout_flags=validated_rollout_flags(),
                    rollout_validation=validated_rollout_validation(),
                )
            )

        self.assertEqual(results[0].submitted, results[1].submitted)
        self.assertEqual(results[0].status, results[1].status)
        self.assertEqual(results[0].reasonCodes, results[1].reasonCodes)

    def test_dedicated_execution_command_converts_accepted_proposal_for_shared_broker(self) -> None:
        proposal = global_proposal()
        application = global_application(proposal)

        command = build_weighted_voting_broker_command(
            proposal=proposal,
            global_application=application,
            accepted_at=NOW,
            mode="automatic",
        )
        repeated = build_weighted_voting_broker_command(
            proposal=proposal,
            global_application=application,
            accepted_at=NOW + timedelta(seconds=5),
            mode="automatic",
        )
        broker_payload = command.as_shared_broker_command()

        self.assertEqual(command.algorithm_id, "weighted_voting")
        self.assertEqual(command.client_order_id, repeated.client_order_id)
        self.assertEqual(command.quantity, application.globallyAllowedQuantity)
        self.assertEqual(broker_payload["brokerConnection"], WEIGHTED_VOTING_BROKER_CONNECTION_BOUNDARY)
        self.assertTrue(broker_payload["preserveAlgorithmOwnership"])
        self.assertFalse(broker_payload["ownershipMutationAllowed"])

    def test_dedicated_execution_reconciliation_records_fill_and_position_ownership(self) -> None:
        store = MemoryStore()
        proposal = global_proposal()
        application = global_application(proposal)
        command = build_weighted_voting_broker_command(proposal=proposal, global_application=application, accepted_at=NOW)
        result = PaperOrderGatewayResult(
            algorithmId="weighted_voting",
            orderIntentId=proposal.orderIntentId,
            clientOrderId=command.client_order_id,
            mode="automatic",
            submitted=True,
            duplicate=False,
            status="FILLED",
            brokerAck=PaperGatewayBrokerAck(
                clientOrderId=command.client_order_id,
                brokerOrderId="broker-wv-1",
                status="ACCEPTED",
                acceptedAt=NOW,
            ),
            fill=PaperGatewayFill(
                clientOrderId=command.client_order_id,
                algorithmId="weighted_voting",
                orderIntentId=proposal.orderIntentId,
                symbol="SPY",
                side=Signal.BUY,
                filledQuantity=7,
                averageFillPrice=100.05,
                status="FILLED",
                filledAt=NOW,
            ),
            cancelReplacePolicy="cancel_stale_unfilled_orders_replace_requires_new_intent",
            reasonCodes=("paper_gateway.submitted",),
            explanation="Synthetic fill.",
            evaluatedAt=NOW,
            configurationHash="filled-config",
        )

        reconciliation = reconcile_weighted_voting_broker_result(
            store=store,
            command=command,
            broker_result=result,
            reconciled_at=NOW,
        )

        self.assertEqual(reconciliation.algorithm_id, "weighted_voting")
        self.assertIsNotNone(reconciliation.fill)
        self.assertIsNotNone(reconciliation.position)
        self.assertEqual(reconciliation.fill.decision_id, proposal.decisionId)
        self.assertEqual(reconciliation.position.algorithm_id, "weighted_voting")
        self.assertIn(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.command.{command.client_order_id}", store.snapshots)
        self.assertIn(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.submission.{command.client_order_id}", store.snapshots)
        self.assertEqual(store.snapshots[f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.position.{reconciliation.position.position_id}"]["algorithmId"], "weighted_voting")

    def test_dedicated_execution_rejection_is_recorded_without_position(self) -> None:
        store = MemoryStore()
        proposal = global_proposal()
        command = build_weighted_voting_broker_command(proposal=proposal, global_application=global_application(proposal), accepted_at=NOW)

        rejection = record_weighted_voting_rejection(
            store=store,
            command=command,
            rejected_at=NOW,
            broker_status="REJECTED",
            rejection_reason="broker rejected",
            reason_codes=("broker.reject",),
        )

        self.assertEqual(rejection.command.algorithm_id, "weighted_voting")
        self.assertIn("weighted_voting.execution.rejected", rejection.reason_codes)
        self.assertIn(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.rejection.{command.client_order_id}", store.snapshots)
        self.assertFalse(any(key.startswith(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.position.") for key in store.snapshots))

    def test_dedicated_execution_status_inventory_declares_owned_boundary(self) -> None:
        status = execution_gateway_status()

        self.assertEqual(status["algorithmId"], "weighted_voting")
        self.assertEqual(status["brokerConnectionBoundary"], WEIGHTED_VOTING_BROKER_CONNECTION_BOUNDARY)
        self.assertIn("deterministic_client_order_id", status["ownedResponsibilities"])
        self.assertEqual(status["executionMode"], "LOCAL_PAPER")
        self.assertEqual(status["sharedServices"], [])

    def test_allowed_automatic_queue_item_reserves_inventory_and_reaches_paper_gateway(self) -> None:
        broker = FakePaperBroker()
        store = MemoryStore()
        gateway = weighted_voting_local_gateway(broker, store)
        inventory = seeded_inventory(store)
        proposal = global_proposal()
        gates = local_gate(True)

        item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=gates,
            enqueued_at=NOW,
            idempotency_key="weighted_voting.runtime.idempotency.allowed",
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
        self.assertEqual(broker.submit_count, 1)
        self.assertEqual(snapshot.reserved_buying_power, 0.0)
        self.assertEqual(len(snapshot.pending_orders), 0)
        self.assertEqual(len(snapshot.open_positions), 1)
        self.assertIn(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.automatic_result.{item.command.client_order_id}", store.snapshots)

    def test_stale_inventory_version_prevents_submission_before_reservation(self) -> None:
        broker = FakePaperBroker()
        store = MemoryStore()
        gateway = weighted_voting_local_gateway(broker, store)
        inventory = seeded_inventory(store)
        proposal = global_proposal()
        gates = local_gate(True)
        item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=gates,
            enqueued_at=NOW,
            idempotency_key="weighted_voting.runtime.idempotency.stale-inventory",
        )
        before = inventory.current_snapshot(now=NOW)
        inventory.append_event(
            event_id="inventory-drift-before-submit",
            event_type=WeightedVotingInventoryEventType.BROKER_RECONCILED,
            payload={"algorithm_id": "weighted_voting", "checkpoint": {"algorithm_id": "weighted_voting", "reason": "test_inventory_drift"}},
            occurred_at=NOW + timedelta(seconds=1),
            expected_snapshot_version=before.snapshot_version,
        )

        result = submit_queued_weighted_voting_paper_order(
            gateway=gateway,
            queue_item=item,
            inventory_repository=inventory,
            evaluated_at=NOW + timedelta(seconds=2),
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )

        self.assertFalse(result.submitted)
        self.assertEqual(result.status, "NOT_SUBMITTED")
        self.assertIn("weighted_voting.execution.stale_inventory_version", result.reasonCodes)
        self.assertEqual(broker.submit_count, 0)
        self.assertEqual(inventory.current_snapshot(now=NOW).pending_orders, ())

    def test_rejected_automatic_proposal_is_not_enqueued_or_submitted(self) -> None:
        broker = FakePaperBroker()
        store = MemoryStore()
        proposal = global_proposal()

        item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=proposal,
            global_application=global_application(proposal, action="REJECT_NEW_ENTRY"),
            local_gate_result=local_gate(True),
            enqueued_at=NOW,
            idempotency_key="weighted_voting.runtime.idempotency.rejected",
        )

        self.assertIsNone(item)
        self.assertEqual(broker.submit_count, 0)
        self.assertTrue(any(key.endswith(".REJECTED") for key in store.snapshots if key.startswith(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.lifecycle.")))

    def test_duplicate_automatic_execution_event_does_not_create_duplicate_broker_order(self) -> None:
        broker = FakePaperBroker()
        store = MemoryStore()
        gateway = weighted_voting_local_gateway(broker, store)
        inventory = seeded_inventory(store)
        proposal = global_proposal()
        item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            enqueued_at=NOW,
            idempotency_key="weighted_voting.runtime.idempotency.duplicate",
        )

        first = submit_queued_weighted_voting_paper_order(
            gateway=gateway,
            queue_item=item,
            inventory_repository=inventory,
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        after_first = inventory.current_snapshot(now=NOW)
        second = submit_queued_weighted_voting_paper_order(
            gateway=gateway,
            queue_item=item,
            inventory_repository=inventory,
            evaluated_at=NOW + timedelta(seconds=1),
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        after_second = inventory.current_snapshot(now=NOW + timedelta(seconds=1))

        self.assertTrue(first.submitted)
        self.assertTrue(second.submitted)
        self.assertEqual(broker.submit_count, 1)
        self.assertEqual(after_second.cash, after_first.cash)
        self.assertEqual(after_second.reserved_cash, after_first.reserved_cash)
        self.assertEqual(after_second.realized_pnl, after_first.realized_pnl)
        self.assertEqual(after_second.unrealized_pnl, after_first.unrealized_pnl)
        self.assertEqual(after_second.open_positions, after_first.open_positions)
        self.assertEqual(after_second.pending_orders, after_first.pending_orders)
        self.assertEqual(after_second.processed_fill_ids, after_first.processed_fill_ids)

    def test_order_intent_idempotency_uses_decision_and_revision_before_execution(self) -> None:
        broker = FakePaperBroker()
        store = MemoryStore()
        gateway = weighted_voting_local_gateway(broker, store)
        inventory = seeded_inventory(store)
        first_proposal = global_proposal(order_intent_id="wv-intent-1")
        first_item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=first_proposal,
            global_application=global_application(first_proposal),
            local_gate_result=local_gate(True),
            enqueued_at=NOW,
            idempotency_key="weighted_voting.market_event.same-bar",
        )
        first = submit_queued_weighted_voting_paper_order(
            gateway=gateway,
            queue_item=first_item,
            inventory_repository=inventory,
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        repeated_proposal = global_proposal(order_intent_id="wv-intent-2").model_copy(
            update={
                "decisionId": first_proposal.decisionId,
                "settingsSnapshot": {"settings_version": "test", "intentRevision": 1},
            }
        )

        second_item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=repeated_proposal,
            global_application=global_application(repeated_proposal),
            local_gate_result=local_gate(True),
            enqueued_at=NOW + timedelta(seconds=1),
            idempotency_key="weighted_voting.market_event.same-bar.redelivery",
        )

        self.assertTrue(first.submitted)
        self.assertIsNone(second_item)
        self.assertEqual(broker.submit_count, 1)
        self.assertEqual(len([key for key in store.snapshots if key.startswith(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.queue.")]), 1)
        self.assertEqual(len([key for key in store.snapshots if key.startswith(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.order_intent_index.")]), 1)

    def test_rejected_broker_response_releases_inventory_reservation(self) -> None:
        broker = FakePaperBroker(ack_status="REJECTED", fill_status=None)
        store = MemoryStore()
        gateway = weighted_voting_local_gateway(broker, store)
        inventory = seeded_inventory(store)
        proposal = global_proposal()
        item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            enqueued_at=NOW,
            idempotency_key="weighted_voting.runtime.idempotency.broker-reject",
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
        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(snapshot.reserved_buying_power, 0.0)
        self.assertEqual(snapshot.pending_orders, ())

    def test_automatic_submission_remains_rollout_gated_and_releases_reservation(self) -> None:
        broker = FakePaperBroker()
        store = MemoryStore()
        gateway = weighted_voting_local_gateway(broker, store)
        inventory = seeded_inventory(store)
        proposal = global_proposal()
        item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            enqueued_at=NOW,
            idempotency_key="weighted_voting.runtime.idempotency.rollout-blocked",
        )

        result = submit_queued_weighted_voting_paper_order(
            gateway=gateway,
            queue_item=item,
            inventory_repository=inventory,
            evaluated_at=NOW,
        )

        self.assertFalse(result.submitted)
        self.assertEqual(broker.submit_count, 0)
        self.assertIn("weighted_voting.rollout.auto_submit_blocked", result.reasonCodes)
        self.assertEqual(inventory.current_snapshot(now=NOW).reserved_buying_power, 0.0)

    def test_persisted_small_allocation_rollout_state_allows_automatic_paper_submission(self) -> None:
        broker = FakePaperBroker()
        store = MemoryStore()
        store.write_snapshot(
            ROLLOUT_STATE_KEY,
            {
                "algorithm_id": "weighted_voting",
                "rollout_version": "weighted_voting_rollout_v2",
                "stage": "automatic_paper_small_allocation",
                "status": "valid",
                "automatic_paper_submission_allowed": True,
                "live_trading_allowed": False,
                "evidence_hash": "approved-evidence",
            },
        )
        persist_rollout_validation_record(
            store,
            validated_rollout_validation(),
            source_authority="backend.weighted_voting.paper_gateway_test_validation",
            approved_by="ops-user",
            recorded_at=NOW,
        )
        gateway = weighted_voting_local_gateway(broker, store)
        inventory = seeded_inventory(store)
        proposal = global_proposal()
        item = enqueue_weighted_voting_execution_order(
            store=store,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            enqueued_at=NOW,
            idempotency_key="weighted_voting.runtime.idempotency.persisted-small-rollout",
        )

        with patch.dict("os.environ", {WEIGHTED_VOTING_AUTO_SUBMIT_ENABLED: "true"}):
            result = submit_queued_weighted_voting_paper_order(
                gateway=gateway,
                queue_item=item,
                inventory_repository=inventory,
                evaluated_at=NOW,
            )

        self.assertTrue(result.submitted)
        self.assertEqual(broker.submit_count, 1)


def validated_rollout_flags() -> WeightedVotingRolloutFlags:
    return WeightedVotingRolloutFlags(
        v2_enabled=True,
        shadow_mode=False,
        dynamic_reduction_enabled=True,
        dynamic_increase_enabled=False,
        auto_submit_enabled=True,
    )


def validated_rollout_validation() -> WeightedVotingRolloutValidation:
    return WeightedVotingRolloutValidation(
        backend_shadow_passed=True,
        shadow_comparison_passed=True,
        static_equal_weights_passed=True,
        performance_weights_validated=True,
        dynamic_reduction_validated=True,
        dynamic_entry_exit_validated=True,
        dynamic_increase_validated=True,
        manual_paper_submission_validated=True,
        tests_passed=True,
        paper_validations_passed=True,
        paper_broker_e2e_validated=True,
        reconciliation_validated=True,
        restart_recovery_validated=True,
        local_paper_broker_validated=True,
        local_inventory_reconciled=True,
        local_balance_accounting_validated=True,
        local_fill_simulation_validated=True,
        local_restart_recovery_validated=True,
        no_cross_algorithm_mutation_validated=True,
        no_alpaca_dependency_validated=True,
        risk_fail_closed_validated=True,
        protective_exits_validated=True,
        persisted_operator_approval=True,
        validation_record_id="weighted_voting.rollout.validation.paper_gateway_test",
        source_authority="backend.weighted_voting.paper_gateway_test_validation",
        approved_by="ops-user",
        recorded_at=NOW.isoformat(),
        live_trading_enabled=False,
    )


def seeded_inventory(store: "MemoryStore") -> WeightedVotingInventoryRepository:
    inventory = WeightedVotingInventoryRepository(store, symbol="SPY", allocated_capital=25_000.0)
    inventory.initialize_session(
        session_date=SESSION_DATE,
        allocated_capital=25_000.0,
        cash_available=25_000.0,
        occurred_at=NOW,
        expected_snapshot_version=0,
        event_id=f"session-{uuid.uuid4().hex}",
    )
    return inventory


def weighted_voting_local_gateway(broker: "FakePaperBroker", store: "MemoryStore", **kwargs) -> PaperOrderGateway:
    return PaperOrderGateway(broker, store, execution_mode="LOCAL_PAPER", **kwargs)


class FakePaperBroker:
    broker_kind = "weighted_voting_local_paper"
    paper_endpoint = True
    live_trading_enabled = False

    def __init__(
        self,
        *,
        ack_status: str = "ACCEPTED",
        fill_status: str | None = "FILLED",
        filled_quantity: int = 10,
        paper_account_verified: bool = True,
        base_url: str = "local-paper://weighted_voting",
    ) -> None:
        self.ack_status = ack_status
        self.fill_status = fill_status
        self.filled_quantity = filled_quantity
        self.paper_account_verified = paper_account_verified
        self.base_url = base_url
        self.submit_count = 0
        self.cancel_count = 0
        self.positions: list[dict] = []
        self.intent_existed_before_submit = False

    def verify_paper_endpoint(self) -> bool:
        return self.base_url == "local-paper://weighted_voting"

    def verify_paper_account(self) -> bool:
        return self.paper_account_verified

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        self.submit_count += 1
        self.intent_existed_before_submit = intent.persistedBeforeSubmission
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=f"broker-{intent.clientOrderId}",
            status=self.ack_status,
            acceptedAt=NOW if self.ack_status != "REJECTED" else None,
            rejectedReason="paper rejected" if self.ack_status == "REJECTED" else None,
        )

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        if self.ack_status == "REJECTED" or self.fill_status is None:
            return None
        fill = PaperGatewayFill(
            clientOrderId=client_order_id,
            algorithmId="weighted_voting",
            orderIntentId="wv-intent-1" if "manual" not in client_order_id else "wv-manual-intent",
            symbol="SPY",
            side=Signal.BUY,
            filledQuantity=self.filled_quantity,
            averageFillPrice=100.01,
            status=self.fill_status,
            filledAt=NOW,
        )
        self.positions.append({"positionId": f"position-{client_order_id}", "clientOrderId": client_order_id, "algorithmId": "weighted_voting"})
        return fill

    def cancel_order(self, client_order_id: str) -> bool:
        self.cancel_count += 1
        return True

    def refresh_positions(self) -> list[dict]:
        return self.positions


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot


def global_proposal(*, quantity: int = 10, proposed_at: datetime = NOW, order_intent_id: str = "wv-intent-1") -> GlobalOrderProposal:
    return GlobalOrderProposal(
        algorithmId="weighted_voting",
        capitalPartitionId="weighted_voting.paper.default",
        decisionId=f"{order_intent_id}.decision",
        orderIntentId=order_intent_id,
        intent="new_entry",
        symbol="SPY",
        side="BUY",
        quantity=quantity,
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
        proposedAt=proposed_at,
        sessionDate=SESSION_DATE,
        configurationHash=f"{order_intent_id}.config",
    )


def global_application(proposal: GlobalOrderProposal, *, action: str = "ALLOW") -> AppliedGlobalGateDecision:
    quantity = proposal.quantity if action == "ALLOW" else 0
    response = GlobalGateResponse(
        action=action,
        maximumAllowedQuantity=quantity,
        maximumAdditionalRiskDollars=proposal.plannedRiskDollars if quantity else 0.0,
        rejectionReasons=() if quantity else ("global.reject",),
        evaluatedAt=NOW,
        configurationHash=f"{proposal.orderIntentId}.global",
    )
    return apply_global_gate_response(proposal, response)


def local_gate(passed: bool) -> WeightedVotingGatePipelineResult:
    return WeightedVotingGatePipelineResult(
        permission_granted=passed,
        mode=WeightedGateEvaluationMode.MANUAL,
        gate_results=(
            WeightedGateResult(
                gate_id="test_gate",
                gate_name="Test Gate",
                status=WeightedGateStatus.PASS if passed else WeightedGateStatus.FAIL,
                blocks_order=not passed,
                data_timestamp=NOW,
                reason_codes=("weighted_voting.test_gate_passed" if passed else "weighted_voting.test_gate_failed",),
                explanation="Synthetic local gate.",
            ),
        ),
        reason_codes=("weighted_voting.test_gate_passed" if passed else "weighted_voting.test_gate_failed",),
        explanation="Synthetic gate pipeline.",
    )


if __name__ == "__main__":
    unittest.main()
