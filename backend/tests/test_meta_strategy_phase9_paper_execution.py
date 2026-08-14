import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from backend.app.algorithms.meta_strategy.alpaca_paper_broker import MetaStrategyAlpacaPaperBroker
from backend.app.algorithms.meta_strategy.execution import (
    MetaStrategyPaperOrderReconciliationWorker,
    MetaStrategyPaperOrderSubmissionWorker,
    MetaStrategyStaleOrderCancellationWorker,
    deterministic_meta_strategy_client_order_id,
)
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore, build_meta_strategy_settings
from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderGateway
from backend.app.gates import AppliedGlobalGateDecision, GlobalGateResponse, apply_global_gate_response
from backend.tests.meta_strategy.activation_fixtures import arm_automatic_paper_trading, readiness_report_ready


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)


def readiness_report_with(
    *,
    status: str = "OK",
    complete: bool = True,
    paper_ready: bool = True,
    runtime_ready: bool = True,
    paper_orders_blocked: bool = False,
    prerequisite_overrides: dict | None = None,
) -> dict[str, object]:
    report = readiness_report_ready()
    prerequisites = dict(report["paperEntryReadinessPrerequisites"])
    prerequisites.update(prerequisite_overrides or {})
    report.update(
        {
            "status": status,
            "complete": complete,
            "paperReady": paper_ready,
            **prerequisites,
            "paperEntryReadinessPrerequisites": prerequisites,
            "operationalPrerequisites": prerequisites,
            "runtimeSupervisor": {
                **dict(report["runtimeSupervisor"]),
                "ready": runtime_ready,
                "status": "ready" if runtime_ready else "unavailable",
            },
            "currentShadowPaperStatus": {
                "paperOrdersBlocked": paper_orders_blocked,
                "liveExecutionEnabled": False,
            },
        }
    )
    return report


