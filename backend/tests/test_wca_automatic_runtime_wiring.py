from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from backend.app.algorithms.wca.contracts import WcaCandle, WcaPaperExecutionRequest
from backend.app.algorithms.wca.contracts import WcaRuntimeMode
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.algorithms.wca.runtime_control import WcaRuntimeControl
from backend.app.algorithms.wca.runtime_commands import WcaRuntimeCommandType, runtime_command
from backend.app.algorithms.wca.runtime_repository import WcaRuntimeRepository
from backend.app.algorithms.wca.runtime_supervisor import WCA_RUNTIME_COMMAND_CONSUMERS, WCA_RUNTIME_COMMAND_RETRY_POLICY, WCA_RUNTIME_WORKERS, WcaRuntimeSettings, WcaRuntimeSupervisor
from backend.app.algorithms.wca.service import WcaService
from backend.tests.test_wca_step7_background_runtime import seeded_repository as seeded_runtime_repository
from backend.tests.test_wca_step6_inventory_persistence import decision_with_order


def test_wca_runtime_supervisor_starts_and_stops_as_background_host() -> None:
    async def scenario() -> None:
        repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
        supervisor = WcaRuntimeSupervisor(
            repository=repository,
            runtime_repository=WcaRuntimeRepository(repository),
            settings=WcaRuntimeSettings(poll_seconds=0.01),
            owner_id="wca-runtime-wiring-test",
        )

        await supervisor.start()
        assert supervisor.status()["supervisorStarted"] is True
        assert "execution_outbox_worker" in supervisor.status()["workers"]

        await supervisor.shutdown()
        assert supervisor.status()["supervisorStarted"] is False

    asyncio.run(scenario())


def test_wca_publisher_requires_real_market_data_credentials(monkeypatch) -> None:
    import backend.app.main as main

    monkeypatch.setattr(main, "settings", SimpleNamespace(has_alpaca_credentials=False))

    wait_seconds = asyncio.run(main.run_wca_finalized_bar_tick({"isOpen": True}))

    assert wait_seconds == main.WCA_FINALIZED_BAR_CLOSED_POLL_SECONDS
    assert main.WCA_FINALIZED_BAR_STATUS["status"] == "waiting_for_credentials"
    assert "real Alpaca market-data credentials" in main.WCA_FINALIZED_BAR_STATUS["message"]


def test_fastapi_startup_launches_wca_publisher_but_not_wca_runtime_process() -> None:
    source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")

    assert "asyncio.create_task(wca_finalized_bar_scheduler())" in source
    assert "await get_wca_runtime_supervisor().start()" not in source
    assert "await get_wca_runtime_supervisor().shutdown()" not in source
    assert 'triggered_by="background_publisher"' in source
    assert "not settings.has_alpaca_credentials" in source


def test_wca_runtime_procfile_declares_independent_process() -> None:
    procfile = Path(__file__).resolve().parents[2] / "Procfile"
    source = procfile.read_text(encoding="utf-8")

    assert "wca-runtime: python -m backend.app.algorithms.wca.runtime_main" in source


def test_wca_runtime_scheduler_enqueues_operational_commands() -> None:
    repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
    runtime_repository = WcaRuntimeRepository(repository)
    supervisor = WcaRuntimeSupervisor(
        repository=repository,
        runtime_repository=runtime_repository,
        settings=WcaRuntimeSettings(poll_seconds=0.01),
        owner_id="wca-runtime-scheduler-test",
    )
    scheduler = next(worker for worker in supervisor.workers if worker.worker_name == "runtime_scheduler_worker")

    result = scheduler.run_once()

    assert result["status"] == "completed"
    assert WcaRuntimeCommandType.RECOVERY.value in result["scheduledTypes"]
    assert WcaRuntimeCommandType.BROKER_RECONCILIATION.value in result["scheduledTypes"]
    assert WcaRuntimeCommandType.HEARTBEAT.value in result["scheduledTypes"]
    assert result["marketReadinessChecked"] is True
    assert result["entryCutoffChecked"] is True


