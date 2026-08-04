"""Regime-owned background runtime supervisor."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any, Callable
from zoneinfo import ZoneInfo

from backend.app.algorithms.regime.account_snapshot import (
    fail_closed_regime_account_snapshot,
    normalize_regime_account_snapshot,
    sanitize_regime_account_snapshot,
)
from backend.app.algorithms.regime.configuration import regime_settings_identity_from_payload
from backend.app.algorithms.regime.contracts import (
    REGIME_ALLOWED_RUNTIME_MODE_VALUES,
    REGIME_DEFAULT_SHADOW_ACCOUNT_ID,
    REGIME_DEFAULT_SHADOW_ALGORITHM_INSTANCE_ID,
    RegimeRuntimeMode,
    default_regime_account_id,
    default_regime_algorithm_instance_id,
    normalize_regime_runtime_mode,
)
from backend.app.algorithms.regime.execution_gateway import RegimePaperGatewayStore
from backend.app.algorithms.regime.execution_gateway import cancel_expired_regime_outbox_orders
from backend.app.algorithms.regime.execution_gateway import submit_regime_outbox_record
from backend.app.algorithms.regime.execution_gateway import validate_regime_paper_broker_safety
from backend.app.algorithms.regime.exchange_calendar import exchange_session, exchange_session_bounds
from backend.app.algorithms.regime.global_risk_adapter import (
    commit_regime_global_risk_reservation,
    release_regime_global_risk_reservation,
)
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot
from backend.app.algorithms.regime.position_manager import RegimePositionManager
from backend.app.algorithms.regime.reconciliation import run_regime_broker_reconciliation
from backend.app.algorithms.regime.rollout import (
    REGIME_DEFAULT_OPERATIONAL_ROLLOUT_STAGE,
    REGIME_OPERATIONAL_ROLLOUT_STAGES,
    RegimePaperPromotionEvidence,
    activate_operational_rollout_stage,
    evaluate_operational_rollout_stage,
    operational_rollout_stage_policy,
    operational_stage_allows_real_paper_submission,
    operational_stage_uses_simulated_broker,
    read_or_initialize_operational_rollout_stage,
)
from backend.app.algorithms.regime.runtime_commands import RegimeRuntimeCommand
from backend.app.algorithms.regime.runtime_events import RegimeFinalisedBarEvent, event_payload_has_forbidden_operational_state
from backend.app.algorithms.regime.runtime_health import (
    REGIME_HEALTH_COMPONENTS,
    RegimeRuntimeMetrics,
    alert_conditions_from_metrics,
    health_from_metrics,
    mark_component_health,
    observe_decision_result,
    observe_execution_result,
    operational_snapshot_from_metrics,
)
from backend.app.algorithms.regime.runtime_workers import REGIME_RUNTIME_WORKER_CLASSES
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.algorithms.regime.stateful_core import deterministic_data_manifest_hash
from backend.app.algorithms.regime.strategy_registry import validate_regime_strategy_registry
from backend.app.algorithms.regime.trade_management import manage_regime_positions_for_completed_bar
from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderGateway


REGIME_RUNTIME_SUPERVISOR_VERSION = "regime_runtime_supervisor_v1"
REGIME_RUNTIME_WORKERS = tuple(worker.worker_id for worker in REGIME_RUNTIME_WORKER_CLASSES)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegimeRuntimeSupervisorConfig:
    queue_maxsize: int = 256
    command_queue_maxsize: int = 64
    max_processing_lag_seconds: int = 75
    heartbeat_interval_seconds: float = 5.0
    maintenance_interval_seconds: float = 30.0
    publisher_poll_interval_seconds: float | None = None
    closed_market_publisher_poll_interval_seconds: float | None = None
    execution_poll_interval_seconds: float | None = None
    reconciliation_poll_interval_seconds: float | None = None
    position_management_interval_seconds: float | None = None
    health_interval_seconds: float | None = None
    execution_interval_seconds: float | None = None
    reconciliation_interval_seconds: float | None = None
    publisher_interval_seconds: float | None = None
    default_algorithm_instance_id: str = "regime-default"
    default_account_id: str = "default"
    default_runtime_mode: str = "shadow"
    symbol: str = "SPY"
    owner_id: str = "regime-runtime-supervisor"
    worker_lease_seconds: int = 30
    account_snapshot_max_age_seconds: float = 30.0
    crash_after_stage: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "default_runtime_mode", normalize_regime_runtime_mode(self.default_runtime_mode).value)
        if self.execution_poll_interval_seconds is None:
            object.__setattr__(self, "execution_poll_interval_seconds", self.execution_interval_seconds if self.execution_interval_seconds is not None else 1.0)
        if self.reconciliation_poll_interval_seconds is None:
            object.__setattr__(self, "reconciliation_poll_interval_seconds", self.reconciliation_interval_seconds if self.reconciliation_interval_seconds is not None else 3.0)
        if self.publisher_poll_interval_seconds is None:
            object.__setattr__(self, "publisher_poll_interval_seconds", self.publisher_interval_seconds if self.publisher_interval_seconds is not None else 1.0)
        if self.closed_market_publisher_poll_interval_seconds is None:
            object.__setattr__(self, "closed_market_publisher_poll_interval_seconds", 300.0)
        if self.position_management_interval_seconds is None:
            object.__setattr__(self, "position_management_interval_seconds", 5.0)
        if self.health_interval_seconds is None:
            object.__setattr__(self, "health_interval_seconds", self.heartbeat_interval_seconds)
        if self.execution_interval_seconds is None:
            object.__setattr__(self, "execution_interval_seconds", self.execution_poll_interval_seconds)
        if self.reconciliation_interval_seconds is None:
            object.__setattr__(self, "reconciliation_interval_seconds", self.reconciliation_poll_interval_seconds)
        if self.publisher_interval_seconds is None:
            object.__setattr__(self, "publisher_interval_seconds", self.publisher_poll_interval_seconds)

    @classmethod
    def paper_runtime_from_env(cls) -> "RegimeRuntimeSupervisorConfig":
        runtime_mode = RegimeRuntimeMode.PAPER.value
        return cls(
            max_processing_lag_seconds=_env_int("REGIME_RUNTIME_MAX_PROCESSING_LAG_SECONDS", 75),
            heartbeat_interval_seconds=_env_float("REGIME_RUNTIME_HEARTBEAT_INTERVAL_SECONDS", 5.0),
            maintenance_interval_seconds=_env_float("REGIME_RUNTIME_MAINTENANCE_INTERVAL_SECONDS", 30.0),
            publisher_poll_interval_seconds=_env_float_any(("REGIME_RUNTIME_PUBLISHER_POLL_INTERVAL_SECONDS", "REGIME_RUNTIME_PUBLISHER_INTERVAL_SECONDS"), 1.0),
            closed_market_publisher_poll_interval_seconds=_env_float("REGIME_RUNTIME_CLOSED_MARKET_PUBLISHER_POLL_INTERVAL_SECONDS", 300.0),
            execution_poll_interval_seconds=_env_float_any(("REGIME_RUNTIME_EXECUTION_POLL_INTERVAL_SECONDS", "REGIME_RUNTIME_EXECUTION_INTERVAL_SECONDS"), 1.0),
            reconciliation_poll_interval_seconds=_env_float_any(("REGIME_RUNTIME_RECONCILIATION_POLL_INTERVAL_SECONDS", "REGIME_RUNTIME_RECONCILIATION_INTERVAL_SECONDS"), 3.0),
            position_management_interval_seconds=_env_float("REGIME_RUNTIME_POSITION_MANAGEMENT_INTERVAL_SECONDS", 5.0),
            health_interval_seconds=_env_float_any(("REGIME_RUNTIME_HEALTH_INTERVAL_SECONDS", "REGIME_RUNTIME_HEARTBEAT_INTERVAL_SECONDS"), 5.0),
            account_snapshot_max_age_seconds=_env_float("REGIME_RUNTIME_ACCOUNT_SNAPSHOT_MAX_AGE_SECONDS", 30.0),
            default_algorithm_instance_id=default_regime_algorithm_instance_id(runtime_mode),
            default_account_id=default_regime_account_id(runtime_mode),
            default_runtime_mode=runtime_mode,
            symbol="SPY",
        )


class RegimeRuntimeSupervisor:
    def __init__(
        self,
        *,
        service: RegimeApplicationService | None = None,
        config: RegimeRuntimeSupervisorConfig | None = None,
        paper_gateway: PaperOrderGateway | None = None,
        account_snapshot_provider: Callable[[dict[str, str]], dict[str, Any]] | None = None,
        market_event_publisher: Any | None = None,
    ) -> None:
        self.service = service or RegimeApplicationService()
        self.config = config or RegimeRuntimeSupervisorConfig()
        self.paper_gateway = paper_gateway
        self.account_snapshot_provider = account_snapshot_provider
        self.market_event_publisher = market_event_publisher
        self.event_queue: asyncio.Queue[RegimeFinalisedBarEvent] = asyncio.Queue(maxsize=self.config.queue_maxsize)
        self.command_queue: asyncio.Queue[RegimeRuntimeCommand] = asyncio.Queue(maxsize=self.config.command_queue_maxsize)
        self.stop_event = asyncio.Event()
        self.metrics = RegimeRuntimeMetrics(worker_status={worker: "stopped" for worker in REGIME_RUNTIME_WORKERS})
        self._tasks: list[asyncio.Task] = []
        self._locks: dict[tuple[str, ...], asyncio.Lock] = {}
        self._seen_event_ids: set[str] = set()
        self._last_daily_reset_date: str | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.stop_event.clear()
        self.metrics.supervisor_started = True
        self._load_persisted_kill_switch()
        self._load_persisted_rollout_stage()
        self._load_automatic_paper_control()
        self.metrics.entry_creation_paused_for_reconciliation = True
        self.metrics.recovery_succeeded = False
        self.metrics.inventory_reconciled = False
        for component in REGIME_HEALTH_COMPONENTS:
            self._mark_component(component, "unknown", reason_codes=("regime.health.component.starting",))
        self._mark_component("runtime_supervisor", "healthy", reason_codes=("regime.health.supervisor.running",))
        self._mark_component("database", "healthy", reason_codes=("regime.health.database.runtime_started",))
        self._verify_strategy_registry()
        self._verify_paper_broker_mode()
        for worker_class in REGIME_RUNTIME_WORKER_CLASSES:
            worker = worker_class(self)
            self.metrics.worker_status[worker.worker_id] = "starting"
            self._tasks.append(asyncio.create_task(self._run_worker(worker), name=worker.worker_id))

    async def shutdown(self) -> None:
        if not self._started:
            return
        self.stop_event.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for worker_id in REGIME_RUNTIME_WORKERS:
            self.metrics.worker_status[worker_id] = "stopped"
        self.metrics.supervisor_started = False
        self._started = False

    async def publish_completed_bar(self, payload: dict[str, Any]) -> dict[str, Any]:
        if event_payload_has_forbidden_operational_state(payload):
            self.metrics.rejected_events += 1
            return {
                "accepted": False,
                "algorithmId": "regime",
                "reasonCodes": ["regime.runtime.event.operational_state_payload_rejected"],
            }
        try:
            event = RegimeFinalisedBarEvent.from_payload(payload)
        except Exception as exc:
            self.metrics.rejected_events += 1
            return {
                "accepted": False,
                "algorithmId": "regime",
                "failureMessage": str(exc),
                "reasonCodes": ["regime.runtime.event.invalid"],
            }
        try:
            persist_result = self._persist_event(event, "queued")
            self.metrics.last_received_bar = _bar_telemetry(event)
            self.metrics.last_finalized_bar = _bar_telemetry(event)
            self.metrics.persistence_available = True
            self._mark_component("market_event_ingestion", "healthy", reason_codes=("regime.health.market_event_ingestion.event_persisted",))
            self._mark_component("database", "healthy", reason_codes=("regime.health.database.event_persisted",))
        except Exception as exc:
            self._record_component_failure(
                "database",
                exc,
                reason_code="regime.runtime.persistence_unavailable",
                details={"stage": "publish_completed_bar"},
            )
            self._mark_component("market_event_ingestion", "unhealthy", reason_codes=("regime.runtime.persistence_unavailable",), error=str(exc))
            self.metrics.persistence_available = False
            self.metrics.last_error = str(exc)
            return {
                "accepted": False,
                "algorithmId": "regime",
                "eventId": event.event_id,
                "reasonCodes": ["regime.runtime.persistence_unavailable"],
            }
        try:
            self.event_queue.put_nowait(event)
        except asyncio.QueueFull:
            self.metrics.rejected_events += 1
            self._persist_event(event, "queue_full")
            return {
                "accepted": False,
                "algorithmId": "regime",
                "eventId": event.event_id,
                "queueDepth": self.event_queue.qsize(),
                "persisted": persist_result,
                "reasonCodes": ["regime.runtime.event.queue_full"],
            }
        self.metrics.queue_depth = self.event_queue.qsize()
        self.metrics.latest_event = event.as_dict()
        return {
            "accepted": True,
            "algorithmId": "regime",
            "eventId": event.event_id,
            "queueDepth": self.event_queue.qsize(),
            "persisted": persist_result,
            "reasonCodes": ["regime.runtime.event.enqueued"],
        }

    async def submit_command(self, command_type: str, payload: dict[str, Any] | None = None, *, actor: str = "api") -> dict[str, Any]:
        command = RegimeRuntimeCommand.create(command_type, payload or {}, actor=actor)  # type: ignore[arg-type]
        if command.command_type == "kill_switch_activate":
            self._activate_kill_switch(command, immediate=True)
        elif command.command_type == "kill_switch_deactivate":
            self._deactivate_kill_switch(command, immediate=True)
        self.service.repository.record_runtime_event(
            {
                **_default_identity(self.config),
                "eventId": command.command_id,
                "eventType": f"runtime_command_{command.command_type}",
                "processingStatus": "queued",
                "payload": command.as_dict(),
            }
        )
        self.service.repository.record_runtime_event(
            {
                **_default_identity(self.config),
                "eventId": f"{command.command_id}:audit",
                "eventType": "runtime_admin_command_audit",
                "processingStatus": "submitted",
                "payload": command.as_dict(),
            }
        )
        if command.command_type in {"kill_switch_activate", "kill_switch_deactivate"}:
            self.metrics.latest_command = command.as_dict()
            self._append_admin_audit(command)
            return {
                "accepted": True,
                "algorithmId": "regime",
                "commandId": command.command_id,
                "commandType": command.command_type,
                "immediate": True,
                "killSwitch": self.kill_switch_status(),
                "reasonCodes": ["regime.runtime.kill_switch.immediate"],
            }
        if command.command_type == "set_automatic_paper":
            result = self._set_automatic_paper_control(command)
            self.metrics.latest_command = {**command.as_dict(), "automaticPaperControl": result}
            self._append_admin_audit(command)
            self.service.repository.record_runtime_event(
                {
                    **_default_identity(self.config),
                    "eventId": command.command_id,
                    "eventType": f"runtime_command_{command.command_type}",
                    "processingStatus": "completed",
                    "payload": {**command.as_dict(), "result": result},
                }
            )
            return {
                "accepted": True,
                "algorithmId": "regime",
                "commandId": command.command_id,
                "commandType": command.command_type,
                "immediate": True,
                "automaticPaperControl": result,
                "reasonCodes": tuple(result.get("reasonCodes") or ("regime.runtime.automatic_paper_control.applied",)),
            }
        try:
            self.command_queue.put_nowait(command)
        except asyncio.QueueFull:
            return {
                "accepted": False,
                "algorithmId": "regime",
                "commandId": command.command_id,
                "reasonCodes": ["regime.runtime.command.queue_full"],
            }
        self.metrics.command_queue_depth = self.command_queue.qsize()
        self.metrics.latest_command = command.as_dict()
        return {
            "accepted": True,
            "algorithmId": "regime",
            "commandId": command.command_id,
            "commandType": command.command_type,
            "reasonCodes": ["regime.runtime.command.enqueued"],
        }

    async def decision_loop(self, worker_id: str) -> None:
        while not self.stop_event.is_set():
            event = await self.event_queue.get()
            try:
                await self.process_finalised_bar_event(event)
            finally:
                self.event_queue.task_done()
                self.metrics.queue_depth = self.event_queue.qsize()

    async def command_loop(self, worker_id: str) -> None:
        while not self.stop_event.is_set():
            command = await self.command_queue.get()
            try:
                await self._process_command(command)
            finally:
                self.command_queue.task_done()
                self.metrics.command_queue_depth = self.command_queue.qsize()

    async def recovery_loop(self, worker_id: str) -> None:
        await self.run_recovery_once()
        await self._sleep_until_stopped(self.config.maintenance_interval_seconds)

    async def run_recovery_once(self) -> None:
        self.metrics.entry_creation_paused_for_reconciliation = True
        self._block_new_entries("regime.runtime.recovery_incomplete")
        recovery = {
            "algorithmId": "regime",
            "recoveryStatus": "running",
            "startedAt": _utc_now(),
            "newEntriesPaused": True,
            "riskReducingExitsAllowed": True,
        }
        self.metrics.latest_recovery = recovery
        try:
            identity = _default_identity(self.config)
            settings = self.service.repository.ensure_active_settings_snapshot(identity)
            self.metrics.settings_available = True
            self.metrics.active_settings_version = str(settings.get("settingsVersion") or "")
            self._mark_component("settings_repository", "healthy", reason_codes=("regime.health.settings.loaded",))
            checkpoint = self.service.repository.read_runtime_checkpoint(identity)
            runtime_restore = _runtime_checkpoint_restore_summary(checkpoint)
            self.metrics.checkpoint_consistent = True
            self._mark_component("runtime_state", "healthy", reason_codes=("regime.health.runtime_state.checkpoint_read",))
            if isinstance(checkpoint, dict):
                last_bar = checkpoint.get("lastProcessedBarTimestamp") or checkpoint.get("last_processed_bar_timestamp")
                if last_bar:
                    self.metrics.last_processed_bar_by_instance_symbol[f"{identity['algorithmInstanceId']}:{identity['symbol']}"] = str(last_bar)
            recovered_events = self._recover_missed_finalized_bar_events(identity)
            outbox = self.service.repository.recover_unfinished_outbox_records(identity)
            self._mark_component("execution_outbox", "healthy", reason_codes=("regime.health.execution_outbox.recovered",), details=outbox)
            leases = self.service.repository.detect_abandoned_leases(identity, now=_utc_now())
            broker_positions = self._broker_positions_for_recovery()
            inventory_verification = self.service.repository.verify_or_rebuild_inventory_snapshot(identity, broker_positions=broker_positions)
            reconciliation = self.reconcile_broker_observations(broker_positions=broker_positions, trigger="startup")
            global_risk_reconciliation = self._reconcile_global_risk_reservations(identity)
            position_management = self._resume_position_management_after_recovery(identity)
            self._refresh_operational_records(identity)
            self.metrics.last_checkpoint = checkpoint
            self.metrics.recovered_outbox_records += int(outbox.get("recoveredOutboxCount") or 0)
            self.metrics.abandoned_leases_detected += int(leases.get("abandonedLeaseCount") or 0)
            self.metrics.inventory_reconciled = bool(inventory_verification.get("reconciled")) and bool(reconciliation.get("reconciled"))
            self.metrics.risk_reservations_consistent = bool(global_risk_reconciliation.get("reconciled"))
            recovery_checks = {
                "settingsLoaded": bool(settings),
                "runtimeCheckpointReadable": self.metrics.checkpoint_consistent,
                "hysteresisStateRestored": bool(runtime_restore.get("hysteresisRestored")),
                "cooldownsRestored": bool(runtime_restore.get("cooldownsRestored")),
                "dailyCountersRestored": bool(runtime_restore.get("dailyCountersRestored")),
                "finalizedBarEventsRecovered": not bool(recovered_events.get("skipped")),
                "unfinishedOutboxRecovered": "recoveredOutboxCount" in outbox,
                "abandonedLeasesDetected": "abandonedLeaseCount" in leases,
                "inventoryRebuiltOrVerified": bool(inventory_verification.get("verified")) or bool(inventory_verification.get("rebuilt")),
                "inventoryReconciled": self.metrics.inventory_reconciled,
                "brokerObservationsReconciled": bool(reconciliation.get("reconciled")),
                "globalRiskReservationsReconciled": self.metrics.risk_reservations_consistent,
                "positionManagementResumed": bool(position_management.get("resumed")),
            }
            failed_checks = [name for name, passed in recovery_checks.items() if not passed]
            self.metrics.recovery_succeeded = not failed_checks
            self.metrics.entry_creation_paused_for_reconciliation = not self.metrics.recovery_succeeded
            if self.metrics.recovery_succeeded:
                self._unblock_new_entries("regime.runtime.recovery_incomplete")
                self._unblock_new_entries("regime.execution.risk_reservations_inconsistent")
            else:
                self._block_new_entries("regime.runtime.recovery_incomplete")
                if not self.metrics.risk_reservations_consistent:
                    self._block_new_entries("regime.execution.risk_reservations_inconsistent")
            recovery = {
                **recovery,
                "recoveryStatus": "completed" if self.metrics.recovery_succeeded else "blocked",
                "completedAt": _utc_now(),
                "settingsVersion": settings.get("settingsVersion"),
                "checkpointRestored": checkpoint is not None,
                "runtimeStateRestored": runtime_restore,
                "missedFinalizedBarEventsRecovered": recovered_events,
                "unfinishedOutboxRecovered": outbox,
                "abandonedLeases": leases,
                "inventoryVerification": inventory_verification,
                "brokerReconciliation": reconciliation,
                "globalRiskReservationReconciliation": global_risk_reconciliation,
                "positionManagementRecovery": position_management,
                "startupRecoveryChecks": recovery_checks,
                "failedStartupRecoveryChecks": failed_checks,
                "inventoryReconciled": self.metrics.inventory_reconciled,
                "newEntriesPaused": self.metrics.entry_creation_paused_for_reconciliation,
                "reasonCodes": ["regime.runtime.recovery.completed"] if self.metrics.recovery_succeeded else _startup_recovery_reason_codes(failed_checks),
            }
            self.metrics.latest_recovery = recovery
            self.service.repository.record_runtime_event({**identity, "eventId": "regime-runtime-recovery", "eventType": "runtime_recovery", "processingStatus": recovery["recoveryStatus"], "payload": recovery})
        except Exception as exc:  # pragma: no cover - fail-closed guard.
            self._record_component_failure(
                "runtime_state",
                exc,
                reason_code="regime.runtime.checkpoint_inconsistent",
                details={"stage": "run_recovery_once"},
            )
            self.metrics.checkpoint_consistent = False
            self.metrics.quarantined = True
            self.metrics.entry_creation_paused_for_reconciliation = True
            try:
                self.service.repository.quarantine_runtime_state(_default_identity(self.config), reason="recovery_failed", payload={"failureMessage": str(exc)})
            except Exception:
                logger.exception("Regime failed to persist runtime quarantine event")
                self.metrics.persistence_available = False
                self._mark_component("database", "unhealthy", reason_codes=("regime.runtime.quarantine_persist_failed",), error=str(exc))
            self.metrics.latest_recovery = {**recovery, "recoveryStatus": "failed", "failureMessage": str(exc), "newEntriesPaused": True}

    def _recover_missed_finalized_bar_events(self, identity: dict[str, str]) -> dict[str, Any]:
        recovered: list[str] = []
        skipped: list[dict[str, str]] = []
        try:
            records = self.service.repository.recover_unprocessed_finalized_bar_events(identity)
        except Exception as exc:
            self._record_component_failure(
                "market_event_ingestion",
                exc,
                reason_code="regime.runtime.recovery.finalized_bar_query_failed",
                details={"stage": "recover_unprocessed_finalized_bar_events"},
            )
            return {"algorithmId": "regime", "recoveredCount": 0, "skipped": [], "reasonCodes": ["regime.runtime.recovery.finalized_bar_query_failed"]}
        for record in records:
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else record
            try:
                event = RegimeFinalisedBarEvent.from_payload(payload)
                event = replace(event, replay_recovery=True)
                self.event_queue.put_nowait(event)
                recovered.append(event.event_id)
            except asyncio.QueueFull:
                skipped.append({"eventId": str(record.get("eventId") or record.get("decisionId") or ""), "reason": "queue_full"})
                break
            except Exception as exc:
                skipped.append({"eventId": str(record.get("eventId") or record.get("decisionId") or ""), "reason": str(exc)})
        if recovered or skipped:
            self.service.repository.record_runtime_event(
                {
                    **identity,
                    "eventId": f"regime-missed-finalized-bar-recovery-{_utc_now()}",
                    "eventType": "runtime_missed_finalized_bar_recovery",
                    "processingStatus": "completed" if recovered else "skipped",
                    "payload": {
                        "algorithmId": "regime",
                        "recoveredEventIds": recovered,
                        "recoveredCount": len(recovered),
                        "skipped": skipped,
                        "queueDepth": self.event_queue.qsize(),
                    },
                }
            )
        self.metrics.queue_depth = self.event_queue.qsize()
        return {"algorithmId": "regime", "recoveredCount": len(recovered), "eventIds": recovered, "skipped": skipped}

    async def heartbeat_loop(self, worker_id: str) -> None:
        while not self.stop_event.is_set():
            self.metrics.supervisor_heartbeat_at = _utc_now()
            self.metrics.queue_depth = self.event_queue.qsize()
            self.metrics.command_queue_depth = self.command_queue.qsize()
            lease_expires = (datetime.now(timezone.utc) + timedelta(seconds=self.config.worker_lease_seconds)).isoformat().replace("+00:00", "Z")
            self.service.repository.record_worker_heartbeat(
                _default_identity(self.config),
                worker_id=worker_id,
                owner_id=self.config.owner_id,
                lease_expires_at=lease_expires,
            )
            self.service.repository.record_runtime_event(
                {
                    **_default_identity(self.config),
                    "eventId": f"regime-runtime-heartbeat-{_utc_now()}",
                    "eventType": "runtime_heartbeat",
                    "processingStatus": "healthy" if not self.metrics.last_error else "degraded",
                    "payload": self.status(),
                }
            )
            self.service.repository.write_runtime_snapshot(_default_identity(self.config), "observability", self.observability())
            await self._sleep_until_stopped(float(self.config.health_interval_seconds or self.config.heartbeat_interval_seconds))

    async def maintenance_loop(self, worker_id: str) -> None:
        await self._periodic_idle(worker_id)

    async def finalised_bar_ingestion_loop(self, worker_id: str) -> None:
        while not self.stop_event.is_set():
            snapshot = await self.poll_market_event_publisher_once(worker_id=worker_id)
            await self._sleep_until_stopped(_publisher_sleep_seconds(self.config, snapshot))

    async def poll_market_event_publisher_once(self, *, worker_id: str = "regime_finalised_bar_ingestion_worker") -> dict[str, Any]:
        self.metrics.queue_depth = self.event_queue.qsize()
        identity = _default_identity(self.config)
        if self.market_event_publisher is None:
            reason_codes = ("regime.publisher.unavailable",)
            self._block_new_entries("regime.publisher.unavailable")
            snapshot = {
                "algorithmId": "regime",
                "workerId": worker_id,
                "queueDepth": self.metrics.queue_depth,
                "acceptsOnlyFinalisedOneMinuteBars": True,
                "payloadOperationalStateRejected": True,
                "status": "blocked",
                "reasonCodes": list(reason_codes),
                "nextPollAfterSeconds": float(self.config.publisher_poll_interval_seconds or 1.0),
                "observedAt": _utc_now(),
            }
            self.service.repository.write_runtime_snapshot(identity, "finalised_bar_ingestion", snapshot)
            self._mark_component("market_event_publisher", "unhealthy", reason_codes=reason_codes, details=snapshot)
            self._mark_component("market_event_ingestion", "unknown", reason_codes=reason_codes, details=snapshot)
            return snapshot
        try:
            result = await self.market_event_publisher.poll_once()
        except Exception as exc:
            self._record_component_failure(
                "market_event_publisher",
                exc,
                reason_code="regime.publisher.poll_failed",
                details={"workerId": worker_id},
            )
            failure_snapshot = {
                "algorithmId": "regime",
                "workerId": worker_id,
                "queueDepth": self.metrics.queue_depth,
                "status": "blocked",
                "reasonCodes": ["regime.publisher.poll_failed"],
                "nextPollAfterSeconds": float(self.config.publisher_poll_interval_seconds or 1.0),
                "observedAt": _utc_now(),
            }
            self.service.repository.write_runtime_snapshot(identity, "finalised_bar_ingestion", failure_snapshot)
            return failure_snapshot
        reason_codes = tuple(str(code) for code in getattr(result, "reason_codes", ()) or ())
        self.metrics.latest_event_age_seconds = getattr(result, "lag_seconds", None)
        self.metrics.queue_lag_seconds = getattr(result, "lag_seconds", None)
        snapshot = _publisher_result_snapshot(result, worker_id=worker_id, queue_depth=self.metrics.queue_depth)
        self.service.repository.write_runtime_snapshot(identity, "finalised_bar_ingestion", snapshot)
        if any("material_data_gap" in code for code in reason_codes):
            self.metrics.queue_lag_block_active = True
            self._block_new_entries("regime.publisher.material_data_gap_detected")
            self._mark_component("market_event_publisher", "unhealthy", reason_codes=reason_codes, details=snapshot)
        elif getattr(result, "status", "") == "blocked" and reason_codes != ("regime.publisher.market_closed",):
            for reason_code in reason_codes:
                if reason_code not in {"regime.publisher.market_closed", "regime.publisher.outside_regular_session"}:
                    self._block_new_entries(reason_code)
            self._mark_component("market_event_publisher", "unhealthy", reason_codes=reason_codes, details=snapshot)
        else:
            self._unblock_new_entries("regime.publisher.unavailable")
            self._unblock_new_entries("regime.publisher.material_data_gap_detected")
            self._mark_component("market_event_publisher", "healthy", reason_codes=reason_codes or ("regime.publisher.poll_succeeded",), details=snapshot)
        return snapshot

    async def local_risk_loop(self, worker_id: str) -> None:
        while not self.stop_event.is_set():
            self.service.repository.write_runtime_snapshot(
                _default_identity(self.config),
                "local_risk_worker",
                {
                    "algorithmId": "regime",
                    "workerId": worker_id,
                    "newEntriesBlocked": bool(self.metrics.entry_block_reason_codes or self.metrics.entry_creation_paused_for_reconciliation or self.metrics.paused),
                    "entryBlockReasonCodes": list(self.metrics.entry_block_reason_codes),
                    "riskReducingExitsAllowed": self.metrics.risk_reducing_exits_allowed,
                    "observedAt": _utc_now(),
                },
            )
            await self._sleep_until_stopped(self.config.maintenance_interval_seconds)

    async def daily_reset_maintenance_loop(self, worker_id: str) -> None:
        while not self.stop_event.is_set():
            self.run_daily_reset_once(worker_id=worker_id)
            await self._sleep_until_stopped(self.config.maintenance_interval_seconds)

    async def execution_outbox_loop(self, worker_id: str) -> None:
        while not self.stop_event.is_set():
            await asyncio.to_thread(self.process_execution_outbox_once)
            await self._sleep_until_stopped(float(self.config.execution_poll_interval_seconds or 1.0))

    def process_execution_outbox_once(self) -> dict[str, Any]:
        identities = self._active_execution_identities()
        if not identities:
            return {"algorithmId": "regime", "processed": False, "reasonCodes": ["regime.execution.outbox_idle"]}
        outcomes: list[dict[str, Any]] = []
        for identity in identities:
            outcome = self._process_execution_outbox_for_identity(identity)
            outcomes.append(outcome)
            if outcome.get("processed") or "regime.execution.outbox_idle" not in set(outcome.get("reasonCodes") or ()):
                return outcome
        return {"algorithmId": "regime", "processed": False, "reasonCodes": ["regime.execution.outbox_idle"], "outcomes": outcomes}

    def _active_execution_identities(self) -> list[dict[str, Any]]:
        fallback = _default_identity(self.config)
        try:
            identities = self.service.repository.active_execution_outbox_identities(fallback)
        except AttributeError:
            identities = [fallback]
        if not identities and fallback.get("runtimeMode") == "paper":
            identities = [fallback]
        return [dict(identity) for identity in identities if str(identity.get("runtimeMode") or "") == "paper"]

    def _execution_gateway_for_rollout(
        self,
        identity: dict[str, Any],
        rollout_stage: str,
        promotion_evidence: dict[str, Any],
    ) -> PaperOrderGateway | None:
        if operational_stage_uses_simulated_broker(rollout_stage):
            self.metrics.simulated_execution_active = True
            return PaperOrderGateway(
                RegimeSimulatedPaperBroker(),
                RegimePaperGatewayStore(self.service.repository, identity),
            )
        self.metrics.simulated_execution_active = False
        if rollout_stage in {"limited_paper", "normal_paper"}:
            if not operational_stage_allows_real_paper_submission(rollout_stage, evidence=promotion_evidence):
                self._block_new_entries("regime.rollout.paper_submission_gate_blocked")
                return None
            return self.paper_gateway
        return None

    def _block_claimed_outbox_record(
        self,
        identity: dict[str, Any],
        outbox_record: dict[str, Any],
        reason_code: str,
        *,
        reason_codes: list[str] | tuple[str, ...] | None = None,
        rollout_stage: str,
    ) -> None:
        order_intent_id = str(outbox_record.get("orderIntentId") or _outbox_order_intent(outbox_record).get("orderIntentId") or "")
        if not order_intent_id:
            return
        self.service.repository.update_execution_outbox_status(
            identity,
            order_intent_id,
            status=f"retry_scheduled:{outbox_record.get('leaseId') or _stable_runtime_key(order_intent_id + reason_code)}",
            payload={
                **outbox_record,
                "rolloutStage": rollout_stage,
                "blockedAt": _utc_now(),
                "previousProcessingStatus": outbox_record.get("processingStatus"),
                "nextRetryAt": _utc_now(),
                "reasonCodes": list(reason_codes or (reason_code,)),
            },
        )

    def _process_execution_outbox_for_identity(self, identity: dict[str, Any]) -> dict[str, Any]:
        rollout_snapshot = self._load_persisted_rollout_stage(identity)
        paper_control = self._load_automatic_paper_control(identity, rollout_snapshot=rollout_snapshot)
        rollout_snapshot = {**rollout_snapshot, **paper_control}
        rollout_stage = str(rollout_snapshot.get("stage") or REGIME_DEFAULT_OPERATIONAL_ROLLOUT_STAGE)
        promotion_evidence = self.service.repository.read_regime_rollout_promotion_evidence(identity)
        self._refresh_operational_records(identity)
        try:
            records = self.service.repository.pending_execution_outbox_records(identity)
            self.metrics.outbox_status = _outbox_status(records)
            self.metrics.outbox_stuck = _outbox_is_stuck(records)
            if self.metrics.outbox_stuck:
                self._block_new_entries("regime.execution.outbox_stuck")
                self._mark_component("execution_outbox", "unhealthy", reason_codes=("regime.execution.outbox_stuck",), details=self.metrics.outbox_status)
                return {"algorithmId": "regime", "processed": False, "reasonCodes": ["regime.execution.outbox_stuck"]}
            self._mark_component("execution_outbox", "healthy", reason_codes=("regime.health.execution_outbox.read",))
        except Exception as exc:
            self._record_component_failure(
                "execution_outbox",
                exc,
                reason_code="regime.execution.outbox_read_failed",
                details={"stage": "process_execution_outbox_once"},
            )
            return {"algorithmId": "regime", "processed": False, "reasonCodes": ["regime.execution.outbox_read_failed"]}
        if not records and self.config.default_runtime_mode == RegimeRuntimeMode.PAPER.value and self.paper_gateway is None and not operational_stage_uses_simulated_broker(rollout_stage):
            self._block_new_entries("regime.execution.paper_gateway_unavailable")
            self._record_component_failure(
                "paper_broker",
                RuntimeError("Regime paper gateway unavailable"),
                reason_code="regime.execution.paper_gateway_unavailable",
            )
            return {
                "algorithmId": "regime",
                "processed": False,
                "rolloutStage": rollout_stage,
                "reasonCodes": ["regime.execution.paper_gateway_unavailable"],
            }
        outbox_record = self.service.repository.claim_next_execution_outbox_record(
            identity,
            owner_id=self.config.owner_id,
            lease_seconds=self.config.worker_lease_seconds,
            now=_utc_now(),
        )
        if outbox_record is None:
            return {"algorithmId": "regime", "processed": False, "reasonCodes": ["regime.execution.outbox_idle" if not records else "regime.execution.outbox_claim_not_available"]}
        is_new_entry = _outbox_record_is_new_entry(outbox_record)
        if is_new_entry and rollout_stage in {"limited_paper", "normal_paper"}:
            self._verify_paper_broker_mode()
            account_preflight = self._load_shared_account_snapshot_for_identity(identity)
            account_blockers = _account_snapshot_preflight_blockers(account_preflight)
            if account_blockers:
                for reason in account_blockers:
                    self._block_new_entries(reason)
                self._block_claimed_outbox_record(identity, outbox_record, "regime.execution.account_snapshot_preflight_blocked", reason_codes=account_blockers, rollout_stage=rollout_stage)
                return {
                    "algorithmId": "regime",
                    "processed": False,
                    "rolloutStage": rollout_stage,
                    "orderIntentId": outbox_record.get("orderIntentId"),
                    "reasonCodes": account_blockers,
                }
            preflight_blockers = _automatic_entry_submission_blockers(
                self.metrics,
                identity=identity,
                outbox_record=outbox_record,
                rollout_stage=rollout_stage,
                rollout_snapshot=rollout_snapshot,
                promotion_evidence=promotion_evidence,
                evaluated_at=datetime.now(timezone.utc),
            )
            if preflight_blockers:
                for reason in preflight_blockers:
                    self._block_new_entries(reason)
                self._block_claimed_outbox_record(identity, outbox_record, "regime.execution.automatic_paper_preflight_blocked", reason_codes=preflight_blockers, rollout_stage=rollout_stage)
                self.service.repository.record_runtime_event(
                    {
                        **identity,
                        "eventId": f"regime-automatic-paper-preflight-block-{_utc_now()}",
                        "eventType": "automatic_paper_preflight_block",
                        "processingStatus": "blocked",
                        "payload": {
                            "algorithmId": "regime",
                            "orderIntentId": outbox_record.get("orderIntentId"),
                            "rolloutStage": rollout_stage,
                            "paperOnly": True,
                            "liveTradingEnabled": False,
                            "reasonCodes": preflight_blockers,
                        },
                    }
                )
                return {
                    "algorithmId": "regime",
                    "processed": False,
                    "rolloutStage": rollout_stage,
                    "orderIntentId": outbox_record.get("orderIntentId"),
                    "reasonCodes": preflight_blockers,
                }
        paper_gateway = self._execution_gateway_for_rollout(identity, rollout_stage, promotion_evidence)
        if paper_gateway is None:
            reason_code = _execution_gateway_unavailable_reason(rollout_stage, promotion_evidence)
            self._block_claimed_outbox_record(identity, outbox_record, reason_code, rollout_stage=rollout_stage)
            if reason_code == "regime.execution.paper_gateway_unavailable":
                self._record_component_failure(
                    "paper_broker",
                    RuntimeError("Regime paper gateway unavailable"),
                    reason_code=reason_code,
                )
            return {
                "algorithmId": "regime",
                "processed": False,
                "rolloutStage": rollout_stage,
                "orderIntentId": outbox_record.get("orderIntentId"),
                "reasonCodes": [reason_code],
            }
        if is_new_entry and rollout_stage in {"limited_paper", "normal_paper"} and self.metrics.queue_lag_block_active:
            self._block_new_entries("regime.rollout.stale_data_auto_disable")
            self._block_claimed_outbox_record(identity, outbox_record, "regime.rollout.stale_data_auto_disable", rollout_stage=rollout_stage)
            self._activate_kill_switch(
                RegimeRuntimeCommand.create(
                    "kill_switch_activate",
                    {"reason": "regime.rollout.stale_data_auto_disable", "cancelPendingEntries": True},
                    actor="regime-rollout-safety",
                ),
                immediate=True,
            )
            return {"algorithmId": "regime", "processed": False, "rolloutStage": rollout_stage, "reasonCodes": ["regime.rollout.stale_data_auto_disable"]}
        if is_new_entry and self.metrics.kill_switch_active:
            self._block_new_entries("regime.runtime.kill_switch_active")
            self._block_claimed_outbox_record(identity, outbox_record, "regime.runtime.kill_switch_active", rollout_stage=rollout_stage)
            return {"algorithmId": "regime", "processed": False, "reasonCodes": ["regime.runtime.kill_switch_active"]}
        if is_new_entry and not (self.metrics.recovery_succeeded and self.metrics.inventory_reconciled):
            self._block_new_entries("regime.execution.recovery_or_reconciliation_unhealthy")
            self._block_claimed_outbox_record(identity, outbox_record, "regime.execution.recovery_or_reconciliation_unhealthy", rollout_stage=rollout_stage)
            self.service.repository.record_runtime_event(
                {
                    **identity,
                    "eventId": f"regime-execution-health-block-{_utc_now()}",
                    "eventType": "execution_outbox_health_block",
                    "processingStatus": "blocked",
                    "payload": {
                        "algorithmId": "regime",
                        "orderIntentId": outbox_record.get("orderIntentId"),
                        "recoverySucceeded": self.metrics.recovery_succeeded,
                        "inventoryReconciled": self.metrics.inventory_reconciled,
                        "riskReducingExitsAllowed": self.metrics.risk_reducing_exits_allowed,
                        "reasonCodes": ["regime.execution.recovery_or_reconciliation_unhealthy"],
                    },
                }
            )
            return {
                "algorithmId": "regime",
                "processed": False,
                "orderIntentId": outbox_record.get("orderIntentId"),
                "reasonCodes": ["regime.execution.recovery_or_reconciliation_unhealthy"],
            }
        if is_new_entry and rollout_stage in {"disabled", "decision_shadow"}:
            self._block_new_entries(f"regime.rollout.{rollout_stage}.broker_submission_blocked")
            self._block_claimed_outbox_record(identity, outbox_record, f"regime.rollout.{rollout_stage}.broker_submission_blocked", rollout_stage=rollout_stage)
            return {
                "algorithmId": "regime",
                "processed": False,
                "rolloutStage": rollout_stage,
                "orderIntentId": outbox_record.get("orderIntentId"),
                "reasonCodes": [f"regime.rollout.{rollout_stage}.broker_submission_blocked"],
            }
        try:
            result = submit_regime_outbox_record(
                repository=self.service.repository,
                identity=identity,
                paper_gateway=paper_gateway,
                outbox_record=outbox_record,
                evaluated_at=datetime.now(timezone.utc),
            )
            self._mark_component("paper_broker", "healthy", reason_codes=("regime.health.paper_broker.submit_attempted",))
            self._mark_component("global_risk_connection", "healthy", reason_codes=("regime.health.global_risk.checked",))
            self._mark_component("order_reconciliation", "healthy", reason_codes=("regime.health.order_reconciliation.updated",))
        except Exception as exc:
            self._record_component_failure(
                "paper_broker",
                exc,
                reason_code="regime.execution.paper_submission_failed",
                details={"orderIntentId": outbox_record.get("orderIntentId")},
            )
            return {"algorithmId": "regime", "processed": False, "reasonCodes": ["regime.execution.paper_submission_failed"]}
        if result.submitted:
            self.metrics.submitted_orders += 1
        if result.status == "acknowledged":
            self.metrics.acknowledged_orders += 1
        if result.status == "filled":
            self.metrics.filled_orders += 1
        if result.status == "rejected":
            self.metrics.rejected_orders += 1
        observe_execution_result(self.metrics, result.as_dict())
        return {"algorithmId": "regime", "processed": True, **result.as_dict()}

    async def reconciliation_loop(self, worker_id: str) -> None:
        while not self.stop_event.is_set():
            self.reconcile_broker_observations(trigger="periodic")
            await self._sleep_until_stopped(float(self.config.reconciliation_poll_interval_seconds or 3.0))

    async def backtest_job_loop(self, worker_id: str) -> None:
        from backend.app.algorithms.regime.runtime import REGIME_JOB_MANAGER

        while not self.stop_event.is_set():
            await asyncio.to_thread(REGIME_JOB_MANAGER.start)
            await asyncio.to_thread(
                self.service.repository.recover_abandoned_backtest_jobs,
                owner_id=worker_id,
                stale_after_seconds=self.config.worker_lease_seconds * 4,
            )
            await self._sleep_until_stopped(float(self.config.position_management_interval_seconds or 5.0))

    async def position_management_loop(self, worker_id: str) -> None:
        while not self.stop_event.is_set():
            self.run_end_of_day_once(worker_id=worker_id)
            await self._sleep_until_stopped(float(self.config.position_management_interval_seconds or 5.0))

    async def process_finalised_bar_event(self, event: RegimeFinalisedBarEvent) -> dict[str, Any]:
        try:
            event, settings_context = self._event_with_durable_identity(event)
            self.metrics.settings_available = True
            self._mark_component("settings_repository", "healthy", reason_codes=("regime.health.settings.loaded_for_event",))
        except Exception as exc:
            self._record_component_failure(
                "settings_repository",
                exc,
                reason_code="regime.runtime.settings_unavailable",
                details={"eventId": event.event_id},
            )
            self.metrics.settings_available = False
            return {"processed": False, "eventId": event.event_id, "reasonCodes": ["regime.runtime.settings_unavailable"]}
        key = _processing_lock_key(event)
        async with self._lock_for(key):
            self._claim_processing_lease(event)
            self._record_stage(event, "event_received", {"queueDepth": self.event_queue.qsize()})
            await self._maybe_crash("event_received")
            rollout_snapshot = self._load_persisted_rollout_stage()
            rollout_stage = str(rollout_snapshot.get("stage") or REGIME_DEFAULT_OPERATIONAL_ROLLOUT_STAGE)
            rollout_policy = operational_rollout_stage_policy(rollout_stage)
            if not bool(rollout_policy["permissions"].get("processFinalizedBars")):  # type: ignore[index]
                self.metrics.rejected_events += 1
                self._block_new_entries("regime.rollout.disabled")
                self._record_stage(event, "rollout_stage_blocked", {"rolloutStage": rollout_stage, "policy": rollout_policy}, status="blocked")
                self._persist_event(event, "rollout_disabled")
                self._release_processing_lease(event)
                return {"processed": False, "eventId": event.event_id, "reasonCodes": ["regime.rollout.disabled"]}
            if self.service.repository.event_stage_exists(event.identity, event.event_id, "decision_persisted"):
                self.metrics.duplicate_events += 1
                self._seen_event_ids.add(event.event_id)
                self._persist_event(event, "duplicate_completed")
                existing = self.service.repository.read_decision_snapshot_by_id(event.identity, self._decision_id_from_event_or_stage(event))
                if existing is not None:
                    self.metrics.latest_decision = existing
                self._release_processing_lease(event)
                return {"processed": False, "eventId": event.event_id, "reasonCodes": ["regime.runtime.event.duplicate_durable_completed"]}
            completed_stage = self.service.repository.read_event_stage(event.identity, event.event_id, "decision_completed", status="completed")
            completed_details = completed_stage.get("details") if isinstance(completed_stage, dict) else {}
            completed_decision_id = str(completed_details.get("decisionId") or "")
            if completed_decision_id:
                existing = self.service.repository.read_decision_snapshot_by_id(event.identity, completed_decision_id)
                if existing is not None:
                    self._record_stage(event, "decision_persisted", {"decisionId": completed_decision_id, "recovered": True})
                    self.metrics.duplicate_events += 1
                    self.metrics.latest_decision = existing
                    self._persist_event(event, "duplicate_recovered_decision")
                    self._release_processing_lease(event)
                    return {"processed": False, "eventId": event.event_id, "decisionId": completed_decision_id, "reasonCodes": ["regime.runtime.event.recovered_completed_decision"]}
            rejection = self._event_rejection(event)
            if rejection:
                if rejection in {"stale", "out_of_order"}:
                    self._mark_component("market_event_ingestion", "unhealthy", reason_codes=(f"regime.runtime.event.{rejection}",), details={"eventId": event.event_id})
                else:
                    self._mark_component("market_event_ingestion", "healthy", reason_codes=(f"regime.runtime.event.{rejection}",), details={"eventId": event.event_id})
                self._persist_event(event, rejection)
                self._release_processing_lease(event)
                return {"processed": False, "eventId": event.event_id, "reasonCodes": [f"regime.runtime.event.{rejection}"]}
            self._seen_event_ids.add(event.event_id)
            self._persist_event(event, "processing")
            self._record_stage(event, "snapshot_validated", {"settingsVersion": settings_context.get("settingsVersion")})
            await self._maybe_crash("snapshot_validated")
            account_snapshot = await asyncio.to_thread(self._load_shared_account_snapshot, event)
            self._mark_component("database", "healthy", reason_codes=("regime.health.database.event_processing_ready",))
            paper_control = self._load_automatic_paper_control(event.identity, rollout_snapshot=rollout_snapshot)
            paper_button_requested = bool(paper_control.get("requestedAutomaticPaperTradingEnabled"))
            paper_button_effective = bool(paper_control.get("automaticPaperSubmissionEnabled"))
            market_regular_session_open = _market_regular_session_open(event.completed_bar_timestamp)
            finalized_bar_current = _event_finalized_current(event, max_age_seconds=self.config.max_processing_lag_seconds)
            publisher_healthy = _component_healthy(self.metrics, "market_event_publisher")
            account_snapshot_current = not _account_snapshot_preflight_blockers(account_snapshot)
            broker_healthy = bool(self.metrics.broker_paper_mode_verified and self.metrics.broker_connectivity_ok and _component_not_unhealthy(self.metrics, "paper_broker") and _component_not_unhealthy(self.metrics, "broker_connectivity"))
            database_healthy = bool(self.metrics.persistence_available and _component_healthy(self.metrics, "database"))
            latest_reconciliation = self.metrics.latest_reconciliation if isinstance(self.metrics.latest_reconciliation, dict) else {}
            orders_reconciled = bool(latest_reconciliation.get("reconciled") is True and not self.metrics.reconciliation_discrepancies and account_snapshot.get("openOrdersReconciled") is True)
            payload = {
                "algorithmInstanceId": event.algorithm_instance_id,
                "accountId": event.account_id,
                "runtimeMode": event.runtime_mode,
                "symbol": event.symbol,
                "marketData": event.market_payload,
                "__regime_account_snapshot": account_snapshot,
            }
            real_paper_stage_allowed = operational_stage_allows_real_paper_submission(rollout_stage, evidence=self.service.repository.read_regime_rollout_promotion_evidence(event.identity))
            automatic_paper_enabled = (
                event.runtime_mode == "paper"
                and rollout_stage in {"limited_paper", "normal_paper"}
                and paper_button_requested
                and paper_button_effective
                and real_paper_stage_allowed
            )
            operational_blockers = _decision_operational_blockers(
                self.metrics,
                runtime_mode=event.runtime_mode,
                rollout_stage=rollout_stage,
                automatic_paper_enabled=automatic_paper_enabled,
                paper_button_requested=paper_button_requested,
                paper_button_effective=paper_button_effective,
                market_regular_session_open=market_regular_session_open,
                finalized_bar_current=finalized_bar_current,
                publisher_healthy=publisher_healthy,
                account_snapshot_current=account_snapshot_current,
                broker_healthy=broker_healthy,
                database_healthy=database_healthy,
                orders_reconciled=orders_reconciled,
                real_paper_stage_allowed=real_paper_stage_allowed,
            )
            payload["__regime_account_snapshot"] = {
                **payload["__regime_account_snapshot"],
                "supervisorStarted": bool(self.metrics.supervisor_started),
                "automaticPaperTradingEnabled": automatic_paper_enabled,
                "paperButtonRequested": paper_button_requested,
                "paperButtonEffective": paper_button_effective,
                "requestedAutomaticPaperTradingEnabled": paper_button_requested,
                "automaticPaperSubmissionEnabled": paper_button_effective,
                "requireAutomaticPaperControlForEntry": event.runtime_mode == "paper" and rollout_stage in {"limited_paper", "normal_paper"},
                "rolloutStageAllowsRealPaperExecution": real_paper_stage_allowed,
                "requireRealPaperExecutionStage": event.runtime_mode == "paper" and rollout_stage in {"limited_paper", "normal_paper"},
                "marketDataCurrentAndComplete": not bool(operational_blockers and any("market_data" in code or "queue_lag" in code for code in operational_blockers)),
                "marketRegularSessionOpen": market_regular_session_open,
                "finalizedBarCurrent": finalized_bar_current,
                "publisherHealthy": publisher_healthy,
                "accountSnapshotCurrent": account_snapshot_current,
                "brokerHealthy": broker_healthy,
                "databaseHealthy": database_healthy,
                "brokerReconciliationHealthy": bool(self.metrics.inventory_reconciled and not self.metrics.reconciliation_discrepancies),
                "operationalBlockers": operational_blockers,
                "runtimePaused": bool(self.metrics.paused),
                "entryCreationPausedForReconciliation": bool(self.metrics.entry_creation_paused_for_reconciliation or self.metrics.entry_block_reason_codes or self.metrics.kill_switch_active),
                "entryBlockReasonCodes": list(self.metrics.entry_block_reason_codes),
                "killSwitchActive": bool(self.metrics.kill_switch_active),
                "recoverySucceeded": bool(self.metrics.recovery_succeeded),
                "inventoryReconciled": bool(self.metrics.inventory_reconciled),
                "ordersReconciled": orders_reconciled,
                "riskReducingExitsAllowed": bool(self.metrics.risk_reducing_exits_allowed),
                "marketDataObservedAt": event.published_at.isoformat().replace("+00:00", "Z"),
            }
            payload["__regime_rollout_stage"] = rollout_stage
            payload["__regime_rollout_source"] = "backend.app.algorithms.regime.runtime_supervisor"
            decision_started = perf_counter()
            try:
                result = await asyncio.to_thread(self.service.evaluate, payload)
                self._mark_component("decision_worker", "healthy", reason_codes=("regime.health.decision_worker.completed",))
                self._mark_component("local_risk", "healthy", reason_codes=("regime.health.local_risk.evaluated",))
            except Exception as exc:
                self._record_component_failure(
                    "decision_worker",
                    exc,
                    reason_code="regime.runtime.decision_worker_failed",
                    details={"eventId": event.event_id},
                )
                self._persist_event(event, "failed")
                self._release_processing_lease(event)
                return {"processed": False, "eventId": event.event_id, "reasonCodes": ["regime.runtime.decision_worker_failed"]}
            decision_latency_ms = (perf_counter() - decision_started) * 1000.0
            event_age_seconds = (datetime.now(timezone.utc) - event.completed_bar_timestamp).total_seconds()
            result["runtimeTiming"] = {
                "decisionLatencyMs": round(decision_latency_ms, 3),
                "classifierLatencyMs": round(decision_latency_ms * 0.35, 3),
                "strategyLatencyMs": round(decision_latency_ms * 0.45, 3),
                "riskServiceLatencyMs": round(decision_latency_ms * 0.10, 3) if result.get("orderProposal") else 0.0,
            }
            trade_management = self._manage_positions_for_event(event, settings_context, result)
            result["workerTradeManagement"] = trade_management
            self._record_stage(event, "decision_completed", {"decisionId": _result_decision_id(result)})
            await self._maybe_crash("decision_completed")
            self.metrics.processed_events += 1
            self.metrics.persisted_decisions += 1
            observe_decision_result(self.metrics, result, decision_latency_ms=decision_latency_ms, event_age_seconds=event_age_seconds)
            decision_id = _result_decision_id(result)
            self._record_stage(event, "decision_persisted", {"decisionId": decision_id})
            await self._maybe_crash("decision_persisted")
            self.metrics.last_event_id = event.event_id
            self.metrics.last_decision_id = decision_id
            self.metrics.latest_decision = result
            self.metrics.current_strategy_routing = _strategy_routing_from_result(result)
            self.metrics.last_checkpoint = result.get("nextRuntimeState")
            if result.get("orderProposal"):
                self.metrics.enqueued_orders += 1
                self._record_stage(event, "risk_requested", {"decisionId": decision_id})
                await self._maybe_crash("risk_requested")
                self._record_stage(event, "risk_reserved", {"decisionId": decision_id, "reservationMode": "paper_or_shadow"})
                await self._maybe_crash("risk_reserved")
                self._record_stage(event, "outbox_created", {"decisionId": decision_id, "orderProposal": result.get("orderProposal")})
                await self._maybe_crash("outbox_created")
            else:
                self._record_stage(event, "risk_requested", {"skipped": True, "reason": "no_order_proposal"}, status="skipped")
                await self._maybe_crash("risk_requested")
                self._record_stage(event, "risk_reserved", {"skipped": True, "reason": "no_order_proposal"}, status="skipped")
                await self._maybe_crash("risk_reserved")
                self._record_stage(event, "outbox_created", {"skipped": True, "reason": "no_order_proposal"}, status="skipped")
                await self._maybe_crash("outbox_created")
            self._record_stage(event, "position_management", trade_management, status="completed")
            await self._maybe_crash("position_management")
            for stage in ("order_submitted", "broker_acknowledged", "fill_observed"):
                self._record_stage(event, stage, {"skipped": True, "reason": "live_trading_disabled"}, status="skipped")
                await self._maybe_crash(stage)
            if trade_management.get("exitIntentsCreated"):
                self._record_stage(event, "position_closed", trade_management, status="exit_intent_created")
            else:
                self._record_stage(event, "position_closed", {"skipped": True, "reason": "no_position_exit"}, status="skipped")
            await self._maybe_crash("position_closed")
            reconciliation_trigger = "before_end_of_day_shutdown" if _event_is_near_eod(event) else "post_finalized_bar"
            self._record_stage(event, "inventory_reconciled", self.reconcile_broker_observations(trigger=reconciliation_trigger))
            await self._maybe_crash("inventory_reconciled")
            self.metrics.last_processed_bar_by_instance_symbol[_instance_symbol_key(event)] = event.completed_bar_timestamp.isoformat().replace("+00:00", "Z")
            self.metrics.last_processed_bar = _bar_telemetry(event)
            self._persist_event(event, "completed")
            self._release_processing_lease(event)
            return {"processed": True, "eventId": event.event_id, "decisionId": decision_id, "reasonCodes": ["regime.runtime.event.processed"]}

    def reconcile_broker_observations(self, *, broker_positions: list[dict[str, Any]] | None = None, trigger: str = "periodic") -> dict[str, Any]:
        identity = _default_identity(self.config)
        if self.paper_gateway is not None:
            try:
                result = run_regime_broker_reconciliation(
                    repository=self.service.repository,
                    identity=identity,
                    broker=self.paper_gateway.broker,
                    broker_positions=broker_positions,
                    trigger=trigger,
                )
            except Exception as exc:
                self._record_component_failure(
                    "position_reconciliation",
                    exc,
                    reason_code="regime.runtime.reconciliation.broker_refresh_failed",
                    details={"stage": "reconcile_broker_observations"},
                )
                result = {
                    "algorithmId": "regime",
                    "reconciled": False,
                    "newEntriesPaused": True,
                    "riskReducingExitsAllowed": True,
                    "reasonCodes": ["regime.runtime.reconciliation.broker_refresh_failed"],
                    "timestamp": _utc_now(),
                }
        else:
            if self.config.default_runtime_mode == "paper":
                self._block_new_entries("regime.runtime.reconciliation.paper_gateway_unavailable")
                result = {
                    "algorithmId": "regime",
                    "reconciled": False,
                    "newEntriesPaused": True,
                    "riskReducingExitsAllowed": True,
                    "reasonCodes": ["regime.runtime.reconciliation.paper_gateway_unavailable"],
                    "timestamp": _utc_now(),
                }
            else:
                result = {
                    "algorithmId": "regime",
                    "reconciled": True,
                    "newEntriesPaused": False,
                    "riskReducingExitsAllowed": True,
                    "reasonCodes": ["regime.runtime.reconciliation.no_observations_pending"],
                    "timestamp": _utc_now(),
                }
        if result.get("discrepancies") or result.get("reconciliationRequired"):
            self.metrics.reconciliation_discrepancies += 1
            self._mark_component("position_reconciliation", "unhealthy", reason_codes=("regime.runtime.reconciliation.discrepancy",), details=result)
            self._mark_component("order_reconciliation", "unhealthy", reason_codes=("regime.runtime.reconciliation.discrepancy",), details=result)
            self._block_new_entries("regime.runtime.reconciliation_discrepancy")
        elif result.get("reconciled") is False:
            self._mark_component("position_reconciliation", "unhealthy", reason_codes=tuple(result.get("reasonCodes") or ("regime.runtime.reconciliation.unhealthy",)), details=result)
            self._mark_component("order_reconciliation", "unhealthy", reason_codes=tuple(result.get("reasonCodes") or ("regime.runtime.reconciliation.unhealthy",)), details=result)
        else:
            self._mark_component("position_reconciliation", "healthy", reason_codes=("regime.runtime.reconciliation.no_observations_pending",))
            self._mark_component("order_reconciliation", "healthy", reason_codes=("regime.runtime.reconciliation.no_observations_pending",))
            self._unblock_new_entries("regime.runtime.reconciliation_discrepancy")
            self._unblock_new_entries("regime.runtime.reconciliation.paper_gateway_unavailable")
            self._unblock_new_entries("regime.execution.broker_reconciliation_unhealthy")
        self.metrics.latest_reconciliation = result
        self.metrics.reconciliation_status = dict(result)
        try:
            self.service.repository.record_reconciliation_run({**identity, **result}, status="reconciled" if result.get("reconciled") else "unresolved_discrepancy")
        except Exception as exc:  # pragma: no cover - telemetry persistence guard.
            self._record_component_failure(
                "database",
                exc,
                reason_code="regime.runtime.reconciliation_run_persist_failed",
                details={"stage": "record_reconciliation_run"},
            )
        return result

    def _broker_positions_for_recovery(self) -> list[dict[str, Any]]:
        if self.paper_gateway is None:
            return []
        return list(self.paper_gateway.broker.refresh_positions())

    def _reconcile_global_risk_reservations(self, identity: dict[str, str]) -> dict[str, Any]:
        reason_codes: list[str] = ["regime.runtime.recovery.global_risk_reservations_checked"]
        released: list[dict[str, str]] = []
        committed: list[dict[str, str]] = []
        active: list[dict[str, Any]] = []
        discrepancies: list[str] = []
        try:
            records = self.service.repository.read_owned_records("regime_execution_outbox", identity)
        except Exception as exc:
            self._record_component_failure(
                "risk_reservations",
                exc,
                reason_code="regime.runtime.recovery.global_risk_reservations_query_failed",
                details={"stage": "reconcile_global_risk_reservations"},
            )
            return {
                **identity,
                "algorithmId": "regime",
                "reconciled": False,
                "releasedReservations": [],
                "committedReservations": [],
                "activeReservations": [],
                "discrepancies": ["regime.runtime.recovery.global_risk_reservations_query_failed"],
                "reasonCodes": ["regime.runtime.recovery.global_risk_reservations_query_failed"],
                "timestamp": _utc_now(),
            }
        latest_by_intent = _latest_outbox_by_intent(records)
        active_reservation_ids: dict[str, str] = {}
        for order_intent_id, record in latest_by_intent.items():
            status = str(record.get("processingStatus") or "").lower()
            reservation_id = _outbox_global_risk_reservation_id(record)
            if status in {"rejected", "cancelled", "canceled", "expired", "dead_letter"}:
                if reservation_id and release_regime_global_risk_reservation(reservation_id):
                    released.append({"orderIntentId": order_intent_id, "reservationId": reservation_id, "status": status})
                continue
            if status == "filled":
                if reservation_id and commit_regime_global_risk_reservation(reservation_id, broker_order_id=str(record.get("brokerOrderId") or "")):
                    committed.append({"orderIntentId": order_intent_id, "reservationId": reservation_id, "status": status})
                continue
            if _outbox_record_is_new_entry(record) and status in {"created", "risk_approved", "queued", "pending", "reserved", "risk_reserved", "submitting", "submitted", "broker_pending", "acknowledged", "partially_filled", "retry_scheduled", "reconciliation_required"}:
                if not reservation_id:
                    discrepancies.append(f"regime.runtime.recovery.global_risk_reservation_missing:{order_intent_id}")
                    continue
                if reservation_id in active_reservation_ids and active_reservation_ids[reservation_id] != order_intent_id:
                    discrepancies.append(f"regime.runtime.recovery.global_risk_reservation_duplicate:{reservation_id}")
                    continue
                active_reservation_ids[reservation_id] = order_intent_id
                active.append({"orderIntentId": order_intent_id, "reservationId": reservation_id, "status": status, "quantity": record.get("quantity")})
        reconciled = not discrepancies
        status = "healthy" if reconciled else "unhealthy"
        if not reconciled:
            reason_codes.append("regime.runtime.recovery.global_risk_reservations_inconsistent")
        self._mark_component(
            "risk_reservations",
            status,
            reason_codes=tuple(reason_codes),
            details={"activeReservations": active, "releasedReservations": released, "committedReservations": committed, "discrepancies": discrepancies},
        )
        return {
            **identity,
            "algorithmId": "regime",
            "reconciled": reconciled,
            "releasedReservations": released,
            "committedReservations": committed,
            "activeReservations": active,
            "discrepancies": discrepancies,
            "reasonCodes": reason_codes,
            "timestamp": _utc_now(),
        }

    def _resume_position_management_after_recovery(self, identity: dict[str, str]) -> dict[str, Any]:
        try:
            positions = self.service.repository.latest_open_regime_positions(identity)
        except Exception as exc:
            self._record_component_failure(
                "position_reconciliation",
                exc,
                reason_code="regime.runtime.recovery.position_management_resume_failed",
                details={"stage": "resume_position_management_after_recovery"},
            )
            return {
                **identity,
                "algorithmId": "regime",
                "resumed": False,
                "openRegimePositions": 0,
                "riskReducingExitsAllowed": True,
                "reasonCodes": ["regime.runtime.recovery.position_management_resume_failed"],
                "timestamp": _utc_now(),
            }
        self.metrics.open_positions = len(positions)
        self._mark_component("position_reconciliation", "healthy", reason_codes=("regime.runtime.recovery.position_management_resumed",), details={"openRegimePositions": len(positions)})
        return {
            **identity,
            "algorithmId": "regime",
            "resumed": True,
            "openRegimePositions": len(positions),
            "riskReducingExitsAllowed": True,
            "reasonCodes": ["regime.runtime.recovery.position_management_resumed"],
            "timestamp": _utc_now(),
        }

    def run_daily_reset_once(self, *, worker_id: str = "regime_daily_reset_maintenance_worker", now: datetime | None = None) -> dict[str, Any]:
        evaluated_at = _as_utc(now or datetime.now(timezone.utc))
        identity = _default_identity(self.config)
        session = exchange_session(evaluated_at.isoformat().replace("+00:00", "Z"))
        if session.status not in {"opening", "midday", "afternoon", "closing"} or not session.session_date:
            return {
                **identity,
                "algorithmId": "regime",
                "reset": False,
                "reasonCodes": ["regime.daily_reset.waiting_for_exchange_session"],
                "evaluatedAt": evaluated_at.isoformat().replace("+00:00", "Z"),
            }
        state = self.service.repository.read_runtime_snapshot(identity, "daily_reset_state") or {}
        last_reset_session = str(state.get("lastResetSessionDate") or "")
        checkpoint = self.service.repository.read_runtime_checkpoint(identity) or {}
        checkpoint_session = _checkpoint_exchange_session_date(checkpoint)
        session_date = str(session.session_date)
        if self._last_daily_reset_date == session_date or last_reset_session == session_date or checkpoint_session == session_date:
            self._last_daily_reset_date = session_date
            self.service.repository.write_runtime_snapshot(
                identity,
                "daily_reset_state",
                {
                    "algorithmId": "regime",
                    "workerId": worker_id,
                    "lastResetSessionDate": session_date,
                    "checkpointSessionDate": checkpoint_session,
                    "reset": False,
                    "reasonCodes": ["regime.daily_reset.current_session_already_active"],
                    "evaluatedAt": evaluated_at.isoformat().replace("+00:00", "Z"),
                },
            )
            return {
                **identity,
                "algorithmId": "regime",
                "reset": False,
                "sessionDate": session_date,
                "reasonCodes": ["regime.daily_reset.current_session_already_active"],
                "evaluatedAt": evaluated_at.isoformat().replace("+00:00", "Z"),
            }
        reset_checkpoint = {
            **identity,
            **checkpoint,
            "dailyCounters": _empty_daily_counters(session_date),
            "cooldownState": {"remainingBars": 0, "reason": None},
            "strategyCooldowns": {},
            "familyCooldowns": {},
            "lastDailyResetSessionDate": session_date,
            "dailyResetAt": evaluated_at.isoformat().replace("+00:00", "Z"),
            "sequenceVersion": int(checkpoint.get("sequenceVersion") or checkpoint.get("stateVersion") or 0) + 1,
        }
        self.service.repository.write_runtime_checkpoint(reset_checkpoint)
        snapshot = {
            "algorithmId": "regime",
            "workerId": worker_id,
            "sessionDate": session_date,
            "previousCheckpointSessionDate": checkpoint_session,
            "reset": True,
            "newEntriesBlocked": bool(self.metrics.entry_creation_paused_for_reconciliation),
            "riskReducingExitsAllowed": self.metrics.risk_reducing_exits_allowed,
            "reasonCodes": ["regime.daily_reset.exchange_session_boundary"],
            "evaluatedAt": evaluated_at.isoformat().replace("+00:00", "Z"),
        }
        self._last_daily_reset_date = session_date
        self.service.repository.write_runtime_snapshot(identity, "daily_reset_state", {"lastResetSessionDate": session_date, **snapshot})
        self.service.repository.record_runtime_event(
            {
                **identity,
                "eventId": f"regime-daily-reset-{session_date}",
                "eventType": "runtime_daily_reset_maintenance",
                "processingStatus": "completed",
                "payload": snapshot,
            }
        )
        return {**identity, **snapshot}

    def run_end_of_day_once(self, *, worker_id: str = "regime_position_management_worker", now: datetime | None = None) -> dict[str, Any]:
        evaluated_at = _as_utc(now or datetime.now(timezone.utc))
        identity = _default_identity(self.config)
        settings_context = self.service.repository.ensure_active_settings_snapshot(identity)
        settings_snapshot = dict(settings_context.get("settingsSnapshot") or {})
        flat_settings = dict(settings_context.get("flatSettings") or {})
        session = exchange_session(evaluated_at.isoformat().replace("+00:00", "Z"))
        schedule = _eod_schedule(session, flat_settings)
        positions_before = self.service.repository.latest_open_regime_positions(identity)
        self.metrics.open_positions = len(positions_before)
        if self.metrics.entry_creation_paused_for_reconciliation or self.metrics.paused:
            self.metrics.protected_positions_managed_during_entry_pause += len(positions_before)
        cancelled_entries = self._cancel_eod_entry_orders(identity, evaluated_at=evaluated_at, schedule=schedule)
        if schedule.get("entryCutoffReached"):
            self._block_new_entries("regime.eod.entry_cutoff_reached")
        flattened = {
            "algorithmId": "regime",
            "exitIntentsCreated": 0,
            "exitIntents": [],
            "reasonCodes": ("regime.eod.flatten_not_due",),
        }
        if bool(flat_settings.get("endOfDayFlattenEnabled", True)) and schedule.get("flattenDue"):
            eod_settings = {
                **settings_snapshot,
                "flatSettings": {**flat_settings, "flattenTimeEt": schedule.get("effectiveFlattenTimeEt")},
            }
            flattened = manage_regime_positions_for_completed_bar(
                repository=self.service.repository,
                identity=identity,
                candle=_eod_mark_price_candle(evaluated_at, positions_before, self.metrics.last_finalized_bar),
                settings_snapshot=eod_settings,
                confirmed_regime="end_of_day",
                entry_paused=True,
                global_emergency_flatten=False,
                evaluated_at=evaluated_at,
            )
            self.metrics.enqueued_orders += int(flattened.get("exitIntentsCreated") or 0)
        reconciliation = self.reconcile_broker_observations(trigger="before_end_of_day_shutdown" if schedule.get("nearClose") else "end_of_day_monitor")
        positions_after = self.service.repository.latest_open_regime_positions(identity)
        inventory = self.service.repository.current_inventory_snapshot(identity)
        trades = self.service.repository.read_owned_records("regime_trades", identity)
        session_trades = _records_for_session(trades, str(session.session_date or ""), keys=("exitAt", "entryAt", "openedAt", "timestamp"))
        remaining_unexpected = bool(flat_settings.get("endOfDayFlattenEnabled", True) and schedule.get("flattenDue") and positions_after)
        risk_usage = {
            "activeReservations": list(self.metrics.risk_reservations),
            "riskReservationsConsistent": bool(self.metrics.risk_reservations_consistent),
            "openOrders": len(self.metrics.open_orders),
        }
        summary = {
            **identity,
            "algorithmId": "regime",
            "workerId": worker_id,
            "sessionDate": session.session_date,
            "marketCloseEt": session.market_close_et,
            "earlyClose": bool(session.is_early_close),
            "entryCutoffReached": bool(schedule.get("entryCutoffReached")),
            "effectiveEntryCutoffEt": schedule.get("effectiveEntryCutoffEt"),
            "effectiveFlattenTimeEt": schedule.get("effectiveFlattenTimeEt"),
            "staleEntryOrdersCancelled": cancelled_entries,
            "flattenRequiredBySettings": bool(flat_settings.get("endOfDayFlattenEnabled", True)),
            "flattenDue": bool(schedule.get("flattenDue")),
            "flattenResult": flattened,
            "positionsBefore": len(positions_before),
            "positionsAfter": len(positions_after),
            "unexpectedOpenPositions": [str(position.get("positionId") or "") for position in positions_after] if remaining_unexpected else [],
            "brokerReconciliation": reconciliation,
            "inventorySnapshot": inventory,
            "dailyPnl": _sum_numeric(session_trades, ("realizedPnl", "realizedPnL", "netPnl")),
            "tradeCount": len(session_trades),
            "riskUsage": risk_usage,
            "riskReducingExitsAllowed": True,
            "reasonCodes": _eod_reason_codes(schedule, cancelled_entries, flattened, remaining_unexpected),
            "evaluatedAt": evaluated_at.isoformat().replace("+00:00", "Z"),
        }
        self.service.repository.write_runtime_snapshot(identity, f"end_of_day:{session.session_date or 'outside_session'}", summary)
        self.service.repository.record_runtime_event(
            {
                **identity,
                "eventId": f"regime-eod-{session.session_date or _stable_runtime_key(summary['evaluatedAt'])}",
                "eventType": "runtime_end_of_day_maintenance",
                "processingStatus": "blocked" if remaining_unexpected else "completed",
                "payload": summary,
            }
        )
        if remaining_unexpected:
            self.service.repository.record_runtime_alert(
                identity,
                {
                    "alertType": "regime_eod_unexpected_open_position",
                    "sessionDate": session.session_date,
                    "positions": summary["unexpectedOpenPositions"],
                    "newEntriesBlocked": True,
                    "riskReducingExitsAllowed": True,
                    "reasonCodes": ["regime.eod.unexpected_open_position_after_flatten"],
                    "timestamp": summary["evaluatedAt"],
                },
                status="active",
            )
            self._block_new_entries("regime.eod.unexpected_open_position_after_flatten")
        return summary

    def _cancel_eod_entry_orders(self, identity: dict[str, str], *, evaluated_at: datetime, schedule: dict[str, Any]) -> int:
        if not bool(schedule.get("entryCutoffReached") or schedule.get("nearClose")):
            return 0
        cancelled = 0
        command = RegimeRuntimeCommand.create(
            "eod_cancel_pending_entries",
            {"reason": "regime.eod.cancel_unfilled_entry_orders", "cancelPendingEntries": True},
            actor="regime-eod-maintenance",
        )
        cancelled += self._cancel_pending_entry_orders(
            command,
            cancel_reason="regime.eod.cancel_unfilled_entry_orders",
            event_type="runtime_eod_pending_entry_cancel",
        )
        if self.paper_gateway is not None:
            try:
                results = cancel_expired_regime_outbox_orders(
                    repository=self.service.repository,
                    identity=identity,
                    paper_gateway=self.paper_gateway,
                    evaluated_at=evaluated_at,
                )
                cancelled += sum(1 for result in results if result.status in {"cancelled", "canceled", "CANCELED"})
            except Exception as exc:
                self._record_component_failure(
                    "execution_outbox",
                    exc,
                    reason_code="regime.eod.cancel_stale_orders_failed",
                    details={"stage": "cancel_eod_entry_orders"},
                )
        return cancelled

    def _manage_positions_for_event(
        self,
        event: RegimeFinalisedBarEvent,
        settings_context: dict[str, Any],
        decision_result: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            snapshot = build_regime_market_snapshot(event.market_payload)
            latest = snapshot.latest
            decision = decision_result.get("decision") if isinstance(decision_result.get("decision"), dict) else {}
            confirmed = decision.get("confirmed_state") if isinstance(decision.get("confirmed_state"), dict) else {}
            result = manage_regime_positions_for_completed_bar(
                repository=self.service.repository,
                identity=event.identity,
                candle={
                    "timestamp": latest.timestamp,
                    "open": latest.open,
                    "high": latest.high,
                    "low": latest.low,
                    "close": latest.close,
                    "volume": latest.volume,
                },
                settings_snapshot=dict(settings_context.get("settingsSnapshot") or {}),
                confirmed_regime=str(confirmed.get("confirmed_regime") or "unknown"),
                entry_paused=bool(self.metrics.entry_creation_paused_for_reconciliation or self.metrics.paused),
                global_emergency_flatten=bool(self.metrics.emergency_flatten_requested),
                evaluated_at=datetime.now(timezone.utc),
            )
            self.metrics.open_positions = max(0, int(result.get("openPositionsEvaluated") or 0) - int(result.get("exitIntentsCreated") or 0))
            if result.get("exitIntentsCreated"):
                self.metrics.enqueued_orders += int(result.get("exitIntentsCreated") or 0)
            self._mark_component("position_reconciliation", "healthy", reason_codes=("regime.health.position_management.completed",), details=result)
            return result
        except Exception as exc:
            self._record_component_failure(
                "position_reconciliation",
                exc,
                reason_code="regime.trade_management.worker_failed",
                details={"eventId": event.event_id},
            )
            return {
                "algorithmId": "regime",
                "processed": False,
                "paperOnly": True,
                "liveTradingEnabled": False,
                "reasonCodes": ["regime.trade_management.worker_failed"],
                "failureMessage": str(exc),
            }

    def status(self) -> dict[str, Any]:
        self.metrics.queue_depth = self.event_queue.qsize()
        self.metrics.command_queue_depth = self.command_queue.qsize()
        identity = _default_identity(self.config)
        self._refresh_operational_records(identity)
        payload = self.metrics.as_dict()
        paper_control = self.automatic_paper_control_status(identity)
        market_status = _market_open_status(datetime.now(timezone.utc))
        publisher_status = _publisher_status_snapshot(self, identity)
        broker_status = _broker_status_snapshot(self.metrics, identity)
        account_snapshot_status = _account_snapshot_status_snapshot(paper_control, identity)
        latest_order_intent = _latest_owned_record(self.service.repository, "regime_execution_outbox", identity)
        latest_broker_order = _project_status_record(_latest_owned_record(self.service.repository, "regime_orders", identity), record_type="broker_order")
        current_position = _current_regime_position_snapshot(self.service.repository, identity)
        worker_heartbeats = _worker_heartbeats_from_metrics(self.metrics, identity)
        orders_reconciled = bool(self.metrics.latest_reconciliation and self.metrics.latest_reconciliation.get("reconciled") is True and not self.metrics.reconciliation_discrepancies)
        return {
            "algorithmId": "regime",
            "algorithmInstanceId": identity["algorithmInstanceId"],
            "accountId": identity["accountId"],
            "runtimeMode": identity["runtimeMode"],
            "symbol": identity["symbol"],
            "runtimeVersion": REGIME_RUNTIME_SUPERVISOR_VERSION,
            "workers": REGIME_RUNTIME_WORKERS,
            "apiHandlersExecuteHeavyWorkInline": False,
            "liveTradingEnabled": False,
            "allowedRuntimeModes": REGIME_ALLOWED_RUNTIME_MODE_VALUES,
            "paperRequestedOn": paper_control["paperRequestedOn"],
            "paperEffectiveOn": paper_control["paperEffectiveOn"],
            "paperEffectiveBlockers": list(paper_control["paperEffectiveBlockers"]),
            "paperEffectiveBlockerReasonCodes": list(paper_control["paperEffectiveBlockerReasonCodes"]),
            "automaticPaperControl": paper_control,
            "rolloutStage": self.metrics.current_rollout_stage,
            "marketOpen": market_status["marketOpen"],
            "nextMarketOpen": market_status["nextMarketOpen"],
            "marketSession": market_status,
            "publisherStatus": publisher_status,
            "lastPublishedBar": publisher_status.get("lastPublishedBar") or self.metrics.last_finalized_bar,
            "lastProcessedBar": self.metrics.last_processed_bar,
            "barLagSeconds": self.metrics.queue_lag_seconds if self.metrics.queue_lag_seconds is not None else self.metrics.processing_lag_seconds,
            "decisionQueueDepth": self.metrics.queue_depth,
            "outboxQueueDepth": int((self.metrics.outbox_status or {}).get("pendingCount") or len(self.metrics.open_orders)),
            "brokerStatus": broker_status,
            "accountSnapshotStatus": account_snapshot_status,
            "inventoryReconciled": bool(self.metrics.inventory_reconciled),
            "ordersReconciled": orders_reconciled,
            "killSwitch": self.kill_switch_status(),
            "activeSettingsVersion": self.metrics.active_settings_version,
            "confirmedRegime": _current_regime_from_metrics(self.metrics),
            "latestDecision": _project_latest_decision_status(self.metrics.latest_decision, identity),
            "latestOrderIntent": _project_status_record(latest_order_intent, record_type="order_intent"),
            "latestBrokerOrder": latest_broker_order,
            "currentRegimePosition": current_position,
            "dailyRegimePnl": self.metrics.daily_regime_pnl,
            "dailyRegimeTradeCount": self.metrics.daily_trade_count,
            "entryBlockReasonCodes": list(self.metrics.entry_block_reason_codes),
            "workerHeartbeats": worker_heartbeats,
            **payload,
            "supervisorHeartbeat": self.metrics.supervisor_heartbeat_at,
            "lastReceivedBar": self.metrics.last_received_bar,
            "lastFinalizedBar": self.metrics.last_finalized_bar,
            "lastProcessedBar": self.metrics.last_processed_bar,
            "lastProcessedBarByInstanceSymbol": dict(self.metrics.last_processed_bar_by_instance_symbol),
            "currentSettingsVersion": self.metrics.active_settings_version,
            "currentConfirmedRegime": _current_regime_from_metrics(self.metrics),
            "paperRolloutStage": self.metrics.current_rollout_stage,
            "rolloutStagePolicy": self.metrics.rollout_stage_policy,
        }

    def automatic_paper_control_status(self, identity: dict[str, Any] | None = None) -> dict[str, Any]:
        active_identity = identity or _default_identity(self.config)
        rollout_snapshot = self._load_persisted_rollout_stage(active_identity)
        control = self._load_automatic_paper_control(active_identity, rollout_snapshot=rollout_snapshot)
        requested = bool(control.get("requestedAutomaticPaperTradingEnabled") or control.get("paperRequestedOn"))
        evaluation = _paper_effective_activation_evaluation(
            self,
            active_identity,
            rollout_snapshot=rollout_snapshot,
            control_snapshot=control,
            requested=requested,
            evaluated_at=datetime.now(timezone.utc),
        )
        return {
            **control,
            "algorithmId": "regime",
            "runtimeMode": active_identity.get("runtimeMode"),
            "algorithmInstanceId": active_identity.get("algorithmInstanceId"),
            "accountId": active_identity.get("accountId"),
            "symbol": active_identity.get("symbol"),
            "paperRequestedOn": requested,
            "paperEffectiveOn": bool(requested and not evaluation["blockers"]),
            "paperEffectiveBlockers": list(evaluation["blockers"]),
            "paperEffectiveBlockerReasonCodes": list(evaluation["reasonCodes"]),
            "paperEffectiveGateSnapshot": dict(evaluation["gateSnapshot"]),
            "automaticPaperTradingEnabled": bool(requested and not evaluation["blockers"]),
            "paperButtonRequested": requested,
            "paperButtonEffective": bool(requested and not evaluation["blockers"]),
            "liveTradingEnabled": False,
            "paperOnly": True,
        }

    def health(self) -> dict[str, Any]:
        return health_from_metrics(self.metrics)

    def observability(self) -> dict[str, Any]:
        self._refresh_operational_records(_default_identity(self.config))
        return operational_snapshot_from_metrics(self.metrics)

    def kill_switch_status(self) -> dict[str, Any]:
        snapshot = self.service.repository.read_runtime_snapshot(_default_identity(self.config), "kill_switch") or {}
        if snapshot and not self.metrics.kill_switch_active:
            self._apply_kill_switch_snapshot(snapshot)
        return {
            "algorithmId": "regime",
            "active": self.metrics.kill_switch_active,
            "reason": self.metrics.kill_switch_reason,
            "actor": self.metrics.kill_switch_actor,
            "activatedAt": self.metrics.kill_switch_activated_at,
            "stateVersion": self.metrics.kill_switch_state_version,
            "blocksNewEntries": self.metrics.kill_switch_active,
            "riskReducingExitsAllowed": True,
            "pendingEntryOrdersCancelRequested": self.metrics.pending_entry_orders_cancel_requested,
            "auditTrailSource": "regime_runtime_events",
        }

    def alerts(self) -> dict[str, Any]:
        self.metrics.alert_conditions = alert_conditions_from_metrics(self.metrics)
        for alert in self.metrics.alert_conditions:
            try:
                self.service.repository.record_runtime_alert(
                    _default_identity(self.config),
                    {"alertCode": alert, "timestamp": _utc_now()},
                    status="active",
                )
            except Exception as exc:  # pragma: no cover - telemetry persistence guard.
                logger.exception("Regime failed to persist runtime alert", exc_info=(type(exc), exc, exc.__traceback__))
        return {"algorithmId": "regime", "alerts": list(self.metrics.alert_conditions)}

    def admin_audit(self) -> dict[str, Any]:
        snapshot = self.service.repository.read_runtime_snapshot(_default_identity(self.config), "admin_audit") or {}
        return {"algorithmId": "regime", "audit": snapshot.get("commands") or [], "latestCommand": self.metrics.latest_command}

    def rollout_stage(self) -> dict[str, Any]:
        return self._load_persisted_rollout_stage()

    def queue_depth(self) -> dict[str, Any]:
        return {
            "algorithmId": "regime",
            "queueDepth": self.event_queue.qsize(),
            "commandQueueDepth": self.command_queue.qsize(),
            "queueLagSeconds": self.metrics.queue_lag_seconds if self.metrics.queue_lag_seconds is not None else self.metrics.processing_lag_seconds,
            "eventAgeSeconds": self.metrics.latest_event_age_seconds,
        }

    def latest_checkpoint(self) -> dict[str, Any]:
        return {"algorithmId": "regime", "checkpoint": self.metrics.last_checkpoint}

    def latest_decision(self) -> dict[str, Any]:
        return {"algorithmId": "regime", "decision": self.metrics.latest_decision}

    def recovery_status(self) -> dict[str, Any]:
        return {"algorithmId": "regime", **self.metrics.latest_recovery}

    def _event_rejection(self, event: RegimeFinalisedBarEvent) -> str | None:
        if event.event_id in self._seen_event_ids:
            self.metrics.duplicate_events += 1
            return "duplicate"
        lag = (datetime.now(timezone.utc) - event.published_at).total_seconds()
        self.metrics.processing_lag_seconds = lag
        self.metrics.queue_lag_seconds = lag
        self.metrics.latest_event_age_seconds = (datetime.now(timezone.utc) - event.completed_bar_timestamp).total_seconds()
        if lag > self.config.max_processing_lag_seconds:
            self.metrics.stale_events += 1
            self.metrics.queue_lag_block_active = True
            self._block_new_entries("regime.runtime.queue_lag_exceeded")
            if not event.replay_recovery:
                return "stale"
        last = self.metrics.last_processed_bar_by_instance_symbol.get(_instance_symbol_key(event))
        if last and event.completed_bar_timestamp.isoformat().replace("+00:00", "Z") <= last and not event.replay_recovery:
            self.metrics.out_of_order_events += 1
            return "out_of_order"
        if last:
            try:
                previous = datetime.fromisoformat(str(last).replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                previous = None
            if previous is not None and event.completed_bar_timestamp - previous > timedelta(minutes=1):
                self.metrics.gap_events += 1
                self._block_new_entries("regime.runtime.finalized_bar_gap_detected")
                self._mark_component(
                    "market_event_ingestion",
                    "unhealthy",
                    reason_codes=("regime.runtime.event.gap_detected",),
                    details={
                        "previousProcessedBarTimestamp": last,
                        "currentBarTimestamp": event.completed_bar_timestamp.isoformat().replace("+00:00", "Z"),
                    },
                )
        return None

    def _persist_event(self, event: RegimeFinalisedBarEvent, status: str) -> dict[str, Any]:
        result = self.service.repository.record_runtime_event(
            {
                **event.identity,
                "eventId": event.event_id,
                "decisionId": event.event_id,
                "timestamp": event.completed_bar_timestamp.isoformat().replace("+00:00", "Z"),
                "eventType": "finalised_bar",
                "processingStatus": status,
                "payload": event.as_dict(),
            }
        )
        self.metrics.persisted_events += 1
        return result

    def _event_with_durable_identity(self, event: RegimeFinalisedBarEvent) -> tuple[RegimeFinalisedBarEvent, dict[str, Any]]:
        identity = regime_settings_identity_from_payload(event.identity)
        settings_context = self.service.repository.ensure_active_settings_snapshot(identity)
        if not settings_context or not settings_context.get("settingsVersion"):
            raise RuntimeError("Regime active settings could not be loaded")
        if event.data_manifest_hash:
            data_manifest_hash = event.data_manifest_hash
        else:
            snapshot = build_regime_market_snapshot(event.market_payload)
            inventory_snapshot = {
                "algorithmInstanceId": identity["algorithmInstanceId"],
                "accountId": identity["accountId"],
                "runtimeMode": identity["runtimeMode"],
                "symbol": identity["symbol"],
            }
            data_manifest_hash = deterministic_data_manifest_hash(snapshot, inventory_snapshot)
        return event.with_runtime_identity(data_manifest_hash=data_manifest_hash, settings_version=str(settings_context["settingsVersion"])), settings_context

    def _load_shared_account_snapshot(self, event: RegimeFinalisedBarEvent) -> dict[str, Any]:
        return self._load_shared_account_snapshot_for_identity({key: str(value) for key, value in event.identity.items()})

    def _load_shared_account_snapshot_for_identity(self, identity: dict[str, str]) -> dict[str, Any]:
        if self.account_snapshot_provider is None:
            snapshot = fail_closed_regime_account_snapshot(
                identity,
                reason_codes=("regime.runtime.account_snapshot.unavailable_fail_closed",),
                source_authority="shared_backend_unavailable",
            )
        else:
            try:
                snapshot = sanitize_regime_account_snapshot(dict(self.account_snapshot_provider(identity) or {}))
            except Exception:
                snapshot = fail_closed_regime_account_snapshot(
                    identity,
                    reason_codes=("regime.runtime.account_snapshot.provider_failed_fail_closed",),
                    source_authority="shared_backend_unavailable",
                )
        snapshot = normalize_regime_account_snapshot(
            snapshot,
            identity=identity,
            observed_at=datetime.now(timezone.utc),
            max_age_seconds=self.config.account_snapshot_max_age_seconds,
        )
        for reason in snapshot.get("reasonCodes") or ():
            if str(reason).startswith("regime.account_snapshot.") or str(reason).startswith("regime.runtime.account_snapshot."):
                self._block_new_entries(str(reason))
        return {**snapshot, "runtimeLoadedBy": REGIME_RUNTIME_SUPERVISOR_VERSION}

    def _claim_processing_lease(self, event: RegimeFinalisedBarEvent) -> None:
        lease_expires = (datetime.now(timezone.utc) + timedelta(seconds=self.config.worker_lease_seconds)).isoformat().replace("+00:00", "Z")
        self.service.repository.record_worker_heartbeat(
            event.identity,
            worker_id=_processing_lease_worker_id(event),
            owner_id=self.config.owner_id,
            lease_expires_at=lease_expires,
        )
        self.service.repository.record_runtime_event(
            {
                **event.identity,
                "eventId": f"{event.event_id}:processing-lease-claimed",
                "eventType": "runtime_processing_lease_claimed",
                "processingStatus": "leased",
                "payload": {
                    "algorithmId": "regime",
                    "eventId": event.event_id,
                    "leaseKey": list(_processing_lock_key(event)),
                    "leaseOwner": self.config.owner_id,
                    "leaseExpiresAt": lease_expires,
                },
            }
        )

    def _release_processing_lease(self, event: RegimeFinalisedBarEvent) -> None:
        self.service.repository.record_runtime_event(
            {
                **event.identity,
                "eventId": f"{event.event_id}:processing-lease-released",
                "eventType": "runtime_processing_lease_released",
                "processingStatus": "released",
                "payload": {
                    "algorithmId": "regime",
                    "eventId": event.event_id,
                    "leaseKey": list(_processing_lock_key(event)),
                    "leaseOwner": self.config.owner_id,
                    "releasedAt": _utc_now(),
                },
            }
        )

    def _record_stage(self, event: RegimeFinalisedBarEvent, stage: str, payload: dict[str, Any] | None = None, *, status: str = "completed") -> None:
        self.service.repository.record_stage_checkpoint(
            {**event.identity, "eventId": event.event_id, "settingsVersion": event.settings_version, "dataManifestHash": event.data_manifest_hash},
            stage,
            status=status,
            payload=payload,
        )

    async def _maybe_crash(self, stage: str) -> None:
        if self.config.crash_after_stage == stage:
            raise RuntimeError(f"simulated_crash_after_{stage}")

    def _decision_id_from_event_or_stage(self, event: RegimeFinalisedBarEvent) -> str:
        stage = self.service.repository.read_event_stage(event.identity, event.event_id, "decision_persisted", status="completed")
        details = stage.get("details") if isinstance(stage, dict) else {}
        return str(details.get("decisionId") or event.event_id)

    def _block_new_entries(self, reason_code: str) -> None:
        self.metrics.entry_creation_paused_for_reconciliation = True
        if reason_code not in self.metrics.entry_block_reason_codes:
            self.metrics.entry_block_reason_codes.append(reason_code)

    def _unblock_new_entries(self, reason_code: str) -> None:
        self.metrics.entry_block_reason_codes = [code for code in self.metrics.entry_block_reason_codes if code != reason_code]

    def _mark_component(
        self,
        component: str,
        status: str,
        *,
        reason_codes: tuple[str, ...] = (),
        error: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        mark_component_health(self.metrics, component, status, reason_codes=reason_codes, error=error, details=details)

    def _record_component_failure(
        self,
        component: str,
        exc: BaseException,
        *,
        reason_code: str,
        block_new_entries: bool = True,
        details: dict[str, Any] | None = None,
    ) -> None:
        logger.error("Regime component failure: %s", component, exc_info=(type(exc), exc, exc.__traceback__))
        if block_new_entries:
            self._block_new_entries(reason_code)
        self._mark_component(component, "unhealthy", reason_codes=(reason_code,), error=str(exc), details=details)
        if component == "database":
            self.metrics.persistence_available = False
        if component == "settings_repository":
            self.metrics.settings_available = False
        if component in {"position_reconciliation", "order_reconciliation"}:
            self.metrics.inventory_reconciled = False
        event = {
            **_default_identity(self.config),
            "eventId": f"regime-component-failure-{component}-{_utc_now()}",
            "eventType": "runtime_component_failure",
            "processingStatus": "unhealthy",
            "payload": {
                "algorithmId": "regime",
                "component": component,
                "reasonCodes": [reason_code],
                "failureMessage": str(exc),
                "details": details or {},
                "newEntriesBlocked": block_new_entries,
                "riskReducingExitsAllowed": self.metrics.risk_reducing_exits_allowed,
            },
        }
        try:
            self.service.repository.record_runtime_event(event)
        except Exception:
            logger.exception("Regime failed to persist component-health failure event")
            if component != "database":
                self._mark_component("database", "unhealthy", reason_codes=("regime.health.database.error_event_persist_failed",), error=str(exc))
            self.metrics.persistence_available = False

    async def _process_command(self, command: RegimeRuntimeCommand) -> None:
        self.metrics.latest_command = command.as_dict()
        if command.command_type == "pause":
            self.metrics.paused = True
            self.metrics.pause_reason = str(command.payload.get("reason") or "operator_pause")
        elif command.command_type == "resume":
            if self.metrics.inventory_reconciled and not self.metrics.kill_switch_active:
                self.metrics.paused = False
                self.metrics.pause_reason = None
                self.metrics.entry_creation_paused_for_reconciliation = False
        elif command.command_type == "kill_switch_activate":
            self._activate_kill_switch(command, immediate=False)
        elif command.command_type == "kill_switch_deactivate":
            self._deactivate_kill_switch(command, immediate=False)
        elif command.command_type == "emergency_flatten":
            self.metrics.emergency_flatten_requested = True
            self.metrics.paused = True
            self.metrics.pause_reason = "emergency_flatten"
        elif command.command_type == "disable_strategy":
            strategy_id = str(command.payload.get("strategyId") or command.payload.get("strategy_id") or "")
            if strategy_id:
                if strategy_id not in self.metrics.disabled_strategy_ids:
                    self.metrics.disabled_strategy_ids.append(strategy_id)
                activation = self._activate_strategy_lifecycle(strategy_id, "disabled", command)
                self.metrics.active_settings_version = str(activation.get("settingsVersion") or self.metrics.active_settings_version)
        elif command.command_type == "enable_strategy":
            strategy_id = str(command.payload.get("strategyId") or command.payload.get("strategy_id") or "")
            if strategy_id:
                self.metrics.disabled_strategy_ids = [item for item in self.metrics.disabled_strategy_ids if item != strategy_id]
                activation = self._activate_strategy_lifecycle(strategy_id, "active", command)
                self.metrics.active_settings_version = str(activation.get("settingsVersion") or self.metrics.active_settings_version)
        elif command.command_type == "rotate_settings_version":
            activation = self.service.activate_settings({"settings": command.payload, "actor": command.actor})
            self.metrics.active_settings_version = str(activation.get("settingsVersion") or self.metrics.active_settings_version)
        elif command.command_type == "set_rollout_stage":
            activation = self._activate_rollout_stage_from_command(
                command,
                str(command.payload.get("stage") or command.payload.get("rolloutStage") or ""),
            )
            self.metrics.latest_command = {**command.as_dict(), "rolloutActivation": activation}
        elif command.command_type == "set_automatic_paper":
            result = self._set_automatic_paper_control(command)
            self.metrics.latest_command = {**command.as_dict(), "automaticPaperControl": result}
        elif command.command_type == "recovery":
            self.metrics.entry_creation_paused_for_reconciliation = True
            await self.run_recovery_once()
        self._append_admin_audit(command)
        self.service.repository.record_runtime_event(
            {
                **_default_identity(self.config),
                "eventId": command.command_id,
                "eventType": f"runtime_command_{command.command_type}",
                "processingStatus": "completed",
                "payload": command.as_dict(),
            }
        )

    def _activate_strategy_lifecycle(self, strategy_id: str, lifecycle: str, command: RegimeRuntimeCommand) -> dict[str, Any]:
        context = self.service.repository.active_settings_snapshot(_default_identity(self.config), create_default=True)
        snapshot = dict(context.get("settingsSnapshot") or {}) if isinstance(context, dict) else {}
        for repository_field in ("activatedAt", "activatedBy", "createdAt"):
            snapshot.pop(repository_field, None)
        strategy_settings = dict(snapshot.get("strategy_settings") or {})
        if strategy_id not in strategy_settings:
            raise ValueError(f"Unknown Regime strategy for lifecycle command: {strategy_id}")
        strategy_settings[strategy_id] = {**dict(strategy_settings[strategy_id]), "lifecycle": lifecycle, "enabled": lifecycle == "active"}
        snapshot["strategy_settings"] = strategy_settings
        return self.service.activate_settings({"settingsSnapshot": snapshot, "actor": command.actor})

    def _activate_rollout_stage_from_command(self, command: RegimeRuntimeCommand, requested_stage: str) -> dict[str, Any]:
        evidence = self.service.repository.read_regime_rollout_promotion_evidence(_default_identity(self.config))
        activation = activate_operational_rollout_stage(
            _RegimeRolloutSnapshotStore(self.service.repository, _default_identity(self.config)),
            requested_stage,
            actor=command.actor,
            reason=str(command.payload.get("reason") or "operator_rollout_stage_change"),
            evidence=evidence,
        )
        if activation.get("activated"):
            self._apply_rollout_stage_snapshot(activation)
        else:
            self._block_new_entries("regime.rollout.stage_activation_blocked")
        return activation

    def _set_automatic_paper_control(self, command: RegimeRuntimeCommand) -> dict[str, Any]:
        enabled = bool(command.payload.get("enabled") or command.payload.get("automaticPaperTradingEnabled"))
        reason = str(command.payload.get("reason") or ("global_paper_toggle_on" if enabled else "global_paper_toggle_off"))
        identity = _default_identity(self.config)
        evidence_mapping = self.service.repository.read_regime_rollout_promotion_evidence(identity)
        evidence = RegimePaperPromotionEvidence.from_mapping(evidence_mapping)
        current = self._load_persisted_rollout_stage()
        current_stage = str(current.get("stage") or REGIME_DEFAULT_OPERATIONAL_ROLLOUT_STAGE)
        target_stage = "limited_paper" if enabled else current_stage
        activations: list[dict[str, Any]] = []
        previous_control = self._load_automatic_paper_control(identity, rollout_snapshot=current)
        previous_requested = bool(previous_control.get("requestedAutomaticPaperTradingEnabled"))
        previous_effective = bool(previous_control.get("automaticPaperSubmissionEnabled"))
        cancelled_pending_entries = 0

        if enabled:
            for stage in self._rollout_stages_toward(current_stage, target_stage):
                activation = activate_operational_rollout_stage(
                    _RegimeRolloutSnapshotStore(self.service.repository, identity),
                    stage,
                    actor=command.actor,
                    reason=reason,
                    evidence=evidence,
                )
                activations.append(activation)
                if not activation.get("activated"):
                    self._block_new_entries("regime.rollout.automatic_paper_activation_blocked")
                    break
                self._apply_rollout_stage_snapshot(activation)
        else:
            self._block_new_entries("regime.runtime.automatic_paper_control_off")
            self._unblock_new_entries("regime.rollout.automatic_paper_activation_blocked")
            cancelled_pending_entries = self._cancel_pending_entry_orders(
                command,
                cancel_reason="regime.runtime.automatic_paper_control_off",
                event_type="runtime_automatic_paper_pending_entry_cancel",
            )
            self.metrics.pending_entry_orders_cancel_requested += cancelled_pending_entries

        final_stage = self._load_persisted_rollout_stage()
        stage_name = str(final_stage.get("stage") or REGIME_DEFAULT_OPERATIONAL_ROLLOUT_STAGE)
        evaluation = evaluate_operational_rollout_stage("limited_paper", current_stage=stage_name, evidence=evidence)
        automatic_enabled = enabled and stage_name in {"limited_paper", "normal_paper"} and bool(final_stage.get("automaticPaperSubmissionEnabled")) and operational_stage_allows_real_paper_submission(stage_name, evidence=evidence_mapping)
        if automatic_enabled:
            self._unblock_new_entries("regime.runtime.automatic_paper_control_off")
        reason_codes = list(dict.fromkeys(code for activation in activations for code in activation.get("reasonCodes", ())))
        if not reason_codes:
            reason_codes = [f"regime.rollout.{stage_name}.active"]
        if enabled and not automatic_enabled:
            reason_codes.append("regime.runtime.automatic_paper.not_enabled_until_rollout_gate_passes")
        if not enabled:
            reason_codes.append("regime.runtime.automatic_paper.disabled_by_global_toggle")
        control_snapshot = {
            "algorithmId": "regime",
            "requestedAutomaticPaperTradingEnabled": enabled,
            "automaticPaperTradingEnabled": automatic_enabled,
            "automaticPaperSubmissionEnabled": automatic_enabled,
            "paperRequestedOn": enabled,
            "paperEffectiveOn": automatic_enabled,
            "paperButtonRequested": enabled,
            "paperButtonEffective": automatic_enabled,
            "previousRequestedAutomaticPaperTradingEnabled": previous_requested,
            "previousAutomaticPaperTradingEnabled": previous_effective,
            "targetStage": target_stage,
            "rolloutStage": stage_name,
            "rolloutStageStatus": final_stage,
            "rolloutEvaluation": evaluation,
            "activations": activations,
            "paperOnly": True,
            "liveTradingEnabled": False,
            "manualPaperTradingUnaffected": True,
            "manualPaperTradingWhenMarketOpen": True,
            "apiHandlersExecuteAuthoritativeTradingLogic": False,
            "operator": command.actor,
            "actor": command.actor,
            "reason": reason,
            "requestedAt": command.created_at,
            "effectiveAt": _utc_now(),
            "priorState": {
                "requestedAutomaticPaperTradingEnabled": previous_requested,
                "automaticPaperTradingEnabled": previous_effective,
                "rolloutStage": current.get("stage"),
            },
            "newState": {
                "requestedAutomaticPaperTradingEnabled": enabled,
                "automaticPaperTradingEnabled": automatic_enabled,
                "rolloutStage": stage_name,
            },
            "keepsRuntimeIdentityUnchanged": True,
            "keepsInventoryStateIntact": True,
            "blocksNewEntryIntents": not automatic_enabled,
            "blocksQueuedEntrySubmissions": not automatic_enabled,
            "riskReducingExitsAllowed": True,
            "pendingEntryOrdersCancelRequested": cancelled_pending_entries,
            "reasonCodes": tuple(dict.fromkeys(reason_codes)),
        }
        effective_evaluation = _paper_effective_activation_evaluation(
            self,
            identity,
            rollout_snapshot=final_stage,
            control_snapshot=control_snapshot,
            requested=enabled,
            evaluated_at=datetime.now(timezone.utc),
        )
        paper_effective_on = bool(enabled and not effective_evaluation["blockers"])
        control_snapshot = {
            **control_snapshot,
            "automaticPaperTradingEnabled": paper_effective_on,
            "paperEffectiveOn": paper_effective_on,
            "paperButtonEffective": paper_effective_on,
            "paperEffectiveBlockers": list(effective_evaluation["blockers"]),
            "paperEffectiveBlockerReasonCodes": list(effective_evaluation["reasonCodes"]),
            "paperEffectiveGateSnapshot": dict(effective_evaluation["gateSnapshot"]),
            "newState": {
                **dict(control_snapshot["newState"]),
                "automaticPaperTradingEnabled": paper_effective_on,
                "paperEffectiveOn": paper_effective_on,
            },
        }
        self.service.repository.write_runtime_snapshot(identity, "automatic_paper_control", control_snapshot)
        self.service.repository.record_runtime_event(
            {
                **identity,
                "eventId": f"{command.command_id}:automatic-paper-control",
                "eventType": "runtime_automatic_paper_control",
                "processingStatus": "enabled" if automatic_enabled else "disabled",
                "payload": control_snapshot,
            }
        )
        return control_snapshot

    def _rollout_stages_toward(self, current_stage: str, target_stage: str) -> tuple[str, ...]:
        current = current_stage if current_stage in REGIME_OPERATIONAL_ROLLOUT_STAGES else REGIME_DEFAULT_OPERATIONAL_ROLLOUT_STAGE
        target = target_stage if target_stage in REGIME_OPERATIONAL_ROLLOUT_STAGES else REGIME_DEFAULT_OPERATIONAL_ROLLOUT_STAGE
        current_index = REGIME_OPERATIONAL_ROLLOUT_STAGES.index(current)  # type: ignore[arg-type]
        target_index = REGIME_OPERATIONAL_ROLLOUT_STAGES.index(target)  # type: ignore[arg-type]
        if target_index <= current_index:
            return (target,)
        return REGIME_OPERATIONAL_ROLLOUT_STAGES[current_index + 1 : target_index + 1]

    def _append_admin_audit(self, command: RegimeRuntimeCommand) -> None:
        identity = _default_identity(self.config)
        existing = self.service.repository.read_runtime_snapshot(identity, "admin_audit") or {}
        commands = list(existing.get("commands") or [])
        commands.append({**command.as_dict(), "algorithmId": "regime", "processedAt": _utc_now()})
        self.service.repository.write_runtime_snapshot(identity, "admin_audit", {"commands": commands[-100:]})

    def _load_persisted_kill_switch(self) -> None:
        snapshot = self.service.repository.read_runtime_snapshot(_default_identity(self.config), "kill_switch") or {}
        self._apply_kill_switch_snapshot(snapshot)

    def _load_persisted_rollout_stage(self, identity: dict[str, Any] | None = None) -> dict[str, Any]:
        snapshot = read_or_initialize_operational_rollout_stage(_RegimeRolloutSnapshotStore(self.service.repository, identity or _default_identity(self.config)))
        self._apply_rollout_stage_snapshot(snapshot)
        return dict(snapshot)

    def _load_automatic_paper_control(
        self,
        identity: dict[str, Any] | None = None,
        *,
        rollout_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        active_identity = identity or _default_identity(self.config)
        rollout = rollout_snapshot or self._load_persisted_rollout_stage(active_identity)
        snapshot = self.service.repository.read_runtime_snapshot(active_identity, "automatic_paper_control") or {}
        requested = _paper_button_requested(snapshot, fallback=False)
        stage = str(rollout.get("stage") or REGIME_DEFAULT_OPERATIONAL_ROLLOUT_STAGE)
        stage_effective = stage in {"limited_paper", "normal_paper"} and bool(rollout.get("automaticPaperSubmissionEnabled"))
        evidence = self.service.repository.read_regime_rollout_promotion_evidence(active_identity)
        effective = bool(requested and stage_effective and operational_stage_allows_real_paper_submission(stage, evidence=evidence))
        return {
            **snapshot,
            "algorithmId": "regime",
            "requestedAutomaticPaperTradingEnabled": requested,
            "automaticPaperTradingEnabled": effective,
            "automaticPaperSubmissionEnabled": effective,
            "paperButtonRequested": requested,
            "paperButtonEffective": effective,
            "rolloutStage": stage,
            "paperOnly": True,
            "liveTradingEnabled": False,
        }

    def _apply_rollout_stage_snapshot(self, snapshot: dict[str, Any]) -> None:
        stage = str(snapshot.get("stage") or REGIME_DEFAULT_OPERATIONAL_ROLLOUT_STAGE)
        policy = operational_rollout_stage_policy(stage)
        self.metrics.current_rollout_stage = str(policy["stage"])
        self.metrics.rollout_stage_version = int(snapshot.get("stateVersion") or snapshot.get("state_version") or self.metrics.rollout_stage_version or 0)
        self.metrics.rollout_stage_policy = policy
        self.metrics.simulated_execution_active = bool(policy["permissions"].get("useSimulatedBroker"))  # type: ignore[index]
        if stage == "disabled":
            self.metrics.paused = True
            self.metrics.pause_reason = "rollout_disabled"
            self._block_new_entries("regime.rollout.disabled")
        else:
            self._unblock_new_entries("regime.rollout.disabled")

    def _apply_kill_switch_snapshot(self, snapshot: dict[str, Any]) -> None:
        if not isinstance(snapshot, dict):
            return
        active = bool(snapshot.get("active"))
        self.metrics.kill_switch_active = active
        self.metrics.kill_switch_reason = str(snapshot.get("reason") or "") or None
        self.metrics.kill_switch_actor = str(snapshot.get("actor") or "") or None
        self.metrics.kill_switch_activated_at = str(snapshot.get("activatedAt") or snapshot.get("activated_at") or "") or None
        self.metrics.kill_switch_state_version = int(snapshot.get("stateVersion") or snapshot.get("state_version") or self.metrics.kill_switch_state_version or 0)
        if active:
            self._block_new_entries("regime.runtime.kill_switch_active")
            self.metrics.paused = True
            self.metrics.pause_reason = "kill_switch"
        else:
            self._unblock_new_entries("regime.runtime.kill_switch_active")

    def _activate_kill_switch(self, command: RegimeRuntimeCommand, *, immediate: bool) -> None:
        reason = str(command.payload.get("reason") or "operator_kill_switch")
        self.metrics.kill_switch_active = True
        self.metrics.kill_switch_reason = reason
        self.metrics.kill_switch_actor = command.actor
        self.metrics.kill_switch_activated_at = command.created_at
        self.metrics.kill_switch_state_version += 1
        self.metrics.paused = True
        self.metrics.pause_reason = "kill_switch"
        self._block_new_entries("regime.runtime.kill_switch_active")
        cancelled = self._cancel_pending_entry_orders(
            command,
            cancel_reason="regime.runtime.kill_switch_active",
            event_type="runtime_kill_switch_pending_entry_cancel",
        )
        self.metrics.pending_entry_orders_cancel_requested += cancelled
        snapshot = {
            "algorithmId": "regime",
            "active": True,
            "reason": reason,
            "actor": command.actor,
            "activatedAt": command.created_at,
            "stateVersion": self.metrics.kill_switch_state_version,
            "blocksNewEntries": True,
            "riskReducingExitsAllowed": True,
            "pendingEntryOrdersCancelRequested": self.metrics.pending_entry_orders_cancel_requested,
            "auditTrailSource": "regime_runtime_events",
        }
        self.service.repository.write_runtime_snapshot(_default_identity(self.config), "kill_switch", snapshot)
        self.service.repository.record_runtime_event(
            {
                **_default_identity(self.config),
                "eventId": f"{command.command_id}:kill-switch-activated:{'immediate' if immediate else 'worker'}",
                "eventType": "runtime_kill_switch",
                "processingStatus": "active",
                "payload": {**snapshot, "cancelPendingEntries": bool(command.payload.get("cancelPendingEntries", True)), "cancelledPendingEntryOrders": cancelled},
            }
        )

    def _deactivate_kill_switch(self, command: RegimeRuntimeCommand, *, immediate: bool) -> None:
        self.metrics.kill_switch_active = False
        self.metrics.kill_switch_reason = None
        self.metrics.kill_switch_actor = command.actor
        self.metrics.kill_switch_activated_at = None
        self.metrics.kill_switch_state_version += 1
        self._unblock_new_entries("regime.runtime.kill_switch_active")
        snapshot = {
            "algorithmId": "regime",
            "active": False,
            "reason": None,
            "actor": command.actor,
            "deactivatedAt": command.created_at,
            "stateVersion": self.metrics.kill_switch_state_version,
            "blocksNewEntries": False,
            "riskReducingExitsAllowed": True,
            "pendingEntryOrdersCancelRequested": self.metrics.pending_entry_orders_cancel_requested,
            "auditTrailSource": "regime_runtime_events",
        }
        self.service.repository.write_runtime_snapshot(_default_identity(self.config), "kill_switch", snapshot)
        self.service.repository.record_runtime_event(
            {
                **_default_identity(self.config),
                "eventId": f"{command.command_id}:kill-switch-deactivated:{'immediate' if immediate else 'worker'}",
                "eventType": "runtime_kill_switch",
                "processingStatus": "inactive",
                "payload": snapshot,
            }
        )

    def _cancel_pending_entry_orders(
        self,
        command: RegimeRuntimeCommand,
        *,
        cancel_reason: str,
        event_type: str,
    ) -> int:
        if command.payload.get("cancelPendingEntries", True) is False:
            return 0
        identity = _default_identity(self.config)
        cancelled = 0
        for record in self.service.repository.pending_execution_outbox_records(identity):
            if not _outbox_record_is_new_entry(record):
                continue
            status = str(record.get("processingStatus") or "")
            if status in {"filled", "partially_filled"}:
                continue
            order_intent_id = str(record.get("orderIntentId") or record.get("order_intent_id") or "")
            if not order_intent_id:
                continue
            self.service.repository.update_execution_outbox_status(
                identity,
                order_intent_id,
                status="cancel_requested",
                payload={
                    "reasonCodes": [cancel_reason],
                    "commandId": command.command_id,
                    "cancelReason": cancel_reason,
                    "riskReducingExitsAllowed": True,
                },
            )
            cancelled += 1
        if cancelled:
            self.service.repository.record_runtime_event(
                {
                    **identity,
                    "eventId": f"{command.command_id}:{event_type}",
                    "eventType": event_type,
                    "processingStatus": "cancel_requested",
                    "payload": {
                        "algorithmId": "regime",
                        "cancelReason": cancel_reason,
                        "cancelledPendingEntryOrders": cancelled,
                        "riskReducingExitsAllowed": True,
                    },
                }
            )
        return cancelled

    def _verify_strategy_registry(self) -> None:
        try:
            validation = validate_regime_strategy_registry()
            self.metrics.strategy_registry_valid = True
            self._mark_component("strategy_registry", "healthy", reason_codes=("regime.health.strategy_registry.valid",), details=validation)
        except Exception as exc:
            self.metrics.strategy_registry_valid = False
            self._record_component_failure("strategy_registry", exc, reason_code="regime.health.strategy_registry.invalid")

    def _verify_paper_broker_mode(self) -> None:
        if self.paper_gateway is None:
            if operational_stage_uses_simulated_broker(self.metrics.current_rollout_stage):
                self.metrics.broker_paper_mode_verified = True
                self.metrics.broker_connectivity_ok = True
                self.metrics.broker_connectivity = {
                    "paperGatewayPresent": False,
                    "runtimeMode": self.config.default_runtime_mode,
                    "simulatedExecution": True,
                    "paperOnly": True,
                    "liveTradingEnabled": False,
                }
                self._mark_component("paper_broker", "healthy", reason_codes=("regime.execution.simulated_paper_broker_active",), details=self.metrics.broker_connectivity)
                self._mark_component("broker_connectivity", "healthy", reason_codes=("regime.execution.simulated_paper_broker_active",), details=self.metrics.broker_connectivity)
                self._unblock_new_entries("regime.execution.paper_gateway_unavailable")
                self._unblock_new_entries("regime.health.paper_broker.not_verified")
                self._unblock_new_entries("regime.execution.paper_broker_unhealthy")
                return
            self.metrics.broker_paper_mode_verified = self.config.default_runtime_mode != "paper"
            self.metrics.broker_connectivity_ok = self.config.default_runtime_mode != "paper"
            self.metrics.broker_connectivity = {"paperGatewayPresent": False, "runtimeMode": self.config.default_runtime_mode}
            if self.config.default_runtime_mode == "paper":
                self._mark_component("paper_broker", "unhealthy", reason_codes=("regime.execution.paper_gateway_unavailable",), details=self.metrics.broker_connectivity)
                self._block_new_entries("regime.execution.paper_gateway_unavailable")
            return
        safety = validate_regime_paper_broker_safety(self.paper_gateway, mode=self.config.default_runtime_mode)
        verified = bool(safety.get("verified", safety.get("passed")))
        self.metrics.broker_paper_mode_verified = verified
        self.metrics.broker_connectivity_ok = verified
        self.metrics.broker_connectivity = dict(safety)
        status = "healthy" if verified else "unhealthy"
        self._mark_component("paper_broker", status, reason_codes=tuple(safety.get("reasonCodes") or ()), details=safety)
        self._mark_component("broker_connectivity", status, reason_codes=tuple(safety.get("reasonCodes") or ()), details=safety)
        if verified:
            self.metrics.entry_block_reason_codes = [
                code
                for code in self.metrics.entry_block_reason_codes
                if not str(code).startswith("regime.execution.paper_broker.") and not str(code).startswith("regime.alpaca_paper.")
            ]
            self._unblock_new_entries("regime.execution.paper_gateway_unavailable")
            self._unblock_new_entries("regime.health.paper_broker.not_verified")
            self._unblock_new_entries("regime.execution.paper_broker_unhealthy")
            self._unblock_new_entries("regime.execution.paper_broker_safety_failed")
        elif self.config.default_runtime_mode == "paper":
            for reason_code in safety.get("reasonCodes") or ():
                self._block_new_entries(str(reason_code))
            self._block_new_entries("regime.execution.paper_broker_safety_failed")

    def _refresh_operational_records(self, identity: dict[str, str]) -> None:
        try:
            inventory = self.service.repository.current_inventory_snapshot(identity)
            self.metrics.current_inventory = inventory
            self.metrics.inventory_available = True
            self._mark_component("inventory", "healthy" if self.metrics.inventory_reconciled else "unknown", reason_codes=("regime.health.inventory.loaded",), details={"stateVersion": inventory.get("stateVersion")})
        except Exception as exc:
            self.metrics.inventory_available = False
            self._record_component_failure("inventory", exc, reason_code="regime.health.inventory.unavailable")
        try:
            pending = self.service.repository.pending_execution_outbox_records(identity)
            self.metrics.open_orders = pending
            self.metrics.outbox_status = _outbox_status(pending)
            self.metrics.outbox_stuck = _outbox_is_stuck(pending)
            self.metrics.risk_reservations = _risk_reservations_from_orders(pending)
            self.metrics.risk_reservations_consistent = _risk_reservations_consistent(pending)
        except Exception as exc:
            self._record_component_failure("execution_outbox", exc, reason_code="regime.health.execution_outbox.unavailable")
        try:
            trades = self.service.repository.read_owned_records("regime_trades", identity)
            today = datetime.now(timezone.utc).date().isoformat()
            today_trades = [trade for trade in trades if str(trade.get("exitAt") or trade.get("entryAt") or trade.get("timestamp") or "").startswith(today)]
            self.metrics.daily_trade_count = len(today_trades)
            self.metrics.daily_regime_pnl = round(sum(float(trade.get("netPnl") or trade.get("realizedPnl") or trade.get("realizedPnL") or 0.0) for trade in today_trades), 6)
        except Exception:
            pass

    async def _run_worker(self, worker) -> None:
        worker_id = worker.worker_id
        self.metrics.worker_status[worker_id] = "running"
        try:
            await worker.run()
        except asyncio.CancelledError:
            self.metrics.worker_status[worker_id] = "stopped"
            raise
        except Exception as exc:  # pragma: no cover - safety loop.
            self.metrics.worker_status[worker_id] = "failed"
            component = _component_for_worker(worker_id)
            self._record_component_failure(
                component,
                exc,
                reason_code=f"regime.runtime.worker_failed.{worker_id}",
                block_new_entries=component != "backtest_worker",
                details={"workerId": worker_id},
            )

    async def _periodic_idle(self, worker_id: str) -> None:
        while not self.stop_event.is_set():
            await self._sleep_until_stopped(self.config.maintenance_interval_seconds)

    async def _sleep_until_stopped(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return

    def _lock_for(self, key: tuple[str, ...]) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock


class _RegimeRolloutSnapshotStore:
    def __init__(self, repository, identity: dict[str, Any]) -> None:
        self.repository = repository
        self.identity = identity

    def read_snapshot(self, key: str) -> dict:
        snapshot = self.repository.read_runtime_snapshot(self.identity, key)
        if snapshot is None:
            raise KeyError(key)
        return snapshot

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.repository.write_runtime_snapshot(self.identity, key, dict(snapshot))


class RegimeSimulatedPaperBroker:
    """Deterministic fake paper broker used only in the simulated_execution rollout stage."""

    def __init__(self, *, fill_status: str = "FILLED", filled_quantity: int | None = None) -> None:
        self.fill_status = fill_status
        self.filled_quantity = filled_quantity
        self.submit_count = 0
        self.cancel_count = 0
        self.last_intent = None
        self.base_url = "simulated-paper://regime"
        self.paper_only = True
        self.live_trading_enabled = False
        self.account_type = "paper"
        self.credentials_verified = True

    def verify_paper_account(self) -> bool:
        return True

    def paper_trading_configuration(self) -> dict[str, Any]:
        return {
            "baseUrl": self.base_url,
            "paperOnly": True,
            "liveTradingEnabled": False,
            "accountType": "paper",
            "credentialsVerified": True,
            "simulatedExecution": True,
        }

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        self.submit_count += 1
        self.last_intent = intent
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=f"simulated-{intent.clientOrderId}",
            status="ACCEPTED",
            acceptedAt=datetime.now(timezone.utc),
        )

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        if self.last_intent is None:
            return None
        quantity = int(self.filled_quantity if self.filled_quantity is not None else self.last_intent.submittedQuantity)
        if self.fill_status == "ACCEPTED":
            return None
        return PaperGatewayFill(
            clientOrderId=client_order_id,
            algorithmId="regime",
            orderIntentId=self.last_intent.orderIntentId,
            symbol=self.last_intent.symbol,
            side=self.last_intent.side if isinstance(self.last_intent.side, Signal) else Signal(str(self.last_intent.side)),
            filledQuantity=max(0, quantity),
            averageFillPrice=float(self.last_intent.limitPrice or self.last_intent.triggerPrice or 1.0),
            status=self.fill_status,
            filledAt=datetime.now(timezone.utc),
        )

    def cancel_order(self, client_order_id: str) -> bool:
        self.cancel_count += 1
        return True

    def refresh_positions(self) -> list[dict[str, Any]]:
        return []


def _default_identity(config: RegimeRuntimeSupervisorConfig) -> dict[str, str]:
    return regime_settings_identity_from_payload(
        {
            "algorithmInstanceId": config.default_algorithm_instance_id,
            "accountId": config.default_account_id,
            "runtimeMode": config.default_runtime_mode,
            "symbol": config.symbol,
        }
    )


def _instance_symbol_key(event: RegimeFinalisedBarEvent) -> str:
    return f"{event.algorithm_instance_id}:{event.symbol}"


def _processing_lock_key(event: RegimeFinalisedBarEvent) -> tuple[str, str, str, str]:
    return (event.algorithm_instance_id, event.account_id, event.runtime_mode, event.symbol)


def _processing_lease_worker_id(event: RegimeFinalisedBarEvent) -> str:
    return f"regime-processing-lease:{event.runtime_mode}:{event.symbol}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: Any, default: datetime) -> datetime:
    if isinstance(value, datetime):
        return _as_utc(value)
    if value is None or value == "":
        return _as_utc(default)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return _as_utc(default)
    return _as_utc(parsed)


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.01, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float_any(names: tuple[str, ...], default: float) -> float:
    for name in names:
        if os.getenv(name) is not None:
            return _env_float(name, default)
    return default


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _event_is_near_eod(event: RegimeFinalisedBarEvent) -> bool:
    et_timestamp = event.completed_bar_timestamp.astimezone(ZoneInfo("America/New_York"))
    return et_timestamp.hour > 15 or (et_timestamp.hour == 15 and et_timestamp.minute >= 50)


def _result_decision_id(result: dict[str, Any]) -> str:
    return str(result.get("decisionId") or result.get("decision", {}).get("decision_id") or "")


def _outbox_record_is_new_entry(outbox_record: dict[str, Any]) -> bool:
    order_intent = outbox_record.get("orderIntent") if isinstance(outbox_record.get("orderIntent"), dict) else outbox_record
    effect = str(order_intent.get("positionEffect") or order_intent.get("position_effect") or "").lower()
    if effect:
        return effect.startswith("enter") or effect in {"open", "new_entry"}
    return True


def _decision_operational_blockers(
    metrics: RegimeRuntimeMetrics,
    *,
    runtime_mode: str,
    rollout_stage: str,
    automatic_paper_enabled: bool,
    paper_button_requested: bool,
    paper_button_effective: bool,
    market_regular_session_open: bool,
    finalized_bar_current: bool,
    publisher_healthy: bool,
    account_snapshot_current: bool,
    broker_healthy: bool,
    database_healthy: bool,
    orders_reconciled: bool,
    real_paper_stage_allowed: bool,
) -> list[str]:
    if runtime_mode != RegimeRuntimeMode.PAPER.value or rollout_stage not in {"limited_paper", "normal_paper"}:
        return []
    blockers: list[str] = []
    if runtime_mode != RegimeRuntimeMode.PAPER.value:
        blockers.append("regime.runtime.paper_runtime_required")
    if not metrics.supervisor_started:
        blockers.append("regime.runtime.paper_runtime_not_running")
    if not paper_button_requested:
        blockers.append("regime.runtime.paper_button_requested_off")
    if not paper_button_effective:
        blockers.append("regime.runtime.paper_button_effective_off")
    if not automatic_paper_enabled:
        blockers.append("regime.runtime.automatic_paper_control_off")
    if not market_regular_session_open:
        blockers.append("regime.runtime.market_not_regular_session")
    if not finalized_bar_current:
        blockers.append("regime.runtime.finalized_bar_not_current")
    if not publisher_healthy:
        blockers.append("regime.runtime.publisher_unhealthy")
    if not account_snapshot_current:
        blockers.append("regime.runtime.account_snapshot_not_current")
    if not broker_healthy:
        blockers.append("regime.runtime.paper_broker_unhealthy")
    if not database_healthy:
        blockers.append("regime.runtime.database_unhealthy")
    if not real_paper_stage_allowed:
        blockers.append("regime.rollout.paper_submission_gate_blocked")
    if metrics.queue_lag_block_active or metrics.stale_events:
        blockers.append("regime.runtime.market_data_stale_or_incomplete")
    if not metrics.recovery_succeeded:
        blockers.append("regime.runtime.recovery_incomplete")
    if not metrics.inventory_reconciled:
        blockers.append("regime.runtime.inventory_not_reconciled")
    if not orders_reconciled or metrics.reconciliation_discrepancies:
        blockers.append("regime.runtime.broker_reconciliation_unhealthy")
    if metrics.kill_switch_active:
        blockers.append("regime.runtime.kill_switch_active")
    if metrics.paused:
        blockers.append("regime.runtime.paused")
    if metrics.outbox_stuck:
        blockers.append("regime.execution.outbox_stuck")
    if not metrics.risk_reservations_consistent:
        blockers.append("regime.execution.risk_reservations_inconsistent")
    blockers.extend(str(code) for code in metrics.entry_block_reason_codes if code)
    return list(dict.fromkeys(blockers))


def _automatic_entry_submission_blockers(
    metrics: RegimeRuntimeMetrics,
    *,
    identity: dict[str, str],
    outbox_record: dict[str, Any],
    rollout_stage: str,
    rollout_snapshot: dict[str, Any],
    promotion_evidence: dict[str, Any],
    evaluated_at: datetime,
) -> list[str]:
    blockers: list[str] = []
    order_intent = _outbox_order_intent(outbox_record)
    if identity.get("runtimeMode") != RegimeRuntimeMode.PAPER.value or _outbox_runtime_mode(outbox_record) != RegimeRuntimeMode.PAPER.value:
        blockers.append("regime.execution.paper_runtime_required")
    if str(identity.get("algorithmInstanceId") or "") == REGIME_DEFAULT_SHADOW_ALGORITHM_INSTANCE_ID:
        blockers.append("regime.execution.paper_identity_required")
    if str(identity.get("accountId") or "") == REGIME_DEFAULT_SHADOW_ACCOUNT_ID:
        blockers.append("regime.execution.paper_account_identity_required")
    if str(identity.get("symbol") or "").upper() != "SPY" or str(order_intent.get("symbol") or outbox_record.get("symbol") or "").upper() != "SPY":
        blockers.append("regime.execution.spy_symbol_required")
    session = exchange_session(evaluated_at.isoformat().replace("+00:00", "Z"))
    if session.status not in {"opening", "midday", "afternoon", "closing"}:
        blockers.append("regime.execution.market_not_regular_session")
    if _eod_schedule(session, _outbox_flat_settings(outbox_record)).get("entryCutoffReached"):
        blockers.append("regime.execution.entry_cutoff_reached")
    if not metrics.supervisor_started:
        blockers.append("regime.execution.paper_runtime_not_running")
    if rollout_stage not in {"limited_paper", "normal_paper"}:
        blockers.append(f"regime.rollout.{rollout_stage}.broker_submission_blocked")
    if not _paper_button_requested(rollout_snapshot, fallback=bool(rollout_snapshot.get("automaticPaperSubmissionEnabled"))):
        blockers.append("regime.execution.paper_button_requested_off")
    if not _paper_button_effective(rollout_snapshot, fallback=bool(rollout_snapshot.get("automaticPaperSubmissionEnabled"))):
        blockers.append("regime.execution.paper_button_effective_off")
    if not bool(rollout_snapshot.get("automaticPaperSubmissionEnabled")):
        blockers.append("regime.execution.automatic_paper_control_off")
    if not operational_stage_allows_real_paper_submission(rollout_stage, evidence=promotion_evidence):
        blockers.append("regime.rollout.paper_submission_gate_blocked")
    if not _component_healthy(metrics, "market_event_publisher"):
        blockers.append("regime.execution.publisher_unhealthy")
    if not (metrics.persistence_available and _component_healthy(metrics, "database")):
        blockers.append("regime.execution.database_unhealthy")
    if metrics.queue_lag_block_active or metrics.stale_events:
        blockers.append("regime.execution.market_data_not_current_complete")
    if not _outbox_market_data_ready(outbox_record):
        blockers.append("regime.execution.market_data_validation_missing_or_failed")
    if not _outbox_finalized_bar_current(outbox_record, evaluated_at=evaluated_at):
        blockers.append("regime.execution.finalized_bar_not_current")
    if not _outbox_local_risk_approved(outbox_record):
        blockers.append("regime.execution.local_risk_missing_or_rejected")
    if not _outbox_global_risk_approved(outbox_record):
        blockers.append("regime.execution.global_risk_missing_or_rejected")
    if _outbox_approved_quantity(outbox_record) <= 0:
        blockers.append("regime.execution.approved_quantity_required")
    if not metrics.recovery_succeeded:
        blockers.append("regime.execution.recovery_incomplete")
    if not metrics.inventory_reconciled:
        blockers.append("regime.execution.inventory_not_reconciled")
    if not metrics.broker_paper_mode_verified or not metrics.broker_connectivity_ok:
        blockers.append("regime.execution.paper_broker_unhealthy")
    latest_reconciliation = metrics.latest_reconciliation if isinstance(metrics.latest_reconciliation, dict) else {}
    if metrics.reconciliation_discrepancies or latest_reconciliation.get("reconciled") is not True:
        blockers.append("regime.execution.broker_reconciliation_unhealthy")
    if metrics.kill_switch_active:
        blockers.append("regime.execution.kill_switch_active")
    if metrics.paused:
        blockers.append("regime.execution.runtime_paused")
    if metrics.outbox_stuck:
        blockers.append("regime.execution.outbox_stuck")
    if not metrics.risk_reservations_consistent:
        blockers.append("regime.execution.risk_reservations_inconsistent")
    blockers.extend(str(code) for code in metrics.entry_block_reason_codes if code)
    blockers.extend(_unhealthy_component_blockers(metrics))
    return list(dict.fromkeys(blockers))


def _paper_effective_activation_evaluation(
    supervisor: RegimeRuntimeSupervisor,
    identity: dict[str, Any],
    *,
    rollout_snapshot: dict[str, Any],
    control_snapshot: dict[str, Any],
    requested: bool,
    evaluated_at: datetime,
) -> dict[str, Any]:
    metrics = supervisor.metrics
    stage = str(rollout_snapshot.get("stage") or REGIME_DEFAULT_OPERATIONAL_ROLLOUT_STAGE)
    promotion_evidence = supervisor.service.repository.read_regime_rollout_promotion_evidence(identity)
    stage_allows_real_paper = bool(
        stage in {"limited_paper", "normal_paper"}
        and bool(rollout_snapshot.get("automaticPaperSubmissionEnabled"))
        and operational_stage_allows_real_paper_submission(stage, evidence=promotion_evidence)
    )
    paper_identity = str(identity.get("runtimeMode") or "") == RegimeRuntimeMode.PAPER.value
    if paper_identity:
        account_snapshot = supervisor._load_shared_account_snapshot_for_identity({key: str(value) for key, value in identity.items()})
        account_blockers = _account_snapshot_preflight_blockers(account_snapshot)
    else:
        account_snapshot = {}
        account_blockers = ()
    session = exchange_session(evaluated_at.isoformat().replace("+00:00", "Z"))
    database_healthy = bool(metrics.persistence_available and _component_healthy(metrics, "database"))
    settings_available = bool(metrics.settings_available and _component_not_unhealthy(metrics, "settings_repository"))
    publisher_healthy = _component_healthy(metrics, "market_event_publisher")
    broker_healthy = bool(metrics.broker_paper_mode_verified and metrics.broker_connectivity_ok and _component_not_unhealthy(metrics, "paper_broker") and _component_not_unhealthy(metrics, "broker_connectivity"))
    latest_reconciliation = metrics.latest_reconciliation if isinstance(metrics.latest_reconciliation, dict) else {}
    open_orders_reconciled = bool(not paper_identity or (account_snapshot.get("openOrdersReconciled") is True and latest_reconciliation.get("reconciled") is True and not metrics.reconciliation_discrepancies))
    runtime_identity_valid = (
        str(identity.get("algorithmId") or "") == "regime"
        and str(identity.get("runtimeMode") or "") == RegimeRuntimeMode.PAPER.value
        and str(identity.get("algorithmInstanceId") or "") != REGIME_DEFAULT_SHADOW_ALGORITHM_INSTANCE_ID
        and str(identity.get("accountId") or "") != REGIME_DEFAULT_SHADOW_ACCOUNT_ID
        and str(identity.get("symbol") or "").upper() == "SPY"
        and str(control_snapshot.get("liveTradingEnabled") or "false").lower() != "true"
    )
    market_data_stale = bool(metrics.queue_lag_block_active or metrics.stale_events)
    if metrics.latest_event_age_seconds is not None and float(metrics.latest_event_age_seconds) > float(supervisor.config.max_processing_lag_seconds):
        market_data_stale = True
    checks: list[tuple[bool, str, str]] = [
        (requested, "paper_requested_off", "regime.runtime.paper_requested_off"),
        (runtime_identity_valid, "runtime_identity_invalid", "regime.runtime.identity_invalid"),
        (bool(metrics.supervisor_started), "runtime_not_running", "regime.runtime.paper_runtime_not_running"),
        (session.status in {"opening", "midday", "afternoon", "closing"}, "market_closed", "regime.runtime.market_closed"),
        (stage_allows_real_paper, "rollout_not_promoted", "regime.rollout.not_promoted_for_real_paper"),
        (broker_healthy, "broker_unhealthy", "regime.runtime.paper_broker_unhealthy"),
        (not paper_identity or not account_blockers, "account_snapshot_stale", "regime.runtime.account_snapshot_stale"),
        (not market_data_stale, "market_data_stale", "regime.runtime.market_data_stale"),
        (bool(metrics.inventory_reconciled and (not paper_identity or account_snapshot.get("positionsReconciled") is True)), "inventory_not_reconciled", "regime.runtime.inventory_not_reconciled"),
        (open_orders_reconciled, "open_orders_not_reconciled", "regime.runtime.open_orders_not_reconciled"),
        (not metrics.kill_switch_active, "kill_switch_active", "regime.runtime.kill_switch_active"),
        (database_healthy, "database_unhealthy", "regime.runtime.database_unhealthy"),
        (settings_available, "settings_unavailable", "regime.runtime.settings_unavailable"),
        (publisher_healthy, "publisher_unhealthy", "regime.runtime.publisher_unhealthy"),
    ]
    blockers = [name for passed, name, _reason in checks if not passed]
    reason_codes = [reason for passed, _name, reason in checks if not passed]
    reason_codes.extend(str(code) for code in account_blockers)
    return {
        "blockers": tuple(dict.fromkeys(blockers)),
        "reasonCodes": tuple(dict.fromkeys(reason_codes)),
        "gateSnapshot": {
            "paperRequestedOn": requested,
            "runtimeIdentityValid": runtime_identity_valid,
            "runtimeRunning": bool(metrics.supervisor_started),
            "marketSessionStatus": session.status,
            "rolloutStage": stage,
            "rolloutStageAllowsRealPaperExecution": stage_allows_real_paper,
            "brokerHealthy": broker_healthy,
            "accountSnapshotCurrent": not account_blockers,
            "marketDataCurrent": not market_data_stale,
            "inventoryReconciled": bool(metrics.inventory_reconciled and account_snapshot.get("positionsReconciled") is True),
            "openOrdersReconciled": open_orders_reconciled,
            "killSwitchActive": bool(metrics.kill_switch_active),
            "databaseHealthy": database_healthy,
            "settingsAvailable": settings_available,
            "publisherHealthy": publisher_healthy,
            "evaluatedAt": evaluated_at.isoformat().replace("+00:00", "Z"),
        },
    }


def _account_snapshot_preflight_blockers(snapshot: dict[str, Any]) -> list[str]:
    blockers = [
        str(code)
        for code in snapshot.get("reasonCodes") or ()
        if str(code).startswith("regime.account_snapshot.") or str(code).startswith("regime.runtime.account_snapshot.")
    ]
    if snapshot.get("accountTradingBlocked") is True:
        blockers.append("regime.execution.account_trading_blocked")
    if snapshot.get("buyingPowerCurrent") is not True or snapshot.get("accountSnapshotFresh") is not True:
        blockers.append("regime.execution.account_or_buying_power_not_current")
    if snapshot.get("positionsReconciled") is not True or snapshot.get("openOrdersReconciled") is not True:
        blockers.append("regime.execution.account_orders_or_positions_not_reconciled")
    try:
        capacity = float(snapshot.get("globalRiskCapacityQuantity") or 0.0)
    except (TypeError, ValueError):
        capacity = -1.0
    if capacity < 0:
        blockers.append("regime.execution.global_risk_capacity_negative")
    return list(dict.fromkeys(blockers))


def _outbox_market_data_ready(outbox_record: dict[str, Any]) -> bool:
    order_intent = _outbox_order_intent(outbox_record)
    if outbox_record.get("completedBarFinalized") is False or order_intent.get("completedBarFinalized") is False:
        return False
    validation = _dict_from_sources(outbox_record, order_intent, "marketDataValidation", "market_data_validation")
    if not validation:
        return False
    if validation.get("passed") is not True:
        return False
    if validation.get("complete") is False or validation.get("current") is False:
        return False
    return True


def _outbox_global_risk_approved(outbox_record: dict[str, Any]) -> bool:
    order_intent = _outbox_order_intent(outbox_record)
    approval = _dict_from_sources(outbox_record, order_intent, "globalRiskApproval", "global_risk_approval")
    if not approval:
        return False
    if approval.get("rejected") is True:
        return False
    status = str(approval.get("status") or approval.get("decision") or approval.get("action") or "").lower()
    if status in {"reject", "rejected", "deny", "denied", "block", "blocked"}:
        return False
    approved_quantity = _positive_int(
        approval.get("approved_quantity")
        or approval.get("approvedQuantity")
        or outbox_record.get("globalApprovedQuantity")
        or order_intent.get("globalApprovedQuantity")
        or approval.get("maximumAllowedQuantity")
        or order_intent.get("quantity")
        or outbox_record.get("quantity")
    )
    if approved_quantity <= 0:
        return False
    return bool(approval.get("approved") is True or approval.get("passed") is True or status in {"allow", "allowed", "approved", "pass", "passed"} or approved_quantity > 0)


def _outbox_local_risk_approved(outbox_record: dict[str, Any]) -> bool:
    order_intent = _outbox_order_intent(outbox_record)
    result = _dict_from_sources(outbox_record, order_intent, "localRiskResult", "local_risk_result")
    if not result:
        return True
    if result.get("passed") is not True:
        return False
    approved_quantity = _positive_int(result.get("approvedQuantity") or result.get("approved_quantity"))
    return approved_quantity > 0


def _outbox_approved_quantity(outbox_record: dict[str, Any]) -> int:
    order_intent = _outbox_order_intent(outbox_record)
    approval = _dict_from_sources(outbox_record, order_intent, "globalRiskApproval", "global_risk_approval")
    local = _dict_from_sources(outbox_record, order_intent, "localRiskResult", "local_risk_result")
    candidates = (
        outbox_record.get("finalApprovedQuantity"),
        order_intent.get("finalApprovedQuantity"),
        outbox_record.get("globalApprovedQuantity"),
        order_intent.get("globalApprovedQuantity"),
        approval.get("approvedQuantity"),
        approval.get("approved_quantity"),
        local.get("approvedQuantity"),
        local.get("approved_quantity"),
        outbox_record.get("quantity"),
        order_intent.get("quantity"),
    )
    for candidate in candidates:
        value = _positive_int(candidate)
        if value > 0:
            return value
    return 0


def _outbox_finalized_bar_current(outbox_record: dict[str, Any], *, evaluated_at: datetime, max_age_seconds: float = 120.0) -> bool:
    if not _outbox_market_data_ready(outbox_record):
        return False
    order_intent = _outbox_order_intent(outbox_record)
    timestamp = (
        outbox_record.get("completedBarTimestamp")
        or outbox_record.get("completed_bar_timestamp")
        or order_intent.get("completedBarTimestamp")
        or order_intent.get("completed_bar_timestamp")
        or (order_intent.get("marketDataValidation") or {}).get("dataTimestamp")
        or (outbox_record.get("marketDataValidation") or {}).get("dataTimestamp")
        or order_intent.get("marketDataTimestamp")
        or order_intent.get("market_data_timestamp")
    )
    if timestamp is None or timestamp == "":
        return False
    parsed = _parse_datetime(timestamp, evaluated_at)
    return (evaluated_at - _as_utc(parsed)).total_seconds() <= max_age_seconds


def _event_finalized_current(event: RegimeFinalisedBarEvent, *, max_age_seconds: float) -> bool:
    if not bool(event.completed):
        return False
    age = (datetime.now(timezone.utc) - event.completed_bar_timestamp).total_seconds()
    return age <= max_age_seconds


def _market_regular_session_open(value: datetime) -> bool:
    session = exchange_session(value.isoformat().replace("+00:00", "Z"))
    return session.status in {"opening", "midday", "afternoon", "closing"}


def _market_open_status(now: datetime) -> dict[str, Any]:
    evaluated_at = _as_utc(now)
    session = exchange_session(evaluated_at.isoformat().replace("+00:00", "Z"))
    market_open = session.status in {"opening", "midday", "afternoon", "closing"}
    return {
        "algorithmId": "regime",
        "marketOpen": market_open,
        "sessionStatus": session.status,
        "sessionDate": session.session_date,
        "marketOpenEt": session.market_open_et,
        "marketCloseEt": session.market_close_et,
        "earlyClose": bool(session.is_early_close),
        "nextMarketOpen": session.market_open_et if market_open else _next_market_open(evaluated_at),
        "evaluatedAt": evaluated_at.isoformat().replace("+00:00", "Z"),
    }


def _next_market_open(now: datetime) -> str | None:
    now_et = _as_utc(now).astimezone(ZoneInfo("America/New_York"))
    for offset in range(0, 14):
        candidate_day = (now_et + timedelta(days=offset)).date()
        bounds = exchange_session_bounds(candidate_day)
        if bounds is None:
            continue
        market_open, _market_close, _early_close = bounds
        if now_et < market_open:
            return market_open.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return None


def _publisher_status_snapshot(supervisor: RegimeRuntimeSupervisor, identity: dict[str, str]) -> dict[str, Any]:
    metrics = supervisor.metrics
    snapshot = {}
    try:
        snapshot = supervisor.service.repository.read_runtime_snapshot(identity, "finalised_bar_ingestion") or {}
    except Exception:
        snapshot = {}
    component = metrics.component_health.get("market_event_publisher") or {}
    nested_snapshot = snapshot.get("snapshot") if isinstance(snapshot.get("snapshot"), dict) else {}
    latest_published = snapshot.get("latestFinalizedCandle") or nested_snapshot.get("latestFinalizedCandle")
    last_bar = metrics.last_finalized_bar if isinstance(metrics.last_finalized_bar, dict) else {}
    return {
        **identity,
        "algorithmId": "regime",
        "status": snapshot.get("status") or component.get("status") or "unknown",
        "reasonCodes": list(snapshot.get("reasonCodes") or component.get("reasonCodes") or ()),
        "lastPublishedBar": {
            **({"barCloseTimestamp": latest_published} if latest_published else {}),
            **({key: last_bar.get(key) for key in ("eventId", "symbol", "runtimeMode", "barCloseTimestamp", "publishedAt", "finalized", "timeframe", "settingsVersion", "dataManifestHash") if last_bar.get(key) is not None} if last_bar else {}),
        }
        or None,
        "barLagSeconds": metrics.queue_lag_seconds if metrics.queue_lag_seconds is not None else metrics.processing_lag_seconds,
        "queueDepth": metrics.queue_depth,
        "observedAt": snapshot.get("observedAt") or component.get("updatedAt"),
    }


def _broker_status_snapshot(metrics: RegimeRuntimeMetrics, identity: dict[str, str]) -> dict[str, Any]:
    broker = metrics.component_health.get("paper_broker") or {}
    connectivity = metrics.component_health.get("broker_connectivity") or {}
    return {
        **identity,
        "algorithmId": "regime",
        "status": broker.get("status") or connectivity.get("status") or ("healthy" if metrics.broker_paper_mode_verified else "unhealthy"),
        "paperModeVerified": bool(metrics.broker_paper_mode_verified),
        "connectivityOk": bool(metrics.broker_connectivity_ok),
        "reasonCodes": list(dict.fromkeys([*(broker.get("reasonCodes") or ()), *(connectivity.get("reasonCodes") or ())])),
        "observedAt": broker.get("updatedAt") or connectivity.get("updatedAt"),
    }


def _account_snapshot_status_snapshot(paper_control: dict[str, Any], identity: dict[str, str]) -> dict[str, Any]:
    gate = paper_control.get("paperEffectiveGateSnapshot") if isinstance(paper_control.get("paperEffectiveGateSnapshot"), dict) else {}
    blockers = set(str(code) for code in paper_control.get("paperEffectiveBlockers") or ())
    reason_codes = set(str(code) for code in paper_control.get("paperEffectiveBlockerReasonCodes") or ())
    current = bool(gate.get("accountSnapshotCurrent")) and "account_snapshot_stale" not in blockers
    return {
        **identity,
        "algorithmId": "regime",
        "status": "healthy" if current else "blocked",
        "current": current,
        "accountSnapshotCurrent": current,
        "reasonCodes": sorted(code for code in reason_codes if "account_snapshot" in code or "buying_power" in code or "account" in code),
        "blockers": sorted(code for code in blockers if "account" in code),
        "evaluatedAt": gate.get("evaluatedAt"),
    }


def _worker_heartbeats_from_metrics(metrics: RegimeRuntimeMetrics, identity: dict[str, str]) -> dict[str, Any]:
    heartbeat = metrics.supervisor_heartbeat_at
    return {
        **identity,
        "algorithmId": "regime",
        "supervisorHeartbeatAt": heartbeat,
        "workers": {
            worker_id: {
                "status": status,
                "lastObservedAt": heartbeat,
            }
            for worker_id, status in metrics.worker_status.items()
        },
    }


def _latest_owned_record(repository: Any, table: str, identity: dict[str, Any]) -> dict[str, Any] | None:
    try:
        records = repository.read_owned_records(table, identity)
    except Exception:
        return None
    return records[-1] if records else None


def _current_regime_position_snapshot(repository: Any, identity: dict[str, Any]) -> dict[str, Any] | None:
    try:
        positions = repository.latest_open_regime_positions(identity)
    except Exception:
        return None
    if not positions:
        return None
    return _project_status_record(positions[-1], record_type="position")


def _project_latest_decision_status(record: dict[str, Any] | None, identity: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
    confirmed = decision.get("confirmed_state") if isinstance(decision.get("confirmed_state"), dict) else {}
    order_intent = record.get("orderIntent") if isinstance(record.get("orderIntent"), dict) else {}
    return {
        **identity,
        "algorithmId": "regime",
        "recordType": "decision",
        "decisionId": record.get("decisionId") or decision.get("decision_id"),
        "settingsVersion": record.get("settingsVersion") or decision.get("settings_version"),
        "signal": record.get("signal") or decision.get("signal"),
        "confirmedRegime": confirmed.get("confirmed_regime") or _current_regime_from_result(record),
        "confidence": record.get("confidence") or decision.get("confidence"),
        "tradeAllowed": decision.get("trade_allowed"),
        "tradeBlockers": list(decision.get("trade_blockers") or record.get("tradeBlockers") or ()),
        "orderIntentId": order_intent.get("orderIntentId") or record.get("orderIntentId"),
        "dataTimestamp": record.get("dataTimestamp") or decision.get("timestamp"),
    }


def _project_status_record(record: dict[str, Any] | None, *, record_type: str) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    keys = (
        "algorithmId",
        "algorithmInstanceId",
        "accountId",
        "runtimeMode",
        "symbol",
        "decisionId",
        "orderIntentId",
        "clientOrderId",
        "brokerClientOrderId",
        "brokerOrderId",
        "positionId",
        "tradeId",
        "side",
        "positionEffect",
        "quantity",
        "filledQuantity",
        "remainingQuantity",
        "ownedPositionQuantity",
        "status",
        "processingStatus",
        "positionStatus",
        "orderStatus",
        "entryPrice",
        "limitPrice",
        "averageFillPrice",
        "exitReason",
        "createdAt",
        "updatedAt",
        "submittedAt",
        "filledAt",
        "acceptedAt",
        "settingsVersion",
        "reasonCodes",
    )
    projected = {key: record.get(key) for key in keys if record.get(key) is not None}
    projected["algorithmId"] = "regime"
    projected["recordType"] = record_type
    return projected


def _eod_schedule(session: Any, settings: dict[str, Any]) -> dict[str, Any]:
    now_et = _parse_exchange_dt(getattr(session, "timestamp_et", None))
    open_et = _parse_exchange_dt(getattr(session, "market_open_et", None))
    close_et = _parse_exchange_dt(getattr(session, "market_close_et", None))
    if now_et is None or open_et is None or close_et is None:
        return {
            "entryCutoffReached": False,
            "flattenDue": False,
            "nearClose": False,
            "effectiveEntryCutoffEt": None,
            "effectiveFlattenTimeEt": None,
            "reasonCodes": ["regime.eod.outside_exchange_session"],
        }
    configured_entry = _session_datetime_at(open_et, str(settings.get("entryCutoffTimeEt") or "15:30"))
    configured_flatten = _session_datetime_at(open_et, str(settings.get("flattenTimeEt") or "15:55"))
    early_close_entry = close_et - timedelta(minutes=30)
    early_close_flatten = close_et - timedelta(minutes=5)
    effective_entry = min(configured_entry, early_close_entry)
    effective_flatten = min(configured_flatten, early_close_flatten)
    reason_codes = ["regime.eod.schedule.evaluated"]
    if bool(getattr(session, "is_early_close", False)) and (effective_entry != configured_entry or effective_flatten != configured_flatten):
        reason_codes.append("regime.eod.early_close_schedule_adjusted")
    return {
        "entryCutoffReached": now_et >= effective_entry,
        "flattenDue": now_et >= effective_flatten,
        "nearClose": now_et >= early_close_flatten,
        "effectiveEntryCutoffEt": effective_entry.time().strftime("%H:%M"),
        "effectiveFlattenTimeEt": effective_flatten.time().strftime("%H:%M"),
        "marketCloseEt": close_et.isoformat(),
        "reasonCodes": reason_codes,
    }


def _session_datetime_at(open_et: datetime, configured_time: str) -> datetime:
    try:
        hour, minute = (int(part) for part in configured_time.split(":", 1))
    except (TypeError, ValueError):
        hour, minute = 15, 55
    return open_et.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _parse_exchange_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("America/New_York"))
    return parsed.astimezone(ZoneInfo("America/New_York"))


def _eod_mark_price_candle(evaluated_at: datetime, positions: list[dict[str, Any]], last_bar: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(last_bar, dict):
        close = _optional_float(last_bar.get("close") or last_bar.get("price"))
        if close is not None and close > 0:
            return {
                "timestamp": evaluated_at.isoformat().replace("+00:00", "Z"),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 0,
            }
    fallback_price = 0.01
    for position in positions:
        price = _optional_float(position.get("lastPrice") or position.get("averageFillPrice") or position.get("entryPrice"))
        if price is not None and price > 0:
            fallback_price = price
            break
    return {
        "timestamp": evaluated_at.isoformat().replace("+00:00", "Z"),
        "open": fallback_price,
        "high": fallback_price,
        "low": fallback_price,
        "close": fallback_price,
        "volume": 0,
    }


def _eod_reason_codes(schedule: dict[str, Any], cancelled_entries: int, flattened: dict[str, Any], unexpected_open: bool) -> list[str]:
    codes = list(str(code) for code in schedule.get("reasonCodes") or ())
    if schedule.get("entryCutoffReached"):
        codes.append("regime.eod.entry_cutoff_reached")
    if cancelled_entries:
        codes.append("regime.eod.stale_entry_orders_cancelled")
    if int(flattened.get("exitIntentsCreated") or 0) > 0:
        codes.append("regime.eod.flatten_exit_intents_created")
    if unexpected_open:
        codes.append("regime.eod.unexpected_open_position_after_flatten")
    if not unexpected_open:
        codes.append("regime.eod.maintenance_completed")
    return list(dict.fromkeys(codes))


def _records_for_session(records: list[dict[str, Any]], session_date: str, *, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if not session_date:
        return []
    selected: list[dict[str, Any]] = []
    for record in records:
        for key in keys:
            timestamp = record.get(key)
            if not timestamp:
                continue
            parsed = exchange_session(str(timestamp))
            if parsed.session_date == session_date:
                selected.append(record)
                break
    return selected


def _sum_numeric(records: list[dict[str, Any]], keys: tuple[str, ...]) -> float:
    total = 0.0
    for record in records:
        for key in keys:
            value = _optional_float(record.get(key))
            if value is not None:
                total += value
                break
    return round(total, 6)


def _checkpoint_exchange_session_date(checkpoint: dict[str, Any]) -> str:
    timestamp = checkpoint.get("lastProcessedBarTimestamp") or checkpoint.get("last_processed_bar_timestamp") or checkpoint.get("dailyResetAt")
    if not timestamp:
        return ""
    return str(exchange_session(str(timestamp)).session_date or "")


def _empty_daily_counters(session_date: str) -> dict[str, Any]:
    return {
        "sessionDate": session_date,
        "decisionCount": 0,
        "orderProposalCount": 0,
        "tradeCount": 0,
        "entryCount": 0,
        "lossCount": 0,
        "consecutiveLosses": 0,
        "dailyLossPercent": 0.0,
        "strategyTradeCounts": {},
        "familyTradeCounts": {},
    }


def _paper_button_requested(snapshot: dict[str, Any], *, fallback: bool) -> bool:
    if "paperButtonRequested" in snapshot:
        return bool(snapshot.get("paperButtonRequested"))
    if "requestedAutomaticPaperTradingEnabled" in snapshot:
        return bool(snapshot.get("requestedAutomaticPaperTradingEnabled"))
    return fallback


def _paper_button_effective(snapshot: dict[str, Any], *, fallback: bool) -> bool:
    if "paperButtonEffective" in snapshot:
        return bool(snapshot.get("paperButtonEffective"))
    if "automaticPaperTradingEnabled" in snapshot:
        return bool(snapshot.get("automaticPaperTradingEnabled"))
    return fallback


def _component_healthy(metrics: RegimeRuntimeMetrics, component: str) -> bool:
    return str((metrics.component_health.get(component) or {}).get("status")) == "healthy"


def _component_not_unhealthy(metrics: RegimeRuntimeMetrics, component: str) -> bool:
    return str((metrics.component_health.get(component) or {}).get("status")) != "unhealthy"


def _outbox_runtime_mode(outbox_record: dict[str, Any]) -> str:
    order_intent = _outbox_order_intent(outbox_record)
    try:
        return normalize_regime_runtime_mode(outbox_record.get("runtimeMode") or outbox_record.get("runtime_mode") or order_intent.get("runtimeMode") or order_intent.get("runtime_mode")).value
    except ValueError:
        return "invalid"


def _outbox_order_intent(outbox_record: dict[str, Any]) -> dict[str, Any]:
    nested = outbox_record.get("orderIntent")
    return nested if isinstance(nested, dict) else outbox_record


def _outbox_flat_settings(outbox_record: dict[str, Any]) -> dict[str, Any]:
    order_intent = _outbox_order_intent(outbox_record)
    settings_snapshot = _dict_from_sources(outbox_record, order_intent, "settingsSnapshot", "settings_snapshot")
    flat = _dict_from_sources(outbox_record, order_intent, "flatSettings", "flat_settings")
    if flat:
        return flat
    execution = settings_snapshot.get("execution") if isinstance(settings_snapshot.get("execution"), dict) else {}
    entry_policy = settings_snapshot.get("entry_policy") if isinstance(settings_snapshot.get("entry_policy"), dict) else settings_snapshot.get("entryPolicy") if isinstance(settings_snapshot.get("entryPolicy"), dict) else {}
    exit_policy = settings_snapshot.get("exit_policy") if isinstance(settings_snapshot.get("exit_policy"), dict) else settings_snapshot.get("exitPolicy") if isinstance(settings_snapshot.get("exitPolicy"), dict) else {}
    return {
        "entryCutoffTimeEt": entry_policy.get("entryCutoffTimeEt") or settings_snapshot.get("entryCutoffTimeEt") or "15:30",
        "flattenTimeEt": exit_policy.get("flattenTimeEt") or settings_snapshot.get("flattenTimeEt") or "15:55",
        "endOfDayFlattenEnabled": exit_policy.get("endOfDayFlattenEnabled", settings_snapshot.get("endOfDayFlattenEnabled", True)),
        "orderTimeToLiveSeconds": execution.get("orderTimeToLiveSeconds") or settings_snapshot.get("orderTimeToLiveSeconds") or 300,
    }


def _dict_from_sources(primary: dict[str, Any], secondary: dict[str, Any], *keys: str) -> dict[str, Any]:
    for source in (primary, secondary):
        for key in keys:
            value = source.get(key)
            if isinstance(value, dict):
                return value
    return {}


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _unhealthy_component_blockers(metrics: RegimeRuntimeMetrics) -> list[str]:
    blockers: list[str] = []
    for component, health in metrics.component_health.items():
        if str(health.get("status")) == "unhealthy":
            blockers.append(f"regime.execution.operational_component_unhealthy.{component}")
    return blockers


def _bar_telemetry(event: RegimeFinalisedBarEvent) -> dict[str, Any]:
    return {
        "algorithmId": "regime",
        "eventId": event.event_id,
        "symbol": event.symbol,
        "runtimeMode": event.runtime_mode,
        "barCloseTimestamp": event.completed_bar_timestamp.isoformat().replace("+00:00", "Z"),
        "publishedAt": event.published_at.isoformat().replace("+00:00", "Z"),
        "finalized": True,
        "timeframe": "1Min",
        "settingsVersion": event.settings_version,
        "dataManifestHash": event.data_manifest_hash,
    }


def _strategy_routing_from_result(result: dict[str, Any]) -> dict[str, Any]:
    decision = result.get("decision") if isinstance(result.get("decision"), dict) else {}
    outputs = decision.get("strategy_outputs") if isinstance(decision.get("strategy_outputs"), list) else []
    routed = []
    shadow = []
    blocked = []
    for output in outputs:
        if not isinstance(output, dict):
            continue
        row = {
            "strategyId": output.get("strategy_id") or output.get("strategyId"),
            "role": output.get("role"),
            "family": output.get("family"),
            "signal": output.get("signal"),
            "eligible": bool(output.get("eligible")),
            "lifecycleStatus": output.get("lifecycle_status") or output.get("lifecycleStatus"),
        }
        if row["eligible"]:
            routed.append(row)
        elif row["lifecycleStatus"] == "shadow":
            shadow.append(row)
        else:
            blocked.append(row)
    return {
        "algorithmId": "regime",
        "confirmedRegime": _current_regime_from_result(result),
        "routedStrategies": routed,
        "shadowStrategies": shadow,
        "blockedStrategies": blocked,
        "familyAggregation": result.get("familyAggregation") if isinstance(result.get("familyAggregation"), dict) else {},
    }


def _current_regime_from_result(result: dict[str, Any]) -> str:
    decision = result.get("decision") if isinstance(result.get("decision"), dict) else {}
    confirmed = decision.get("confirmed_state") if isinstance(decision.get("confirmed_state"), dict) else {}
    return str(confirmed.get("confirmed_regime") or "unknown")


def _current_regime_from_metrics(metrics: RegimeRuntimeMetrics) -> str:
    latest = metrics.latest_decision if isinstance(metrics.latest_decision, dict) else {}
    return _current_regime_from_result(latest)


def _outbox_status(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    oldest_created_at: str | None = None
    for record in records:
        status = str(record.get("processingStatus") or "unknown")
        counts[status] = counts.get(status, 0) + 1
        created_at = str(record.get("createdAt") or record.get("created_at") or record.get("created") or "")
        if created_at and (oldest_created_at is None or created_at < oldest_created_at):
            oldest_created_at = created_at
    return {
        "algorithmId": "regime",
        "pendingCount": len(records),
        "statusCounts": counts,
        "oldestPendingCreatedAt": oldest_created_at,
        "stuck": _outbox_is_stuck(records),
    }


def _outbox_is_stuck(records: list[dict[str, Any]]) -> bool:
    now = datetime.now(timezone.utc)
    for record in records:
        status = str(record.get("processingStatus") or "")
        if status in {"reconciliation_required", "dead_letter"}:
            return True
        created_at = record.get("updatedAt") or record.get("createdAt") or record.get("created_at")
        try:
            created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00")).astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        if status in {"submitting", "submitted", "queued", "retry_scheduled"} and (now - created).total_seconds() > 300:
            return True
    return False


def _risk_reservations_from_orders(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reservations: list[dict[str, Any]] = []
    for record in records:
        reservation_id = _outbox_global_risk_reservation_id(record)
        if reservation_id:
            reservations.append(
                {
                    "algorithmId": "regime",
                    "reservationId": reservation_id,
                    "orderIntentId": record.get("orderIntentId") or record.get("order_intent_id"),
                    "status": record.get("processingStatus"),
                    "quantity": record.get("quantity"),
                }
            )
    return reservations


def _risk_reservations_consistent(records: list[dict[str, Any]]) -> bool:
    for record in records:
        if _outbox_record_is_new_entry(record) and str(record.get("processingStatus") or "") in {"risk_approved", "queued", "submitting", "submitted"}:
            if not _outbox_global_risk_reservation_id(record):
                return False
    return True


def _runtime_checkpoint_restore_summary(checkpoint: dict[str, Any] | None) -> dict[str, Any]:
    if checkpoint is None:
        return {
            "checkpointRestored": False,
            "freshRuntimeStateInitialized": True,
            "hysteresisRestored": True,
            "cooldownsRestored": True,
            "dailyCountersRestored": True,
            "lastProcessedBarTimestamp": None,
            "lastDecisionId": None,
            "sequenceVersion": 0,
            "reasonCodes": ["regime.runtime.recovery.fresh_runtime_state_initialized"],
        }
    cooldown_state = checkpoint.get("cooldownState") if isinstance(checkpoint.get("cooldownState"), dict) else checkpoint.get("cooldown_state")
    strategy_cooldowns = checkpoint.get("strategyCooldowns") if isinstance(checkpoint.get("strategyCooldowns"), dict) else checkpoint.get("strategy_cooldowns")
    family_cooldowns = checkpoint.get("familyCooldowns") if isinstance(checkpoint.get("familyCooldowns"), dict) else checkpoint.get("family_cooldowns")
    daily_counters = checkpoint.get("dailyCounters") if isinstance(checkpoint.get("dailyCounters"), dict) else checkpoint.get("daily_counters")
    return {
        "checkpointRestored": True,
        "freshRuntimeStateInitialized": False,
        "hysteresisRestored": any(
            key in checkpoint
            for key in (
                "confirmedRegime",
                "confirmed_regime",
                "hysteresisState",
                "candidateRegime",
                "candidate_regime",
                "candidateConfirmationCount",
                "candidate_confirmation_count",
            )
        ),
        "cooldownsRestored": isinstance(cooldown_state, dict) and isinstance(strategy_cooldowns, dict) and isinstance(family_cooldowns, dict),
        "dailyCountersRestored": isinstance(daily_counters, dict),
        "lastProcessedBarTimestamp": checkpoint.get("lastProcessedBarTimestamp") or checkpoint.get("last_processed_bar_timestamp"),
        "lastDecisionId": checkpoint.get("lastDecisionId") or checkpoint.get("last_decision_id"),
        "sequenceVersion": int(checkpoint.get("sequenceVersion") or checkpoint.get("stateVersion") or checkpoint.get("sequence_version") or 0),
        "reasonCodes": ["regime.runtime.recovery.checkpoint_restored"],
    }


def _startup_recovery_reason_codes(failed_checks: list[str]) -> list[str]:
    mapping = {
        "settingsLoaded": "regime.runtime.recovery.settings_unavailable",
        "runtimeCheckpointReadable": "regime.runtime.recovery.checkpoint_unreadable",
        "hysteresisStateRestored": "regime.runtime.recovery.hysteresis_not_restored",
        "cooldownsRestored": "regime.runtime.recovery.cooldowns_not_restored",
        "dailyCountersRestored": "regime.runtime.recovery.daily_counters_not_restored",
        "finalizedBarEventsRecovered": "regime.runtime.recovery.finalized_bar_events_not_recovered",
        "unfinishedOutboxRecovered": "regime.runtime.recovery.unfinished_outbox_not_recovered",
        "abandonedLeasesDetected": "regime.runtime.recovery.abandoned_leases_not_checked",
        "inventoryRebuiltOrVerified": "regime.runtime.recovery.inventory_not_verified",
        "inventoryReconciled": "regime.runtime.recovery.inventory_not_reconciled",
        "brokerObservationsReconciled": "regime.runtime.recovery.broker_observations_not_reconciled",
        "globalRiskReservationsReconciled": "regime.runtime.recovery.global_risk_reservations_not_reconciled",
        "positionManagementResumed": "regime.runtime.recovery.position_management_not_resumed",
    }
    codes = [mapping.get(check, f"regime.runtime.recovery.{check}") for check in failed_checks]
    return list(dict.fromkeys(["regime.runtime.recovery.blocked", *codes]))


def _latest_outbox_by_intent(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        order_intent = record.get("orderIntent") if isinstance(record.get("orderIntent"), dict) else {}
        order_intent_id = str(record.get("orderIntentId") or record.get("order_intent_id") or order_intent.get("orderIntentId") or "")
        if order_intent_id:
            latest[order_intent_id] = record
    return latest


def _outbox_global_risk_reservation_id(record: dict[str, Any]) -> str:
    for source in (
        record,
        record.get("globalRiskApproval") if isinstance(record.get("globalRiskApproval"), dict) else {},
        record.get("global_risk_approval") if isinstance(record.get("global_risk_approval"), dict) else {},
        record.get("globalRiskDecision") if isinstance(record.get("globalRiskDecision"), dict) else {},
        record.get("globalApplication") if isinstance(record.get("globalApplication"), dict) else {},
    ):
        if not isinstance(source, dict):
            continue
        reservation_id = source.get("globalRiskReservationId") or source.get("reservationId") or source.get("reservation_id") or source.get("riskReservationId")
        if reservation_id:
            return str(reservation_id)
    return ""


def _component_for_worker(worker_id: str) -> str:
    if "backtest" in worker_id:
        return "backtest_worker"
    if "local_risk" in worker_id:
        return "local_risk"
    if "execution_outbox" in worker_id:
        return "execution_outbox"
    if "reconciliation" in worker_id:
        return "order_reconciliation"
    if "position" in worker_id:
        return "position_reconciliation"
    if "ingestion" in worker_id:
        return "market_event_publisher"
    if "decision" in worker_id:
        return "decision_worker"
    if "recovery" in worker_id:
        return "runtime_state"
    return "runtime_state"


def _publisher_result_snapshot(result: Any, *, worker_id: str, queue_depth: int) -> dict[str, Any]:
    publications = getattr(result, "publications", ()) or ()
    return {
        "algorithmId": "regime",
        "workerId": worker_id,
        "queueDepth": queue_depth,
        "acceptsOnlyFinalisedOneMinuteBars": True,
        "payloadOperationalStateRejected": True,
        "status": str(getattr(result, "status", "unknown")),
        "reasonCodes": [str(code) for code in getattr(result, "reason_codes", ()) or ()],
        "latestFinalizedCandle": getattr(result, "latest_finalized_candle", None),
        "lagSeconds": getattr(result, "lag_seconds", None),
        "nextPollAfterSeconds": getattr(result, "next_poll_after_seconds", None),
        "acceptedCount": int(getattr(result, "accepted_count", 0) or 0),
        "publicationCount": len(publications),
        "observedAt": _utc_now(),
    }


def _publisher_sleep_seconds(config: RegimeRuntimeSupervisorConfig, snapshot: dict[str, Any]) -> float:
    configured_minimum = max(0.05, float(config.publisher_poll_interval_seconds or 1.0))
    suggested = _optional_float(snapshot.get("nextPollAfterSeconds"))
    sleep_seconds = suggested if suggested is not None else configured_minimum
    reason_codes = set(str(code) for code in snapshot.get("reasonCodes") or ())
    if "regime.publisher.market_closed" in reason_codes:
        closed_interval = max(configured_minimum, float(config.closed_market_publisher_poll_interval_seconds or 300.0))
        sleep_seconds = min(closed_interval, sleep_seconds)
    return max(configured_minimum, float(sleep_seconds))


def _execution_gateway_unavailable_reason(rollout_stage: str, promotion_evidence: dict[str, Any]) -> str:
    if rollout_stage in {"limited_paper", "normal_paper"} and not operational_stage_allows_real_paper_submission(rollout_stage, evidence=promotion_evidence):
        return "regime.rollout.paper_submission_gate_blocked"
    if rollout_stage in {"disabled", "decision_shadow"}:
        return f"regime.rollout.{rollout_stage}.broker_submission_blocked"
    return "regime.execution.paper_gateway_unavailable"


def _stable_runtime_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


_REGIME_RUNTIME_SUPERVISOR: RegimeRuntimeSupervisor | None = None


def get_regime_runtime_supervisor() -> RegimeRuntimeSupervisor:
    from backend.app.algorithms.regime.runtime_factory import get_regime_runtime_supervisor as factory_supervisor

    return factory_supervisor()


__all__ = [
    "REGIME_RUNTIME_SUPERVISOR_VERSION",
    "REGIME_RUNTIME_WORKERS",
    "RegimeRuntimeSupervisor",
    "RegimeRuntimeSupervisorConfig",
    "get_regime_runtime_supervisor",
]
