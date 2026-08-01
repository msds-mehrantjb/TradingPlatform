from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.meta_strategy.decision_worker import MetaStrategyFinalisedBarDecisionWorker
from backend.app.algorithms.meta_strategy.jobs import META_STRATEGY_JOB_QUEUES, MetaStrategyJobRepository, MetaStrategyWorker
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.settings import build_meta_strategy_settings
from backend.app.algorithms.meta_strategy.worker_main import build_meta_strategy_worker
from backend.app.algorithms.meta_strategy.workers import (
    MetaStrategyBacktestingWorker,
    MetaStrategyInventoryReconciliationWorker,
    MetaStrategyModelEvaluationWorker,
    MetaStrategyOrderReconciliationWorker,
    MetaStrategyOrderSubmissionWorker,
    MetaStrategyPositionManagementWorker,
    MetaStrategyPromotionWorker,
    MetaStrategyReplayWorker,
    MetaStrategyReportingWorker,
    MetaStrategyStaleOrderHandlingWorker,
    MetaStrategyTrainingWorker,
)
from backend.app.execution import PaperOrderGateway
from backend.app.gates import GlobalGateResponse


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)
REQUIRED_ENVELOPE_KEYS = (
    "algorithm_id",
    "capital_partition_id",
    "settings_version",
    "strategy_catalog_version",
    "feature_schema_version",
    "model_version",
    "event_id",
    "job_id",
    "decision_id",
    "correlation_id",
)