def test_wca_runtime_scheduler_generates_calendar_end_of_session_command(monkeypatch) -> None:
    repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
    runtime_repository = WcaRuntimeRepository(repository)
    supervisor = WcaRuntimeSupervisor(
        repository=repository,
        runtime_repository=runtime_repository,
        settings=WcaRuntimeSettings(poll_seconds=0.01),
        owner_id="wca-runtime-eod-scheduler-test",
    )
    scheduler = next(worker for worker in supervisor.workers if worker.worker_name == "runtime_scheduler_worker")
    near_close = datetime(2026, 1, 2, 20, 56, tzinfo=timezone.utc)
    monkeypatch.setattr("backend.app.algorithms.wca.runtime_supervisor._utc_now", lambda: near_close)

    result = scheduler.run_once()

    end_of_session = [item for item in result["scheduled"] if item["commandType"] == WcaRuntimeCommandType.END_OF_SESSION.value]
    assert end_of_session
    assert end_of_session[0]["accepted"] is True
    assert "2026-01-02" in end_of_session[0]["commandId"]


def test_wca_global_paper_control_is_background_enqueued_and_worker_applied() -> None:
    repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
    runtime_repository = WcaRuntimeRepository(repository)
    supervisor = WcaRuntimeSupervisor(
        repository=repository,
        runtime_repository=runtime_repository,
        settings=WcaRuntimeSettings(poll_seconds=0.01),
        owner_id="wca-runtime-control-test",
    )
    command = runtime_command(
        WcaRuntimeCommandType.SET_AUTOMATIC_PAPER,
        payload={"enabled": False, "actor": "test", "reason": "wca.test.global_paper_off"},
        priority=1,
    )

    queued = runtime_repository.enqueue_command(command)
    result = supervisor.run_once()
    health = runtime_repository.read_latest_runtime_health()

    assert queued.accepted is True
    assert result["workers"]["runtime_control_worker"]["status"] == "completed"
    assert health is not None
    assert health.paused_new_entries is True
    assert "wca.runtime_control.paper_trading_requested_off" in health.reason_codes
    control = repository.read_runtime_control()
    assert control.paper_trading_requested is False
    assert control.automatic_entries_requested is False
    assert control.pause_new_entries is True
    assert control.effective_automatic_entries_enabled is False
    assert control.control_revision == 2


def test_wca_runtime_control_defaults_fail_closed_when_missing() -> None:
    repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")

    control = repository.read_runtime_control(broker_account_id="paper", symbol="SPY")

    assert control.algorithm_id == "wca"
    assert control.paper_trading_requested is False
    assert control.automatic_entries_requested is False
    assert control.pause_new_entries is True
    assert control.effective_paper_trading_enabled is False
    assert control.effective_automatic_entries_enabled is False
    assert control.control_hash
    assert "wca.runtime_control.missing_default_fail_closed" in control.reason_codes


def test_wca_runtime_control_on_records_request_but_keeps_effective_off_when_gates_fail() -> None:
    repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
    runtime_repository = WcaRuntimeRepository(repository)
    supervisor = WcaRuntimeSupervisor(
        repository=repository,
        runtime_repository=runtime_repository,
        settings=WcaRuntimeSettings(poll_seconds=0.01),
        owner_id="wca-runtime-control-gates-test",
    )
    command = runtime_command(
        WcaRuntimeCommandType.SET_AUTOMATIC_PAPER,
        payload={"enabled": True, "actor": "test", "reason": "wca.test.global_paper_on"},
        priority=1,
    )

    runtime_repository.enqueue_command(command)
    result = supervisor.run_once()
    control = repository.read_runtime_control()

    assert result["workers"]["runtime_control_worker"]["status"] == "completed"
    assert control.paper_trading_requested is True
    assert control.automatic_entries_requested is True
    assert control.effective_automatic_entries_enabled is False
    assert "wca.runtime_control.paper_account_unverified" in control.reason_codes
    assert "wca.runtime_control.rollout_automatic_paper_blocked" in control.reason_codes