class MetaStrategyPhase9PaperExecutionTest(unittest.TestCase):
    def test_crash_before_broker_submission_recovers_without_losing_outbox(self) -> None:
        env = RuntimeEnv()
        env.create_outbox()
        claimed = env.jobs.claim_next_execution_outbox(worker_id="crashed-before-submit", lease_seconds=10, now=NOW)

        worker = env.submission_worker()
        worker.run_once(now=NOW + timedelta(seconds=11))

        outbox = env.jobs.outbox_for_order_intent("intent-1")
        self.assertEqual(claimed["status"], "SUBMITTING")
        self.assertEqual(env.broker.submit_count, 1)
        self.assertEqual(outbox["status"], "ACKNOWLEDGED")
        self.assertEqual(env.inventory.current_inventory_snapshot().open_positions, ())

    def test_crash_after_submission_before_ack_persistence_is_recovered_by_reconciliation(self) -> None:
        env = RuntimeEnv(broker=FakePaperBroker(crash_after_submit=True))
        env.create_outbox()
        first = env.submission_worker().run_once(now=NOW)

        env.broker.crash_after_submit = False
        env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=30))

        outbox = env.jobs.outbox_for_order_intent("intent-1")
        self.assertEqual(first["status"], "RECONCILIATION_REQUIRED")
        self.assertEqual(env.broker.submit_count, 1)
        self.assertEqual(outbox["status"], "ACKNOWLEDGED")
        self.assertEqual(outbox["brokerOrderId"], "broker-intent-1")
        transitions = env.jobs.execution_outbox_transitions(outbox["outboxId"])
        self.assertIn("RECONCILIATION_REQUIRED", {transition["nextStatus"] for transition in transitions})

    def test_submission_timeout_followed_by_later_broker_discovery_does_not_create_fill(self) -> None:
        env = RuntimeEnv(broker=FakePaperBroker(timeout_after_submit=True))
        env.create_outbox()

        timeout_result = env.submission_worker().run_once(now=NOW)
        timed_out = env.jobs.outbox_for_order_intent("intent-1")
        self.assertEqual(timeout_result["status"], "RECONCILIATION_REQUIRED")
        self.assertEqual(timed_out["status"], "RECONCILIATION_REQUIRED")
        self.assertEqual(env.inventory.current_inventory_snapshot().reserved_risk_dollars, 100.0)
        env.broker.timeout_after_submit = False
        env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=20))

        outbox = env.jobs.outbox_for_order_intent("intent-1")
        snapshot = env.inventory.current_inventory_snapshot()
        self.assertEqual(outbox["status"], "ACKNOWLEDGED")
        self.assertEqual(snapshot.open_positions, ())
        self.assertEqual(env.jobs.broker_event_count(), 1)

    def test_duplicate_submission_retry_submits_one_logical_order(self) -> None:
        env = RuntimeEnv()
        env.create_outbox()
        env.submission_worker().run_once(now=NOW)
        duplicate = env.submission_worker().run_once(now=NOW + timedelta(seconds=1))

        self.assertIsNone(duplicate)
        self.assertEqual(env.broker.submit_count, 1)
        self.assertEqual(env.jobs.outbox_for_order_intent("intent-1")["clientOrderId"], env.broker.submitted_client_ids[0])

    def test_duplicate_worker_claim_cannot_submit_same_outbox_twice(self) -> None:
        env = RuntimeEnv()
        env.create_outbox()

        first = env.jobs.claim_next_execution_outbox(worker_id="worker-1", lease_seconds=300, now=NOW)
        second = env.jobs.claim_next_execution_outbox(worker_id="worker-2", lease_seconds=300, now=NOW)

        transitions = env.jobs.execution_outbox_transitions(first["outboxId"])
        self.assertEqual(first["status"], "SUBMITTING")
        self.assertIsNone(second)
        self.assertEqual(env.broker.submit_count, 0)
        self.assertEqual(transitions[-1]["previousStatus"], "PENDING")
        self.assertEqual(transitions[-1]["nextStatus"], "SUBMITTING")

    def test_decision_order_intent_reservation_and_outbox_are_atomic_before_broker(self) -> None:
        env = RuntimeEnv()
        env.create_outbox(quantity=10, reserved_risk=100.0)

        outbox = env.jobs.outbox_for_order_intent("intent-1")
        snapshot = env.inventory.current_inventory_snapshot()
        transitions = env.jobs.execution_outbox_transitions(outbox["outboxId"])

        self.assertEqual(outbox["status"], "PENDING")
        self.assertTrue(outbox["payload"]["atomicPersistence"]["decisionRecordPersisted"])
        self.assertTrue(outbox["payload"]["atomicPersistence"]["riskReservationPersisted"])
        self.assertEqual(len(env.inventory.inventory_records("order_intents")), 1)
        self.assertEqual(snapshot.reserved_risk_dollars, 100.0)
        self.assertEqual(transitions[-1]["nextStatus"], "PENDING")
        self.assertEqual(env.broker.submit_count, 0)

    def test_crash_before_atomic_transaction_commit_leaves_no_decision_or_outbox(self) -> None:
        env = RuntimeEnv()
        job = env.jobs.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW, settings_version=env.settings.settings_version, now=NOW)
        claimed = env.jobs.claim_next_job(queue_name="finalised_bar_decisions", worker_id="decision-worker", now=NOW)
        event_id = env.jobs.read_payload(job.payload_reference)["payload"]["eventId"]
        event = env.jobs.event_by_id(event_id)

        with self.assertRaises(ValueError):
            env.jobs.persist_decision_atomic(
                job=claimed,
                event=event,
                decision_id="decision-crash-before-commit",
                payload={
                    "algorithmId": "meta_strategy",
                    "decisionId": "decision-crash-before-commit",
                    "eventId": event_id,
                    "jobId": claimed.job_id,
                    "symbol": "SPY",
                    "barEnd": NOW.isoformat(),
                    "settingsVersion": env.settings.settings_version,
                    "modelVersion": "phase9-model",
                    "decisionStatus": "ORDER_PROPOSED",
                },
                order_intent={
                    "algorithmId": "meta_strategy",
                    "capitalPartitionId": "meta_strategy.paper.default",
                    "mode": "PAPER",
                    "settingsVersion": env.settings.settings_version,
                    "decisionId": "decision-crash-before-commit",
                    "jobId": claimed.job_id,
                    "eventId": event_id,
                    "orderIntentId": "intent-crash-before-commit",
                    "symbol": "SPY",
                    "side": "BUY",
                    "quantity": 10,
                    "limitPrice": 100.0,
                    "reservedRiskDollars": 0.0,
                    "createdAt": NOW.isoformat(),
                    "timestamp": NOW.isoformat(),
                },
                now=NOW,
            )

        self.assertIsNone(env.jobs.decision_for_event(event_id))
        self.assertIsNone(env.jobs.outbox_for_decision("decision-crash-before-commit"))
        self.assertEqual(env.inventory.inventory_records("order_intents"), ())
        self.assertEqual(env.inventory.current_inventory_snapshot().reserved_risk_dollars, 0.0)

    def test_partial_fill_followed_by_cancellation_updates_fill_quantity_and_releases_risk(self) -> None:
        env = RuntimeEnv()
        env.create_outbox(quantity=10, reserved_risk=100.0)
        env.submission_worker().run_once(now=NOW)
        env.broker.events.append(env.broker.fill_event(quantity=4, status="PARTIALLY_FILLED", event_id="fill-partial"))
        env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=10))
        env.broker.events.append(env.broker.status_event(status="CANCELED", event_id="cancel-after-partial"))
        env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=20))

        snapshot = env.inventory.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        outbox = env.jobs.outbox_for_order_intent("intent-1")
        self.assertEqual(snapshot.open_positions[0].quantity, 4.0)
        self.assertEqual(snapshot.reserved_risk_dollars, 0.0)
        self.assertEqual(outbox["status"], "CANCELLED")

    def test_reconciliation_applies_partial_fills_incrementally_to_one_position(self) -> None:
        env = RuntimeEnv()
        env.create_outbox(quantity=100, reserved_risk=1000.0)
        env.submission_worker().run_once(now=NOW)
        first = {**env.broker.fill_event(quantity=30, status="PARTIALLY_FILLED", event_id="fill-30"), "averageFillPrice": 100.0}
        second = {**env.broker.fill_event(quantity=40, status="PARTIALLY_FILLED", event_id="fill-40"), "averageFillPrice": 101.0}
        final = {**env.broker.fill_event(quantity=30, status="FILLED", event_id="fill-30-final"), "averageFillPrice": 103.0}

        env.broker.events.append(first)
        env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=10))
        after_30 = env.inventory.current_inventory_snapshot(mark_prices={"SPY": 104.0})
        self.assertEqual(after_30.open_positions[0].quantity, 30.0)
        self.assertEqual(after_30.open_positions[0].average_price, 100.0)
        self.assertEqual(after_30.reserved_risk_dollars, 700.0)

        env.broker.events.append(second)
        env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=20))
        after_70 = env.inventory.current_inventory_snapshot(mark_prices={"SPY": 104.0})
        self.assertEqual(len(after_70.open_positions), 1)
        self.assertEqual(after_70.open_positions[0].quantity, 70.0)
        self.assertEqual(after_70.open_positions[0].average_price, round(((30 * 100.0) + (40 * 101.0)) / 70, 10))
        self.assertEqual(after_70.reserved_risk_dollars, 300.0)

        env.broker.events.append(final)
        env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=30))
        after_100 = env.inventory.current_inventory_snapshot(mark_prices={"SPY": 104.0})
        self.assertEqual(len(after_100.open_positions), 1)
        self.assertEqual(after_100.open_positions[0].quantity, 100.0)
        self.assertEqual(after_100.open_positions[0].average_price, 101.3)
        self.assertEqual(after_100.unrealised_pnl, 270.0)
        self.assertEqual(after_100.reserved_risk_dollars, 0.0)
        self.assertEqual(env.jobs.outbox_for_order_intent("intent-1")["status"], "FILLED")

    def test_conflicting_broker_fill_ownership_is_quarantined_without_position_change(self) -> None:
        env = RuntimeEnv()
        env.create_outbox(quantity=10, reserved_risk=100.0)
        env.submission_worker().run_once(now=NOW)
        env.broker.events.append(
            {
                **env.broker.fill_event(quantity=10, status="FILLED", event_id="fill-foreign-partition"),
                "capitalPartitionId": "meta_strategy.paper.other",
            }
        )

        result = env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=10))

        quarantine = env.inventory.inventory_records("quarantine", limit=5)[0]
        snapshot = env.inventory.current_inventory_snapshot()
        self.assertEqual(result["status"], "OK")
        self.assertEqual(quarantine["payload"]["quarantineReason"], "BROKER_EVENT_FOREIGN_PARTITION")
        self.assertEqual(quarantine["payload"]["observedCapitalPartitionId"], "meta_strategy.paper.other")
        self.assertEqual(snapshot.open_positions, ())
        self.assertEqual(snapshot.reserved_risk_dollars, 100.0)

    def test_duplicate_fill_event_is_idempotent(self) -> None:
        env = RuntimeEnv()
        env.create_outbox(quantity=10, reserved_risk=100.0)
        env.submission_worker().run_once(now=NOW)
        fill = env.broker.fill_event(quantity=3, status="PARTIALLY_FILLED", event_id="duplicate-fill")
        env.broker.events.extend([fill, fill])

        env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=10))
        env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=11))

        snapshot = env.inventory.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        self.assertEqual(snapshot.open_positions[0].quantity, 3.0)
        self.assertEqual(env.jobs.broker_event_count(), 2)

    def test_duplicate_broker_fill_id_with_new_event_id_is_idempotent(self) -> None:
        env = RuntimeEnv()
        env.create_outbox(quantity=10, reserved_risk=100.0)
        env.submission_worker().run_once(now=NOW)
        fill = env.broker.fill_event(quantity=3, status="PARTIALLY_FILLED", event_id="same-fill-id-first-event")
        duplicate = {**fill, "brokerEventId": "same-fill-id-second-event", "timestamp": (NOW + timedelta(seconds=1)).isoformat()}
        env.broker.events.extend([fill, duplicate])

        result = env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=10))

        snapshot = env.inventory.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(len(env.inventory.inventory_records("fills")), 1)
        self.assertEqual(snapshot.open_positions[0].quantity, 3.0)
        self.assertEqual(snapshot.reserved_risk_dollars, 70.0)
        self.assertEqual(snapshot.realised_pnl, 0.0)

    def test_reconciliation_matches_known_order_by_broker_order_id_without_frontend_state(self) -> None:
        env = RuntimeEnv()
        env.create_outbox(quantity=10, reserved_risk=100.0)
        env.submission_worker().run_once(now=NOW)
        outbox = env.jobs.outbox_for_order_intent("intent-1")
        env.broker.events.append(
            {
                "brokerEventId": "broker-id-only-fill",
                "brokerOrderId": outbox["brokerOrderId"],
                "brokerFillId": "broker-id-only-fill-id",
                "status": "FILLED",
                "symbol": "SPY",
                "side": "BUY",
                "filledQuantity": 10,
                "averageFillPrice": 100.0,
                "timestamp": NOW.isoformat(),
            }
        )

        result = env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=10))

        snapshot = env.inventory.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        self.assertEqual(result["quarantined"], 0)
        self.assertEqual(env.jobs.outbox_for_order_intent("intent-1")["status"], "FILLED")
        self.assertEqual(snapshot.open_positions[0].quantity, 10.0)

    def test_malformed_fill_missing_broker_fill_id_is_quarantined_without_position_change(self) -> None:
        env = RuntimeEnv()
        env.create_outbox(quantity=10, reserved_risk=100.0)
        env.submission_worker().run_once(now=NOW)
        outbox = env.jobs.outbox_for_order_intent("intent-1")
        env.broker.events.append(
            {
                "brokerEventId": "missing-fill-id-event",
                "brokerOrderId": outbox["brokerOrderId"],
                "status": "FILLED",
                "symbol": "SPY",
                "side": "BUY",
                "filledQuantity": 10,
                "averageFillPrice": 100.0,
                "timestamp": NOW.isoformat(),
            }
        )

        result = env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=10))

        quarantine = env.inventory.inventory_records("quarantine", limit=5)[0]
        snapshot = env.inventory.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["quarantined"], 1)
        self.assertEqual(quarantine["payload"]["quarantineReason"], "FILL_MALFORMED_MISSING_BROKER_FILL_ID")
        self.assertEqual(env.jobs.outbox_for_order_intent("intent-1")["status"], "ACKNOWLEDGED")
        self.assertEqual(snapshot.open_positions, ())
        self.assertEqual(snapshot.reserved_risk_dollars, 100.0)

    def test_rejected_order_releases_reserved_risk_without_position(self) -> None:
        env = RuntimeEnv(broker=FakePaperBroker(ack_status="REJECTED"))
        env.create_outbox(quantity=10, reserved_risk=100.0)

        env.submission_worker().run_once(now=NOW)

        snapshot = env.inventory.current_inventory_snapshot()
        outbox = env.jobs.outbox_for_order_intent("intent-1")
        self.assertEqual(outbox["status"], "REJECTED")
        self.assertEqual(snapshot.reserved_risk_dollars, 0.0)
        self.assertEqual(snapshot.open_positions, ())

    def test_stale_order_cancellation_worker_persists_cancel_evidence(self) -> None:
        env = RuntimeEnv(broker=FakePaperBroker(fill_on_submit=False))
        env.create_outbox()
        env.submission_worker().run_once(now=NOW)

        result = env.stale_worker().run_once(now=NOW + timedelta(minutes=10))

        outbox = env.jobs.outbox_for_order_intent("intent-1")
        transitions = env.jobs.execution_outbox_transitions(outbox["outboxId"])
        self.assertEqual(result["cancelled"], 1)
        self.assertEqual(outbox["status"], "CANCELLED")
        self.assertEqual(env.broker.cancel_count, 1)
        self.assertIn("CANCEL_PENDING", {transition["nextStatus"] for transition in transitions})
        self.assertIn("meta_strategy.execution.stale_order_cancelled", outbox["payload"]["reasonCodes"])

    def test_stale_worker_reconciles_fill_before_cancel_attempt(self) -> None:
        env = RuntimeEnv(broker=FakePaperBroker(fill_on_submit=False))
        env.create_outbox(quantity=10, reserved_risk=100.0)
        env.submission_worker().run_once(now=NOW)
        env.broker.events.append(env.broker.fill_event(quantity=10, status="FILLED", event_id="fill-before-stale-cancel"))

        result = env.stale_worker().run_once(now=NOW + timedelta(minutes=10))

        outbox = env.jobs.outbox_for_order_intent("intent-1")
        snapshot = env.inventory.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        self.assertEqual(result["cancelled"], 0)
        self.assertGreaterEqual(result["reconciliation"]["processed"], 1)
        self.assertEqual(outbox["status"], "FILLED")
        self.assertEqual(env.broker.cancel_count, 0)
        self.assertEqual(snapshot.open_positions[0].quantity, 10.0)
        self.assertEqual(snapshot.reserved_risk_dollars, 0.0)

    def test_restart_recovery_does_not_duplicate_submitted_order(self) -> None:
        database_url = f"sqlite:///{temp_db_path()}"
        broker = FakePaperBroker()
        jobs = MetaStrategyJobRepository(database_url)
        inventory = MetaStrategySqliteRepository(database_url)
        env = RuntimeEnv(jobs=jobs, inventory=inventory, broker=broker)
        env.create_outbox()
        MetaStrategyPaperOrderSubmissionWorker(
            repository=jobs,
            inventory_repository=inventory,
            paper_gateway=PaperOrderGateway(broker, jobs.gateway_store()),
            global_risk_source=AllowRisk(),
            settings_store=env.settings_store,
            readiness_report_source=readiness_report_ready,
        ).run_once(now=NOW)

        restarted_jobs = MetaStrategyJobRepository(database_url)
        restarted_inventory = MetaStrategySqliteRepository(database_url)
        recovered = MetaStrategyPaperOrderReconciliationWorker(repository=restarted_jobs, inventory_repository=restarted_inventory, paper_gateway=PaperOrderGateway(broker, restarted_jobs.gateway_store())).run_once(now=NOW + timedelta(seconds=30))

        self.assertEqual(broker.submit_count, 1)
        self.assertEqual(recovered["status"], "OK")
        self.assertEqual(restarted_jobs.outbox_for_order_intent("intent-1")["status"], "ACKNOWLEDGED")

    def test_broker_order_belonging_to_another_algorithm_is_quarantined(self) -> None:
        env = RuntimeEnv()
        env.broker.events.append(
            {
                "brokerEventId": "foreign-event",
                "algorithmId": "weighted_voting",
                "clientOrderId": "foreign-client",
                "brokerOrderId": "foreign-broker",
                "orderIntentId": "foreign-intent",
                "status": "FILLED",
                "symbol": "SPY",
                "side": "BUY",
                "filledQuantity": 5,
                "averageFillPrice": 100.0,
                "timestamp": NOW.isoformat(),
            }
        )

        result = env.reconciliation_worker().run_once(now=NOW)

        self.assertEqual(result["quarantined"], 1)
        self.assertEqual(env.inventory.current_inventory_snapshot().open_positions, ())

    def test_global_risk_rejection_and_resize_are_applied_before_submission(self) -> None:
        rejected = RuntimeEnv(global_risk=RejectRisk())
        rejected.create_outbox(quantity=10, reserved_risk=100.0)
        reject_result = rejected.submission_worker().run_once(now=NOW)

        resized = RuntimeEnv(global_risk=ResizeRisk(quantity=4, risk=40.0))
        resized.create_outbox(quantity=10, reserved_risk=100.0)
        resize_result = resized.submission_worker().run_once(now=NOW)

        self.assertEqual(reject_result["status"], "REJECTED")
        self.assertEqual(rejected.broker.submit_count, 0)
        self.assertEqual(rejected.inventory.current_inventory_snapshot().reserved_risk_dollars, 0.0)
        self.assertEqual(resize_result["status"], "ACKNOWLEDGED")
        self.assertEqual(resized.broker.last_quantity, 4)
        self.assertEqual(resized.inventory.current_inventory_snapshot().reserved_risk_dollars, 40.0)

    def test_new_entry_is_rejected_when_meta_strategy_paper_control_is_off(self) -> None:
        env = RuntimeEnv()
        env.create_outbox()
        env.jobs.update_paper_trading_control(
            new_paper_entries_enabled=False,
            updated_by="test",
            reason="meta_strategy.test.paper_off",
            now=NOW,
        )

        result = env.submission_worker().run_once(now=NOW)

        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("meta_strategy.paper_control.new_entry_blocked_before_submission", result["reasonCodes"])
        self.assertEqual(env.broker.submit_count, 0)

    def test_new_entry_is_rejected_when_authoritative_clock_is_closed(self) -> None:
        env = RuntimeEnv(broker=FakePaperBroker(market_open=False))
        env.create_outbox()

        result = env.submission_worker().run_once(now=NOW)

        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("meta_strategy.execution.market_closed", result["reasonCodes"])
        self.assertEqual(env.broker.submit_count, 0)

    def test_readiness_false_blocks_broker_call(self) -> None:
        env = RuntimeEnv(readiness_report_source=lambda: readiness_report_with(status="REJECTED", complete=False, paper_ready=False))
        env.create_outbox()

        result = env.submission_worker().run_once(now=NOW)

        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("meta_strategy.execution_guard.readiness_status_not_ok", result["reasonCodes"])
        self.assertIn("meta_strategy.execution_guard.readiness_incomplete", result["reasonCodes"])
        self.assertEqual(env.broker.submit_count, 0)

    def test_phase11_readiness_prerequisites_block_before_broker(self) -> None:
        cases = (
            ("runtime_worker_unhealthy", {"requiredWorkersHealthy": False}, "meta_strategy.readiness.worker_unhealthy"),
            ("queue_lag", {"queueLagBelowThreshold": False}, "meta_strategy.readiness.queue_lag_exceeded"),
            ("dead_letter", {"deadLetterWithinThreshold": False}, "meta_strategy.readiness.dead_letter_threshold_exceeded"),
            ("failed_reconstruction", {"restartReconstructionSucceeded": False}, "meta_strategy.readiness.restart_reconstruction_failed"),
            ("stale_inventory_reconciliation", {"inventoryReconciliationCurrent": False}, "meta_strategy.readiness.inventory_reconciliation_stale"),
        )
        for name, overrides, reason in cases:
            with self.subTest(name=name):
                env = RuntimeEnv(readiness_report_source=lambda overrides=overrides: readiness_report_with(prerequisite_overrides=overrides))
                env.create_outbox()

                result = env.submission_worker().run_once(now=NOW)

                self.assertEqual(result["status"], "REJECTED")
                self.assertIn(reason, result["reasonCodes"])
                self.assertEqual(env.broker.submit_count, 0)

    def test_readiness_becoming_false_after_decision_blocks_submission(self) -> None:
        env = RuntimeEnv()
        env.create_outbox()
        env.readiness_report_source = lambda: readiness_report_with(
            prerequisite_overrides={"authoritativeMarketDataHealthy": False}
        )

        result = env.submission_worker().run_once(now=NOW)

        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("meta_strategy.readiness.market_data_unhealthy", result["reasonCodes"])
        self.assertEqual(env.broker.submit_count, 0)

    def test_protective_exit_can_submit_when_entry_control_is_off(self) -> None:
        env = RuntimeEnv()
        env.record_open_position(quantity=10)
        env.create_outbox(extra_order_payload={"intent": "protective_exit", "orderIntentType": "protective_exit", "side": "SELL"})
        env.jobs.update_paper_trading_control(
            new_paper_entries_enabled=False,
            updated_by="test",
            reason="meta_strategy.test.paper_off",
            now=NOW,
        )

        result = env.submission_worker().run_once(now=NOW)

        self.assertEqual(result["status"], "ACKNOWLEDGED")
        self.assertEqual(env.broker.submit_count, 1)
        outbox = env.jobs.outbox_for_order_intent("intent-1")
        self.assertIn("meta_strategy.paper_control.protective_exit_allowed", outbox["payload"]["reasonCodes"])

    def test_protective_exit_can_submit_when_market_data_readiness_blocks_new_entries(self) -> None:
        env = RuntimeEnv(readiness_report_source=lambda: readiness_report_with(prerequisite_overrides={"authoritativeMarketDataHealthy": False}))
        env.record_open_position(quantity=10)
        env.create_outbox(extra_order_payload={"intent": "protective_exit", "orderIntentType": "protective_exit", "side": "SELL"})

        result = env.submission_worker().run_once(now=NOW)

        self.assertEqual(result["status"], "ACKNOWLEDGED")
        self.assertEqual(env.broker.submit_count, 1)
        outbox = env.jobs.outbox_for_order_intent("intent-1")
        self.assertIn("meta_strategy.paper_control.protective_exit_allowed", outbox["payload"]["reasonCodes"])

    def test_end_of_day_liquidation_can_submit_when_entry_control_is_off(self) -> None:
        env = RuntimeEnv()
        env.record_open_position(quantity=10)
        env.create_outbox(extra_order_payload={"intent": "end_of_day_liquidation", "orderIntentType": "end_of_day_liquidation", "side": "SELL"})
        env.jobs.update_paper_trading_control(
            new_paper_entries_enabled=False,
            updated_by="test",
            reason="meta_strategy.test.paper_off",
            now=NOW,
        )

        result = env.submission_worker().run_once(now=NOW)

        self.assertEqual(result["status"], "ACKNOWLEDGED")
        self.assertEqual(env.broker.submit_count, 1)

    def test_execution_guard_blocks_each_new_entry_condition_before_broker(self) -> None:
        cases = (
            ("runtime_disabled", lambda env: env.set_runtime(enabled=False), "meta_strategy.execution_guard.runtime_disabled"),
            ("runtime_shadow", lambda env: env.set_runtime(mode="SHADOW"), "meta_strategy.execution_guard.runtime_not_paper"),
            ("paper_toggle_off", lambda env: env.jobs.update_paper_trading_control(new_paper_entries_enabled=False, updated_by="test", reason="off", now=NOW), "meta_strategy.paper_control.new_entry_blocked_before_submission"),
            ("toggle_off_after_decision", lambda env: env.jobs.update_paper_trading_control(new_paper_entries_enabled=False, updated_by="test", reason="race-off", now=NOW + timedelta(seconds=1)), "meta_strategy.paper_control.new_entry_blocked_before_submission"),
            ("pause_new_entries", lambda env: env.write_control("PAUSE_NEW_ENTRIES", {"newEntriesPaused": True}), "meta_strategy.execution_guard.pause_new_entries_active"),
            ("exit_only", lambda env: env.write_control("EXIT_ONLY", {"exitOnly": True}), "meta_strategy.execution_guard.exit_only_active"),
            ("emergency_stop", lambda env: env.write_control("STOP_META_RUNTIME", {"runtimeStopRequested": True}), "meta_strategy.execution_guard.emergency_stop_active"),
            ("market_closed", lambda env: setattr(env.broker, "market_open", False), "meta_strategy.execution_guard.market_closed"),
            ("early_close", lambda env: setattr(env.broker, "next_close", NOW + timedelta(minutes=5)), "meta_strategy.execution_guard.early_close_boundary"),
            ("stale_broker_clock", lambda env: setattr(env.broker, "clock_captured_at", NOW - timedelta(minutes=2)), "meta_strategy.execution_guard.market_clock_stale"),
            ("broker_clock_unavailable", lambda env: setattr(env.broker, "clock_unavailable", True), "meta_strategy.execution.authoritative_market_clock_unavailable"),
            ("local_fallback_clock", lambda env: (setattr(env.broker, "clock_source", "meta_strategy.local_replay_calendar"), setattr(env.broker, "clock_authoritative", False)), "meta_strategy.execution_guard.market_clock_not_authoritative"),
            ("stale_quote", lambda env: env.create_outbox(extra_order_payload={"quoteTimestamp": (NOW - timedelta(seconds=61)).isoformat()}), "meta_strategy.execution_guard.quote_stale"),
            ("stale_intent", lambda env: env.create_outbox(extra_order_payload={"createdAt": (NOW - timedelta(seconds=301)).isoformat(), "timestamp": (NOW - timedelta(seconds=301)).isoformat()}), "meta_strategy.execution_guard.intent_stale"),
            ("stale_global_risk", lambda env: setattr(env, "global_risk", StaleRisk()), "meta_strategy.execution_guard.global_risk_approval_stale"),
            ("settings_changed", lambda env: env.activate_new_settings(), "meta_strategy.execution_guard.settings_version_changed"),
            ("zero_account_equity", lambda env: env.create_outbox(extra_order_payload={"accountEquity": 0.0}), "meta_strategy.sizing.zero_account_equity"),
            ("zero_algorithm_risk", lambda env: env.create_outbox(extra_order_payload={"remainingAlgorithmRisk": 0.0}), "meta_strategy.sizing.zero_algorithm_risk"),
            ("zero_global_risk", lambda env: setattr(env, "global_risk", ResizeRisk(quantity=10, risk=0.0)), "meta_strategy.execution_guard.zero_global_risk"),
            ("zero_buying_power", lambda env: env.create_outbox(extra_order_payload={"buyingPower": 0.0}), "meta_strategy.sizing.zero_buying_power"),
            ("missing_account_equity", lambda env: env.create_outbox(omit_order_fields=("accountEquity",)), "meta_strategy.sizing.account_equity_unavailable"),
            ("missing_buying_power", lambda env: env.create_outbox(omit_order_fields=("buyingPower",)), "meta_strategy.sizing.buying_power_unavailable"),
            ("missing_algorithm_risk", lambda env: env.create_outbox(omit_order_fields=("remainingAlgorithmRisk",)), "meta_strategy.sizing.algorithm_risk_unavailable"),
            ("duplicate_client_order_id", lambda env: env.write_duplicate_client_snapshot(), "meta_strategy.execution_guard.duplicate_client_order_id"),
            ("existing_position", lambda env: env.record_open_position(quantity=3), "meta_strategy.execution_guard.existing_position"),
            ("existing_open_entry_order", lambda env: env.create_existing_open_entry_order(), "meta_strategy.execution_guard.existing_open_entry_order"),
            ("wrong_capital_partition", lambda env: env.create_outbox(extra_order_payload={"capitalPartitionId": "weighted_voting.paper.default"}), "meta_strategy.execution_guard.wrong_capital_partition"),
        )
        for name, mutate, reason in cases:
            with self.subTest(name=name):
                env = RuntimeEnv()
                if name not in {
                    "stale_quote",
                    "stale_intent",
                    "zero_account_equity",
                    "zero_algorithm_risk",
                    "zero_buying_power",
                    "missing_account_equity",
                    "missing_buying_power",
                    "missing_algorithm_risk",
                    "wrong_capital_partition",
                }:
                    env.create_outbox()
                mutate(env)

                result = env.submission_worker().run_once(now=NOW)

                self.assertEqual(result["status"], "REJECTED")
                self.assertIn(reason, result["reasonCodes"])
                self.assertEqual(env.broker.submit_count, 0)
                outbox = env.jobs.outbox_for_order_intent("intent-1")
                self.assertIn("executionGuard", outbox["payload"])

    def test_duplicate_owned_position_has_explicit_meta_strategy_policy_reason(self) -> None:
        env = RuntimeEnv()
        env.create_outbox()
        env.record_open_position(quantity=3)

        result = env.submission_worker().run_once(now=NOW)

        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("meta_strategy.execution_guard.existing_meta_strategy_position", result["reasonCodes"])
        self.assertIn("meta_strategy.execution_guard.existing_position", result["reasonCodes"])
        self.assertEqual(env.broker.submit_count, 0)
        guard = env.jobs.outbox_for_order_intent("intent-1")["payload"]["executionGuard"]
        self.assertEqual(guard["evidence"]["metaStrategyPositionPolicy"]["source"], "meta_strategy_repository.current_inventory_snapshot")
        self.assertEqual(guard["evidence"]["metaStrategyPositionPolicy"]["action"], "BLOCK_DUPLICATE_META_STRATEGY_ENTRY")

    def test_pyramiding_setting_allows_add_to_owned_meta_strategy_position(self) -> None:
        env = RuntimeEnv()
        env.enable_pyramiding()
        env.record_open_position(quantity=3)
        env.create_outbox()

        result = env.submission_worker().run_once(now=NOW)

        self.assertEqual(result["status"], "ACKNOWLEDGED")
        self.assertEqual(env.broker.submit_count, 1)
        guard = env.jobs.outbox_for_order_intent("intent-1")["payload"]["executionGuard"]
        self.assertEqual(guard["evidence"]["metaStrategyPositionPolicy"]["action"], "ALLOW_ADD_TO_META_STRATEGY_POSITION")
        self.assertIn("meta_strategy.execution_guard.new_entry_allowed", guard["reasonCodes"])

    def test_protective_sell_requires_meta_strategy_owned_position(self) -> None:
        env = RuntimeEnv()
        env.create_outbox(
            quantity=3,
            extra_order_payload={
                "intent": "protective_exit",
                "orderIntentType": "protective_exit",
                "side": "SELL",
                "foreignBrokerPosition": {"algorithmId": "weighted_voting", "symbol": "SPY", "quantity": 3},
            },
        )

        result = env.submission_worker().run_once(now=NOW)

        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("meta_strategy.execution_guard.protective_order_not_risk_reducing", result["reasonCodes"])
        self.assertEqual(env.broker.submit_count, 0)
        self.assertEqual(env.inventory.current_inventory_snapshot().open_positions, ())

    def test_wrong_algorithm_outbox_is_rejected_before_broker(self) -> None:
        env = RuntimeEnv()
        env.create_outbox()
        env.corrupt_current_outbox_algorithm()

        result = env.submission_worker().run_once(now=NOW)

        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("meta_strategy.execution.foreign_outbox_rejected", result["reasonCodes"])
        self.assertEqual(env.broker.submit_count, 0)

    def test_missing_paper_control_state_fails_closed_before_submission(self) -> None:
        env = RuntimeEnv(arm_control=False)
        env.create_outbox()

        result = env.submission_worker().run_once(now=NOW)

        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("meta_strategy.paper_control.state_unavailable", result["reasonCodes"])
        self.assertEqual(env.broker.submit_count, 0)

    def test_paper_control_off_does_not_stop_reconciliation(self) -> None:
        env = RuntimeEnv(broker=FakePaperBroker(fill_on_submit=False))
        env.create_outbox()
        env.submission_worker().run_once(now=NOW)
        env.broker.events.append(env.broker.fill_event(quantity=2, status="PARTIALLY_FILLED", event_id="fill-while-off"))
        env.jobs.update_paper_trading_control(
            new_paper_entries_enabled=False,
            updated_by="test",
            reason="meta_strategy.test.paper_off_after_submit",
            now=NOW + timedelta(seconds=1),
        )

        result = env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=10))

        self.assertEqual(result["status"], "OK")
        self.assertEqual(env.inventory.current_inventory_snapshot(mark_prices={"SPY": 100.0}).open_positions[0].quantity, 2.0)

    def test_submission_uses_meta_strategy_partitioned_client_order_id(self) -> None:
        env = RuntimeEnv()
        env.create_outbox()

        env.submission_worker().run_once(now=NOW)

        self.assertEqual(env.broker.submit_count, 1)
        self.assertTrue(env.broker.submitted_client_ids[0].startswith("meta-strategy-meta-strategy-paper-defa-"))

    def test_order_policy_is_carried_to_paper_gateway_and_broker_intent(self) -> None:
        env = RuntimeEnv()
        env.create_outbox(
            extra_order_payload={
                "orderType": "STOP_LIMIT",
                "timeInForce": "GTC",
                "stopLimitPrice": 94.5,
                "cancelAndReplaceEnabled": True,
                "maximumOrderAgeSeconds": 120,
                "maximumReplacementCount": 2,
                "protectiveExitEscalationPolicy": "CANCEL_AND_MARKETABLE_LIMIT",
            }
        )

        env.submission_worker().run_once(now=NOW)

        self.assertEqual(env.broker.last_intent.orderType, "STOP_LIMIT")
        self.assertEqual(env.broker.last_intent.timeInForce, "GTC")
        self.assertEqual(env.broker.last_intent.stopLimitPrice, 94.5)
        self.assertTrue(env.broker.last_intent.cancelAndReplaceEnabled)
        self.assertEqual(env.broker.last_intent.maxReplacementCount, 2)

    def test_broker_ack_and_fill_enqueue_position_management(self) -> None:
        env = RuntimeEnv(broker=FakePaperBroker(fill_on_submit=True))
        env.create_outbox()

        env.submission_worker().run_once(now=NOW)

        status = env.jobs.queue_status(queue_name="position_management", now=NOW)
        self.assertGreaterEqual(status["queues"]["position_management"]["pending"], 1)

    def test_stale_order_replaces_only_with_configured_budget(self) -> None:
        env = RuntimeEnv(broker=FakePaperBroker(fill_on_submit=False))
        env.create_outbox(
            extra_order_payload={
                "cancelAndReplaceEnabled": True,
                "maximumReplacementCount": 1,
            }
        )
        env.submission_worker().run_once(now=NOW)

        result = env.stale_worker().run_once(now=NOW + timedelta(minutes=10))

        outbox = env.jobs.outbox_for_order_intent("intent-1")
        self.assertEqual(result["cancelled"], 0)
        self.assertEqual(env.broker.replace_count, 1)
        self.assertEqual(outbox["status"], "REPLACED")
        self.assertEqual(env.inventory.current_inventory_snapshot().reserved_risk_dollars, 100.0)

    def test_alpaca_paper_adapter_posts_configured_order_body(self) -> None:
        client = RecordingHttpClient(
            post_payload={
                "id": "alpaca-order-1",
                "client_order_id": "meta-strategy-meta-strategy-paper-def-test",
                "status": "accepted",
                "submitted_at": NOW.isoformat(),
            }
        )
        broker = MetaStrategyAlpacaPaperBroker(
            SimpleNamespace(
                alpaca_key_id="paper-key",
                alpaca_secret_key="paper-secret",
                alpaca_trading_base_url="https://paper-api.alpaca.markets/v2",
                has_alpaca_credentials=True,
            ),
            http_client=client,
        )
        intent = SimpleNamespace(
            symbol="SPY",
            submittedQuantity=10,
            side=Signal.BUY,
            orderType="STOP_LIMIT",
            timeInForce="GTC",
            clientOrderId="meta-strategy-meta-strategy-paper-def-test",
            limitPrice=100.0,
            stopPrice=95.0,
            stopLimitPrice=94.5,
            targetPrice=110.0,
        )

        ack = broker.submit_bracket_order(intent)

        self.assertEqual(ack.status, "ACCEPTED")
        self.assertEqual(client.last_post_json["client_order_id"], intent.clientOrderId)
        self.assertEqual(client.last_post_json["type"], "stop_limit")
        self.assertEqual(client.last_post_json["time_in_force"], "gtc")
        self.assertEqual(client.last_post_json["stop_loss"]["stop_price"], "95.0")
        self.assertEqual(client.last_post_json["stop_loss"]["limit_price"], "94.5")
        self.assertEqual(client.last_post_json["take_profit"]["limit_price"], "110.0")


