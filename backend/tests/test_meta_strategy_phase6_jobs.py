from __future__ import annotations

import sqlite3
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.app.algorithms.meta_strategy import ALGORITHM_ID, MetaStrategyApplicationService
from backend.app.algorithms.meta_strategy.jobs import (
    META_STRATEGY_JOB_QUEUES,
    MetaStrategyJobRepository,
    MetaStrategyJobStatus,
    MetaStrategyWorker,
)


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)


class MetaStrategyPhase6JobsTest(unittest.TestCase):
    def test_migration_creates_durable_meta_strategy_job_tables_and_queues(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")

        with sqlite3.connect(repository.path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        self.assertTrue({"meta_strategy_jobs", "meta_strategy_job_payloads", "meta_strategy_job_events", "meta_strategy_worker_heartbeats"}.issubset(tables))
        self.assertTrue(
            {
                "finalised_bar_decisions",
                "order_submission",
                "order_reconciliation",
                "stale_order_handling",
                "inventory_reconciliation",
                "training",
                "backtesting",
                "replay",
                "model_evaluation",
                "promotion",
                "reporting",
            }.issubset(META_STRATEGY_JOB_QUEUES)
        )

    def test_two_workers_cannot_claim_one_job_simultaneously(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        queued = repository.enqueue_job(
            job_type="finalised_bar_decision",
            idempotency_key="bar-SPY-1545",
            payload={"symbol": "SPY"},
            now=NOW,
        )

        first = repository.claim_next_job(queue_name="finalised_bar_decisions", worker_id="worker-a", lease_seconds=60, now=NOW)
        second = repository.claim_next_job(queue_name="finalised_bar_decisions", worker_id="worker-b", lease_seconds=60, now=NOW)

        self.assertEqual(first.job_id, queued.job_id)
        self.assertEqual(first.lease_owner, "worker-a")
        self.assertIsNone(second)

    def test_expired_lease_is_recovered(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        queued = repository.enqueue_job(job_type="order_submission", idempotency_key="order-1", payload={}, now=NOW)
        repository.claim_next_job(queue_name="order_submission", worker_id="worker-a", lease_seconds=10, now=NOW)

        recovered = repository.claim_next_job(queue_name="order_submission", worker_id="worker-b", lease_seconds=30, now=NOW + timedelta(seconds=11))

        self.assertEqual(recovered.job_id, queued.job_id)
        self.assertEqual(recovered.status, MetaStrategyJobStatus.RUNNING)
        self.assertEqual(recovered.lease_owner, "worker-b")
        self.assertEqual(recovered.attempt_count, 2)

    def test_duplicate_idempotency_key_creates_one_logical_job_and_suppresses_duplicate_events(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")

        first = repository.enqueue_job(job_type="backtesting", idempotency_key="idem-heavy-1", payload={"run": 1}, now=NOW)
        duplicate = repository.enqueue_job(job_type="backtesting", idempotency_key="idem-heavy-1", payload={"run": 2}, now=NOW)
        event = repository.record_event(event_type="finalised_bar", queue_name="finalised_bar_decisions", idempotency_key="event-1", payload={"bar": 1}, now=NOW)
        duplicate_event = repository.record_event(event_type="finalised_bar", queue_name="finalised_bar_decisions", idempotency_key="event-1", payload={"bar": 2}, now=NOW)

        self.assertEqual(duplicate.job_id, first.job_id)
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(repository.queue_status(now=NOW)["totalJobs"], 1)
        self.assertFalse(event.duplicate)
        self.assertTrue(duplicate_event.duplicate)

    def test_worker_termination_does_not_lose_queued_work(self) -> None:
        database_url = f"sqlite:///{temp_db_path()}"
        first_repository = MetaStrategyJobRepository(database_url)
        queued = first_repository.enqueue_job(job_type="inventory_reconciliation", idempotency_key="inventory-restart", payload={}, now=NOW)
        claimed = first_repository.claim_next_job(queue_name="inventory_reconciliation", worker_id="worker-before-crash", lease_seconds=5, now=NOW)
        self.assertEqual(claimed.job_id, queued.job_id)

        restarted_repository = MetaStrategyJobRepository(database_url)
        recovered = restarted_repository.claim_next_job(queue_name="inventory_reconciliation", worker_id="worker-after-restart", lease_seconds=30, now=NOW + timedelta(seconds=6))

        self.assertEqual(recovered.job_id, queued.job_id)
        self.assertEqual(recovered.lease_owner, "worker-after-restart")

    def test_repeated_failure_reaches_dead_letter_state(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        queued = repository.enqueue_job(job_type="training", idempotency_key="train-fail", payload={}, max_attempts=2, now=NOW)

        first = repository.claim_next_job(queue_name="training", worker_id="trainer", lease_seconds=30, now=NOW)
        repository.fail_job(first.job_id, worker_id="trainer", error_category="transient", error_details="token=secret internal failure", now=NOW)
        retry = repository.claim_next_job(queue_name="training", worker_id="trainer", lease_seconds=30, now=NOW + timedelta(seconds=10))
        repository.fail_job(retry.job_id, worker_id="trainer", error_category="transient", error_details="token=secret internal failure", now=NOW + timedelta(seconds=10))

        failed = repository.read_job(queued.job_id)

        self.assertEqual(failed.status, MetaStrategyJobStatus.DEAD_LETTER)
        self.assertEqual(failed.error_category, "transient")
        self.assertNotIn("secret", failed.error_details)

    def test_jobs_from_another_algorithm_cannot_be_claimed_by_meta_strategy_workers(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        with sqlite3.connect(repository.path) as conn:
            conn.execute(
                """
                INSERT INTO meta_strategy_jobs (
                    job_id, algorithm_id, job_type, queue_name, idempotency_key, payload_reference,
                    status, priority, attempt_count, max_attempts, next_attempt_at,
                    created_at, updated_at, cancellable
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "foreign-job",
                    "weighted_voting",
                    "finalised_bar_decision",
                    "finalised_bar_decisions",
                    "foreign-idem",
                    "foreign-payload",
                    MetaStrategyJobStatus.PENDING.value,
                    100,
                    0,
                    3,
                    NOW.isoformat(),
                    NOW.isoformat(),
                    NOW.isoformat(),
                    0,
                ),
            )

        claimed = repository.claim_next_job(queue_name="finalised_bar_decisions", worker_id="meta-worker", lease_seconds=30, now=NOW)

        self.assertIsNone(claimed)

    def test_worker_entrypoint_processes_one_job_and_honours_cancellation(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        completed = repository.enqueue_job(job_type="reporting", idempotency_key="report-ok", payload={}, now=NOW)
        cancelled = repository.enqueue_job(job_type="replay", idempotency_key="replay-cancel", payload={}, cancellable=True, now=NOW)
        repository.cancel_job(cancelled.job_id, now=NOW)
        worker = MetaStrategyWorker(repository=repository, queue_name="reporting", worker_id="report-worker")

        result = worker.run_once(now=NOW, handler=lambda job: {"result": job.job_id})
        replay_claim = repository.claim_next_job(queue_name="replay", worker_id="replay-worker", lease_seconds=30, now=NOW)

        self.assertEqual(result.job_id, completed.job_id)
        self.assertEqual(repository.read_job(completed.job_id).status, MetaStrategyJobStatus.SUCCEEDED)
        self.assertIsNone(replay_claim)
        self.assertEqual(repository.read_job(cancelled.job_id).status, MetaStrategyJobStatus.CANCELLED)

    def test_heavy_service_operation_enqueues_job_without_inline_execution(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        service = MetaStrategyApplicationService(job_repository=repository)

        response = service.train({"trainingArguments": {"notARealTrainerArgument": True}, "idempotencyKey": "train-http"})

        self.assertEqual(response["status"], "OK")
        self.assertTrue(response["payload"]["durableQueue"])
        self.assertTrue(response["payload"]["backgroundWorkerRequired"])
        self.assertEqual(response["payload"]["job"]["queueName"], "training")
        self.assertEqual(repository.queue_status(queue_name="training", now=NOW)["queues"]["training"]["pending"], 1)
        self.assertIn("meta_strategy.service.training_job_queued", response["reasonCodes"])

    def test_queue_status_exposes_lag_and_worker_heartbeats(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        repository.enqueue_job(job_type="model_evaluation", idempotency_key="model-eval", payload={}, now=NOW - timedelta(seconds=25))
        repository.record_worker_heartbeat(worker_id="model-worker", queue_name="model_evaluation", now=NOW)

        status = repository.queue_status(queue_name="model_evaluation", now=NOW)

        self.assertEqual(status["algorithmId"], ALGORITHM_ID)
        self.assertEqual(status["queues"]["model_evaluation"]["pending"], 1)
        self.assertEqual(status["queues"]["model_evaluation"]["lagSeconds"], 25)
        self.assertEqual(status["workers"]["model-worker"]["queueName"], "model_evaluation")


def temp_db_path() -> str:
    return str((__import__("pathlib").Path("data/test_tmp") / f"meta-strategy-phase6-{uuid4().hex}.sqlite").resolve())