def test_wca_execution_outbox_blocks_stale_runtime_control_before_submission() -> None:
    repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
    runtime_repository = WcaRuntimeRepository(repository)
    supervisor = WcaRuntimeSupervisor(
        repository=repository,
        runtime_repository=runtime_repository,
        settings=WcaRuntimeSettings(runtime_mode=WcaRuntimeMode.AUTOMATIC_PAPER, poll_seconds=0.01),
        owner_id="wca-stale-control-test",
    )
    decision = decision_with_order("wca-stale-control-decision", "wca-stale-control-intent", "wca-stale-control-key")
    assert decision.proposed_order is not None
    on_control = WcaRuntimeControl(
        paper_trading_requested=True,
        automatic_entries_requested=True,
        pause_new_entries=False,
        effective_paper_trading_enabled=True,
        effective_automatic_entries_enabled=True,
        control_revision=1,
        reason="wca.test.control_on",
        reason_codes=("wca.test.control_on",),
    ).with_hash()
    repository.write_runtime_control(on_control)
    proposed = decision.proposed_order.model_copy(
        update={
            "runtime_control_revision": on_control.control_revision,
            "runtime_control_hash": on_control.control_hash,
            "weight_version": decision.weight_version,
        }
    )
    decision = decision.model_copy(
        update={
            "proposed_order": proposed,
            "runtime_control_revision": on_control.control_revision,
            "runtime_control_hash": on_control.control_hash,
        }
    )
    off_control = on_control.model_copy(
        update={
            "paper_trading_requested": False,
            "automatic_entries_requested": False,
            "pause_new_entries": True,
            "effective_paper_trading_enabled": False,
            "effective_automatic_entries_enabled": False,
            "control_revision": 2,
            "reason": "wca.test.paper_off",
            "reason_codes": ("wca.test.paper_off",),
        }
    ).with_hash()
    repository.write_runtime_control(off_control)
    command = runtime_command(
        WcaRuntimeCommandType.EXECUTION_OUTBOX,
        payload={"decision": decision.model_dump(mode="json")},
        decision_id=decision.decision_id,
        run_id="wca-stale-control-run",
        account_id="paper",
        symbol="SPY",
    )

    runtime_repository.enqueue_command(command)
    result = supervisor.run_once()

    worker = result["workers"]["execution_outbox_worker"]
    assert worker["status"] == "blocked"
    assert worker["submitted"] is False
    assert "wca.runtime_control.decision_control_revision_stale" in worker["reasonCodes"]
    assert "wca.runtime_control.effective_automatic_entries_disabled" in worker["reasonCodes"]


def test_wca_global_risk_worker_persists_fresh_approval_before_outbox() -> None:
    repository = seeded_runtime_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    supervisor = WcaRuntimeSupervisor(
        repository=repository,
        runtime_repository=runtime_repository,
        settings=WcaRuntimeSettings(max_state_age_seconds=999_999_999, runtime_mode=WcaRuntimeMode.AUTOMATIC_PAPER, poll_seconds=0.01),
        owner_id="wca-global-risk-stage-test",
    )
    decision = decision_with_order("wca-global-risk-decision", "wca-global-risk-intent", "wca-global-risk-key")
    assert decision.proposed_order is not None
    command = runtime_command(
        WcaRuntimeCommandType.GLOBAL_RISK_REQUEST,
        payload={"decision": decision.model_dump(mode="json")},
        decision_id=decision.decision_id,
        run_id="wca-global-risk-run",
        account_id="paper",
        symbol="SPY",
    )

    runtime_repository.enqueue_command(command)
    result = supervisor.run_once()
    approval = repository.read_global_risk_approval(decision_id=decision.decision_id)

    assert result["workers"]["global_risk_request_worker"]["status"] == "completed"
    assert approval is not None
    assert approval.global_risk_decision_id
    assert approval.evaluated_at is not None
    assert approval.expires_at is not None
    assert approval.entry_permitted is True
    assert approval.allowed_quantity <= approval.requested_quantity
    assert approval.approved_risk_dollars >= 0
    assert approval.global_state_hash
    assert "wca.global_risk.durable_approval_persisted" in approval.reason_codes


