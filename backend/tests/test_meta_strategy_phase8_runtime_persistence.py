import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from backend.app.algorithms.meta_strategy.broker_adapter import NoopMetaStrategyBrokerAdapter
from backend.app.algorithms.meta_strategy.decision_worker import (
    MetaStrategyDecisionWorkerContext,
    MetaStrategyFinalisedBarDecisionWorker,
)
from backend.app.algorithms.meta_strategy.execution_pipeline import InMemoryMetaStrategyPersistenceAdapter
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.repository import MetaStrategyRepositoryPersistenceAdapter, MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.runtime import (
    MetaStrategyRuntimeDependencies,
    MetaStrategyRuntimeStartupError,
    MetaStrategyRuntimeMode,
    configured_meta_strategy_runtime,
    meta_strategy_runtime_retention_policies,
    validate_meta_strategy_runtime_startup,
)
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore, build_meta_strategy_settings
from backend.tests.test_meta_strategy_step7_market_snapshot import request_with


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)


class MetaStrategyPhase8RuntimePersistenceTest(unittest.TestCase):
    def test_paper_startup_rejects_memory_persistence_noop_broker_and_missing_sources(self) -> None:
        deps = MetaStrategyRuntimeDependencies(
            mode=MetaStrategyRuntimeMode.PAPER,
            persistence_adapter=InMemoryMetaStrategyPersistenceAdapter(),
            broker_adapter=NoopMetaStrategyBrokerAdapter(),
        )

        with self.assertRaises(MetaStrategyRuntimeStartupError) as raised:
            validate_meta_strategy_runtime_startup(deps)

        reason_codes = raised.exception.reason_codes
        self.assertIn("meta_strategy.runtime.durable_persistence_required", reason_codes)
        self.assertIn("meta_strategy.runtime.paper_broker_required", reason_codes)
        self.assertIn("meta_strategy.runtime.active_settings_required", reason_codes)
        self.assertIn("meta_strategy.runtime.inventory_repository_required", reason_codes)
        self.assertIn("meta_strategy.runtime.account_data_source_required", reason_codes)
        self.assertIn("meta_strategy.runtime.global_risk_source_required", reason_codes)
        self.assertIn("meta_strategy.runtime.operational_health_source_required", reason_codes)

    def test_diagnostics_must_explicitly_label_test_fallbacks(self) -> None:
        unlabeled = MetaStrategyRuntimeDependencies(
            mode=MetaStrategyRuntimeMode.DIAGNOSTICS,
            persistence_adapter=InMemoryMetaStrategyPersistenceAdapter(),
            broker_adapter=NoopMetaStrategyBrokerAdapter(),
        )
        labeled = MetaStrategyRuntimeDependencies(
            mode=MetaStrategyRuntimeMode.DIAGNOSTICS,
            persistence_adapter=InMemoryMetaStrategyPersistenceAdapter(),
            broker_adapter=NoopMetaStrategyBrokerAdapter(),
            diagnostic_label="local-diagnostics-only",
        )

        with self.assertRaises(MetaStrategyRuntimeStartupError):
            validate_meta_strategy_runtime_startup(unlabeled)

        self.assertTrue(validate_meta_strategy_runtime_startup(labeled).diagnostic_fallbacks_allowed)

    def test_configured_paper_runtime_uses_durable_repository_dependencies(self) -> None:
        database_url = f"sqlite:///{temp_db_path('runtime')}"
        settings_store = MetaStrategySettingsStore(temp_db_path("settings"))
        settings = settings_store.create_baseline(build_meta_strategy_settings(settings_version="phase8-active", created_at=NOW), actor="test")
        settings_store.activate_settings(settings.settings_version, actor="test")

        deps = configured_meta_strategy_runtime(
            mode=MetaStrategyRuntimeMode.PAPER,
            database_url=database_url,
            settings_store=settings_store,
            broker_adapter=ConfiguredPaperBroker(),
            account_data_source=Source("account"),
            global_risk_source=Source("global-risk"),
            operational_health_source=Source("health"),
        )
        report = validate_meta_strategy_runtime_startup(deps)

        self.assertTrue(report.ready)
        self.assertIsInstance(deps.inventory_repository, MetaStrategySqliteRepository)
        self.assertIsInstance(deps.persistence_adapter, MetaStrategyRepositoryPersistenceAdapter)
        self.assertIsInstance(deps.job_repository, MetaStrategyJobRepository)

    def test_worker_reconstructs_state_before_claiming_decision_jobs(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path('reconstruct')}")
        settings = build_meta_strategy_settings(settings_version="phase8-settings", created_at=NOW)
        repository.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW, settings_version=settings.settings_version, now=NOW)
        calls: list[str] = []

        def reconstruct() -> dict[str, object]:
            calls.append("reconstructed")
            self.assertEqual(repository.queue_status(queue_name="finalised_bar_decisions", now=NOW)["queues"]["finalised_bar_decisions"]["pending"], 1)
            return {"status": "OK"}

        worker = MetaStrategyFinalisedBarDecisionWorker(
            repository=repository,
            state_provider=FixtureStateProvider(settings=settings),
            startup_reconstructor=reconstruct,
        )

        worker.run_once(now=NOW)

        self.assertEqual(calls, ["reconstructed"])
        self.assertEqual(repository.queue_status(queue_name="finalised_bar_decisions", now=NOW)["queues"]["finalised_bar_decisions"]["succeeded"], 1)

    def test_persisted_decision_and_outbox_have_schema_trace_metadata_and_survive_restart(self) -> None:
        database_url = f"sqlite:///{temp_db_path('restart')}"
        repository = MetaStrategyJobRepository(database_url)
        settings = build_meta_strategy_settings(settings_version="phase8-settings", created_at=NOW)
        job = repository.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW, settings_version=settings.settings_version, now=NOW)
        worker = MetaStrategyFinalisedBarDecisionWorker(repository=repository, state_provider=FixtureStateProvider(settings=settings), pipeline_runner=fake_order_runner)

        worker.run_once(now=NOW)
        restarted_repository = MetaStrategyJobRepository(database_url)
        event_id = restarted_repository.read_payload(job.payload_reference)["payload"]["eventId"]
        decision = restarted_repository.decision_for_event(event_id)
        outbox = restarted_repository.outbox_for_decision(decision["decisionId"])
        trace = restarted_repository.decision_trace(decision["decisionId"])

        self.assertEqual(decision["schemaVersion"], "meta_strategy_worker_decision_v1")
        self.assertEqual(decision["modelVersion"], "phase8-model")
        self.assertEqual(decision["eventTimestamp"], NOW.isoformat())
        self.assertEqual(decision["payload"]["processingTimestamp"], NOW.isoformat())
        self.assertEqual(decision["payload"]["causalIds"]["eventId"], event_id)
        self.assertEqual(decision["payload"]["causalIds"]["jobId"], job.job_id)
        self.assertEqual(outbox["schemaVersion"], "meta_strategy_execution_outbox_v1")
        self.assertEqual(outbox["settingsVersion"], settings.settings_version)
        self.assertEqual(outbox["payload"]["causalIds"]["decisionId"], decision["decisionId"])
        self.assertEqual(trace["decision"]["decisionId"], decision["decisionId"])
        self.assertEqual(trace["event"]["eventId"], event_id)
        self.assertEqual(trace["outbox"]["orderIntentId"], "intent-phase8")

    def test_state_projection_validates_against_append_only_decision_history(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path('projection')}")
        settings = build_meta_strategy_settings(settings_version="phase8-settings", created_at=NOW)
        repository.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW, settings_version=settings.settings_version, now=NOW)
        worker = MetaStrategyFinalisedBarDecisionWorker(repository=repository, state_provider=FixtureStateProvider(settings=settings))

        worker.run_once(now=NOW)
        projection = repository.validate_decision_projection()

        self.assertTrue(projection["valid"])
        self.assertEqual(projection["decisionCount"], 1)
        self.assertEqual(projection["orphanOutboxCount"], 0)
        self.assertIn("meta_strategy.runtime.decision_projection_valid", projection["reasonCodes"])

    def test_retention_policy_is_explicit_for_high_volume_runtime_records(self) -> None:
        policies = meta_strategy_runtime_retention_policies()

        self.assertIn("meta_strategy_worker_decisions", policies)
        self.assertEqual(policies["meta_strategy_worker_decisions"]["mode"], "append_only_then_archive")
        self.assertIn("meta_strategy_job_events", policies)
        self.assertIn("meta_strategy_high_volume_evidence", policies)


