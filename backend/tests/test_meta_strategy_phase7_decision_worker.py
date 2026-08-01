from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from backend.app.algorithms.meta_strategy.decision_worker import (
    MetaStrategyDecisionWorkerContext,
    MetaStrategyFinalisedBarDecisionWorker,
)
from backend.app.algorithms.meta_strategy.backtest import MetaStrategyBacktestRequest, run_meta_strategy_backtest
from backend.app.algorithms.meta_strategy.execution_pipeline import (
    MetaStrategyExecutionPipelineConfig,
    MetaStrategyExecutionPipelineRequest,
    run_meta_strategy_execution_pipeline,
)
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository, MetaStrategyJobStatus, finalised_bar_idempotency_key
from backend.app.algorithms.meta_strategy.service import MetaStrategyApplicationService
from backend.app.algorithms.meta_strategy.settings import build_meta_strategy_settings
from backend.tests.test_meta_strategy_step7_market_snapshot import request_with


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)


class MetaStrategyPhase7DecisionWorkerTest(unittest.TestCase):
    def test_duplicate_finalised_bar_event_creates_one_durable_decision(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        settings = build_meta_strategy_settings(settings_version="phase7-settings-v1", created_at=NOW)
        first = repository.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW, settings_version=settings.settings_version, now=NOW)
        duplicate = repository.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW, settings_version=settings.settings_version, now=NOW)
        worker = MetaStrategyFinalisedBarDecisionWorker(repository=repository, state_provider=FixtureStateProvider(settings=settings))

        worker.run_once(now=NOW)

        event_id = repository.read_payload(first.payload_reference)["payload"]["eventId"]
        decision = repository.decision_for_event(event_id)
        self.assertEqual(duplicate.job_id, first.job_id)
        self.assertTrue(duplicate.duplicate)
        self.assertIsNotNone(decision)
        self.assertEqual(repository.queue_status(queue_name="finalised_bar_decisions", now=NOW)["queues"]["finalised_bar_decisions"]["succeeded"], 1)

    def test_idempotency_key_includes_capital_partition_symbol_timeframe_bar_end_and_settings_version(self) -> None:
        key = finalised_bar_idempotency_key(mode="paper", symbol="spy", timeframe="1m", bar_end=NOW, settings_version="settings-v1")

        self.assertEqual(key, "meta_strategy:meta_strategy.paper.default:SPY:1m:2026-01-05T15:45:00+00:00:settings-v1")

    def test_finalised_bar_api_boundary_enqueues_without_inline_decision(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        service = MetaStrategyApplicationService(job_repository=repository)

        response = service.enqueue_finalised_bar({"mode": "PAPER", "symbol": "SPY", "timeframe": "1m", "barEnd": NOW.isoformat()})

        self.assertEqual(response["status"], "OK")
        self.assertTrue(response["payload"]["durableQueue"])
        self.assertEqual(response["payload"]["job"]["queueName"], "finalised_bar_decisions")
        self.assertIsNone(repository.claim_next_job(queue_name="order_submission", worker_id="wrong-worker", now=NOW))
        self.assertEqual(repository.queue_status(queue_name="finalised_bar_decisions", now=NOW)["queues"]["finalised_bar_decisions"]["pending"], 1)

    def test_out_of_order_bar_is_blocked_without_decision_persistence(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        settings = build_meta_strategy_settings(settings_version="phase7-settings-v1", created_at=NOW)
        later = repository.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW, settings_version=settings.settings_version, now=NOW)
        earlier = repository.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW - timedelta(minutes=1), settings_version=settings.settings_version, now=NOW + timedelta(seconds=1))
        provider = FixtureStateProvider(settings=settings)
        worker = MetaStrategyFinalisedBarDecisionWorker(repository=repository, state_provider=provider)
        worker.run_once(now=NOW)
        worker.run_once(now=NOW + timedelta(seconds=1))
        worker.run_once(now=NOW + timedelta(seconds=10))
        worker.run_once(now=NOW + timedelta(seconds=30))

        later_event = repository.read_payload(later.payload_reference)["payload"]["eventId"]
        earlier_event = repository.read_payload(earlier.payload_reference)["payload"]["eventId"]

        self.assertIsNotNone(repository.decision_for_event(later_event))
        self.assertIsNone(repository.decision_for_event(earlier_event))
        self.assertEqual(repository.read_job(earlier.job_id).status, MetaStrategyJobStatus.DEAD_LETTER)

    def test_late_bar_correction_uses_duplicate_suppression_policy(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        settings = build_meta_strategy_settings(settings_version="phase7-settings-v1", created_at=NOW)
        original = repository.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW, settings_version=settings.settings_version, payload={"correction": False}, now=NOW)
        correction = repository.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW, settings_version=settings.settings_version, payload={"correction": True}, now=NOW + timedelta(seconds=10))

        self.assertEqual(correction.job_id, original.job_id)
        self.assertTrue(correction.duplicate)

    def test_worker_crash_before_persistence_recovers_after_lease_expiry(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        settings = build_meta_strategy_settings(settings_version="phase7-settings-v1", created_at=NOW)
        job = repository.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW, settings_version=settings.settings_version, now=NOW)
        worker = MetaStrategyFinalisedBarDecisionWorker(repository=repository, state_provider=FailOnceStateProvider(settings=settings))

        worker.run_once(now=NOW)
        recovered = MetaStrategyFinalisedBarDecisionWorker(repository=repository, state_provider=FixtureStateProvider(settings=settings), worker_id="meta_strategy.recovered_decision_worker")
        recovered.run_once(now=NOW + timedelta(minutes=6))

        event_id = repository.read_payload(job.payload_reference)["payload"]["eventId"]
        self.assertIsNotNone(repository.decision_for_event(event_id))
        self.assertEqual(repository.read_job(job.job_id).status, MetaStrategyJobStatus.SUCCEEDED)

    def test_worker_crash_after_decision_before_completion_is_idempotent_on_restart(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        settings = build_meta_strategy_settings(settings_version="phase7-settings-v1", created_at=NOW)
        job = repository.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW, settings_version=settings.settings_version, now=NOW)
        event_id = repository.read_payload(job.payload_reference)["payload"]["eventId"]
        event = repository.event_by_id(event_id)
        claimed = repository.claim_next_job(queue_name="finalised_bar_decisions", worker_id="crashing-worker", lease_seconds=10, now=NOW)
        repository.persist_decision_atomic(job=claimed, event=event, decision_id="decision-crashed-after-persist", payload={"symbol": "SPY", "barEnd": NOW.isoformat(), "settingsVersion": settings.settings_version, "decisionStatus": "HOLD_OR_BLOCKED"}, order_intent=None, now=NOW)

        recovered = MetaStrategyFinalisedBarDecisionWorker(repository=repository, state_provider=FixtureStateProvider(settings=settings), worker_id="recovered-worker")
        recovered.run_once(now=NOW + timedelta(seconds=11))

        self.assertEqual(repository.decision_for_event(event_id)["decisionId"], "decision-crashed-after-persist")
        self.assertEqual(repository.read_job(job.job_id).status, MetaStrategyJobStatus.SUCCEEDED)

    def test_restart_after_order_intent_creation_does_not_duplicate_outbox(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        settings = build_meta_strategy_settings(settings_version="phase7-settings-v1", created_at=NOW)
        job = repository.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW, settings_version=settings.settings_version, now=NOW)
        worker = MetaStrategyFinalisedBarDecisionWorker(repository=repository, state_provider=FixtureStateProvider(settings=settings), pipeline_runner=fake_order_runner)

        worker.run_once(now=NOW)
        event_id = repository.read_payload(job.payload_reference)["payload"]["eventId"]
        decision = repository.decision_for_event(event_id)
        outbox = repository.outbox_for_decision(decision["decisionId"])
        worker.run_once(now=NOW + timedelta(seconds=1))

        self.assertEqual(outbox["orderIntentId"], "intent-phase7")
        self.assertEqual(repository.outbox_for_decision(decision["decisionId"])["outboxId"], outbox["outboxId"])

    def test_same_inputs_match_worker_and_direct_replay_pipeline(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        settings = build_meta_strategy_settings(settings_version="phase7-settings-v1", created_at=NOW)
        job = repository.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW, settings_version=settings.settings_version, now=NOW)
        provider = FixtureStateProvider(settings=settings)
        worker = MetaStrategyFinalisedBarDecisionWorker(repository=repository, state_provider=provider)
        worker.run_once(now=NOW)
        event_id = repository.read_payload(job.payload_reference)["payload"]["eventId"]
        decision = repository.decision_for_event(event_id)
        direct = run_meta_strategy_execution_pipeline(
            MetaStrategyExecutionPipelineRequest(mode="PAPER", snapshot_request=provider.request),
            config=MetaStrategyExecutionPipelineConfig(submit_to_broker=False),
            config_settings=settings,
        )
        backtest = run_meta_strategy_backtest(MetaStrategyBacktestRequest(decision_requests=(provider.request,)))

        self.assertEqual(decision["payload"]["reasonCodes"], list(direct.reason_codes))
        self.assertEqual(decision["payload"]["stages"]["orderProposal"], jsonable(direct.stage_results["order_intent"]))
        self.assertEqual(backtest.decisions[0].stage_results["order_intent"]["status"], direct.stage_results["order_intent"]["status"])
        self.assertEqual(backtest.decisions[0].stage_results["order_intent"]["quantity"], direct.stage_results["order_intent"]["quantity"])
        self.assertEqual(jsonable(backtest.decisions[0].stage_results["order_intent"]["reasonCodes"]), jsonable(direct.stage_results["order_intent"]["reasonCodes"]))

    def test_no_broker_call_occurs_in_decision_worker(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        settings = build_meta_strategy_settings(settings_version="phase7-settings-v1", created_at=NOW)
        job = repository.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW, settings_version=settings.settings_version, now=NOW)
        worker = MetaStrategyFinalisedBarDecisionWorker(repository=repository, state_provider=FixtureStateProvider(settings=settings))

        worker.run_once(now=NOW)

        event_id = repository.read_payload(job.payload_reference)["payload"]["eventId"]
        decision = repository.decision_for_event(event_id)
        self.assertIn("meta_strategy.pipeline.broker_skipped_in_decision_worker", decision["payload"]["reasonCodes"])


