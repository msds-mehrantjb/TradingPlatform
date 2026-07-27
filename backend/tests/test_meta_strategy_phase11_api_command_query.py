from __future__ import annotations

import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.algorithms.meta_strategy import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.service import MetaStrategyApplicationService
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore
from backend.app.main import app
from backend.tests.test_meta_strategy_step7_market_snapshot import request_with


class MetaStrategyPhase11ApiCommandQueryTest(unittest.TestCase):
    def setUp(self) -> None:
        scratch = Path("data/test_tmp").resolve()
        scratch.mkdir(exist_ok=True)
        root = scratch / f"meta-strategy-phase11-{uuid4().hex}"
        root.mkdir(exist_ok=True)
        self.root = root
        self.jobs_url = f"sqlite:///{root / 'jobs.db'}"
        self.inventory_url = f"sqlite:///{root / 'inventory.db'}"
        self.settings_path = root / "settings.db"
        self.jobs = MetaStrategyJobRepository(self.jobs_url)
        self.settings = MetaStrategySettingsStore(self.settings_path)
        self.inventory = MetaStrategySqliteRepository(self.inventory_url)
        self.service = MetaStrategyApplicationService(settings_store=self.settings, job_repository=self.jobs, repository=self.inventory)
        self.client = TestClient(app)
        patcher = patch("backend.app.algorithms.meta_strategy.api.META_STRATEGY_SERVICE", self.service)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_evaluate_api_returns_202_before_background_work_completes(self) -> None:
        with patch("backend.app.algorithms.meta_strategy.service.run_meta_strategy_execution_pipeline") as inline_pipeline:
            inline_pipeline.side_effect = AssertionError("API must not run the decision pipeline inline")
            response = self.client.post("/api/meta-strategy/evaluate", json={"snapshotRequest": request_with().model_dump(mode="json")})

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["algorithmId"], ALGORITHM_ID)
        self.assertEqual(body["operation"], "evaluation_command")
        self.assertEqual(body["payload"]["job"]["queueName"], "finalised_bar_decisions")
        self.assertEqual(body["payload"]["job"]["status"], "PENDING")
        self.assertIn("correlationIds", body["payload"])
        self.assertEqual(self.jobs.queue_status(queue_name="finalised_bar_decisions")["queues"]["finalised_bar_decisions"]["pending"], 1)

    def test_submitted_command_survives_api_restart(self) -> None:
        first = self.client.post("/api/meta-strategy/training/run", json={"trainingArguments": {"datasetVersion": "dataset-v1"}}).json()
        job_id = first["payload"]["job"]["jobId"]

        restarted = MetaStrategyApplicationService(
            settings_store=MetaStrategySettingsStore(self.settings_path),
            job_repository=MetaStrategyJobRepository(self.jobs_url),
            repository=MetaStrategySqliteRepository(self.inventory_url),
        )

        job = restarted.query_job(job_id)
        self.assertEqual(job["status"], "OK")
        self.assertEqual(job["payload"]["job"]["jobId"], job_id)
        self.assertEqual(job["payload"]["job"]["status"], "PENDING")

    def test_authoritative_runtime_fields_are_rejected_with_migration_reason(self) -> None:
        payload = {
            "snapshotRequest": request_with().model_dump(mode="json"),
            "accountEquity": 1_000_000.0,
            "availableBuyingPower": 1_000_000.0,
            "cashAvailability": True,
            "operationalHealthStatus": "GREEN",
        }
        response = self.client.post("/api/meta-strategy/paper/evaluate", json=payload)

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["status"], "REJECTED")
        self.assertIn("accountEquity", body["payload"]["rejectedFields"])
        self.assertIn("meta_strategy.api.authoritative_fields_rejected", body["reasonCodes"])
        self.assertEqual(self.jobs.queue_status()["totalJobs"], 0)

    def test_api_cannot_select_live_mode_or_impersonate_another_algorithm(self) -> None:
        live = self.client.post("/api/meta-strategy/events/finalised-bars", json={"mode": "LIVE", "symbol": "SPY", "barEnd": request_with().decision_timestamp.isoformat()})
        foreign = self.client.post("/api/meta-strategy/evaluate", json={"algorithmId": "sibling_algorithm", "snapshotRequest": request_with().model_dump(mode="json")})

        self.assertEqual(live.status_code, 202)
        self.assertEqual(live.json()["status"], "REJECTED")
        self.assertIn("meta_strategy.api.live_mode_rejected", live.json()["reasonCodes"])
        self.assertEqual(foreign.status_code, 202)
        self.assertEqual(foreign.json()["status"], "REJECTED")
        self.assertIn("meta_strategy.api.algorithm_impersonation_rejected", foreign.json()["reasonCodes"])

    def test_query_endpoints_are_read_only_and_expose_progress_results_inventory_and_health(self) -> None:
        before = self.jobs.queue_status()["totalJobs"]

        status = self.client.get("/api/meta-strategy/jobs/status")
        workers = self.client.get("/api/meta-strategy/workers/health")
        lag = self.client.get("/api/meta-strategy/queues/lag")
        inventory = self.client.get("/api/meta-strategy/inventory")
        positions = self.client.get("/api/meta-strategy/positions")
        orders = self.client.get("/api/meta-strategy/orders")
        fills = self.client.get("/api/meta-strategy/fills")
        trades = self.client.get("/api/meta-strategy/trades")
        pnl = self.client.get("/api/meta-strategy/pnl")
        risk = self.client.get("/api/meta-strategy/risk-reservations")
        blocked = self.client.get("/api/meta-strategy/decisions/blocked")
        settings = self.client.get("/api/meta-strategy/settings/active")
        profile = self.client.get("/api/meta-strategy/settings/effective-profile")
        model = self.client.get("/api/meta-strategy/models/active")

        for response in (status, workers, lag, inventory, positions, orders, fills, trades, pnl, risk, blocked, settings, profile, model):
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["algorithmId"], ALGORITHM_ID)
        self.assertEqual(self.jobs.queue_status()["totalJobs"], before)


if __name__ == "__main__":
    unittest.main()
