from __future__ import annotations

import sqlite3
from datetime import timedelta
from unittest.mock import patch

import pytest

from backend.app.algorithms.wca.contracts import WcaLatencyMetrics, WcaLatencySnapshot, WcaLatencyTimestamps
from backend.app.algorithms.wca.runtime_health import WcaRuntimeHealthSnapshot
from backend.app.algorithms.wca.runtime_repository import WcaRuntimeRepository
from backend.app.algorithms.wca.runtime_supervisor import WcaRuntimeSettings, WcaRuntimeSupervisor
from backend.tests.test_wca_phase2_runtime_state import seeded_repository
from backend.tests.test_wca_phase3_finalized_event_publisher import publish
from backend.tests.test_wca_step6_inventory_persistence import decision_with_order


def test_latency_observations_persist_percentiles_maximum_and_failure_counts() -> None:
    repository, snapshot = seeded_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    latency = WcaLatencySnapshot(
        timestamps=WcaLatencyTimestamps(
            candle_open=snapshot.data_timestamp - timedelta(minutes=1),
            candle_close=snapshot.data_timestamp,
            bar_finalization=snapshot.data_timestamp + timedelta(seconds=1),
            event_publication=snapshot.data_timestamp + timedelta(seconds=2),
            event_receipt=snapshot.data_timestamp + timedelta(seconds=4),
            event_claimed=snapshot.data_timestamp + timedelta(seconds=5),
            decision_start=snapshot.data_timestamp + timedelta(seconds=5),
            snapshot_construction_start=snapshot.data_timestamp + timedelta(seconds=4),
            snapshot_completion=snapshot.data_timestamp + timedelta(seconds=5),
            strategy_start=snapshot.data_timestamp + timedelta(seconds=5),
            strategy_completion=snapshot.data_timestamp + timedelta(seconds=6),
            aggregation_start=snapshot.data_timestamp + timedelta(seconds=6),
            aggregation_completion=snapshot.data_timestamp + timedelta(seconds=7),
            risk_validation_start=snapshot.data_timestamp + timedelta(seconds=7),
            risk_validation_completion=snapshot.data_timestamp + timedelta(seconds=8),
            outbox_queued=snapshot.data_timestamp + timedelta(seconds=8),
            outbox_claimed=snapshot.data_timestamp + timedelta(seconds=10),
            broker_request=snapshot.data_timestamp + timedelta(seconds=10),
            broker_acknowledgement=snapshot.data_timestamp + timedelta(seconds=12),
            first_fill=snapshot.data_timestamp + timedelta(seconds=15),
            final_fill=snapshot.data_timestamp + timedelta(seconds=16),
        ),
        metrics=WcaLatencyMetrics(
            candle_finalization_delay_seconds=1,
            event_publication_delay_seconds=1,
            queue_delay_seconds=3,
            event_receipt_delay_seconds=2,
            snapshot_construction_seconds=1,
            strategy_evaluation_seconds=1,
            aggregation_seconds=1,
            risk_validation_seconds=1,
            outbox_delay_seconds=2,
            broker_submission_seconds=2,
            broker_acknowledgement_seconds=2,
            fill_delay_seconds=3,
            decision_to_fill_seconds=11,
        ),
    )

    runtime_repository.record_latency_snapshot(latency, account_id="paper", symbol="SPY", timestamp=snapshot.decision_timestamp)
    runtime_repository.record_latency_observation(component="broker_submission", value_seconds=None, account_id="paper", symbol="SPY", failed=True)

    summaries = runtime_repository.read_latency_summaries(account_id="paper", symbol="SPY")

    for component in (
        "candle_finalization_delay",
        "event_publication_delay",
        "queue_delay",
        "event_receipt_delay",
        "snapshot_construction",
        "strategy_evaluation",
        "aggregation",
        "risk_validation",
        "outbox_delay",
        "broker_submission",
        "broker_acknowledgement",
        "fill_delay",
        "decision_to_fill",
    ):
        assert summaries[component]["sample_count"] >= 1
        assert "p50_seconds" in summaries[component]
        assert "p95_seconds" in summaries[component]
        assert "p99_seconds" in summaries[component]
        assert "max_seconds" in summaries[component]
    assert summaries["broker_submission"]["failure_count"] == 1


@pytest.mark.parametrize(
    ("reason", "prior_health_update", "settings_update", "database_available", "age_queue"),
    (
        ("wca.runtime.health.worker_heartbeat", {"stale_worker_heartbeat": True}, {}, True, False),
        ("wca.runtime.health.broker_available", {"broker_available": False}, {}, True, False),
        ("wca.runtime.health.market_data_available", {"market_data_available": False}, {}, True, False),
        ("wca.runtime.health.clock_skew", {"clock_skew_seconds": 10}, {}, True, False),
        ("wca.runtime.health.unprotected_position_clear", {"unprotected_position": True}, {}, True, False),
        ("wca.runtime.health.duplicate_order_evidence_clear", {"duplicate_order_evidence": True}, {}, True, False),
        ("wca.runtime.health.configuration_ready", {"configuration_ready": False}, {}, True, False),
        ("wca.runtime.health.weight_calibration_ready", {"weight_calibration_ready": False}, {}, True, False),
        ("wca.runtime.health.circuit_breaker_closed", {"circuit_breaker_open": True}, {}, True, False),
        ("wca.runtime.health.queue_depth", None, {"max_event_queue_depth": 0}, True, False),
        ("wca.runtime.health.queue_age", None, {"max_queue_delay_seconds": 1}, True, True),
        ("wca.runtime.health.database_available", None, {}, False, False),
    ),
)
def test_critical_health_failures_stop_entries_and_keep_protective_exits_operational(reason, prior_health_update, settings_update, database_available, age_queue) -> None:
    calls, result, runtime_repository = run_health_case(
        prior_health_update=prior_health_update,
        settings_update=settings_update,
        database_available=database_available,
        age_queue=age_queue,
    )

    assert calls[0].global_gate_quantity_cap == 0
    assert reason in result["workers"]["decision_worker"]["reasonCodes"]
    assert result["workers"]["position_and_protective_exit_worker"]["status"] == "completed"
    health = runtime_repository.read_latest_runtime_health()
    assert health is not None
    assert health.paused_new_entries is True
    assert health.protective_management_active is True


