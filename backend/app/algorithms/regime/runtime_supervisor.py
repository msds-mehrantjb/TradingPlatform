"""Regime-owned background runtime supervisor."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any, Callable

from backend.app.algorithms.regime.configuration import regime_settings_identity_from_payload
from backend.app.algorithms.regime.contracts import REGIME_ALLOWED_RUNTIME_MODE_VALUES, normalize_regime_runtime_mode
from backend.app.algorithms.regime.execution_gateway import submit_regime_outbox_record
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot
from backend.app.algorithms.regime.position_manager import RegimePositionManager
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
from backend.app.execution import PaperOrderGateway


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
    default_algorithm_instance_id: str = "regime-default"
    default_account_id: str = "default"
    default_runtime_mode: str = "shadow"
    symbol: str = "SPY"
    owner_id: str = "regime-runtime-supervisor"
    worker_lease_seconds: int = 30
    crash_after_stage: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "default_runtime_mode", normalize_regime_runtime_mode(self.default_runtime_mode).value)


class RegimeRuntimeSupervisor:
    def __init__(
        self,
        *,
        service: RegimeApplicationService | None = None,
        config: RegimeRuntimeSupervisorConfig | None = None,
        paper_gateway: PaperOrderGateway | None = None,
        account_snapshot_provider: Callable[[dict[str, str]], dict[str, Any]] | None = None,
    ) -> None:
        self.service = service or RegimeApplicationService()
        self.config = config or RegimeRuntimeSupervisorConfig()
        self.paper_gateway = paper_gateway
        self.account_snapshot_provider = account_snapshot_provider
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
        self.metrics.entry_creation_paused_for_reconciliation = True
        self.metrics.recovery_succeeded = False
        self.metrics.inventory_reconciled = False
        for component in REGIME_HEALTH_COMPONENTS:
            self._mark_component(component, "unknown", reason_codes=("regime.health.component.starting",))
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
            self._mark_component("settings_repository", "healthy", reason_codes=("regime.health.settings.loaded",))
            checkpoint = self.service.repository.read_runtime_checkpoint(identity)
            self.metrics.checkpoint_consistent = True
            self._mark_component("runtime_state", "healthy", reason_codes=("regime.health.runtime_state.checkpoint_read",))
            if isinstance(checkpoint, dict):
                last_bar = checkpoint.get("lastProcessedBarTimestamp") or checkpoint.get("last_processed_bar_timestamp")
                if last_bar:
                    self.metrics.last_processed_bar_by_instance_symbol[f"{identity['algorithmInstanceId']}:{identity['symbol']}"] = str(last_bar)
            outbox = self.service.repository.recover_unfinished_outbox_records(identity)
            self._mark_component("execution_outbox", "healthy", reason_codes=("regime.health.execution_outbox.recovered",), details=outbox)
            leases = self.service.repository.detect_abandoned_leases(identity, now=_utc_now())
            reconciliation = self.reconcile_broker_observations()
            self.metrics.last_checkpoint = checkpoint
            self.metrics.recovered_outbox_records += int(outbox.get("recoveredOutboxCount") or 0)
            self.metrics.abandoned_leases_detected += int(leases.get("abandonedLeaseCount") or 0)
            self.metrics.inventory_reconciled = bool(reconciliation.get("reconciled"))
            self.metrics.recovery_succeeded = self.metrics.inventory_reconciled
            self.metrics.entry_creation_paused_for_reconciliation = not self.metrics.inventory_reconciled
            if self.metrics.inventory_reconciled:
                self._unblock_new_entries("regime.runtime.recovery_incomplete")
            recovery = {
                **recovery,
                "recoveryStatus": "completed" if self.metrics.recovery_succeeded else "blocked",
                "completedAt": _utc_now(),
                "settingsVersion": settings.get("settingsVersion"),
                "checkpointRestored": checkpoint is not None,
                "unfinishedOutboxRecovered": outbox,
                "abandonedLeases": leases,
                "inventoryReconciled": self.metrics.inventory_reconciled,
                "newEntriesPaused": self.metrics.entry_creation_paused_for_reconciliation,
                "reasonCodes": ["regime.runtime.recovery.completed" if self.metrics.recovery_succeeded else "regime.runtime.recovery.inventory_not_reconciled"],
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

    async def heartbeat_loop(self, worker_id: str) -> None:
        while not self.stop_event.is_set():
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
            await self._sleep_until_stopped(self.config.heartbeat_interval_seconds)

    async def maintenance_loop(self, worker_id: str) -> None:
        await self._periodic_idle(worker_id)

    async def finalised_bar_ingestion_loop(self, worker_id: str) -> None:
        while not self.stop_event.is_set():
            self.metrics.queue_depth = self.event_queue.qsize()
            self.service.repository.write_runtime_snapshot(
                _default_identity(self.config),
                "finalised_bar_ingestion",
                {
                    "algorithmId": "regime",
                    "workerId": worker_id,
                    "queueDepth": self.metrics.queue_depth,
                    "acceptsOnlyFinalisedOneMinuteBars": True,
                    "payloadOperationalStateRejected": True,
                    "observedAt": _utc_now(),
                },
            )
            await self._sleep_until_stopped(self.config.maintenance_interval_seconds)

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
            today = datetime.now(timezone.utc).date().isoformat()
            if today != self._last_daily_reset_date:
                self._last_daily_reset_date = today
                self.service.repository.record_runtime_event(
                    {
                        **_default_identity(self.config),
                        "eventId": f"regime-daily-reset-{today}",
                        "eventType": "runtime_daily_reset_maintenance",
                        "processingStatus": "completed",
                        "payload": {
                            "algorithmId": "regime",
                            "workerId": worker_id,
                            "sessionDate": today,
                            "newEntriesBlocked": bool(self.metrics.entry_creation_paused_for_reconciliation),
                            "riskReducingExitsAllowed": self.metrics.risk_reducing_exits_allowed,
                        },
                    }
                )
            await self._sleep_until_stopped(self.config.maintenance_interval_seconds)

    async def execution_outbox_loop(self, worker_id: str) -> None:
        while not self.stop_event.is_set():
            if self.paper_gateway is not None:
                await asyncio.to_thread(self.process_execution_outbox_once)
            await self._sleep_until_stopped(self.config.maintenance_interval_seconds)

    def process_execution_outbox_once(self) -> dict[str, Any]:
        if self.paper_gateway is None:
            self._record_component_failure(
                "paper_broker",
                RuntimeError("Regime paper gateway unavailable"),
                reason_code="regime.execution.paper_gateway_unavailable",
            )
            return {"algorithmId": "regime", "processed": False, "reasonCodes": ["regime.execution.paper_gateway_unavailable"]}
        identity = _default_identity(self.config)
        try:
            records = self.service.repository.pending_execution_outbox_records(identity)
            self._mark_component("execution_outbox", "healthy", reason_codes=("regime.health.execution_outbox.read",))
        except Exception as exc:
            self._record_component_failure(
                "execution_outbox",
                exc,
                reason_code="regime.execution.outbox_read_failed",
                details={"stage": "process_execution_outbox_once"},
            )
            return {"algorithmId": "regime", "processed": False, "reasonCodes": ["regime.execution.outbox_read_failed"]}
        if not records:
            return {"algorithmId": "regime", "processed": False, "reasonCodes": ["regime.execution.outbox_idle"]}
        outbox_record = records[0]
        if _outbox_record_is_new_entry(outbox_record) and not (self.metrics.recovery_succeeded and self.metrics.inventory_reconciled):
            self._block_new_entries("regime.execution.recovery_or_reconciliation_unhealthy")
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
        try:
            result = submit_regime_outbox_record(
                repository=self.service.repository,
                identity=identity,
                paper_gateway=self.paper_gateway,
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
            self.reconcile_broker_observations()
            await self._sleep_until_stopped(self.config.maintenance_interval_seconds)

    async def backtest_job_loop(self, worker_id: str) -> None:
        from backend.app.algorithms.regime.runtime import REGIME_JOB_MANAGER

        while not self.stop_event.is_set():
            await asyncio.to_thread(REGIME_JOB_MANAGER.start)
            await asyncio.to_thread(
                self.service.repository.recover_abandoned_backtest_jobs,
                owner_id=worker_id,
                stale_after_seconds=self.config.worker_lease_seconds * 4,
            )
            await self._sleep_until_stopped(self.config.maintenance_interval_seconds)

    async def position_management_loop(self, worker_id: str) -> None:
        while not self.stop_event.is_set():
            manager = RegimePositionManager(self.service.repository)
            positions = manager.restore_open_positions(_default_identity(self.config))
            self.metrics.open_positions = len(positions)
            if self.metrics.entry_creation_paused_for_reconciliation or self.metrics.paused:
                self.metrics.protected_positions_managed_during_entry_pause += len(positions)
            await self._sleep_until_stopped(self.config.maintenance_interval_seconds)

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
            payload = {
                "algorithmInstanceId": event.algorithm_instance_id,
                "accountId": event.account_id,
                "runtimeMode": event.runtime_mode,
                "symbol": event.symbol,
                "marketData": event.market_payload,
                "account": await asyncio.to_thread(self._load_shared_account_snapshot, event),
            }
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
            for stage in ("order_submitted", "broker_acknowledged", "fill_observed", "position_closed"):
                self._record_stage(event, stage, {"skipped": True, "reason": "live_trading_disabled"}, status="skipped")
                await self._maybe_crash(stage)
            self._record_stage(event, "inventory_reconciled", self.reconcile_broker_observations())
            await self._maybe_crash("inventory_reconciled")
            self.metrics.last_processed_bar_by_instance_symbol[_instance_symbol_key(event)] = event.completed_bar_timestamp.isoformat().replace("+00:00", "Z")
            self._persist_event(event, "completed")
            self._release_processing_lease(event)
            return {"processed": True, "eventId": event.event_id, "decisionId": decision_id, "reasonCodes": ["regime.runtime.event.processed"]}

    def reconcile_broker_observations(self) -> dict[str, Any]:
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
        else:
            self._mark_component("position_reconciliation", "healthy", reason_codes=("regime.runtime.reconciliation.no_observations_pending",))
            self._mark_component("order_reconciliation", "healthy", reason_codes=("regime.runtime.reconciliation.no_observations_pending",))
        self.metrics.latest_reconciliation = result
        return result

    def status(self) -> dict[str, Any]:
        self.metrics.queue_depth = self.event_queue.qsize()
        self.metrics.command_queue_depth = self.command_queue.qsize()
        return {
            "algorithmId": "regime",
            "runtimeVersion": REGIME_RUNTIME_SUPERVISOR_VERSION,
            "workers": REGIME_RUNTIME_WORKERS,
            "apiHandlersExecuteHeavyWorkInline": False,
            "liveTradingEnabled": False,
            "allowedRuntimeModes": REGIME_ALLOWED_RUNTIME_MODE_VALUES,
            **self.metrics.as_dict(),
        }

    def health(self) -> dict[str, Any]:
        return health_from_metrics(self.metrics)

    def observability(self) -> dict[str, Any]:
        return operational_snapshot_from_metrics(self.metrics)

    def alerts(self) -> dict[str, Any]:
        self.metrics.alert_conditions = alert_conditions_from_metrics(self.metrics)
        return {"algorithmId": "regime", "alerts": list(self.metrics.alert_conditions)}

    def admin_audit(self) -> dict[str, Any]:
        snapshot = self.service.repository.read_runtime_snapshot(_default_identity(self.config), "admin_audit") or {}
        return {"algorithmId": "regime", "audit": snapshot.get("commands") or [], "latestCommand": self.metrics.latest_command}

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
            return "stale"
        last = self.metrics.last_processed_bar_by_instance_symbol.get(_instance_symbol_key(event))
        if last and event.completed_bar_timestamp.isoformat().replace("+00:00", "Z") <= last and not event.replay_recovery:
            self.metrics.out_of_order_events += 1
            return "out_of_order"
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
        identity = {key: str(value) for key, value in event.identity.items()}
        if self.account_snapshot_provider is None:
            return {
                "algorithmId": "regime",
                "sourceAuthority": "shared_backend_unavailable",
                "accountId": event.account_id,
                "availableBuyingPower": 0.0,
                "buyingPower": 0.0,
                "globalRiskCapacityQuantity": 0,
                "buyingPowerCurrent": False,
                "positionsReconciled": self.metrics.inventory_reconciled,
                "openOrdersReconciled": self.metrics.inventory_reconciled,
                "observedAt": _utc_now(),
                "reasonCodes": ["regime.runtime.account_snapshot.unavailable_fail_closed"],
            }
        snapshot = dict(self.account_snapshot_provider(identity) or {})
        snapshot.pop("settings", None)
        snapshot.pop("settingsSnapshot", None)
        snapshot.pop("inventory", None)
        snapshot.pop("inventorySnapshot", None)
        return {
            "algorithmId": "regime",
            "sourceAuthority": snapshot.get("sourceAuthority") or snapshot.get("authority") or "shared_backend_service",
            "accountId": event.account_id,
            **snapshot,
            "runtimeLoadedBy": REGIME_RUNTIME_SUPERVISOR_VERSION,
        }

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
            if self.metrics.inventory_reconciled:
                self.metrics.paused = False
                self.metrics.pause_reason = None
                self.metrics.entry_creation_paused_for_reconciliation = False
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
            activation = self.service.activate_settings({**command.payload, "actor": command.actor})
            self.metrics.active_settings_version = str(activation.get("settingsVersion") or self.metrics.active_settings_version)
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
        strategy_settings = dict(snapshot.get("strategy_settings") or {})
        if strategy_id not in strategy_settings:
            raise ValueError(f"Unknown Regime strategy for lifecycle command: {strategy_id}")
        strategy_settings[strategy_id] = {**dict(strategy_settings[strategy_id]), "lifecycle": lifecycle, "enabled": lifecycle == "active"}
        snapshot["strategy_settings"] = strategy_settings
        return self.service.activate_settings({"settingsSnapshot": snapshot, "actor": command.actor})

    def _append_admin_audit(self, command: RegimeRuntimeCommand) -> None:
        identity = _default_identity(self.config)
        existing = self.service.repository.read_runtime_snapshot(identity, "admin_audit") or {}
        commands = list(existing.get("commands") or [])
        commands.append({**command.as_dict(), "algorithmId": "regime", "processedAt": _utc_now()})
        self.service.repository.write_runtime_snapshot(identity, "admin_audit", {"commands": commands[-100:]})

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


def _result_decision_id(result: dict[str, Any]) -> str:
    return str(result.get("decisionId") or result.get("decision", {}).get("decision_id") or "")


def _outbox_record_is_new_entry(outbox_record: dict[str, Any]) -> bool:
    order_intent = outbox_record.get("orderIntent") if isinstance(outbox_record.get("orderIntent"), dict) else outbox_record
    effect = str(order_intent.get("positionEffect") or order_intent.get("position_effect") or "").lower()
    if effect:
        return effect.startswith("enter") or effect in {"open", "new_entry"}
    return True


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
        return "market_event_ingestion"
    if "decision" in worker_id:
        return "decision_worker"
    if "recovery" in worker_id:
        return "runtime_state"
    return "runtime_state"


_REGIME_RUNTIME_SUPERVISOR: RegimeRuntimeSupervisor | None = None


def get_regime_runtime_supervisor() -> RegimeRuntimeSupervisor:
    global _REGIME_RUNTIME_SUPERVISOR
    if _REGIME_RUNTIME_SUPERVISOR is None:
        _REGIME_RUNTIME_SUPERVISOR = RegimeRuntimeSupervisor()
    return _REGIME_RUNTIME_SUPERVISOR


__all__ = [
    "REGIME_RUNTIME_SUPERVISOR_VERSION",
    "REGIME_RUNTIME_WORKERS",
    "RegimeRuntimeSupervisor",
    "RegimeRuntimeSupervisorConfig",
    "get_regime_runtime_supervisor",
]
