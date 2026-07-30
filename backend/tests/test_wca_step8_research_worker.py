from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.algorithms.wca.research_jobs import WcaResearchJob, WcaResearchJobStatus, WcaResearchJobType, research_job
from backend.app.algorithms.wca.research_repository import WcaResearchRepository
from backend.app.algorithms.wca.research_worker import WCA_RESEARCH_JOB_TYPES, WCA_RESEARCH_WORKER_REQUIRES_OS_PROCESS, WcaResearchWorker
from backend.app.algorithms.wca.service import WcaService
from backend.app.main import app
from backend.tests.test_wca_step14_15_backend_backtest import backtest_request


class WcaStep8ResearchWorkerTests(unittest.TestCase):
    def test_research_worker_entrypoint_runs_as_separate_process(self) -> None:
        db_path = temp_db_path()
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "backend.app.algorithms.wca.research_worker_main",
                "--once",
                "--database-url",
                f"sqlite:///{db_path}",
                "--owner-id",
                "step8-process",
            ],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["researchWorkerProcess"])
        self.assertTrue(WCA_RESEARCH_WORKER_REQUIRES_OS_PROCESS)
        source = Path("backend/app/algorithms/wca/research_worker_main.py").read_text(encoding="utf-8")
        self.assertNotIn("FastAPI", source)
        self.assertNotIn("backend.app.main", source)

    def test_research_worker_declares_all_required_job_types(self) -> None:
        self.assertEqual(
            set(WCA_RESEARCH_JOB_TYPES),
            {
                "backtest",
                "backtest_modes",
                "walk_forward",
                "holdout",
                "historical_replay",
                "confidence_calibration",
                "performance_statistics_update",
                "weight_candidate_calculation",
                "compute_strategy_weight_candidate",
                "validate_strategy_weight_candidate",
                "promote_strategy_weight_version",
                "rollback_strategy_weight_version",
                "correlation_analysis",
                "strategy_health_analysis",
                "shadow_comparison",
                "paper_stability_report",
                "export_report_generation",
            },
        )

    def test_api_enqueues_backtest_job_and_does_not_run_synchronously(self) -> None:
        repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
        service = WcaService(repository=repository, research_repository=WcaResearchRepository(repository))
        payload = backtest_request().model_dump(mode="json")
        client = TestClient(app)

        with patch("backend.app.algorithms.wca.api.WCA_API_SERVICE", service):
            response = client.post("/api/wca/backtests", json=payload)

        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertEqual(body["status"], "QUEUED")
        self.assertTrue(body["queued"])
        with sqlite3.connect(repository.path) as conn:
            jobs = conn.execute("SELECT COUNT(*) FROM wca_background_jobs WHERE job_id = ?", (body["job_id"],)).fetchone()[0]
            backtests = conn.execute("SELECT COUNT(*) FROM wca_backtest_runs").fetchone()[0]
        self.assertEqual(jobs, 1)
        self.assertEqual(backtests, 0)

    def test_backtest_status_can_poll_queued_job_by_run_id(self) -> None:
        repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
        research_repository = WcaResearchRepository(repository)
        service = WcaService(repository=repository, research_repository=research_repository)
        request = backtest_request()

        receipt = service.enqueue_backtest(request)
        status = service.backtest_status(request.configuration.run_id)

        self.assertEqual(status["status"], "queued")
        self.assertEqual(status["jobId"], receipt.job_id)
        self.assertEqual(status["runId"], request.configuration.run_id)

    def test_worker_runs_backtest_job_through_lifecycle_and_persists_result_reference(self) -> None:
        repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
        research_repository = WcaResearchRepository(repository)
        receipt = research_repository.enqueue_job(research_job(WcaResearchJobType.BACKTEST, payload={"request": backtest_request().model_dump(mode="json")}, run_id="step8-backtest"))
        worker = WcaResearchWorker(repository=repository, research_repository=research_repository, owner_id="step8-worker")

        result = worker.run_once()
        snapshot = research_repository.read_job(receipt.job_id)

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(snapshot.status, WcaResearchJobStatus.SUCCEEDED.value)
        self.assertEqual(snapshot.progress_percent, 100)
        self.assertEqual(snapshot.result_reference["kind"], "backtest_result")
        with sqlite3.connect(repository.path) as conn:
            self.assertGreater(conn.execute("SELECT COUNT(*) FROM wca_backtest_runs").fetchone()[0], 0)

    def test_cancellation_marks_queued_job_cancelled_without_running_heavy_work(self) -> None:
        repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
        research_repository = WcaResearchRepository(repository)
        receipt = research_repository.enqueue_job(research_job(WcaResearchJobType.EXPORT_REPORT_GENERATION, payload={"report": "summary"}, run_id="cancel-me"))
        self.assertTrue(research_repository.request_cancellation(receipt.job_id))
        worker = WcaResearchWorker(repository=repository, research_repository=research_repository, owner_id="step8-cancel")

        result = worker.run_once()
        snapshot = research_repository.read_job(receipt.job_id)

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(snapshot.status, WcaResearchJobStatus.CANCELLED.value)

    def test_candidate_weight_job_does_not_modify_active_runtime_weights(self) -> None:
        repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
        research_repository = WcaResearchRepository(repository)
        starting_weights = repository.table_counts().table_counts["wca_weight_snapshots"]
        receipt = research_repository.enqueue_job(
            research_job(
                WcaResearchJobType.WEIGHT_CANDIDATE_CALCULATION,
                payload={"window": "holdout", "activation": "forbidden_without_promotion"},
                run_id="candidate-weight",
            )
        )
        worker = WcaResearchWorker(repository=repository, research_repository=research_repository, owner_id="step8-candidate")

        result = worker.run_once()
        ending_weights = repository.table_counts().table_counts["wca_weight_snapshots"]
        with sqlite3.connect(repository.path) as conn:
            candidate = conn.execute("SELECT promotion_status FROM wca_research_candidates WHERE job_id = ?", (receipt.job_id,)).fetchone()

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(ending_weights, starting_weights)
        self.assertEqual(candidate[0], "pending_promotion")

    def test_weight_candidate_validation_and_promotion_require_persisted_evidence(self) -> None:
        repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
        research_repository = WcaResearchRepository(repository)
        compute_receipt = research_repository.enqueue_job(
            research_job(
                WcaResearchJobType.COMPUTE_STRATEGY_WEIGHT_CANDIDATE,
                payload={"cutoff": "2026-07-30T20:00:00+00:00", "performance_records": []},
                run_id="weight-compute",
            )
        )
        worker = WcaResearchWorker(repository=repository, research_repository=research_repository, owner_id="step8-weight-lifecycle")
        compute = worker.run_once()
        candidate_id = compute["resultReference"]["candidateId"]
        validate_receipt = research_repository.enqueue_job(
            research_job(WcaResearchJobType.VALIDATE_STRATEGY_WEIGHT_CANDIDATE, payload={"candidate_id": candidate_id}, run_id="weight-validate")
        )

        validation = worker.run_once()
        promote_receipt = research_repository.enqueue_job(
            research_job(WcaResearchJobType.PROMOTE_STRATEGY_WEIGHT_VERSION, payload={"candidate_id": candidate_id}, run_id="weight-promote", expires_in_seconds=None)
        )
        promotion = worker.run_once()

        candidate = research_repository.read_candidate_result(candidate_id)
        self.assertEqual(compute_receipt.status, WcaResearchJobStatus.QUEUED)
        self.assertEqual(validate_receipt.status, WcaResearchJobStatus.QUEUED)
        self.assertEqual(promote_receipt.status, WcaResearchJobStatus.QUEUED)
        self.assertEqual(validation["resultReference"]["validationStatus"], "blocked")
        self.assertEqual(promotion["status"], "failed")
        self.assertEqual(candidate["validation_status"], "blocked")
        self.assertEqual(repository.table_counts().table_counts["wca_weight_snapshots"], 0)

    def test_retry_limit_quarantines_failed_job(self) -> None:
        repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
        research_repository = WcaResearchRepository(repository)
        job = WcaResearchJob(job_type=WcaResearchJobType.BACKTEST, payload={"request": {"invalid": True}}, max_attempts=1)
        receipt = research_repository.enqueue_job(job)
        worker = WcaResearchWorker(repository=repository, research_repository=research_repository, owner_id="step8-fail")

        result = worker.run_once()
        snapshot = research_repository.read_job(receipt.job_id)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(snapshot.status, WcaResearchJobStatus.QUARANTINED.value)
        self.assertTrue(snapshot.error)

    def test_research_processing_is_isolated_from_latency_runtime_and_sibling_mutable_stores(self) -> None:
        violations: list[str] = []
        for path in (
            Path("backend/app/algorithms/wca/research_jobs.py"),
            Path("backend/app/algorithms/wca/research_repository.py"),
            Path("backend/app/algorithms/wca/research_worker.py"),
            Path("backend/app/algorithms/wca/research_worker_main.py"),
        ):
            source = path.read_text(encoding="utf-8")
            for forbidden in (
                "backend.app.algorithms.weighted_voting",
                "backend.app.algorithms.voting_ensemble",
                "backend.app.algorithms.regime",
                "backend.app.algorithms.session",
                "backend.app.algorithms.meta_strategy",
                "runtime_supervisor",
                "runtime_repository",
            ):
                if forbidden in source:
                    violations.append(f"{path}: imports {forbidden}")

        self.assertEqual(violations, [])


def temp_db_path() -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"wca-step8-{uuid4().hex}.sqlite"


if __name__ == "__main__":
    unittest.main()