def test_stale_reconciliation_stops_entries_and_keeps_protective_exits_operational() -> None:
    repository, snapshot = seeded_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    publication = publish(snapshot, runtime_repository)
    with sqlite3.connect(repository.path) as conn:
        conn.execute(
            "UPDATE wca_broker_reconciliations SET timestamp = ?",
            ((snapshot.decision_timestamp - timedelta(seconds=300)).isoformat(),),
        )
    supervisor = WcaRuntimeSupervisor(
        repository=repository,
        runtime_repository=runtime_repository,
        settings=WcaRuntimeSettings(max_lag_seconds=99_999_999, max_quote_age_seconds=99_999_999, max_reconciliation_age_seconds=1),
        owner_id="phase12-reconciliation",
    )
    calls = []

    with patch("backend.app.algorithms.wca.runtime_supervisor.run_wca_paper_pipeline_adapter", side_effect=fake_pipeline(calls)):
        result = supervisor.run_once()

    assert publication.accepted
    assert calls[0].global_gate_quantity_cap == 0
    assert "wca.runtime.health.reconciliation_fresh" in result["workers"]["decision_worker"]["reasonCodes"]
    assert result["workers"]["position_and_protective_exit_worker"]["status"] == "completed"


def test_finalized_bar_and_quote_freshness_are_separate_entry_gates() -> None:
    bar_calls, bar_result, _ = run_health_case(settings_update={"max_lag_seconds": 20, "max_quote_age_seconds": 99_999_999}, now_offset_seconds=30)
    quote_calls, quote_result, _ = run_health_case(settings_update={"max_lag_seconds": 99_999_999, "max_quote_age_seconds": 5}, now_offset_seconds=30)

    assert bar_calls[0].global_gate_quantity_cap == 0
    assert "wca.runtime.health.finalized_bar_age_exceeded" in bar_result["workers"]["decision_worker"]["reasonCodes"]
    assert quote_calls[0].global_gate_quantity_cap == 0
    assert "wca.runtime.health.quote_age_exceeded" in quote_result["workers"]["decision_worker"]["reasonCodes"]


def run_health_case(
    *,
    prior_health_update: dict | None = None,
    settings_update: dict | None = None,
    database_available: bool = True,
    age_queue: bool = False,
    now_offset_seconds: int = 1,
):
    repository, snapshot = seeded_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    publication = publish(snapshot, runtime_repository)
    if prior_health_update is not None:
        update = dict(prior_health_update)
        if update.pop("stale_worker_heartbeat", False):
            update["worker_heartbeats"] = {"decision_worker": snapshot.decision_timestamp - timedelta(seconds=120)}
        runtime_repository.write_runtime_health(WcaRuntimeHealthSnapshot(**update))
    if age_queue:
        with sqlite3.connect(repository.path) as conn:
            old = (snapshot.decision_timestamp - timedelta(seconds=60)).isoformat()
            conn.execute("UPDATE wca_runtime_event_queue SET created_at = ?, updated_at = ?", (old, old))
            conn.execute("UPDATE wca_runtime_command_queue SET created_at = ?, updated_at = ?", (old, old))
    settings_payload = {
        "max_lag_seconds": 99_999_999,
        "max_quote_age_seconds": 99_999_999,
        "max_state_age_seconds": 99_999_999,
        "max_reconciliation_age_seconds": 99_999_999,
        **(settings_update or {}),
    }
    supervisor = WcaRuntimeSupervisor(
        repository=repository,
        runtime_repository=runtime_repository,
        settings=WcaRuntimeSettings(**settings_payload),
        owner_id="phase12-health",
    )
    calls = []
    now = snapshot.decision_timestamp + timedelta(seconds=now_offset_seconds)

    with (
        patch("backend.app.algorithms.wca.runtime_supervisor._utc_now", return_value=now),
        patch.object(runtime_repository, "database_available", return_value=database_available),
        patch("backend.app.algorithms.wca.runtime_supervisor.run_wca_paper_pipeline_adapter", side_effect=fake_pipeline(calls)),
    ):
        result = supervisor.run_once()

    assert publication.accepted
    return calls, result, runtime_repository


def fake_pipeline(calls):
    def _fake(pipeline_input):
        calls.append(pipeline_input)
        decision = decision_with_order("phase12-decision", "phase12-intent", "phase12-key")
        decision = decision.model_copy(update={"proposed_order": None})
        return type("PipelineResult", (), {"decision": decision})()

    return _fake