class RuntimeEnv:
    def __init__(
        self,
        *,
        jobs: MetaStrategyJobRepository | None = None,
        inventory: MetaStrategySqliteRepository | None = None,
        broker: "FakePaperBroker | None" = None,
        global_risk=None,
        arm_control: bool = True,
        readiness_report_source=None,
    ) -> None:
        database_url = f"sqlite:///{temp_db_path()}"
        self.jobs = jobs or MetaStrategyJobRepository(database_url)
        self.inventory = inventory or MetaStrategySqliteRepository(database_url)
        self.settings_store = MetaStrategySettingsStore(temp_db_path(prefix="meta-strategy-phase9-settings"))
        self.settings = self.settings_store.create_baseline(build_meta_strategy_settings(settings_version="phase9-settings", created_at=NOW), actor="test")
        self.settings_store.activate_settings(self.settings.settings_version, actor="test")
        self.broker = broker or FakePaperBroker()
        self.global_risk = global_risk or AllowRisk()
        self.readiness_report_source = readiness_report_source or readiness_report_ready
        self.gateway = PaperOrderGateway(self.broker, self.jobs.gateway_store())
        if arm_control:
            arm_automatic_paper_trading(self.jobs, now=NOW)

    def create_outbox(
        self,
        *,
        quantity: int = 10,
        reserved_risk: float = 100.0,
        extra_order_payload: dict | None = None,
        omit_order_fields: tuple[str, ...] = (),
    ) -> None:
        extra = dict(extra_order_payload or {})
        job = self.jobs.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW, settings_version=self.settings.settings_version, now=NOW)
        claimed = self.jobs.claim_next_job(queue_name="finalised_bar_decisions", worker_id="decision-worker", now=NOW)
        event_id = self.jobs.read_payload(job.payload_reference)["payload"]["eventId"]
        event = self.jobs.event_by_id(event_id)
        order_intent = {
            "algorithmId": "meta_strategy",
            "capitalPartitionId": "meta_strategy.paper.default",
            "mode": "PAPER",
            "settingsVersion": self.settings.settings_version,
            "decisionId": "decision-1",
            "jobId": claimed.job_id,
            "eventId": event_id,
            "orderIntentId": "intent-1",
            "symbol": "SPY",
            "side": extra.pop("side", "BUY"),
            "quantity": quantity,
            "limitPrice": 100.0,
            "stopPrice": 95.0,
            "targetPrice": 110.0,
            "reservedRiskDollars": reserved_risk,
            "localGatesPassed": True,
            "decisionTimestamp": NOW.isoformat(),
            "quoteTimestamp": NOW.isoformat(),
            "accountEquity": 100_000.0,
            "buyingPower": 100_000.0,
            "remainingAlgorithmRisk": 1_000.0,
            "globalAvailableRisk": 1_000.0,
            "globalQuantityCap": 10_000,
            "createdAt": NOW.isoformat(),
            "timestamp": NOW.isoformat(),
            **extra,
        }
        for field in omit_order_fields:
            order_intent.pop(field, None)
        self.jobs.persist_decision_atomic(
            job=claimed,
            event=event,
            decision_id="decision-1",
            payload={
                "algorithmId": "meta_strategy",
                "decisionId": "decision-1",
                "eventId": event_id,
                "jobId": claimed.job_id,
                "symbol": "SPY",
                "barEnd": NOW.isoformat(),
                "decisionTimestamp": NOW.isoformat(),
                "settingsVersion": self.settings.settings_version,
                "modelVersion": "phase9-model",
                "decisionStatus": "ORDER_PROPOSED",
                "reasonCodes": ("meta_strategy.test.local_gates_passed",),
            },
            order_intent=order_intent,
            now=NOW,
        )
        self.jobs.complete_job(claimed.job_id, worker_id="decision-worker", now=NOW)

    def record_open_position(self, *, quantity: int = 10, side: str = "BUY") -> None:
        self.inventory.ingest_broker_fill(
            {
                "algorithmId": "meta_strategy",
                "capitalPartitionId": "meta_strategy.paper.default",
                "settingsVersion": self.settings.settings_version,
                "correlationId": "open-position",
                "decisionId": "decision-open-position",
                "jobId": "job-open-position",
                "eventId": "event-open-position",
                "orderIntentId": "intent-open-position",
                "clientOrderId": "client-open-position",
                "brokerOrderId": "broker-open-position",
                "brokerFillId": f"fill-open-position-{quantity}-{side}",
                "symbol": "SPY",
                "side": side,
                "quantity": quantity,
                "price": 100.0,
                "status": "FILLED",
                "timestamp": (NOW - timedelta(minutes=1)).isoformat(),
            }
        )

    def set_runtime(self, *, enabled: bool = True, mode: str = "PAPER", ready: bool = True, paper_blocked: bool = False) -> None:
        self.jobs.write_gateway_snapshot(
            "meta_strategy.runtime.readiness",
            {
                "algorithmId": "meta_strategy",
                "enabled": enabled,
                "ready": ready,
                "status": "ready" if ready else "unavailable",
                "mode": mode,
                "paperOrdersBlocked": paper_blocked,
                "liveTradingEnabled": False,
                "reasonCodes": ("meta_strategy.runtime.ready",) if ready else ("meta_strategy.runtime.unavailable",),
            },
            now=NOW,
        )

    def write_control(self, control: str, state: dict) -> None:
        self.jobs.write_gateway_snapshot(
            f"meta_strategy.controls.{control}",
            {"control": control, "state": state, "actor": "test", "reason": f"meta_strategy.test.{control.lower()}"},
            now=NOW,
        )

    def activate_new_settings(self) -> None:
        replacement = self.settings_store.create_baseline(build_meta_strategy_settings(settings_version="phase9-settings-replacement", created_at=NOW), actor="test")
        self.settings_store.activate_settings(replacement.settings_version, actor="test")

    def enable_pyramiding(self) -> None:
        self.settings = self.settings_store.create_baseline(
            build_meta_strategy_settings(
                settings_version="phase9-settings-pyramiding",
                created_at=NOW,
                position_management={"one_position_per_symbol": False},
            ),
            actor="test",
        )
        self.settings_store.activate_settings(self.settings.settings_version, actor="test")

    def reserve_all_algorithm_risk(self) -> None:
        self.inventory.record_order_intent(
            {
                "algorithmId": "meta_strategy",
                "capitalPartitionId": "meta_strategy.paper.default",
                "settingsVersion": self.settings.settings_version,
                "correlationId": "reserve-all-risk",
                "decisionId": "decision-reserve-all-risk",
                "jobId": "job-reserve-all-risk",
                "eventId": "event-reserve-all-risk",
                "orderIntentId": "intent-reserve-all-risk",
                "symbol": "QQQ",
                "side": "BUY",
                "quantity": 1,
                "reservedRiskDollars": self.settings.local_risk.maximum_open_risk,
                "timestamp": NOW.isoformat(),
            }
        )

    def write_duplicate_client_snapshot(self) -> None:
        outbox = self.jobs.outbox_for_order_intent("intent-1")
        client_order_id = deterministic_meta_strategy_client_order_id(outbox["payload"])
        self.jobs.write_gateway_snapshot(
            f"paper_order_gateway.client_order.{client_order_id}",
            {"clientOrderId": client_order_id, "orderIntentId": "already-used", "algorithmId": "meta_strategy"},
            now=NOW,
        )

    def create_existing_open_entry_order(self) -> None:
        job = self.jobs.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW - timedelta(minutes=1), settings_version=self.settings.settings_version, now=NOW)
        claimed = self.jobs.claim_next_job(queue_name="finalised_bar_decisions", worker_id="decision-worker-existing", now=NOW)
        event_id = self.jobs.read_payload(job.payload_reference)["payload"]["eventId"]
        event = self.jobs.event_by_id(event_id)
        self.jobs.persist_decision_atomic(
            job=claimed,
            event=event,
            decision_id="decision-existing-open",
            payload={
                "algorithmId": "meta_strategy",
                "decisionId": "decision-existing-open",
                "eventId": event_id,
                "jobId": claimed.job_id,
                "symbol": "SPY",
                "barEnd": (NOW - timedelta(minutes=1)).isoformat(),
                "decisionTimestamp": (NOW - timedelta(minutes=1)).isoformat(),
                "settingsVersion": self.settings.settings_version,
                "modelVersion": "phase9-model",
                "decisionStatus": "ORDER_PROPOSED",
            },
            order_intent={
                "algorithmId": "meta_strategy",
                "capitalPartitionId": "meta_strategy.paper.default",
                "mode": "PAPER",
                "settingsVersion": self.settings.settings_version,
                "decisionId": "decision-existing-open",
                "jobId": claimed.job_id,
                "eventId": event_id,
                "orderIntentId": "intent-existing-open",
                "symbol": "SPY",
                "side": "BUY",
                "quantity": 1,
                "limitPrice": 100.0,
                "stopPrice": 95.0,
                "targetPrice": 110.0,
                "reservedRiskDollars": 10.0,
                "localGatesPassed": True,
                "decisionTimestamp": (NOW - timedelta(minutes=1)).isoformat(),
                "quoteTimestamp": NOW.isoformat(),
                "buyingPower": 100_000.0,
                "createdAt": (NOW - timedelta(minutes=1)).isoformat(),
                "timestamp": (NOW - timedelta(minutes=1)).isoformat(),
            },
            now=NOW,
        )
        self.jobs.update_execution_outbox(
            "meta_strategy.execution_outbox.intent-existing-open",
            status="ACKNOWLEDGED",
            payload={"status": "ACKNOWLEDGED"},
            client_order_id="meta-strategy-existing-open",
            now=NOW,
        )
        self.jobs.complete_job(claimed.job_id, worker_id="decision-worker-existing", now=NOW)

    def corrupt_current_outbox_algorithm(self) -> None:
        outbox = self.jobs.outbox_for_order_intent("intent-1")
        self.jobs.update_execution_outbox(
            outbox["outboxId"],
            status="PENDING",
            payload={"algorithmId": "weighted_voting", "algorithm_id": "weighted_voting"},
            now=NOW,
        )

    def submission_worker(self) -> MetaStrategyPaperOrderSubmissionWorker:
        return MetaStrategyPaperOrderSubmissionWorker(
            repository=self.jobs,
            inventory_repository=self.inventory,
            paper_gateway=self.gateway,
            global_risk_source=self.global_risk,
            settings_store=self.settings_store,
            readiness_report_source=self.readiness_report_source,
        )

    def reconciliation_worker(self) -> MetaStrategyPaperOrderReconciliationWorker:
        return MetaStrategyPaperOrderReconciliationWorker(
            repository=self.jobs,
            inventory_repository=self.inventory,
            paper_gateway=self.gateway,
        )

    def stale_worker(self) -> MetaStrategyStaleOrderCancellationWorker:
        return MetaStrategyStaleOrderCancellationWorker(
            repository=self.jobs,
            inventory_repository=self.inventory,
            paper_gateway=self.gateway,
        )


