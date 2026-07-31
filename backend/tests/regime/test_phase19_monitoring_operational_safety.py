from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime_health import RegimeRuntimeMetrics, health_from_metrics, operational_snapshot_from_metrics
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisor, RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.service import RegimeApplicationService


IDENTITY = {
    "algorithmId": "regime",
    "algorithmInstanceId": "regime-phase19",
    "accountId": "paper-phase19",
    "runtimeMode": "paper",
    "symbol": "SPY",
}


def test_phase19_kill_switch_blocks_entries_persists_and_requests_pending_entry_cancel() -> None:
    repository = _repository()
    repository.insert_execution_outbox_record(
        IDENTITY,
        {
            **IDENTITY,
            "decisionId": "phase19-decision-1",
            "orderIntentId": "phase19-intent-entry",
            "side": "Buy",
            "positionEffect": "enter_long",
            "quantity": 1,
            "globalRiskReservationId": "phase19-reservation",
        },
    )
    supervisor = _supervisor(repository)

    result = asyncio.run(supervisor.submit_command("kill_switch_activate", {"reason": "phase19-test", "cancelPendingEntries": True}, actor="ops-user"))
    outbox = repository.read_execution_outbox_record(IDENTITY, "phase19-intent-entry")
    persisted = repository.read_runtime_snapshot(IDENTITY, "kill_switch")

    assert result["accepted"] is True
    assert result["immediate"] is True
    assert result["killSwitch"]["active"] is True
    assert "regime.runtime.kill_switch_active" in supervisor.metrics.entry_block_reason_codes
    assert supervisor.metrics.risk_reducing_exits_allowed is True
    assert outbox["processingStatus"] == "cancel_requested"
    assert persisted["active"] is True
    assert persisted["reason"] == "phase19-test"
    assert repository.read_owned_records("regime_runtime_events", IDENTITY)


def test_phase19_kill_switch_survives_supervisor_restart() -> None:
    repository = _repository()
    supervisor = _supervisor(repository)
    asyncio.run(supervisor.submit_command("kill_switch_activate", {"reason": "persisted"}, actor="ops-user"))

    restarted = _supervisor(repository)
    restarted._load_persisted_kill_switch()

    assert restarted.kill_switch_status()["active"] is True
    assert restarted.kill_switch_status()["reason"] == "persisted"
    assert "regime.runtime.kill_switch_active" in restarted.metrics.entry_block_reason_codes


def test_phase19_health_fails_closed_for_required_operational_failures() -> None:
    metrics = RegimeRuntimeMetrics(
        supervisor_started=True,
        recovery_succeeded=True,
        inventory_reconciled=True,
        broker_paper_mode_verified=True,
        broker_connectivity_ok=True,
    )
    metrics.settings_available = False
    metrics.inventory_available = False
    metrics.reconciliation_discrepancies = 1
    metrics.outbox_stuck = True
    metrics.risk_reservations_consistent = False
    metrics.strategy_registry_valid = False

    health = health_from_metrics(metrics)

    assert health["healthy"] is False
    assert health["settingsAvailable"] is False
    assert health["inventoryAvailable"] is False
    assert health["outboxStuck"] is True
    assert health["riskReservationsConsistent"] is False
    assert "settings_repository" in health["unhealthyComponents"]
    assert "inventory" in health["unhealthyComponents"]
    assert "execution_outbox" in health["unhealthyComponents"]
    assert "risk_reservations" in health["unhealthyComponents"]
    assert "strategy_registry" in health["unhealthyComponents"]


def test_phase19_operational_snapshot_exposes_required_regime_telemetry() -> None:
    metrics = RegimeRuntimeMetrics(supervisor_started=True, supervisor_heartbeat_at="2026-07-31T16:00:00Z")
    metrics.last_received_bar = {"symbol": "SPY", "barCloseTimestamp": "2026-07-31T15:59:00Z"}
    metrics.last_finalized_bar = {"symbol": "SPY", "barCloseTimestamp": "2026-07-31T15:59:00Z"}
    metrics.last_processed_bar = {"symbol": "SPY", "barCloseTimestamp": "2026-07-31T15:59:00Z"}
    metrics.queue_depth = 3
    metrics.queue_lag_seconds = 4.5
    metrics.duplicate_events = 2
    metrics.missing_bar_count = 1
    metrics.active_settings_version = "settings-v1"
    metrics.current_inventory = {"quantity": 1}
    metrics.open_orders = [{"orderIntentId": "intent-1"}]
    metrics.risk_reservations = [{"reservationId": "reservation-1"}]
    metrics.outbox_status = {"pendingCount": 1}
    metrics.reconciliation_status = {"reconciled": True}
    metrics.broker_connectivity = {"verified": True}
    metrics.daily_regime_pnl = 12.34
    metrics.daily_trade_count = 2
    metrics.kill_switch_active = True
    metrics.kill_switch_reason = "test"

    snapshot = operational_snapshot_from_metrics(metrics)

    assert snapshot["algorithmId"] == "regime"
    for key in (
        "supervisorHeartbeat",
        "lastReceivedBar",
        "lastFinalizedBar",
        "lastProcessedBar",
        "queueDepth",
        "queueLag",
        "duplicateBarCount",
        "missingBarCount",
        "staleDataState",
        "currentConfirmedRegime",
        "currentStrategyRouting",
        "entryBlockers",
        "activeSettingsVersion",
        "currentInventory",
        "openOrders",
        "riskReservations",
        "outboxStatus",
        "reconciliationStatus",
        "brokerConnectivity",
        "dailyRegimePnl",
        "dailyTradeCount",
        "killSwitch",
    ):
        assert key in snapshot
    assert snapshot["killSwitch"]["active"] is True
    assert snapshot["dailyRegimePnl"] == 12.34


def _repository() -> RegimeRepository:
    root = Path(__file__).resolve().parents[1] / "tmp" / "regime_phase19"
    root.mkdir(parents=True, exist_ok=True)
    return RegimeRepository(f"sqlite:///{root / f'{uuid4().hex}.sqlite'}")


def _supervisor(repository: RegimeRepository) -> RegimeRuntimeSupervisor:
    return RegimeRuntimeSupervisor(
        service=RegimeApplicationService(repository=repository),
        config=RegimeRuntimeSupervisorConfig(
            queue_maxsize=8,
            command_queue_maxsize=4,
            default_algorithm_instance_id=IDENTITY["algorithmInstanceId"],
            default_account_id=IDENTITY["accountId"],
            default_runtime_mode=IDENTITY["runtimeMode"],
            symbol=IDENTITY["symbol"],
            maintenance_interval_seconds=60,
            heartbeat_interval_seconds=60,
        ),
    )
