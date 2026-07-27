from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.meta_strategy import (
    ALGORITHM_ID,
    META_STRATEGY_FINAL_DOD_IDS,
    META_STRATEGY_RECOVERY_TEST_IDS,
    MetaStrategyApplicationService,
)
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.observability import (
    apply_meta_strategy_operational_control,
    build_meta_strategy_evidence_acceptance_report,
    build_meta_strategy_observability_snapshot,
    record_meta_strategy_test_evidence,
)
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore, build_meta_strategy_settings


NOW = datetime(2026, 1, 5, 16, 0, tzinfo=UTC)


class MetaStrategyPhase12ObservabilityAcceptanceTest(unittest.TestCase):
    def setUp(self) -> None:
        root = Path("data/test_tmp").resolve() / f"meta-strategy-phase12-{uuid4().hex}"
        root.mkdir(parents=True, exist_ok=True)
        self.jobs = MetaStrategyJobRepository(f"sqlite:///{root / 'jobs.db'}")
        self.inventory = MetaStrategySqliteRepository(f"sqlite:///{root / 'inventory.db'}")
        self.settings = MetaStrategySettingsStore(root / "settings.db")

    def test_observability_reports_required_metrics_and_versions(self) -> None:
        self.jobs.enqueue_job(job_type="training", idempotency_key="training-old", payload={}, now=NOW - timedelta(seconds=45))
        self.jobs.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW - timedelta(minutes=1), settings_version="settings-v1", now=NOW - timedelta(seconds=30))
        self.jobs.record_worker_heartbeat(worker_id="decision-worker", queue_name="finalised_bar_decisions", now=NOW)
        self.jobs.write_gateway_snapshot("paper_broker_connectivity", {"status": "OK", "configured": True}, now=NOW)

        snapshot = build_meta_strategy_observability_snapshot(job_repository=self.jobs, inventory_repository=self.inventory, settings_store=self.settings, now=NOW)
        metrics = snapshot["metrics"]

        self.assertEqual(snapshot["algorithmId"], ALGORITHM_ID)
        for key in (
            "finalisedBarEventLagSeconds",
            "queueDepth",
            "oldestQueuedJob",
            "workerHeartbeat",
            "leaseRecoveryCount",
            "jobRetryCount",
            "jobDeadLetterCount",
            "snapshotDataAgeSeconds",
            "decisionLatencyMs",
            "blockedDecisionReasonCounts",
            "strategyActivationCounts",
            "orderOutboxOldestAgeSeconds",
            "brokerSubmissionLatencySeconds",
            "reconciliationLagSeconds",
            "openOrderAgeSeconds",
            "inventoryMismatchCount",
            "dailyPnl",
            "reservedRisk",
            "paperBrokerConnectivity",
            "settingsVersion",
            "modelVersion",
        ):
            self.assertIn(key, metrics)
        self.assertEqual(metrics["paperBrokerConnectivity"]["status"], "OK")
        self.assertGreaterEqual(metrics["strategyActivationCounts"]["active"], 8)

    def test_operational_controls_are_audited_without_deleting_job_evidence(self) -> None:
        queued = self.jobs.enqueue_job(job_type="training", idempotency_key="pending-control", payload={}, now=NOW)

        pause = apply_meta_strategy_operational_control(job_repository=self.jobs, control="pause_new_entries", actor="ops", reason="test.pause", now=NOW)
        disabled = apply_meta_strategy_operational_control(job_repository=self.jobs, control="disable_strategy", actor="ops", reason="test.disable", payload={"strategyId": "opening_range_breakout"}, now=NOW)
        cancelled = apply_meta_strategy_operational_control(job_repository=self.jobs, control="cancel_pending_jobs", actor="ops", reason="test.cancel", payload={"queueName": "training"}, now=NOW)

        self.assertEqual(pause.status, "RECORDED")
        self.assertEqual(disabled.payload["state"]["mode"], "DISABLED")
        self.assertEqual(cancelled.payload["cancelledJobs"], 1)
        self.assertEqual(self.jobs.read_job(queued.job_id).status.value, "CANCELLED")
        self.assertTrue(self.jobs.operational_events(event_type="control.pause_new_entries"))
        self.assertTrue(self.jobs.operational_events(event_type="cancel_pending_jobs"))

    def test_recovery_evidence_paths_are_exercised_and_recorded(self) -> None:
        job = self.jobs.enqueue_job(job_type="training", idempotency_key="lease", payload={}, now=NOW)
        self.jobs.claim_next_job(queue_name="training", worker_id="worker-before-restart", lease_seconds=1, now=NOW)
        recovered = self.jobs.claim_next_job(queue_name="training", worker_id="worker-after-restart", lease_seconds=10, now=NOW + timedelta(seconds=2))
        first_event = self.jobs.record_event(event_type="finalised_bar", queue_name="finalised_bar_decisions", idempotency_key="dup-event", payload={}, now=NOW)
        duplicate_event = self.jobs.record_event(event_type="finalised_bar", queue_name="finalised_bar_decisions", idempotency_key="dup-event", payload={}, now=NOW)
        first_broker = self.jobs.record_broker_event({"algorithmId": ALGORITHM_ID, "brokerEventId": "broker-event-1", "clientOrderId": "client-1"}, now=NOW)
        duplicate_broker = self.jobs.record_broker_event({"algorithmId": ALGORITHM_ID, "brokerEventId": "broker-event-1", "clientOrderId": "client-1"}, now=NOW)
        baseline = build_meta_strategy_settings(settings_version="baseline-phase12", created_at=NOW)
        self.settings.create_baseline(baseline, actor="ops")
        self.settings.activate_settings("baseline-phase12", actor="ops")
        rolled_back = self.settings.rollback_to("baseline-phase12", actor="ops", reason="test.rollback")
        model_rollback = self.jobs.rollback_active_model(actor="ops", reason="test.model.rollback", now=NOW)
        rebuild = self.inventory.rebuild_inventory_from_ledger()

        self.assertEqual(recovered.job_id, job.job_id)
        self.assertFalse(first_event.duplicate)
        self.assertTrue(duplicate_event.duplicate)
        self.assertFalse(first_broker["duplicate"])
        self.assertTrue(duplicate_broker["duplicate"])
        self.assertEqual(rolled_back.restored_settings_version, "baseline-phase12")
        self.assertIn("meta_strategy.model.rollback_applied", model_rollback["reasonCodes"])
        self.assertTrue(rebuild.rebuilt_from_ledger)
        self.assertGreaterEqual(self.jobs.operational_metrics(now=NOW + timedelta(seconds=2))["leaseRecoveryCount"], 1)

    def test_final_acceptance_is_computed_from_evidence_not_static_flags(self) -> None:
        self.jobs.record_worker_heartbeat(worker_id="decision-worker", queue_name="finalised_bar_decisions", now=NOW)
        missing = build_meta_strategy_evidence_acceptance_report(
            build_meta_strategy_observability_snapshot(job_repository=self.jobs, inventory_repository=self.inventory, settings_store=self.settings, now=NOW)
        )
        self.assertFalse(missing["complete"])
        self.assertGreater(missing["counts"]["FAILED"], 0)

        for test_id in (*META_STRATEGY_RECOVERY_TEST_IDS, *META_STRATEGY_FINAL_DOD_IDS):
            record_meta_strategy_test_evidence(job_repository=self.jobs, test_id=test_id, passed=True, command="pytest evidence", evidence=f"record:{test_id}", now=NOW)

        ready = build_meta_strategy_evidence_acceptance_report(
            build_meta_strategy_observability_snapshot(job_repository=self.jobs, inventory_repository=self.inventory, settings_store=self.settings, now=NOW)
        )
        self.assertTrue(ready["complete"])
        self.assertTrue(ready["paperReady"])
        self.assertEqual(ready["paperStatus"], "READY")
        self.assertFalse(ready["liveExecutionEnabled"])

    def test_service_exposes_readiness_controls_and_evidence_endpoints(self) -> None:
        service = MetaStrategyApplicationService(settings_store=self.settings, job_repository=self.jobs, repository=self.inventory)

        control = service.apply_control("stop_execution_continue_decisions", {"actor": "ops", "reason": "test.stop_execution"})
        evidence = service.record_test_evidence({"testId": "api_restart", "passed": True, "command": "pytest", "evidence": "phase12"})
        readiness = service.readiness_report()

        self.assertEqual(control["status"], "OK")
        self.assertEqual(evidence["status"], "OK")
        self.assertEqual(readiness["algorithmId"], ALGORITHM_ID)
        self.assertEqual(readiness["payload"]["currentShadowPaperStatus"]["liveExecutionEnabled"], False)


if __name__ == "__main__":
    unittest.main()