class FakePaperBroker:
    broker_kind = "alpaca_paper"
    configured = True
    paper_endpoint = True

    def __init__(
        self,
        *,
        ack_status: str = "ACCEPTED",
        fill_on_submit: bool = False,
        crash_after_submit: bool = False,
        timeout_after_submit: bool = False,
        market_open: bool = True,
        next_close: datetime | None = None,
        clock_unavailable: bool = False,
        clock_captured_at: datetime | None = None,
        clock_source: str = "test_alpaca_paper_clock",
        clock_authoritative: bool = True,
    ) -> None:
        self.ack_status = ack_status
        self.fill_on_submit = fill_on_submit
        self.crash_after_submit = crash_after_submit
        self.timeout_after_submit = timeout_after_submit
        self.market_open = market_open
        self.next_close = next_close
        self.clock_unavailable = clock_unavailable
        self.clock_captured_at = clock_captured_at
        self.clock_source = clock_source
        self.clock_authoritative = clock_authoritative
        self.submit_count = 0
        self.cancel_count = 0
        self.replace_count = 0
        self.orders: dict[str, dict] = {}
        self.events: list[dict] = []
        self.positions: list[dict] = []
        self.submitted_client_ids: list[str] = []
        self.last_quantity = 0
        self.last_intent = None

    def verify_paper_account(self) -> bool:
        return True

    def get_clock(self):
        if self.clock_unavailable:
            return None
        captured_at = self.clock_captured_at or NOW
        return {
            "source": self.clock_source,
            "capturedAt": captured_at.isoformat(),
            "dataSourceTimestamp": captured_at.isoformat(),
            "isOpen": self.market_open,
            "status": "open" if self.market_open else "closed",
            "authoritativeReadOnly": self.clock_authoritative,
            **({"nextClose": self.next_close.isoformat()} if self.next_close else {}),
        }

    def submit_bracket_order(self, intent):
        self.submit_count += 1
        self.last_intent = intent
        self.last_quantity = intent.submittedQuantity
        self.submitted_client_ids.append(intent.clientOrderId)
        self.orders[intent.clientOrderId] = {
            "brokerEventId": f"ack-{intent.orderIntentId}",
            "algorithmId": intent.algorithmId,
            "clientOrderId": intent.clientOrderId,
            "brokerOrderId": f"broker-{intent.orderIntentId}",
            "orderIntentId": intent.orderIntentId,
            "status": self.ack_status,
            "symbol": intent.symbol,
            "side": intent.side.value if hasattr(intent.side, "value") else str(intent.side),
            "submittedQuantity": intent.submittedQuantity,
            "timestamp": NOW.isoformat(),
        }
        if self.crash_after_submit:
            raise RuntimeError("process crashed after broker accepted request")
        if self.timeout_after_submit:
            raise TimeoutError("broker submission timed out")
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=f"broker-{intent.orderIntentId}" if self.ack_status != "REJECTED" else None,
            status=self.ack_status,
            acceptedAt=NOW if self.ack_status != "REJECTED" else None,
            rejectedReason="rejected-by-test" if self.ack_status == "REJECTED" else None,
        )

    def refresh_order(self, client_order_id: str):
        if not self.fill_on_submit:
            return None
        return PaperGatewayFill(
            clientOrderId=client_order_id,
            algorithmId="meta_strategy",
            capitalPartitionId="meta_strategy.paper.default",
            orderIntentId="intent-1",
            brokerOrderId=f"broker-intent-1",
            brokerFillId=f"fill-{client_order_id}",
            symbol="SPY",
            side=Signal.BUY,
            filledQuantity=10,
            averageFillPrice=100.0,
            status="FILLED",
            filledAt=NOW,
        )

    def cancel_order(self, client_order_id: str) -> bool:
        self.cancel_count += 1
        order = self.orders.get(client_order_id)
        if order:
            order["status"] = "CANCELED"
            self.events.append({**order, "brokerEventId": f"cancel-{order['orderIntentId']}", "status": "CANCELED"})
        return bool(order)

    def replace_order(self, broker_order_id: str, *, quantity: int | None = None, limit_price: float | None = None, stop_price: float | None = None, client_order_id: str | None = None):
        self.replace_count += 1
        order = next((item for item in self.orders.values() if item["brokerOrderId"] == broker_order_id), None)
        if order is None:
            return None
        order["status"] = "REPLACED"
        order["clientOrderId"] = client_order_id or order["clientOrderId"]
        return {**order, "brokerEventId": f"replace-{order['orderIntentId']}", "status": "REPLACED"}

    def refresh_positions(self):
        return list(self.positions)

    def list_order_events(self):
        events = list(self.orders.values()) + list(self.events)
        self.events.clear()
        return events

    def fill_event(self, *, quantity: int, status: str, event_id: str) -> dict:
        order = next(iter(self.orders.values()))
        return {
            **order,
            "brokerEventId": event_id,
            "brokerFillId": event_id,
            "capitalPartitionId": "meta_strategy.paper.default",
            "status": status,
            "filledQuantity": quantity,
            "averageFillPrice": 100.0,
            "timestamp": NOW.isoformat(),
        }

    def status_event(self, *, status: str, event_id: str) -> dict:
        order = next(iter(self.orders.values()))
        return {**order, "brokerEventId": event_id, "status": status, "timestamp": NOW.isoformat()}


