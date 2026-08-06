from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.meta_strategy import (
    META_STRATEGY_FINAL_DOD_IDS,
    META_STRATEGY_PAPER_READINESS_ACCEPTANCE_VERSION,
    META_STRATEGY_PAPER_READINESS_CRITERIA,
    META_STRATEGY_PAPER_READINESS_TEST_IDS,
    META_STRATEGY_RECOVERY_TEST_IDS,
)
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.runtime_supervisor import MARKET_TIME_QUEUES
from backend.app.algorithms.meta_strategy.service import MetaStrategyApplicationService
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore, build_meta_strategy_settings


class MetaStrategyPaperReadinessAcceptanceTest(unittest.TestCase):
    def test_step17_declares_all_paper_readiness_criteria(self) -> None:
        criterion_ids = {criterion.criterion_id for criterion in META_STRATEGY_PAPER_READINESS_CRITERIA}

        self.assertEqual(len(META_STRATEGY_PAPER_READINESS_CRITERIA), 22)
        self.assertEqual(len(META_STRATEGY_PAPER_READINESS_TEST_IDS), 22)
        for expected in {
            "finalized_spy_candle_exactly_one_event",
            "api_routes_enqueue_only",
            "unhandled_worker_jobs_fail_closed",
            "finalized_bar_worker_running",
            "paper_submission_worker_running",
            "reconciliation_and_stale_order_workers_running",
            "position_management_worker_running",
            "alpaca_paper_endpoint_only",
            "missing_broker_configuration_blocks_startup",
            "synthetic_fills_disabled",
            "decision_and_intent_before_submission",
            "duplicate_processing_idempotent_order",
            "partial_fills_apply_once",
            "inventory_isolation",
            "caller_authoritative_state_rejected",
            "existing_position_management_complete",
            "restart_reconstruction_and_broker_reconciliation",
            "parity_same_decision_logic",
            "ml_disabled_or_shadow_only",
            "dynamic_overlays_do_not_increase_baseline_risk",
            "all_required_tests_pass",
            "live_trading_disabled",
        }:
            self.assertIn(expected, criterion_ids)

    def test_readiness_report_blocks_without_step17_evidence(self) -> None:
        service = service_with_runtime(healthy_runtime())

        response = service.readiness_report()
        paper = response["payload"]["paperReadinessAcceptance"]

        self.assertEqual(response["status"], "REJECTED")
        self.assertFalse(response["payload"]["paperReady"])
        self.assertEqual(paper["version"], META_STRATEGY_PAPER_READINESS_ACCEPTANCE_VERSION)
        self.assertGreater(paper["counts"]["FAILED"], 0)
        self.assertIn("finalized_spy_candle_exactly_one_event", paper["blockingCriteria"])

    def test_all_evidence_and_healthy_paper_runtime_can_mark_paper_ready(self) -> None:
        service = service_with_runtime(healthy_runtime())
        record_acceptance_evidence(service)

        response = service.readiness_report()
        paper = response["payload"]["paperReadinessAcceptance"]

        self.assertEqual(response["status"], "OK")
        self.assertTrue(response["payload"]["complete"])
        self.assertTrue(response["payload"]["paperReady"])
        self.assertEqual(response["payload"]["currentShadowPaperStatus"]["paper"], "READY")
        self.assertEqual(paper["counts"]["FAILED"], 0)
        self.assertFalse(response["payload"]["liveExecutionEnabled"])

    def test_stopped_market_worker_blocks_paper_ready_even_with_evidence(self) -> None:
        runtime = healthy_runtime()
        runtime["workers"] = {**runtime["workers"], "position_management": "stopped"}
        runtime["ready"] = False
        runtime["paperOrdersBlocked"] = True
        service = service_with_runtime(runtime)
        record_acceptance_evidence(service)

        response = service.readiness_report()
        paper = response["payload"]["paperReadinessAcceptance"]

        self.assertEqual(response["status"], "REJECTED")
        self.assertFalse(response["payload"]["paperReady"])
        self.assertIn("position_management_worker_running", paper["blockingCriteria"])
        self.assertEqual(response["payload"]["currentShadowPaperStatus"]["paper"], "blocked")


def service_with_runtime(runtime: dict) -> MetaStrategyApplicationService:
    database_url = f"sqlite:///{temp_db_path()}"
    settings_store = MetaStrategySettingsStore(temp_db_path(prefix="meta-strategy-paper-readiness-settings"))
    settings = settings_store.create_baseline(
        build_meta_strategy_settings(settings_version=f"paper-readiness-{uuid4().hex}"),
        actor="test",
    )
    settings_store.activate_settings(settings.settings_version, actor="test")
    service = MetaStrategyApplicationService(
        settings_store=settings_store,
        job_repository=MetaStrategyJobRepository(database_url),
        repository=MetaStrategySqliteRepository(database_url),
        runtime_readiness_provider=lambda: runtime,
    )
    for queue_name in MARKET_TIME_QUEUES:
        service.job_repository.record_worker_heartbeat(
            worker_id=f"meta_strategy.paper_readiness.{queue_name}",
            queue_name="finalised_bar_decisions" if queue_name == "finalized_candle_producer" else queue_name,
            now=datetime.now(UTC),
        )
    return service


def healthy_runtime() -> dict:
    prerequisites = {
        "durableDatabaseAvailable": True,
        "activeSettingsPromotedForPaper": True,
        "paperBrokerVerified": True,
        "authoritativeMarketDataHealthy": True,
        "marketClockHealthy": True,
        "requiredWorkersHealthy": True,
        "queueLagBelowThreshold": True,
        "deadLetterWithinThreshold": True,
        "restartReconstructionSucceeded": True,
        "inventoryReconciliationCurrent": True,
        "globalRiskSourceCurrent": True,
        "requiredAcceptanceTestsPassed": True,
    }
    return {
        "algorithmId": "meta_strategy",
        "ready": True,
        "status": "ready",
        "mode": "PAPER",
        "paperOrdersBlocked": False,
        "workers": {queue: "healthy" for queue in MARKET_TIME_QUEUES},
        "marketWorkersHealthy": True,
        "paperReadinessPrerequisites": prerequisites,
        "queueLagSeconds": {queue: 0 for queue in MARKET_TIME_QUEUES},
        "deadLetterCount": 0,
        "restartState": {"status": "OK"},
        "reasonCodes": ("meta_strategy.runtime.ready",),
    }


def record_acceptance_evidence(service: MetaStrategyApplicationService) -> None:
    for test_id in (*META_STRATEGY_RECOVERY_TEST_IDS, *META_STRATEGY_FINAL_DOD_IDS, *META_STRATEGY_PAPER_READINESS_TEST_IDS):
        service.record_test_evidence(
            {
                "testId": test_id,
                "passed": True,
                "command": "backend\\.venv\\Scripts\\python -m pytest backend\\tests -k meta_strategy -q",
                "evidence": "843 passed, 2334 deselected",
            }
        )


def temp_db_path(*, prefix: str = "meta-strategy-paper-readiness") -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"{prefix}-{uuid4().hex}.sqlite"
