from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.algorithms.voting_ensemble.paper_execution import (
    AlpacaPaperBrokerClient,
    VOTING_ENSEMBLE_ALGORITHM_ID,
    VotingEnsembleAlpacaPaperBrokerConfigurationError,
    VotingEnsembleDurableExecutionStateStore,
    VotingEnsemblePaperExecutionNamespaceError,
    VotingEnsemblePaperExecutionPersistenceError,
    VotingEnsemblePaperExecutionRepository,
    VotingEnsemblePaperExecutionRuntime,
    VotingEnsemblePaperExecutionWorker,
    VotingEnsemblePaperExecutionQueue,
)
from backend.app.algorithms.voting_ensemble.runtime.events import FinalizedOneMinuteBarEvent
from backend.app.algorithms.voting_ensemble.runtime.orchestrator import VotingEnsembleRuntimeOrchestrator
from backend.app.domain.models import OrderPlan, Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderGateway
from backend.app.execution.broker_reconciliation import BrokerFillUpdate, BrokerOrderAck
from backend.app.gates import BrokerAccountSnapshot, BrokerOrderState, BrokerPositionState


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)


class VotingEnsembleAutomaticPaperExecutionTest(unittest.TestCase):
    def test_finalized_bar_evaluation_hands_eligible_order_to_execution_worker(self) -> None:
        execution_runtime = RecordingExecutionRuntime()
        runtime = VotingEnsembleRuntimeOrchestrator(
            service=BuyDecisionService(),
            paper_execution_runtime=execution_runtime,
            automatic_payload_builder=PassthroughAutomaticPayloadBuilder(),
            auto_start=False,
        )
        event = FinalizedOneMinuteBarEvent(
            symbol="SPY",
            barEndTimestamp=NOW,
            finalized=True,
            settingsHash="settings-a",
            evaluationPayload={"symbol": "SPY", "data_timestamp": NOW.isoformat(), "candles": [{"timestamp": NOW.isoformat(), "timeframe": "1Min"}]},
            correlationId="corr-1",
        )

        job = runtime.enqueue_finalized_bar_event(event)
        runtime.drain_in_process()
        completed = runtime.get_job(job["jobId"])

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(len(execution_runtime.enqueued), 1)
        self.assertEqual(execution_runtime.enqueued[0]["algorithm_id"], VOTING_ENSEMBLE_ALGORITHM_ID)
        self.assertTrue(completed["result"]["paperExecution"]["enqueued"])
        self.assertIn("voting_ensemble.runtime.finalized_bar_order_intent_handed_to_execution_worker", completed["result"]["reasonCodes"])

    def test_manual_evaluation_never_hands_order_to_execution_worker(self) -> None:
        execution_runtime = RecordingExecutionRuntime()
        runtime = VotingEnsembleRuntimeOrchestrator(
            service=BuyDecisionService(),
            paper_execution_runtime=execution_runtime,
            auto_start=False,
        )

        job = runtime.enqueue_manual_evaluation({"symbol": "SPY", "data_timestamp": NOW.isoformat(), "candles": [candle()]})
        runtime.drain_in_process()

        self.assertEqual(runtime.get_job(job["jobId"])["status"], "completed")
        self.assertEqual(execution_runtime.enqueued, [])

    def test_finalized_bar_without_backend_snapshot_builder_fails_closed(self) -> None:
        execution_runtime = RecordingExecutionRuntime()
        runtime = VotingEnsembleRuntimeOrchestrator(
            service=ExplodingService(),
            paper_execution_runtime=execution_runtime,
            auto_start=False,
        )
        event = FinalizedOneMinuteBarEvent(
            symbol="SPY",
            barEndTimestamp=NOW,
            finalized=True,
            settingsHash="settings-a",
            evaluationPayload={
                "symbol": "SPY",
                "data_timestamp": NOW.isoformat(),
                "candles": [candle()],
                "accountRiskSnapshot": {"equity": 999999},
                "tradingEnabled": True,
                "marketOpen": True,
            },
            correlationId="corr-builder-missing",
        )

        job = runtime.enqueue_finalized_bar_event(event)
        runtime.drain_in_process()
        completed = runtime.get_job(job["jobId"])

        self.assertEqual(completed["status"], "completed")
        decision = completed["result"]["decision"]
        self.assertEqual(decision["final_signal"], "Hold")
        self.assertEqual(decision["order_plan"]["quantity"], 0)
        self.assertFalse(completed["result"]["paperExecution"]["enqueued"])
        self.assertEqual(execution_runtime.enqueued, [])
        self.assertIn("voting_ensemble.runtime.automatic_snapshot_builder_missing", decision["reason_codes"])

    def test_execution_worker_submits_paper_order_and_repository_stamps_owned_records(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        queue = VotingEnsemblePaperExecutionQueue()
        broker = FakePaperBroker(fill_status="FILLED", filled_quantity=3)
        gateway = PaperOrderGateway(broker, repository)
        execution_runtime = VotingEnsemblePaperExecutionRuntime(repository=repository, queue=queue, paper_gateway=gateway, auto_start=False)

        enqueued = execution_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-2",
            idempotency_key="idem-2",
            source_job_id="job-2",
            source_command_id="cmd-2",
            evaluated_at=NOW,
        )
        result = execution_runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))

        self.assertTrue(enqueued["enqueued"])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result["submitted"])
        self.assertEqual(broker.submit_count, 1)
        self.assertTrue(all(record["algorithm_id"] == VOTING_ENSEMBLE_ALGORITHM_ID for record in repository.snapshots.values()))
        self.assertTrue(any(key.startswith("voting_ensemble.paper_gateway.paper_order_gateway.intent.") for key in repository.snapshots))
        self.assertTrue(any(key.startswith("voting_ensemble.paper_gateway.paper_order_gateway.fill.") for key in repository.snapshots))

    def test_decision_and_execution_intent_are_persisted_before_submission(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        broker = FakePaperBroker()
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(broker, repository),
            auto_start=False,
        )

        enqueued = execution_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-persist",
            idempotency_key="idem-persist",
            source_job_id="job-persist",
            source_command_id="cmd-persist",
            evaluated_at=NOW,
        )
        inventory = execution_runtime.inventory_snapshot()

        self.assertTrue(enqueued["enqueued"])
        self.assertEqual(broker.submit_count, 0)
        self.assertEqual(inventory["decisions"][0]["decisionId"], "ve-order-plan-1")
        self.assertEqual(inventory["outbox"][0]["status"], "PENDING")
        self.assertEqual(inventory["outbox"][0]["sourceCommandIdempotencyKey"], "idem-persist")
        self.assertTrue(inventory["outbox"][0]["executionIdempotencyKey"].startswith("voting_ensemble:execution:"))

    def test_durable_outbox_survives_restart_without_losing_pending_intent(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-execution-{uuid4().hex}.json"
        first_repository = VotingEnsemblePaperExecutionRepository(store_path)
        first_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=first_repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), first_repository),
            auto_start=False,
        )
        first_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-restart",
            idempotency_key="idem-restart",
            source_job_id="job-restart",
            source_command_id="cmd-restart",
            evaluated_at=NOW,
        )

        restarted_repository = VotingEnsemblePaperExecutionRepository(store_path)
        broker = FakePaperBroker()
        restarted_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=restarted_repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(broker, restarted_repository),
            auto_start=False,
        )
        result = restarted_runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))
        inventory = restarted_runtime.inventory_snapshot()

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result["submitted"])
        self.assertEqual(broker.submit_count, 1)
        self.assertIn(inventory["outbox"][0]["status"], {"ACCEPTED", "FILLED"})
        store_path.unlink(missing_ok=True)

    def test_uncertain_submitting_outbox_requires_reconciliation_after_restart(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-uncertain-{uuid4().hex}.json"
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            auto_start=False,
        )
        enqueued = runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-uncertain",
            idempotency_key="idem-uncertain",
            source_job_id="job-uncertain",
            source_command_id="cmd-uncertain",
            evaluated_at=NOW,
        )
        intent = repository.pending_intents()[0]
        repository.mark_outbox_status(intent, "SUBMITTING", reason_codes=("test.crash_after_submitting_state",))

        restarted_repository = VotingEnsemblePaperExecutionRepository(store_path)
        broker = FakePaperBroker()
        restarted_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=restarted_repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(broker, restarted_repository),
            auto_start=False,
        )
        result = restarted_runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))

        self.assertTrue(enqueued["enqueued"])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result["submitted"])
        self.assertEqual(result["status"], "RECONCILIATION_REQUIRED")
        self.assertEqual(broker.submit_count, 0)
        self.assertEqual(restarted_runtime.inventory_snapshot()["outbox"][0]["status"], "RECONCILIATION_REQUIRED")
        store_path.unlink(missing_ok=True)

    def test_non_finalized_commands_cannot_create_automatic_execution_intent(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            auto_start=False,
        )

        result = execution_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-manual",
            idempotency_key="idem-manual",
            source_job_id="job-manual",
            source_command_id="cmd-manual",
            evaluated_at=NOW,
            source_command_kind="manual_evaluation",
        )

        self.assertFalse(result["enqueued"])
        self.assertEqual(execution_runtime.inventory_snapshot()["outbox"], [])
        self.assertIn("voting_ensemble.paper_execution.non_finalized_command_cannot_create_automatic_intent", result["reasonCodes"])

    def test_alpaca_paper_broker_client_rejects_live_endpoint(self) -> None:
        with self.assertRaises(VotingEnsembleAlpacaPaperBrokerConfigurationError):
            AlpacaPaperBrokerClient(settings=FakeAlpacaSettings("https://api.alpaca.markets/v2"), http_client=FakeAlpacaHttpClient())

    def test_alpaca_paper_broker_client_uses_real_paper_order_and_broker_state_endpoints(self) -> None:
        http_client = FakeAlpacaHttpClient()
        client = AlpacaPaperBrokerClient(settings=FakeAlpacaSettings("https://paper-api.alpaca.markets/v2"), http_client=http_client)
        plan = order_plan()

        account = client.refresh_account_snapshot()
        clock = client.refresh_market_clock()
        tradable = client.verify_symbol_tradable("SPY")
        buying_power = client.verify_buying_power(plan)
        ack = client.submit_order(plan, "ve-client-paper")
        status = client.refresh_order_status("ve-client-paper")
        fill = client.refresh_order("ve-client-paper")
        open_orders = client.refresh_open_orders()
        positions = client.refresh_positions()
        fills = client.retrieve_fills(after=NOW - timedelta(minutes=1))
        protective = client.submit_protective_order(
            symbol="SPY",
            side=Signal.BUY,
            quantity=1,
            stop_price=99.0,
            target_price=101.0,
            client_order_id="ve-protective-paper",
        )
        replaced = client.replace_order("ve-client-paper", limit_price=100.05)
        canceled = client.cancel_order("ve-client-paper")

        self.assertEqual(account.accountId, "paper-account")
        self.assertTrue(clock["isOpen"])
        self.assertTrue(tradable)
        self.assertTrue(buying_power)
        self.assertEqual(ack.status, "ACCEPTED")
        self.assertEqual(status, "PARTIALLY_FILLED")
        self.assertEqual(fill.filledQuantity, 1)
        self.assertEqual(open_orders[0].clientOrderId, "ve-client-paper")
        self.assertEqual(positions[0].algorithmId, VOTING_ENSEMBLE_ALGORITHM_ID)
        self.assertEqual(fills[0].filledQuantity, 1)
        self.assertEqual(protective.status, "ACCEPTED")
        self.assertEqual(replaced["client_order_id"], "ve-client-paper")
        self.assertTrue(canceled)
        self.assertTrue(any(call["method"] == "POST" and call["url"].endswith("/orders") for call in http_client.calls))

    def test_paper_inventory_snapshot_reports_backend_orders_fills_and_positions(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        broker = FakePaperBroker(fill_status="FILLED", filled_quantity=3)
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(broker, repository),
            auto_start=False,
        )

        execution_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-inventory",
            idempotency_key="idem-inventory",
            source_job_id="job-inventory",
            source_command_id="cmd-inventory",
            evaluated_at=NOW,
        )
        execution_runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))
        inventory = execution_runtime.inventory_snapshot()

        self.assertEqual(inventory["algorithm_id"], VOTING_ENSEMBLE_ALGORITHM_ID)
        self.assertEqual(len(inventory["orders"]), 1)
        self.assertEqual(len(inventory["fills"]), 1)
        self.assertEqual(inventory["positions"][0]["symbol"], "SPY")
        self.assertEqual(inventory["positions"][0]["quantity"], 3)
        self.assertTrue(inventory["orders"][0]["snapshotKey"].startswith("voting_ensemble.paper_gateway.paper_order_gateway.intent."))

    def test_automatic_broker_client_execution_state_is_durable_and_reloadable(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-execution-state-{uuid4().hex}.json"
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        broker_client = FakePaperBrokerClient(
            fill=BrokerFillUpdate(
                clientOrderId="placeholder",
                filledQuantity=2,
                averageFillPrice=100.04,
                status="PARTIALLY_FILLED",
                updatedAt=NOW + timedelta(seconds=2),
            )
        )
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            broker_client=broker_client,
            auto_start=False,
        )

        enqueued = execution_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({"settingsHash": "settings-state"}),
            correlation_id="corr-state",
            idempotency_key="idem-state",
            source_job_id="job-state",
            source_command_id="event-state",
            evaluated_at=NOW,
        )
        result = execution_runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))
        reloaded_repository = VotingEnsemblePaperExecutionRepository(store_path)
        reloaded_store = VotingEnsembleDurableExecutionStateStore(reloaded_repository)
        execution_states = reloaded_repository.inventory_snapshot()["executionStates"]

        self.assertTrue(enqueued["enqueued"])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(broker_client.submit_count, 1)
        self.assertEqual(len(execution_states), 1)
        state_record = execution_states[0]
        self.assertEqual(state_record["algorithm_id"], VOTING_ENSEMBLE_ALGORITHM_ID)
        self.assertEqual(state_record["brokerOrderId"], f"broker-{result['clientOrderId']}")
        self.assertEqual(state_record["idempotencyKey"], enqueued["executionIdempotencyKey"])
        self.assertEqual(state_record["parentDecisionId"], "ve-order-plan-1")
        self.assertEqual(state_record["parentEventId"], "event-state")
        self.assertEqual(state_record["settingsHash"], "order-config")
        self.assertEqual(state_record["symbol"], "SPY")
        self.assertEqual(state_record["side"], "BUY")
        self.assertEqual(state_record["requestedQuantity"], 3)
        self.assertEqual(state_record["filledQuantity"], 2)
        self.assertEqual(state_record["averageFillPrice"], 100.04)
        self.assertEqual(state_record["entryOrderStatus"], "ACCEPTED")
        self.assertEqual(state_record["stopPrice"], 99.0)
        self.assertEqual(state_record["targetPrice"], 101.5)
        self.assertIn("voting_ensemble.execution_adapter.partial_fill_tracked", state_record["completeReasonCodes"])
        self.assertIsNotNone(reloaded_store.get(result["clientOrderId"]))
        store_path.unlink(missing_ok=True)

    def test_automatic_broker_client_requires_durable_store_and_persistence_failure_blocks_entries(self) -> None:
        memory_repository = VotingEnsemblePaperExecutionRepository()
        with self.assertRaises(VotingEnsemblePaperExecutionPersistenceError):
            VotingEnsemblePaperExecutionRuntime(
                repository=memory_repository,
                queue=VotingEnsemblePaperExecutionQueue(),
                paper_gateway=PaperOrderGateway(FakePaperBroker(), memory_repository),
                broker_client=FakePaperBrokerClient(),
                auto_start=False,
            )

        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-persistence-failure-{uuid4().hex}.json"
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        repository.record_persistence_failure(RuntimeError("disk full"))
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            auto_start=False,
        )

        permission = execution_runtime._entry_permission()
        summary = execution_runtime.summary()

        self.assertFalse(permission["newEntriesAllowed"])
        self.assertFalse(summary["persistenceHealthy"])
        self.assertEqual(summary["highSeverityRuntimeWarnings"][0]["severity"], "HIGH")
        self.assertIn("voting_ensemble.paper_execution.persistence_failure_blocks_new_entries", permission["reasonCodes"])
        store_path.unlink(missing_ok=True)

    def test_broker_confirmed_fill_is_required_before_local_position_exists(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-no-fill-position-{uuid4().hex}.json"
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        broker_client = FakePaperBrokerClient(fill=None)
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            broker_client=broker_client,
            auto_start=False,
        )

        execution_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-no-fill",
            idempotency_key="idem-no-fill",
            source_job_id="job-no-fill",
            source_command_id="event-no-fill",
            evaluated_at=NOW,
        )
        execution_runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))
        inventory = execution_runtime.inventory_snapshot()

        self.assertEqual(inventory["positions"], [])
        self.assertEqual(inventory["fills"], [])
        self.assertEqual(broker_client.submit_count, 1)
        store_path.unlink(missing_ok=True)

    def test_partial_fills_submit_and_resize_actual_protective_orders(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-protection-{uuid4().hex}.json"
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        broker_client = FakePaperBrokerClient(
            fill=BrokerFillUpdate(
                clientOrderId="placeholder",
                filledQuantity=1,
                averageFillPrice=100.02,
                status="PARTIALLY_FILLED",
                updatedAt=NOW + timedelta(seconds=2),
            )
        )
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            broker_client=broker_client,
            auto_start=False,
        )

        execution_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-protect",
            idempotency_key="idem-protect",
            source_job_id="job-protect",
            source_command_id="event-protect",
            evaluated_at=NOW,
        )
        first = execution_runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))
        broker_client.fill = BrokerFillUpdate(
            clientOrderId="placeholder",
            filledQuantity=3,
            averageFillPrice=100.03,
            status="FILLED",
            updatedAt=NOW + timedelta(seconds=10),
        )
        execution_runtime.reconcile_broker_state(evaluated_at=NOW + timedelta(seconds=10))
        inventory = execution_runtime.inventory_snapshot()

        self.assertIsNotNone(first)
        self.assertEqual(broker_client.protective_orders[0]["quantity"], 1)
        self.assertEqual(broker_client.replacements[0]["quantity"], 3)
        self.assertEqual(inventory["protectiveOrders"][0]["quantity"], 3)
        self.assertEqual(inventory["positions"][0]["quantity"], 3)
        self.assertLessEqual(inventory["protectiveOrders"][0]["quantity"], inventory["fills"][0]["filledQuantity"])
        store_path.unlink(missing_ok=True)

    def test_paper_off_still_allows_position_reducing_sell_exit(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-off-exit-{uuid4().hex}.json"
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        broker_client = FakePaperBrokerClient(
            fill=BrokerFillUpdate(
                clientOrderId="placeholder",
                filledQuantity=3,
                averageFillPrice=100.02,
                status="FILLED",
                updatedAt=NOW + timedelta(seconds=2),
            )
        )
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            broker_client=broker_client,
            entry_permission_provider=lambda: {"newEntriesAllowed": False, "effectivePaperTradingEnabled": False, "reasonCodes": ["paper.off"]},
            auto_start=False,
        )
        repository.upsert_broker_fill(
            BrokerFillUpdate(clientOrderId="ve-existing-long", filledQuantity=3, averageFillPrice=100.0, status="FILLED", updatedAt=NOW),
            order_plan=order_plan(),
            order_intent_id="existing",
            observed_at=NOW,
        )

        enqueued = execution_runtime.enqueue_from_decision(
            {"algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID, "final_signal": "Sell", "safety_gate_failed": False, "order_plan": order_plan(side=Signal.SELL).model_dump(mode="json")},
            correlation_id="corr-exit",
            idempotency_key="idem-exit",
            source_job_id="job-exit",
            source_command_id="event-exit",
            evaluated_at=NOW,
        )
        result = execution_runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))

        self.assertTrue(enqueued["enqueued"])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result["submitted"])
        self.assertEqual(broker_client.submit_count, 1)
        store_path.unlink(missing_ok=True)

    def test_short_entries_are_blocked_when_voting_ensemble_short_trading_is_disabled(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-short-block-{uuid4().hex}.json"
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            broker_client=FakePaperBrokerClient(),
            short_trading_enabled=False,
            auto_start=False,
        )

        result = execution_runtime.enqueue_from_decision(
            {"algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID, "final_signal": "Sell", "safety_gate_failed": False, "order_plan": order_plan(side=Signal.SELL).model_dump(mode="json")},
            correlation_id="corr-short",
            idempotency_key="idem-short",
            source_job_id="job-short",
            source_command_id="event-short",
            evaluated_at=NOW,
        )

        self.assertFalse(result["enqueued"])
        self.assertIn("voting_ensemble.paper_execution.short_entries_disabled", result["reasonCodes"])
        store_path.unlink(missing_ok=True)

    def test_startup_reconciliation_reads_broker_state_and_blocks_unattributed_position(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-startup-reconcile-{uuid4().hex}.json"
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        broker_client = FakePaperBrokerClient()
        broker_client.positions = [
            BrokerPositionState(
                algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
                symbol="SPY",
                side=Signal.BUY,
                quantity=5,
                averageEntryPrice=100.0,
                markPrice=100.2,
                realizedPnlToday=0.0,
                openedAt=NOW,
            )
        ]
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            broker_client=broker_client,
            auto_start=False,
        )

        execution_runtime.start()
        execution_runtime.stop()
        inventory = execution_runtime.inventory_snapshot()
        blocked = execution_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-ambiguous",
            idempotency_key="idem-ambiguous",
            source_job_id="job-ambiguous",
            source_command_id="event-ambiguous",
            evaluated_at=NOW,
        )

        self.assertGreaterEqual(broker_client.account_read_count, 1)
        self.assertGreaterEqual(broker_client.open_order_read_count, 1)
        self.assertGreaterEqual(broker_client.position_read_count, 1)
        self.assertEqual(inventory["brokerPositions"], [])
        self.assertIn("voting_ensemble.paper_execution.unattributed_broker_position_not_claimed", inventory["reconciliationBlocks"][0]["reasonCodes"])
        self.assertFalse(blocked["enqueued"])
        self.assertIn("voting_ensemble.paper_execution.unattributed_broker_position_not_claimed", blocked["reasonCodes"])
        store_path.unlink(missing_ok=True)

    def test_reconciliation_recovers_position_only_when_attributed_to_voting_ensemble_fill(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-attributed-reconcile-{uuid4().hex}.json"
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        repository.upsert_broker_fill(
            BrokerFillUpdate(clientOrderId="ve-attributed-entry", filledQuantity=5, averageFillPrice=100.0, status="FILLED", updatedAt=NOW),
            order_plan=order_plan(),
            order_intent_id="attributed",
            observed_at=NOW,
        )
        broker_client = FakePaperBrokerClient()
        broker_client.positions = [
            BrokerPositionState(
                algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
                symbol="SPY",
                side=Signal.BUY,
                quantity=5,
                averageEntryPrice=100.0,
                markPrice=100.2,
                realizedPnlToday=0.0,
                openedAt=NOW,
            )
        ]
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            broker_client=broker_client,
            auto_start=False,
        )

        result = execution_runtime.reconcile_broker_state(evaluated_at=NOW + timedelta(seconds=1))
        inventory = execution_runtime.inventory_snapshot()

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["status"], "RECONCILED")
        self.assertEqual(inventory["brokerPositions"][0]["quantity"], 5)
        self.assertFalse(inventory["reconciliationBlocks"])
        store_path.unlink(missing_ok=True)

    def test_repository_rejects_foreign_algorithm_records(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()

        with self.assertRaises(VotingEnsemblePaperExecutionNamespaceError):
            repository.write_snapshot("paper_order_gateway.intent.foreign", {"algorithmId": "weighted_voting", "orderIntentId": "foreign"})

    def test_finalized_bar_event_rejects_non_spy_or_non_one_minute(self) -> None:
        with self.assertRaisesRegex(ValueError, "SPY"):
            FinalizedOneMinuteBarEvent(
                symbol="QQQ",
                barEndTimestamp=NOW,
                finalized=True,
                evaluationPayload={"symbol": "QQQ", "timeframe": "1Min"},
            ).to_command()
        with self.assertRaisesRegex(ValueError, "one-minute"):
            FinalizedOneMinuteBarEvent(
                symbol="SPY",
                barEndTimestamp=NOW,
                finalized=True,
                evaluationPayload={"symbol": "SPY", "timeframe": "5Min"},
            ).to_command()


class RecordingExecutionRuntime:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, Any]] = []

    def enqueue_from_decision(self, decision: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.enqueued.append({"algorithm_id": decision["algorithm_id"], **kwargs})
        return {
            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "orderIntentId": "ve-intent-recording",
            "enqueued": True,
            "deduplicated": False,
            "reasonCodes": ["voting_ensemble.paper_execution.intent_enqueued"],
        }


class PassthroughAutomaticPayloadBuilder:
    def build(self, command: Any) -> dict[str, Any]:
        return dict(command.payload)


class BuyDecisionService:
    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "final_signal": "Buy",
            "safety_gate_failed": False,
            "order_plan": order_plan().model_dump(mode="json"),
            "reason_codes": ["test.buy"],
        }