class AllowRisk:
    def approve_order(self, proposal):
        return GlobalGateResponse(
            action="ALLOW",
            maximumAllowedQuantity=proposal.quantity,
            maximumAdditionalRiskDollars=proposal.plannedRiskDollars,
            evaluatedAt=NOW,
            configurationHash="allow-risk",
        )


class RejectRisk:
    def approve_order(self, proposal):
        return GlobalGateResponse(
            action="REJECT_NEW_ENTRY",
            maximumAllowedQuantity=0,
            maximumAdditionalRiskDollars=0.0,
            rejectionReasons=("phase9.global_risk_rejected",),
            evaluatedAt=NOW,
            configurationHash="reject-risk",
        )


class StaleRisk:
    def approve_order(self, proposal):
        return GlobalGateResponse(
            action="ALLOW",
            maximumAllowedQuantity=proposal.quantity,
            maximumAdditionalRiskDollars=proposal.plannedRiskDollars,
            evaluatedAt=NOW - timedelta(seconds=31),
            configurationHash="stale-risk",
        )


class ResizeRisk:
    def __init__(self, *, quantity: int, risk: float) -> None:
        self.quantity = quantity
        self.risk = risk

    def approve_order(self, proposal):
        return GlobalGateResponse(
            action="REDUCE_QUANTITY",
            maximumAllowedQuantity=self.quantity,
            maximumAdditionalRiskDollars=self.risk,
            evaluatedAt=NOW,
            configurationHash="resize-risk",
        )


class RecordingHttpClient:
    def __init__(self, *, post_payload: dict) -> None:
        self.post_payload = post_payload
        self.last_post_json: dict | None = None

    def post(self, url: str, *, headers: dict, json: dict):
        self.last_post_json = dict(json)
        return RecordingHttpResponse(self.post_payload)

    def get(self, url: str, *, headers: dict, params: dict | None = None):
        return RecordingHttpResponse({})

    def patch(self, url: str, *, headers: dict, json: dict):
        return RecordingHttpResponse({"id": "replacement", "client_order_id": json.get("client_order_id"), "status": "replaced"})


class RecordingHttpResponse:
    status_code = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.text = str(payload)

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return dict(self._payload)


def temp_db_path(*, prefix: str = "meta-strategy-phase9") -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"{prefix}-{uuid4().hex}.sqlite"


if __name__ == "__main__":
    unittest.main()