def test_wca_execution_outbox_requires_persisted_global_risk_approval() -> None:
    repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
    runtime_repository = WcaRuntimeRepository(repository)
    supervisor = WcaRuntimeSupervisor(
        repository=repository,
        runtime_repository=runtime_repository,
        settings=WcaRuntimeSettings(runtime_mode=WcaRuntimeMode.AUTOMATIC_PAPER, poll_seconds=0.01),
        owner_id="wca-missing-global-risk-test",
    )
    decision = decision_with_order("wca-missing-risk-decision", "wca-missing-risk-intent", "wca-missing-risk-key")
    assert decision.proposed_order is not None
    control = WcaRuntimeControl(
        paper_trading_requested=True,
        automatic_entries_requested=True,
        pause_new_entries=False,
        effective_paper_trading_enabled=True,
        effective_automatic_entries_enabled=True,
        rollout_stage="AUTOMATIC_PAPER",
        control_revision=1,
        reason="wca.test.control_on",
        reason_codes=("wca.test.control_on",),
    ).with_hash()
    repository.write_runtime_control(control)
    proposed = decision.proposed_order.model_copy(
        update={
            "runtime_control_revision": control.control_revision,
            "runtime_control_hash": control.control_hash,
        }
    )
    decision = decision.model_copy(
        update={
            "proposed_order": proposed,
            "runtime_control_revision": control.control_revision,
            "runtime_control_hash": control.control_hash,
        }
    )
    command = runtime_command(
        WcaRuntimeCommandType.EXECUTION_OUTBOX,
        payload={"decision": decision.model_dump(mode="json")},
        decision_id=decision.decision_id,
        run_id="wca-missing-risk-run",
        account_id="paper",
        symbol="SPY",
    )

    runtime_repository.enqueue_command(command)
    execution_worker = next(worker for worker in supervisor.workers if worker.worker_name == "execution_outbox_worker")
    worker = execution_worker.run_once()

    assert worker["status"] == "blocked"
    assert worker["submitted"] is False
    assert "wca.runtime.execution_outbox.global_risk_approval_missing" in worker["reasonCodes"]


def test_wca_runtime_command_consumer_registry_covers_every_command_enum() -> None:
    assert set(WCA_RUNTIME_COMMAND_CONSUMERS) == set(WcaRuntimeCommandType)
    assert set(WCA_RUNTIME_COMMAND_CONSUMERS.values()) <= set(WCA_RUNTIME_WORKERS)
    assert WCA_RUNTIME_COMMAND_RETRY_POLICY["leaseExpiration"] == "running_commands_are_requeued_by_recovery_worker"
    assert set(WCA_RUNTIME_COMMAND_RETRY_POLICY["terminalStatuses"]) == {"completed", "blocked", "failed"}