class ExplodingService:
    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("automatic finalized-bar evaluation must fail closed before service evaluation")


class FakeAlpacaSettings:
    def __init__(self, base_url: str) -> None:
        self.alpaca_trading_base_url = base_url
        self.alpaca_key_id = "paper-key"
        self.alpaca_secret_key = "paper-secret"

    @property
    def has_alpaca_credentials(self) -> bool:
        return True


class FakeAlpacaHttpClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> "FakeAlpacaResponse":
        self.calls.append({"method": "GET", "url": url, "kwargs": kwargs})
        if url.endswith("/account"):
            return FakeAlpacaResponse({"id": "paper-account", "equity": "100000", "buying_power": "90000"})
        if url.endswith("/clock"):
            return FakeAlpacaResponse({"is_open": True, "timestamp": NOW.isoformat().replace("+00:00", "Z")})
        if url.endswith("/assets/SPY"):
            return FakeAlpacaResponse({"symbol": "SPY", "status": "active", "tradable": True})
        if url.endswith("/orders"):
            return FakeAlpacaResponse([alpaca_order_payload()])
        if url.endswith("/positions"):
            return FakeAlpacaResponse([{"symbol": "SPY", "qty": "1", "side": "long", "avg_entry_price": "100", "current_price": "100.1"}])
        if url.endswith("/account/activities/FILL"):
            return FakeAlpacaResponse([{"client_order_id": "ve-client-paper", "qty": "1", "price": "100.01", "transaction_time": NOW.isoformat().replace("+00:00", "Z")}])
        if url.endswith("/orders:by_client_order_id"):
            return FakeAlpacaResponse(alpaca_order_payload())
        return FakeAlpacaResponse({})

    def post(self, url: str, **kwargs: Any) -> "FakeAlpacaResponse":
        self.calls.append({"method": "POST", "url": url, "kwargs": kwargs})
        client_order_id = str((kwargs.get("json") or {}).get("client_order_id") or "ve-client-paper")
        return FakeAlpacaResponse({**alpaca_order_payload(client_order_id=client_order_id), "status": "accepted", "filled_qty": "0"})

    def patch(self, url: str, **kwargs: Any) -> "FakeAlpacaResponse":
        self.calls.append({"method": "PATCH", "url": url, "kwargs": kwargs})
        return FakeAlpacaResponse(alpaca_order_payload())

    def delete(self, url: str, **kwargs: Any) -> "FakeAlpacaResponse":
        self.calls.append({"method": "DELETE", "url": url, "kwargs": kwargs})
        return FakeAlpacaResponse({}, status_code=204)