class MetaStrategyOneMinuteRuntimeActivationTest(unittest.TestCase):
    def test_worker_factory_uses_concrete_finalised_bar_decision_worker(self) -> None:
        database_url = f"sqlite:///{temp_db_path()}"
        repository = MetaStrategyJobRepository(database_url)

        worker = build_meta_strategy_worker(
            repository=repository,
            queue_name="finalised_bar_decisions",
            worker_id="factory-worker",
            state_provider=FixtureStateProvider(),
        )

        self.assertIsInstance(worker, MetaStrategyFinalisedBarDecisionWorker)

    def test_unknown_queue_fails_startup(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")

        with self.assertRaisesRegex(RuntimeError, "meta_strategy.worker.unsupported_queue"):
            build_meta_strategy_worker(repository=repository, queue_name="unknown", worker_id="factory-worker")

    def test_paper_execution_queue_without_paper_broker_fails_startup(self) -> None:
        database_url = f"sqlite:///{temp_db_path()}"
        repository = MetaStrategyJobRepository(database_url)
        inventory = MetaStrategySqliteRepository(database_url)

        with self.assertRaisesRegex(RuntimeError, "meta_strategy.worker.paper_broker_required"):
            build_meta_strategy_worker(
                repository=repository,
                queue_name="order_submission",
                worker_id="factory-worker",
                inventory_repository=inventory,
                global_risk_source=AllowRisk(),
            )

    def test_order_submission_without_global_risk_source_fails_startup(self) -> None:
        database_url = f"sqlite:///{temp_db_path()}"
        repository = MetaStrategyJobRepository(database_url)
        inventory = MetaStrategySqliteRepository(database_url)
        gateway = PaperOrderGateway(FakePaperBroker(), repository.gateway_store())

        with self.assertRaisesRegex(RuntimeError, "meta_strategy.worker.global_risk_source_required"):
            build_meta_strategy_worker(
                repository=repository,
                queue_name="order_submission",
                worker_id="factory-worker",
                inventory_repository=inventory,
                paper_gateway=gateway,
            )

    def test_each_exposed_queue_constructs_real_worker_or_is_rejected_before_startup(self) -> None:
        database_url = f"sqlite:///{temp_db_path()}"
        repository = MetaStrategyJobRepository(database_url)
        inventory = MetaStrategySqliteRepository(database_url)
        gateway = PaperOrderGateway(FakePaperBroker(), repository.gateway_store())
        expected_types = {
            "finalised_bar_decisions": MetaStrategyFinalisedBarDecisionWorker,
            "order_submission": MetaStrategyOrderSubmissionWorker,
            "order_reconciliation": MetaStrategyOrderReconciliationWorker,
            "stale_order_handling": MetaStrategyStaleOrderHandlingWorker,
            "inventory_reconciliation": MetaStrategyInventoryReconciliationWorker,
            "position_management": MetaStrategyPositionManagementWorker,
            "training": MetaStrategyTrainingWorker,
            "backtesting": MetaStrategyBacktestingWorker,
            "replay": MetaStrategyReplayWorker,
            "model_evaluation": MetaStrategyModelEvaluationWorker,
            "promotion": MetaStrategyPromotionWorker,
            "reporting": MetaStrategyReportingWorker,
        }

        self.assertEqual(set(expected_types), set(META_STRATEGY_JOB_QUEUES))
        for queue_name, expected_type in expected_types.items():
            with self.subTest(queue_name=queue_name):
                worker = build_meta_strategy_worker(
                    repository=repository,
                    queue_name=queue_name,
                    worker_id=f"factory-{queue_name}",
                    inventory_repository=inventory,
                    state_provider=FixtureStateProvider(),
                    paper_gateway=gateway,
                    global_risk_source=AllowRisk(),
                )
                self.assertIsInstance(worker, expected_type)
                self.assertIsNot(type(worker), MetaStrategyWorker)

    def test_durable_event_decision_and_outbox_carry_required_envelope(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        settings = build_meta_strategy_settings(settings_version="one-minute-settings", created_at=NOW)
        job = repository.enqueue_finalised_bar_decision(
            mode="PAPER",
            symbol="SPY",
            timeframe="1m",
            bar_end=NOW,
            settings_version=settings.settings_version,
            now=NOW,
        )
        event_id = repository.read_payload(job.payload_reference)["payload"]["eventId"]
        event = repository.event_by_id(event_id)
        claimed = repository.claim_next_job(queue_name="finalised_bar_decisions", worker_id="decision-worker", now=NOW)
        repository.persist_decision_atomic(
            job=claimed,
            event=event,
            decision_id="decision-envelope",
            payload={
                "algorithmId": "meta_strategy",
                "decisionId": "decision-envelope",
                "symbol": "SPY",
                "barEnd": NOW.isoformat(),
                "settingsVersion": settings.settings_version,
                "modelVersion": "model-envelope",
                "decisionStatus": "ORDER_PROPOSED",
            },
            order_intent={
                "algorithmId": "meta_strategy",
                "mode": "PAPER",
                "decisionId": "decision-envelope",
                "orderIntentId": "intent-envelope",
                "symbol": "SPY",
                "side": "BUY",
                "quantity": 1,
                "reservedRiskDollars": 10.0,
                "timestamp": NOW.isoformat(),
            },
            now=NOW,
        )

        decision = repository.decision_for_event(event_id)
        outbox = repository.outbox_for_decision("decision-envelope")

        self.assertEqual(event.job_id, job.job_id)
        for key in REQUIRED_ENVELOPE_KEYS:
            with self.subTest(key=key):
                self.assertTrue(decision[key])
                self.assertTrue(decision["payload"][key])
                self.assertTrue(outbox[key])
                self.assertTrue(outbox["payload"][key])


def temp_db_path() -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"meta-strategy-runtime-{uuid4().hex}.sqlite"


class FixtureStateProvider:
    def load_context(self, event):
        raise AssertionError("factory construction should not load state")


class FakePaperBroker:
    broker_kind = "alpaca_paper"
    configured = True
    paper_endpoint = True

    def verify_paper_account(self) -> bool:
        return True

    def submit_bracket_order(self, intent):
        raise AssertionError("factory construction should not submit orders")

    def refresh_order(self, client_order_id: str):
        raise AssertionError("factory construction should not refresh orders")

    def cancel_order(self, client_order_id: str) -> bool:
        raise AssertionError("factory construction should not cancel orders")

    def refresh_positions(self):
        return []

    def list_order_events(self):
        return []


class AllowRisk:
    def approve_order(self, proposal):
        return GlobalGateResponse(
            action="ALLOW",
            maximumAllowedQuantity=proposal.quantity,
            maximumAdditionalRiskDollars=proposal.plannedRiskDollars,
            evaluatedAt=NOW,
            configurationHash="allow-risk",
        )


if __name__ == "__main__":
    unittest.main()