def test_wca_recovery_worker_fails_unsupported_queued_command_types() -> None:
    repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
    runtime_repository = WcaRuntimeRepository(repository)
    supervisor = WcaRuntimeSupervisor(
        repository=repository,
        runtime_repository=runtime_repository,
        settings=WcaRuntimeSettings(poll_seconds=0.01),
        owner_id="wca-unsupported-command-test",
    )
    command_id = "wca-unsupported-command"
    timestamp = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    with repository.connect() as conn:
        conn.execute(
            """
            INSERT INTO wca_runtime_command_queue (
                command_id, algorithm_id, account_id, symbol, timestamp,
                configuration_version, engine_version, market_snapshot_id,
                decision_id, run_id, event_id, command_type, priority, status,
                reason_codes_json, payload_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                command_id,
                "wca",
                "paper",
                "SPY",
                timestamp.isoformat(),
                "phase14",
                "phase14",
                "unsupported-snapshot",
                "unsupported-decision",
                "unsupported-run",
                None,
                "UNSUPPORTED_FUTURE_COMMAND",
                1,
                "queued",
                "[]",
                "{}",
                timestamp.isoformat(),
            ),
        )
    recovery_worker = next(worker for worker in supervisor.workers if worker.worker_name == "recovery_worker")

    result = recovery_worker.run_once()

    assert result["unsupported_commands_failed"] == 1
    assert "wca.runtime.command.unsupported_failed" in result["reasonCodes"]
    with repository.connect() as conn:
        row = conn.execute(
            "SELECT status, reason_codes_json FROM wca_runtime_command_queue WHERE command_id = ?",
            (command_id,),
        ).fetchone()
    assert row["status"] == "failed"
    reasons = json.loads(row["reason_codes_json"])
    assert "wca.runtime.command.unsupported" in reasons
    assert "wca.runtime.command.failed" in reasons


def test_every_wca_service_api_runtime_command_reaches_terminal_worker_state() -> None:
    repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
    service = WcaService(repository=repository)
    runtime_repository = WcaRuntimeRepository(repository)
    supervisor = WcaRuntimeSupervisor(
        repository=repository,
        runtime_repository=runtime_repository,
        settings=WcaRuntimeSettings(poll_seconds=0.01),
        owner_id="wca-api-command-consumer-test",
    )
    active = repository.read_active_configuration()
    assert active is not None

    receipts = [
        service.enqueue_paper_command(WcaPaperExecutionRequest(candles=_runtime_command_candles(), runId="api-manual-command")),
        service.enqueue_pause_new_entries(reason="api-test-pause"),
        service.enqueue_resume_new_entries(reason="api-test-resume"),
        service.enqueue_automatic_paper_control(enabled=False, actor="test", reason="api-test-paper-off"),
        service.enqueue_configuration_activation(active.configuration_version),
        service.enqueue_configuration_rollback(active.configuration_version),
        service.enqueue_reconciliation_request(account_id="paper", symbol="SPY"),
        service.enqueue_emergency_risk_reduction(account_id="paper", symbol="SPY", reason="api-test-emergency"),
    ]
    expected_types = {
        WcaRuntimeCommandType.MANUAL_PAPER_COMMAND.value,
        WcaRuntimeCommandType.PAUSE_NEW_ENTRIES.value,
        WcaRuntimeCommandType.RESUME_NEW_ENTRIES.value,
        WcaRuntimeCommandType.SET_AUTOMATIC_PAPER.value,
        WcaRuntimeCommandType.CONFIGURATION_ACTIVATION.value,
        WcaRuntimeCommandType.CONFIGURATION_ROLLBACK.value,
        WcaRuntimeCommandType.BROKER_RECONCILIATION.value,
        WcaRuntimeCommandType.EMERGENCY_RISK_REDUCTION.value,
    }
    assert {receipt["commandType"] for receipt in receipts} == expected_types

    terminal = {"completed", "blocked", "failed"}
    for receipt in receipts:
        command_id = receipt["commandId"]
        for _ in range(20):
            supervisor.run_once()
            status = service.command_status(command_id)
            if status["status"] in terminal:
                break
        else:
            raise AssertionError(f"{receipt['commandType']} did not reach a terminal state")
        status = service.command_status(command_id)
        assert status["status"] in terminal
        assert status["reasonCodes"]


def test_wca_global_paper_control_route_and_frontend_fanout_are_present() -> None:
    api_source = (Path(__file__).resolve().parents[1] / "app" / "algorithms" / "wca" / "api.py").read_text(encoding="utf-8")
    frontend_source = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "main.ts").read_text(encoding="utf-8")
    frontend_api_source = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / "wca" / "api.ts").read_text(encoding="utf-8")

    assert "/runtime/automatic-paper" in api_source
    assert "enqueue_automatic_paper_control" in api_source
    assert "syncWcaAutomaticPaperControl" in frontend_source
    assert "setWcaAutomaticPaperTrading" in frontend_api_source


def temp_db_path() -> Path:
    root = Path.cwd() / "backend" / "data" / "test_tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"wca-runtime-wiring-{uuid4().hex}.sqlite"


def _runtime_command_candles() -> tuple[WcaCandle, ...]:
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    return tuple(
        WcaCandle(
            timestamp=base + timedelta(minutes=index),
            open=100 + index * 0.01,
            high=101 + index * 0.01,
            low=99 + index * 0.01,
            close=100.5 + index * 0.01,
            volume=100000,
        )
        for index in range(30)
    )