class FakeAlpacaResponse:
    def __init__(self, payload: Any, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        return None


def alpaca_order_payload(*, client_order_id: str = "ve-client-paper") -> dict[str, Any]:
    return {
        "id": "broker-order-1",
        "client_order_id": client_order_id,
        "symbol": "SPY",
        "side": "buy",
        "type": "limit",
        "status": "partially_filled",
        "qty": "3",
        "filled_qty": "1",
        "filled_avg_price": "100.01",
        "limit_price": "100.0",
        "submitted_at": NOW.isoformat().replace("+00:00", "Z"),
        "updated_at": NOW.isoformat().replace("+00:00", "Z"),
        "filled_at": NOW.isoformat().replace("+00:00", "Z"),
    }


class FakePaperBrokerClient:
    broker_kind = "alpaca_paper_client"
    paper_endpoint = True
    configured = True

    def __init__(self, *, fill: BrokerFillUpdate | None = None) -> None:
        self.fill = fill
        self.submit_count = 0
        self.orders: list[BrokerOrderState] = []
        self.positions: list[BrokerPositionState] = []
        self.protective_orders: list[dict[str, Any]] = []
        self.replacements: list[dict[str, Any]] = []
        self.exit_orders: list[dict[str, Any]] = []
        self.clock: dict[str, Any] = {"isOpen": True, "sourceAuthority": "test_broker_clock"}
        self.account_read_count = 0
        self.open_order_read_count = 0
        self.position_read_count = 0

    def refresh_market_clock(self) -> dict[str, Any]:
        return dict(self.clock)

    def refresh_account_snapshot(self) -> BrokerAccountSnapshot:
        self.account_read_count += 1
        return BrokerAccountSnapshot(
            accountId="paper",
            equity=100_000,
            buyingPower=100_000,
            observedAt=NOW,
            sessionDate=SESSION_DATE,
            sourceAuthority="broker",
            positionsReconciled=True,
            openOrdersReconciled=True,
        )

    def refresh_positions(self) -> list[BrokerPositionState]:
        self.position_read_count += 1
        return self.positions

    def refresh_open_orders(self) -> list[BrokerOrderState]:
        self.open_order_read_count += 1
        return self.orders

    def verify_symbol_tradable(self, symbol: str) -> bool:
        return symbol.upper() == "SPY"

    def verify_buying_power(self, order_plan: OrderPlan) -> bool:
        return True

    def submit_order(self, order_plan: OrderPlan, client_order_id: str) -> BrokerOrderAck:
        self.submit_count += 1
        if self.fill and self.fill.filledQuantity > 0:
            signed_side = order_plan.side
            self.positions = [
                BrokerPositionState(
                    algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
                    symbol=order_plan.symbol,
                    side=signed_side,
                    quantity=self.fill.filledQuantity,
                    averageEntryPrice=self.fill.averageFillPrice or order_plan.entryPrice,
                    markPrice=self.fill.averageFillPrice or order_plan.entryPrice,
                    realizedPnlToday=0.0,
                    openedAt=NOW,
                )
            ]
        self.orders.append(
            BrokerOrderState(
                algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
                symbol=order_plan.symbol,
                side=order_plan.side,
                clientOrderId=client_order_id,
                orderType=order_plan.orderType,
                status="ACCEPTED",
                quantity=order_plan.quantity,
                filledQuantity=self.fill.filledQuantity if self.fill else 0,
                entryPrice=order_plan.entryPrice,
                stopPrice=order_plan.stopPrice,
                submittedAt=NOW,
            )
        )
        return BrokerOrderAck(
            clientOrderId=client_order_id,
            brokerOrderId=f"broker-{client_order_id}",
            status="ACCEPTED",
            acceptedAt=NOW + timedelta(seconds=1),
        )

    def refresh_order(self, client_order_id: str) -> BrokerFillUpdate | None:
        return self.fill.model_copy(update={"clientOrderId": client_order_id}) if self.fill else None

    def refresh_order_status(self, client_order_id: str) -> str | None:
        return "FILLED" if self.fill and self.fill.status == "FILLED" else "PARTIALLY_FILLED" if self.fill else "ACCEPTED"

    def submit_protective_order(self, *, symbol: str, side: Signal | str, quantity: int, stop_price: float | None, target_price: float | None, client_order_id: str) -> BrokerOrderAck:
        payload = {
            "symbol": symbol,
            "side": Signal(side).value,
            "quantity": quantity,
            "stopPrice": stop_price,
            "targetPrice": target_price,
            "clientOrderId": client_order_id,
        }
        self.protective_orders.append(payload)
        return BrokerOrderAck(clientOrderId=client_order_id, brokerOrderId=f"broker-{client_order_id}", status="ACCEPTED", acceptedAt=NOW)

    def replace_order(self, client_order_id: str, *, limit_price: float | None = None, quantity: int | None = None, stop_price: float | None = None) -> dict[str, Any] | None:
        payload = {"id": f"broker-{client_order_id}", "client_order_id": client_order_id, "status": "accepted", "quantity": quantity, "stop_price": stop_price, "limit_price": limit_price}
        self.replacements.append(payload)
        return payload

    def submit_position_exit_order(self, *, symbol: str, side: Signal | str, quantity: int, limit_price: float, client_order_id: str) -> BrokerOrderAck:
        self.exit_orders.append({"symbol": symbol, "side": Signal(side).value, "quantity": quantity, "limitPrice": limit_price, "clientOrderId": client_order_id})
        return BrokerOrderAck(clientOrderId=client_order_id, brokerOrderId=f"broker-{client_order_id}", status="ACCEPTED", acceptedAt=NOW)


class FakePaperBroker:
    configured = True
    broker_kind = "alpaca_paper"
    paper_endpoint = True

    def __init__(self, *, fill_status: str = "ACCEPTED", filled_quantity: int = 0) -> None:
        self.fill_status = fill_status
        self.filled_quantity = filled_quantity
        self.submit_count = 0

    def verify_paper_account(self) -> bool:
        return True

    def submit_bracket_order(self, intent: Any) -> PaperGatewayBrokerAck:
        self.submit_count += 1
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=f"broker-{intent.clientOrderId}",
            status="ACCEPTED",
            acceptedAt=NOW,
        )

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        if self.filled_quantity <= 0:
            return None
        return PaperGatewayFill(
            clientOrderId=client_order_id,
            algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
            orderIntentId=client_order_id,
            symbol="SPY",
            side=Signal.BUY,
            filledQuantity=self.filled_quantity,
            averageFillPrice=100.01,
            status=self.fill_status,
            filledAt=NOW,
        )

    def cancel_order(self, client_order_id: str) -> bool:
        return True

    def refresh_positions(self) -> list[dict[str, Any]]:
        return []


def order_plan(*, side: Signal = Signal.BUY) -> OrderPlan:
    stop_price = 99.0 if side == Signal.BUY else 101.0
    target_price = 101.5 if side == Signal.BUY else 98.5
    return OrderPlan(
        orderPlanId="ve-order-plan-1",
        candidateId="ve-candidate-1",
        symbol="SPY",
        side=side,
        orderType="LIMIT",
        quantity=3,
        entryPrice=100.0,
        stopPrice=stop_price,
        targetPrice=target_price,
        limitPrice=100.0,
        maximumHoldingMinutes=30,
        timeInForce="DAY",
        eligible=True,
        validationErrors=[],
        explanation="test order",
        generatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="order-config",
    )


def candle() -> dict[str, Any]:
    return {
        "timestamp": NOW.isoformat(),
        "open": 100.0,
        "high": 100.2,
        "low": 99.9,
        "close": 100.1,
        "volume": 1000,
    }


if __name__ == "__main__":
    unittest.main()
