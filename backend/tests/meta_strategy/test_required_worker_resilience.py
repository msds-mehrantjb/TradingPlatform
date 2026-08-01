from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository, MetaStrategyJobStatus, MetaStrategyWorker
from backend.app.algorithms.meta_strategy.worker_main import build_meta_strategy_worker
from backend.tests.test_meta_strategy_runtime_supervisor import runtime_dependencies, temp_db_path


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)


class MetaStrategyRequiredWorkerResilienceTest(unittest.TestCase):
    def test_graceful_shutdown_releases_no_completed_job_incorrectly(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path(prefix='required-worker')}")
        completed = repository.enqueue_job(job_type="reporting", idempotency_key="done", payload={}, now=NOW)
        pending = repository.enqueue_job(job_type="reporting", idempotency_key="pending", payload={}, now=NOW)
        worker = MetaStrategyWorker(repository=repository, queue_name="reporting", worker_id="required-worker")
        worker.run_once(now=NOW, handler=lambda job: {"status": "OK"})

        worker.request_shutdown()
        second = worker.run_once(now=NOW + timedelta(seconds=1), handler=lambda job: {"status": "SHOULD_NOT_RUN"})

        self.assertIsNone(second)
        self.assertEqual(repository.read_job(completed.job_id).status, MetaStrategyJobStatus.SUCCEEDED)
        self.assertEqual(repository.read_job(pending.job_id).status, MetaStrategyJobStatus.PENDING)

    def test_runtime_restart_reconstructs_state_before_claiming_work(self) -> None:
        dependencies, _gateway = runtime_dependencies(with_broker=True)
        dependencies.job_repository.enqueue_job(job_type="inventory_reconciliation", idempotency_key="startup-reconstruct", payload={}, now=NOW)

        worker = build_meta_strategy_worker(
            repository=dependencies.job_repository,
            queue_name="inventory_reconciliation",
            worker_id="required-inventory-worker",
            inventory_repository=dependencies.inventory_repository,
        )
        result = worker.run_once(now=NOW)

        self.assertEqual(result["status"], "INVENTORY_RECONCILED")
        self.assertEqual(dependencies.job_repository.queue_status(queue_name="inventory_reconciliation", now=NOW)["queues"]["inventory_reconciliation"]["succeeded"], 1)


if __name__ == "__main__":
    unittest.main()