class FixtureStateProvider:
    def __init__(self, *, settings):
        self.settings = settings
        self.request = request_with().model_copy(update={"decision_id": "phase7-decision", "snapshot_id": "phase7-snapshot"})
        self.latest_by_symbol: dict[str, datetime] = {}

    def load_context(self, event):
        last = self.latest_by_symbol.get(event.symbol)
        if last is not None and event.bar_end < last:
            raise RuntimeError("meta_strategy.decision_worker.out_of_order_finalised_bar")
        self.latest_by_symbol[event.symbol] = event.bar_end
        return MetaStrategyDecisionWorkerContext(
            event=event,
            settings=self.settings,
            market_snapshot_request=self.request,
            inventory_snapshot={"positions": []},
            account_snapshot={"source": "read_only_account_view"},
            global_risk_snapshot={"source": "read_only_global_risk"},
            event_state={"blackout": False},
            operational_health={"status": "ok"},
            active_model_artifact={"modelVersion": "phase7-model"},
        )


class FailOnceStateProvider(FixtureStateProvider):
    def __init__(self, *, settings):
        super().__init__(settings=settings)
        self.failed = False

    def load_context(self, event):
        if not self.failed:
            self.failed = True
            raise RuntimeError("crash before persistence")
        return super().load_context(event)


def fake_order_runner(request, settings, global_risk_snapshot):
    snapshot = SimpleNamespace(decision_id="phase7-order-decision")
    return SimpleNamespace(
        snapshot=snapshot,
        settings_version=settings.settings_version,
        effective_settings_hash=settings.effective_settings_hash,
        order_intent={"orderIntentId": "intent-phase7", "symbol": "SPY", "side": "BUY", "quantity": 1},
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


def temp_db_path() -> str:
    return str((Path("data/test_tmp") / f"meta-strategy-phase7-{uuid4().hex}.sqlite").resolve())


def jsonable(value):
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [jsonable(item) for item in value]
    return value