class ConfiguredPaperBroker:
    broker_kind = "alpaca_paper"
    configured = True
    paper_endpoint = True

    def submit(self, order_intent, *, mode: str):
        return {"status": "PAPER_ACCEPTED", "brokerOrderId": "paper-order", "filledQuantity": 0}


class Source:
    def __init__(self, name: str) -> None:
        self.name = name

    def load_snapshot(self) -> dict[str, str]:
        return {"source": self.name}


class FixtureStateProvider:
    def __init__(self, *, settings):
        self.settings = settings
        self.request = request_with().model_copy(update={"decision_id": "phase8-decision", "snapshot_id": "phase8-snapshot"})

    def load_context(self, event):
        return MetaStrategyDecisionWorkerContext(
            event=event,
            settings=self.settings,
            market_snapshot_request=self.request,
            inventory_snapshot={"positions": []},
            account_snapshot={"source": "read_only_account_view"},
            global_risk_snapshot={"source": "read_only_global_risk"},
            event_state={"blackout": False},
            operational_health={"status": "ok"},
            active_model_artifact={"modelVersion": "phase8-model"},
        )


def fake_order_runner(request, settings, global_risk_snapshot):
    return SimpleNamespace(
        snapshot=SimpleNamespace(decision_id="phase8-order-decision"),
        settings_version=settings.settings_version,
        effective_settings_hash=settings.effective_settings_hash,
        order_intent={"orderIntentId": "intent-phase8", "symbol": "SPY", "side": "BUY", "quantity": 1},
        final_valid=True,
        reason_codes=("meta_strategy.test.order",),
        stage_results={
            "market_snapshot": {"reasonCodes": ("snapshot",)},
            "strategies": {"reasonCodes": ("strategies",)},
            "context_and_regime": {"reasonCodes": ("regime",)},
            "safety": {"reasonCodes": ("safety",)},
            "family_aggregation": {"reasonCodes": ("aggregate",)},
            "model_inference": {"reasonCodes": ("model",)},
            "ml_decision_policy": {"reasonCodes": ("policy",)},
            "local_gates": {"reasonCodes": ("local",)},
            "sizing": {"reasonCodes": ("sizing",)},
            "order_intent": {"status": "ORDER", "reasonCodes": ("order",)},
        },
    )


def temp_db_path(label: str) -> str:
    return str((Path("data/test_tmp") / f"meta-strategy-phase8-{label}-{uuid4().hex}.sqlite").resolve())
