"""Standalone WCA background runtime supervisor and logical workers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from backend.app.algorithms.wca.broker_reconciliation import reconcile_wca_broker
from backend.app.algorithms.wca.alpaca_paper_broker import WcaAlpacaPaperBroker, WcaAlpacaPaperBrokerConfigurationError
from backend.app.algorithms.wca.contracts import (
    GlobalGateResult,
    WcaPaperExecutionRequest,
    WcaAggregationResult,
    WcaDecision,
    WcaEvaluationStatus,
    WcaGateStatus,
    WcaLatencyTimestamps,
    WcaLocalGateResult,
    WcaMarketStatus,
    WcaOrderStatus,
    WcaOrderValidationContext,
    WcaRuntimeMode,
    WcaSide,
    WcaSizingResult,
    coerce_wca_order_status,
    coerce_wca_runtime_mode,
)
from backend.app.algorithms.wca.configuration import default_effective_settings
from backend.app.algorithms.wca.execution_pipeline import WcaExecutionPipelineInput, run_wca_paper_pipeline_adapter
from backend.app.algorithms.wca.global_risk import WCA_GLOBAL_RISK_ADAPTER_VERSION, WcaGlobalRiskAdapter, build_wca_global_risk_proposal
from backend.app.algorithms.wca.paper_account import validate_wca_automatic_paper_account
from backend.app.algorithms.wca.paper_broker import WcaPaperBrokerOrderRequest, WcaPaperBrokerOutboxAdapter, WcaPaperBrokerTimeout, build_wca_paper_broker_request, place_or_replace_wca_protective_orders
from backend.app.algorithms.wca.position_management import manage_wca_position
from backend.app.algorithms.wca.market_calendar import WcaMarketCalendar
from backend.app.algorithms.wca.repository import WcaGlobalRiskApprovalRecord, WcaInventoryLedgerEvent, WcaSqliteRepository
from backend.app.algorithms.wca.runtime_state import WcaAuthoritativeRuntimeState, load_wca_authoritative_runtime_state
from backend.app.algorithms.wca.runtime_commands import WcaRuntimeCommand, WcaRuntimeCommandType, runtime_command
from backend.app.algorithms.wca.runtime_events import WcaFinalizedBarEvent
from backend.app.algorithms.wca.runtime_health import WcaRuntimeHealthSnapshot, critical_health_reason_codes, healthy_runtime_snapshot
from backend.app.algorithms.wca.runtime_repository import WcaRuntimeRepository
from backend.app.algorithms.wca.runtime_control import WcaRuntimeControl, WcaRuntimeControlEvidence, resolve_wca_effective_runtime_control
from backend.app.algorithms.wca.session_validation import WcaBrokerClock, validate_wca_entry_session
from backend.app.algorithms.wca.rollout import (
    WCA_AUTOMATIC_PAPER_ROLLOUT_STAGES,
    WcaLimitedAutomaticPaperCaps,
    WcaRolloutEvidence,
    evaluate_wca_automatic_paper_rollout,
    wca_rollout_feature_flags,
)
from backend.app.execution import BrokerFillUpdate
from backend.app.gates import BrokerAccountSnapshot


WCA_RUNTIME_SUPERVISOR_VERSION = "wca_background_runtime_supervisor_v1"
WCA_RUNTIME_REQUIRES_OS_PROCESS = True
WCA_RUNTIME_WORKERS = (
    "runtime_scheduler_worker",
    "finalised_bar_consumer",
    "decision_worker",
    "manual_paper_command_worker",
    "position_and_protective_exit_worker",
    "global_risk_request_worker",
    "execution_outbox_worker",
    "broker_reconciliation_worker",
    "recovery_worker",
    "configuration_activation_worker",
    "runtime_control_worker",
    "configuration_rollback_worker",
    "emergency_risk_reduction_worker",
    "heartbeat_and_health_worker",
    "end_of_session_worker",
)
WCA_RUNTIME_COMMAND_RETRY_POLICY = {
    "terminalStatuses": ("completed", "blocked", "failed"),
    "leaseExpiration": "running_commands_are_requeued_by_recovery_worker",
    "leaseSecondsSetting": "WcaRuntimeSettings.lease_seconds",
    "retryWorker": "recovery_worker",
    "reasonCodes": (
        "wca.runtime.recovery.command_requeued",
        "wca.runtime.command.completed",
        "wca.runtime.command.blocked",
        "wca.runtime.command.failed",
    ),
}
WCA_RUNTIME_COMMAND_CONSUMERS = {
    WcaRuntimeCommandType.FINALIZED_BAR_DECISION: "decision_worker",
    WcaRuntimeCommandType.MANUAL_PAPER_COMMAND: "manual_paper_command_worker",
    WcaRuntimeCommandType.PAUSE_NEW_ENTRIES: "runtime_control_worker",
    WcaRuntimeCommandType.RESUME_NEW_ENTRIES: "runtime_control_worker",
    WcaRuntimeCommandType.SET_AUTOMATIC_PAPER: "runtime_control_worker",
    WcaRuntimeCommandType.CONFIGURATION_ACTIVATION: "configuration_activation_worker",
    WcaRuntimeCommandType.CONFIGURATION_ROLLBACK: "configuration_rollback_worker",
    WcaRuntimeCommandType.POSITION_PROTECTIVE_EXIT: "position_and_protective_exit_worker",
    WcaRuntimeCommandType.GLOBAL_RISK_REQUEST: "global_risk_request_worker",
    WcaRuntimeCommandType.EXECUTION_OUTBOX: "execution_outbox_worker",
    WcaRuntimeCommandType.BROKER_RECONCILIATION: "broker_reconciliation_worker",
    WcaRuntimeCommandType.RECOVERY: "recovery_worker",
    WcaRuntimeCommandType.EMERGENCY_RISK_REDUCTION: "emergency_risk_reduction_worker",
    WcaRuntimeCommandType.HEARTBEAT: "heartbeat_and_health_worker",
    WcaRuntimeCommandType.END_OF_SESSION: "end_of_session_worker",
}


@dataclass(frozen=True)
class WcaRuntimeSettings:
    account_id: str = "paper"
    symbol: str = "SPY"
    runtime_mode: WcaRuntimeMode | str = WcaRuntimeMode.AUTOMATIC_PAPER
    max_event_queue_depth: int = 200
    max_command_queue_depth: int = 500
    max_event_age_seconds: int = 300
    max_state_age_seconds: int = 120
    max_finalized_bar_age_seconds: int | None = None
    max_quote_age_seconds: int = 15
    max_authoritative_account_state_age_seconds: int | None = None
    max_reconciliation_age_seconds: int = 120
    max_queue_delay_seconds: int = 20
    max_clock_skew_seconds: int = 2
    max_worker_heartbeat_age_seconds: int = 60
    max_lag_seconds: int = 120
    lease_seconds: int = 30
    poll_seconds: float = 1.0
    heartbeat_interval_seconds: int = 30
    recovery_interval_seconds: int = 30
    broker_snapshot_interval_seconds: int = 60
    market_readiness_interval_seconds: int = 60
    entry_cutoff_interval_seconds: int = 60
    end_of_session_flatten_buffer_minutes: int = 5

    def __post_init__(self) -> None:
        object.__setattr__(self, "runtime_mode", coerce_wca_runtime_mode(self.runtime_mode))
        if self.max_finalized_bar_age_seconds is None:
            object.__setattr__(self, "max_finalized_bar_age_seconds", self.max_lag_seconds)
        if self.max_authoritative_account_state_age_seconds is None:
            object.__setattr__(self, "max_authoritative_account_state_age_seconds", self.max_state_age_seconds)


class WcaRuntimeSupervisor:
    def __init__(
        self,
        *,
        repository: WcaSqliteRepository | None = None,
        runtime_repository: WcaRuntimeRepository | None = None,
        settings: WcaRuntimeSettings | None = None,
        owner_id: str | None = None,
    ) -> None:
        self.repository = repository or WcaSqliteRepository()
        self.runtime_repository = runtime_repository or WcaRuntimeRepository(self.repository)
        self.settings = settings or WcaRuntimeSettings()
        self.owner_id = owner_id or f"wca-runtime-{uuid4().hex}"
        self.global_risk_client = WcaGlobalRiskAdapter()
        self.recovery_state = "not_started"
        self._started = False
        self._startup_scheduled = False
        self._last_readiness_check_at: datetime | None = None
        self._last_entry_cutoff_check_at: datetime | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self.workers = (
            RuntimeSchedulerWorker(self),
            RecoveryWorker(self),
            FinalizedBarConsumerWorker(self),
            DecisionWorker(self),
            ManualPaperCommandWorker(self),
            PositionProtectiveExitWorker(self),
            GlobalRiskRequestWorker(self),
            ExecutionOutboxWorker(self),
            BrokerReconciliationWorker(self),
            ConfigurationActivationWorker(self),
            EndOfSessionWorker(self),
            RuntimeControlWorker(self),
            ConfigurationRollbackWorker(self),
            EmergencyRiskReductionWorker(self),
            HeartbeatHealthWorker(self),
        )

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_async_loop(), name="wca-runtime-supervisor")
        self.runtime_repository.write_runtime_health(
            self.health_snapshot(
                paused_new_entries=True,
                reason_codes=("wca.runtime.supervisor.started_fail_closed_until_recovery",),
            )
        )

    async def shutdown(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._stop_event = None
        self._started = False
        self.runtime_repository.release_symbol_lease(
            symbol=self.settings.symbol,
            owner_id=self.owner_id,
            account_id=self.settings.account_id,
        )
        self.runtime_repository.write_runtime_health(
            self.health_snapshot(
                paused_new_entries=True,
                reason_codes=("wca.runtime.supervisor.stopped",),
            )
        )

    def status(self) -> dict[str, Any]:
        health = self.runtime_repository.read_latest_runtime_health()
        heartbeat_at = health.heartbeat_at if health is not None else None
        heartbeat_age_seconds = max(0.0, (_utc_now() - heartbeat_at).total_seconds()) if heartbeat_at is not None else None
        heartbeat_fresh = heartbeat_age_seconds is not None and heartbeat_age_seconds <= self.settings.max_worker_heartbeat_age_seconds
        return {
            "algorithmId": "wca",
            "runtimeVersion": WCA_RUNTIME_SUPERVISOR_VERSION,
            "processModel": "external_os_process",
            "requiredProcessCommand": "python -m backend.app.algorithms.wca.runtime_main",
            "supervisorStarted": self._started,
            "ownerId": self.owner_id,
            "workers": WCA_RUNTIME_WORKERS,
            "commandConsumers": {command.value: worker for command, worker in WCA_RUNTIME_COMMAND_CONSUMERS.items()},
            "retryPolicy": WCA_RUNTIME_COMMAND_RETRY_POLICY,
            "queueDepths": self.runtime_repository.queue_depths(),
            "heartbeatAt": heartbeat_at.isoformat() if heartbeat_at is not None else None,
            "heartbeatAgeSeconds": heartbeat_age_seconds,
            "heartbeatFresh": heartbeat_fresh,
            "health": health.model_dump(mode="json") if health is not None else None,
            "runtimeControl": self.runtime_control(),
            "reasonCodes": (
                "wca.runtime.supervisor.started"
                if self._started
                else "wca.runtime.supervisor.stopped"
            ),
        }

    def runtime_control(self) -> dict[str, Any]:
        return self.repository.read_runtime_control(
            broker_account_id=self.settings.account_id,
            symbol=self.settings.symbol,
        ).api_dict()

    def resolve_runtime_control(
        self,
        *,
        broker_account_id: str | None = None,
        symbol: str | None = None,
        event: WcaFinalizedBarEvent | None = None,
        state: WcaAuthoritativeRuntimeState | None = None,
        configuration: Any | None = None,
        weights: Any | None = None,
        calibration_count: int | None = None,
        health: WcaRuntimeHealthSnapshot | None = None,
        updated_by: str | None = None,
        reason: str | None = None,
        reason_codes: tuple[str, ...] = (),
    ) -> WcaRuntimeControl:
        account_id = broker_account_id or self.settings.account_id
        control_symbol = (symbol or self.settings.symbol).upper()
        prior = self.repository.read_runtime_control(
            broker_account_id=account_id,
            symbol=control_symbol,
        )
        evidence = _runtime_control_evidence(
            self,
            prior=prior,
            event=event,
            state=state,
            configuration=configuration,
            weights=weights,
            calibration_count=calibration_count,
            health=health,
        )
        resolved = resolve_wca_effective_runtime_control(
            prior,
            evidence,
            updated_by=updated_by,
            reason=reason,
            reason_codes=reason_codes,
        )
        return self.repository.write_runtime_control(resolved)

    async def _run_async_loop(self) -> None:
        stop_event = self._stop_event
        if stop_event is None:
            return
        while not stop_event.is_set():
            try:
                await asyncio.to_thread(self.run_once)
            except Exception as exc:
                self.runtime_repository.write_runtime_health(
                    self.health_snapshot(
                        paused_new_entries=True,
                        reason_codes=("wca.runtime.supervisor.worker_iteration_failed", type(exc).__name__),
                    )
                )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=max(0.1, self.settings.poll_seconds))
            except TimeoutError:
                pass

    def publish_finalized_bar_event(self, event: WcaFinalizedBarEvent):
        return self.runtime_repository.publish_finalized_bar_event(
            event,
            account_id=self.settings.account_id,
            max_queue_depth=self.settings.max_event_queue_depth,
            max_event_age_seconds=self.settings.max_event_age_seconds,
        )

    def run_once(self) -> dict[str, Any]:
        results: dict[str, Any] = {"runtimeVersion": WCA_RUNTIME_SUPERVISOR_VERSION, "ownerId": self.owner_id, "workers": {}}
        if not self.runtime_repository.acquire_symbol_lease(
            symbol=self.settings.symbol,
            owner_id=self.owner_id,
            ttl_seconds=self.settings.lease_seconds,
            account_id=self.settings.account_id,
        ):
            reasons = ("wca.runtime.symbol_lease_unavailable", "wca.runtime.single_active_owner_required")
            self.runtime_repository.write_runtime_health(self.health_snapshot(paused_new_entries=True, reason_codes=reasons))
            results["workers"]["runtime_scheduler_worker"] = {"status": "blocked", "reasonCodes": list(reasons)}
            return results
        for worker in self.workers:
            results["workers"][worker.worker_name] = worker.run_once()
        return results

    def run_forever(self, *, max_iterations: int | None = None) -> None:
        iterations = 0
        while max_iterations is None or iterations < max_iterations:
            self.run_once()
            iterations += 1
            time.sleep(self.settings.poll_seconds)

    def health_snapshot(self, *, paused_new_entries: bool, reason_codes: tuple[str, ...]) -> WcaRuntimeHealthSnapshot:
        prior = self.runtime_repository.read_latest_runtime_health()
        prior_critical = _prior_critical_health_reasons(prior)
        prior_pause = prior is not None and prior.paused_new_entries
        prior_pause = prior is not None and prior.paused_new_entries and not _clean_reconciliation_clears_prior_pause(prior, reason_codes)
        if (prior_critical or prior_pause) and not paused_new_entries and all(code in {"wca.runtime.healthy", "wca.runtime.broker_reconciliation.clean"} or code.startswith("wca.broker_reconciliation.") for code in reason_codes):
            paused_new_entries = True
            reason_codes = prior.reason_codes if prior is not None else prior_critical
        depths = self.runtime_repository.queue_depths()
        ages = self.runtime_repository.queue_ages()
        last_bar = self.runtime_repository.last_processed_bar(symbol=self.settings.symbol)
        lag_seconds = max(0.0, (_utc_now() - last_bar).total_seconds()) if last_bar else 0.0
        return healthy_runtime_snapshot(
            queue_depth=depths["events"],
            command_depth=depths["commands"],
            max_queue_age_seconds=ages["maximum"],
            last_processed_bar=last_bar,
            lag_seconds=lag_seconds,
            last_decision_id=self.runtime_repository.last_decision_id(),
            recovery_state=self.recovery_state,
            paused_new_entries=paused_new_entries,
            reason_codes=reason_codes,
            latency_summary=self.runtime_repository.read_latency_summaries(account_id=self.settings.account_id, symbol=self.settings.symbol),
        )


class RuntimeWorker:
    worker_name = "runtime_worker"

    def __init__(self, supervisor: WcaRuntimeSupervisor) -> None:
        self.supervisor = supervisor

    @property
    def runtime_repository(self) -> WcaRuntimeRepository:
        return self.supervisor.runtime_repository

    @property
    def repository(self) -> WcaSqliteRepository:
        return self.supervisor.repository


class RuntimeSchedulerWorker(RuntimeWorker):
    worker_name = "runtime_scheduler_worker"

    def run_once(self) -> dict[str, Any]:
        now = _utc_now()
        scheduled: list[dict[str, Any]] = []
        scheduled.extend(self._schedule_startup_commands(now))
        scheduled.extend(
            self._schedule_periodic_command(
                WcaRuntimeCommandType.HEARTBEAT,
                marker="runtime_heartbeat",
                interval_seconds=self.supervisor.settings.heartbeat_interval_seconds,
                priority=1,
                now=now,
                reason_codes=("wca.runtime.scheduler.heartbeat_scheduled",),
            )
        )
        scheduled.extend(
            self._schedule_periodic_command(
                WcaRuntimeCommandType.RECOVERY,
                marker="stale_work_recovery",
                interval_seconds=self.supervisor.settings.recovery_interval_seconds,
                priority=2,
                now=now,
                reason_codes=("wca.runtime.scheduler.recovery_scheduled",),
            )
        )
        scheduled.extend(
            self._schedule_periodic_command(
                WcaRuntimeCommandType.BROKER_RECONCILIATION,
                marker="periodic_broker_snapshot_and_reconciliation",
                interval_seconds=self.supervisor.settings.broker_snapshot_interval_seconds,
                priority=5,
                now=now,
                reason_codes=("wca.runtime.scheduler.broker_snapshot_reconciliation_scheduled",),
            )
        )
        readiness_checked = self._refresh_market_open_readiness(now)
        cutoff_checked, end_of_session = self._process_entry_cutoff_and_end_of_session(now)
        if end_of_session is not None:
            scheduled.append(end_of_session)
        return {
            "status": "completed",
            "scheduled": scheduled,
            "scheduledTypes": [item["commandType"] for item in scheduled if item.get("accepted")],
            "marketReadinessChecked": readiness_checked,
            "entryCutoffChecked": cutoff_checked,
            "reasonCodes": ["wca.runtime.scheduler.completed"],
        }

    def _schedule_startup_commands(self, now: datetime) -> list[dict[str, Any]]:
        if self.supervisor._startup_scheduled:
            return []
        self.supervisor._startup_scheduled = True
        scheduled: list[dict[str, Any]] = []
        for command_type, marker, priority, reasons in (
            (
                WcaRuntimeCommandType.RECOVERY,
                "startup_stale_work_recovery",
                1,
                ("wca.runtime.scheduler.startup_recovery_scheduled",),
            ),
            (
                WcaRuntimeCommandType.BROKER_RECONCILIATION,
                "startup_reconciliation",
                1,
                ("wca.runtime.scheduler.startup_reconciliation_scheduled",),
            ),
            (
                WcaRuntimeCommandType.HEARTBEAT,
                "startup_heartbeat",
                1,
                ("wca.runtime.scheduler.startup_heartbeat_scheduled",),
            ),
        ):
            scheduled.append(
                self._enqueue_scheduled_command(
                    command_type,
                    marker=marker,
                    command_id=f"wca-cmd-{command_type.value}-{self._scope()}-{marker}-{self.supervisor.owner_id}",
                    priority=priority,
                    now=now,
                    reason_codes=reasons,
                    payload={"scheduler": "startup", "scheduled_at": now.isoformat(), "marker": marker},
                )
            )
        return scheduled

    def _schedule_periodic_command(
        self,
        command_type: WcaRuntimeCommandType,
        *,
        marker: str,
        interval_seconds: int,
        priority: int,
        now: datetime,
        reason_codes: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        bucket = int(now.timestamp()) // max(1, interval_seconds)
        command_id = f"wca-cmd-{command_type.value}-{self._scope()}-{marker}-{bucket}"
        result = self._enqueue_scheduled_command(
            command_type,
            marker=marker,
            command_id=command_id,
            priority=priority,
            now=now,
            reason_codes=reason_codes,
            payload={
                "scheduler": "periodic",
                "scheduled_at": now.isoformat(),
                "marker": marker,
                "interval_seconds": interval_seconds,
                "bucket": bucket,
            },
        )
        return [result]

    def _refresh_market_open_readiness(self, now: datetime) -> bool:
        prior = self.supervisor._last_readiness_check_at
        if prior is not None and (now - prior).total_seconds() < self.supervisor.settings.market_readiness_interval_seconds:
            return False
        self.supervisor._last_readiness_check_at = now
        self.supervisor.resolve_runtime_control(
            updated_by="wca.runtime.scheduler",
            reason="wca.runtime.scheduler.market_open_readiness_check",
            reason_codes=("wca.runtime.scheduler.market_open_readiness_check",),
        )
        return True

    def _process_entry_cutoff_and_end_of_session(self, now: datetime) -> tuple[bool, dict[str, Any] | None]:
        prior = self.supervisor._last_entry_cutoff_check_at
        if prior is not None and (now - prior).total_seconds() < self.supervisor.settings.entry_cutoff_interval_seconds:
            return False, None
        self.supervisor._last_entry_cutoff_check_at = now
        calendar = WcaMarketCalendar()
        session = calendar.session_for(now)
        configuration = self.repository.read_active_configuration()
        cutoff_minutes = configuration.execution.entry_cutoff_minutes if configuration is not None else 15 * 60 + 30
        if session is None:
            local_now = now
            session_date = now.date().isoformat()
            entry_cutoff_reached = True
        else:
            local_now = now.astimezone(session.market_close.tzinfo)
            session_date = session.session_date.isoformat()
            entry_cutoff_reached = (local_now.hour * 60 + local_now.minute) >= cutoff_minutes
        flatten_due = session is None or calendar.should_flatten(
            now,
            buffer_minutes=self.supervisor.settings.end_of_session_flatten_buffer_minutes,
        )
        if entry_cutoff_reached:
            self.supervisor.resolve_runtime_control(
                updated_by="wca.runtime.scheduler",
                reason="wca.runtime.scheduler.entry_cutoff_reached",
                reason_codes=("wca.runtime.scheduler.entry_cutoff_reached",),
            )
        if not flatten_due:
            return True, None
        bucket = int(now.timestamp()) // max(60, self.supervisor.settings.entry_cutoff_interval_seconds)
        marker = "calendar_end_of_session"
        return True, self._enqueue_scheduled_command(
            WcaRuntimeCommandType.END_OF_SESSION,
            marker=marker,
            command_id=f"wca-cmd-end-of-session-{self._scope()}-{session_date}-{bucket}",
            priority=1,
            now=now,
            reason_codes=("wca.runtime.scheduler.end_of_session_scheduled",),
            payload={
                "scheduler": "exchange_calendar",
                "marker": marker,
                "evaluated_at": now.isoformat(),
                "session_date": session_date,
                "entry_cutoff_reached": entry_cutoff_reached,
                "flatten_due": flatten_due,
                "end_of_session_actions": (
                    "cancel_unfilled_entries",
                    "flatten_wca_exposure",
                    "final_reconciliation",
                ),
            },
        )

    def _enqueue_scheduled_command(
        self,
        command_type: WcaRuntimeCommandType,
        *,
        marker: str,
        command_id: str,
        priority: int,
        now: datetime,
        reason_codes: tuple[str, ...],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        command = runtime_command(
            command_type,
            command_id=command_id,
            account_id=self.supervisor.settings.account_id,
            symbol=self.supervisor.settings.symbol,
            payload=payload,
            priority=priority,
            deadline_seconds=max(60, self.supervisor.settings.max_queue_delay_seconds * 3),
            reason_codes=reason_codes,
        )
        result = self.runtime_repository.enqueue_command(
            command,
            max_queue_depth=self.supervisor.settings.max_command_queue_depth,
        )
        return {
            "commandType": command_type.value,
            "commandId": command.command_id,
            "marker": marker,
            "accepted": result.accepted,
            "status": result.status,
            "reasonCodes": list(result.reason_codes),
        }

    def _scope(self) -> str:
        return f"{self.supervisor.settings.account_id}-{self.supervisor.settings.symbol.lower()}"


class FinalizedBarConsumerWorker(RuntimeWorker):
    worker_name = "finalised_bar_consumer"

    def run_once(self) -> dict[str, Any]:
        event = self.runtime_repository.claim_next_event(owner_id=self.supervisor.owner_id, lease_seconds=self.supervisor.settings.lease_seconds)
        if event is None:
            return {"status": "idle", "reasonCodes": ["wca.runtime.finalized_bar_consumer.idle"]}
        command = WcaRuntimeCommand.from_finalized_bar_event(event, account_id=self.supervisor.settings.account_id)
        result = self.runtime_repository.enqueue_command(command, max_queue_depth=self.supervisor.settings.max_command_queue_depth)
        if result.accepted:
            self.runtime_repository.mark_event_decision_queued(event.event_id, command_id=command.command_id)
        return {"status": result.status, "eventId": event.event_id, "commandId": command.command_id, "reasonCodes": list(result.reason_codes)}


class DecisionWorker(RuntimeWorker):
    worker_name = "decision_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(WcaRuntimeCommandType.FINALIZED_BAR_DECISION, owner_id=self.supervisor.owner_id, lease_seconds=self.supervisor.settings.lease_seconds)
        if command is None:
            return {"status": "idle", "reasonCodes": ["wca.runtime.decision_worker.idle"]}
        if _command_deadline_expired(command):
            reasons = ("wca.runtime.decision_worker.command_deadline_expired",)
            self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": list(reasons)}
        event = WcaFinalizedBarEvent.model_validate(command.payload["event"])
        if not self.runtime_repository.acquire_symbol_lease(symbol=event.symbol, owner_id=self.supervisor.owner_id, ttl_seconds=self.supervisor.settings.lease_seconds):
            self.runtime_repository.block_command(command.command_id, reason_codes=("wca.runtime.symbol_lease_unavailable",))
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": ["wca.runtime.symbol_lease_unavailable"]}
        try:
            result = self._process_decision(command, event)
            self.runtime_repository.complete_command(command.command_id)
            return result
        except Exception as exc:
            self.runtime_repository.fail_command(command.command_id, reason_codes=("wca.runtime.decision_worker.failed", type(exc).__name__))
            raise
        finally:
            self.runtime_repository.release_symbol_lease(symbol=event.symbol, owner_id=self.supervisor.owner_id)

    def _process_decision(self, command: WcaRuntimeCommand, event: WcaFinalizedBarEvent) -> dict[str, Any]:
        configuration = self.repository.read_active_configuration()
        weights = self.repository.read_active_weights(as_of=event.finalized_candle_timestamp)
        if configuration is None or weights is None:
            reasons = (
                "wca.runtime.fail_closed.configuration_or_weights_missing",
                "wca.runtime.health.configuration_ready" if configuration is None else "wca.runtime.health.weight_calibration_ready",
            )
            self.runtime_repository.block_command(command.command_id, reason_codes=tuple(dict.fromkeys(reasons)))
            self.runtime_repository.enqueue_command(
                runtime_command(WcaRuntimeCommandType.POSITION_PROTECTIVE_EXIT, event_id=event.event_id, decision_id=command.decision_id, run_id=command.run_id, reason_codes=("wca.runtime.protective_management.continues",)),
                max_queue_depth=self.supervisor.settings.max_command_queue_depth,
            )
            self.runtime_repository.write_runtime_health(WcaRuntimeHealthSnapshot(status="starting_fail_closed", paused_new_entries=True, protective_management_active=True, configuration_ready=configuration is not None, weight_calibration_ready=weights is not None, reason_codes=tuple(dict.fromkeys(reasons))))
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": ["wca.runtime.fail_closed.configuration_or_weights_missing"]}
        if event.snapshot is None:
            self.runtime_repository.block_command(command.command_id, reason_codes=("wca.runtime.snapshot_missing",))
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": ["wca.runtime.snapshot_missing"]}
        state = load_wca_authoritative_runtime_state(
            self.repository,
            broker_account_id=self.supervisor.settings.account_id,
            symbol=event.symbol,
            state_timestamp=event.finalized_candle_timestamp,
            maximum_permitted_state_age_seconds=int(self.supervisor.settings.max_authoritative_account_state_age_seconds or self.supervisor.settings.max_state_age_seconds),
        )
        if not state.fresh or not state.account_wide_entry_permission:
            control = self.supervisor.resolve_runtime_control(
                broker_account_id=command.account_id,
                symbol=event.symbol,
                event=event,
                state=state,
                configuration=configuration,
                weights=weights,
                calibration_count=len(self.repository.read_active_confidence_calibrations(symbol=event.symbol, as_of=event.finalized_candle_timestamp)),
                health=self.supervisor.health_snapshot(
                    paused_new_entries=True,
                    reason_codes=("wca.runtime.fail_closed.authoritative_state_blocked", *state.reason_codes),
                ),
                reason_codes=("wca.runtime_control.authoritative_state_blocked",),
            )
            decision = self._hold_decision(command, event, state, configuration_hash=configuration.content_hash, weight_version=weights.weight_version)
            decision = _stamp_decision_with_runtime_control(decision, control)
            self.repository.write_decision_snapshot(decision, run_id=command.run_id)
            self.runtime_repository.complete_event_and_checkpoint(event, decision_id=decision.decision_id, run_id=command.run_id)
            self._enqueue_downstream_commands(event, command, decision)
            self.runtime_repository.write_runtime_health(
                self.supervisor.health_snapshot(
                    paused_new_entries=True,
                    reason_codes=("wca.runtime.fail_closed.authoritative_state_blocked", *state.reason_codes),
                )
            )
            return {
                "status": "blocked",
                "commandId": command.command_id,
                "decisionId": decision.decision_id,
                "authoritativeStateHash": state.state_hash,
                "reasonCodes": ["wca.runtime.fail_closed.authoritative_state_blocked", *state.reason_codes],
            }
        entry_health = _evaluate_entry_health(
            self.supervisor,
            event=event,
            state=state,
            configuration_ready=True,
            weight_calibration_ready=True,
        )
        health_block_reasons = critical_health_reason_codes(entry_health)
        if self.repository.reconciliation_blocks_new_entries(account_id=self.supervisor.settings.account_id, symbol=event.symbol):
            self.runtime_repository.block_command(command.command_id, reason_codes=("wca.runtime.fail_closed.reconciliation_blocks_entries",))
            self.runtime_repository.enqueue_command(
                runtime_command(WcaRuntimeCommandType.POSITION_PROTECTIVE_EXIT, event_id=event.event_id, decision_id=command.decision_id, run_id=command.run_id, reason_codes=("wca.runtime.protective_management.continues",)),
                max_queue_depth=self.supervisor.settings.max_command_queue_depth,
            )
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": ["wca.runtime.fail_closed.reconciliation_blocks_entries"]}
        lag_seconds = max(0.0, (_utc_now() - event.finalized_candle_timestamp.astimezone(timezone.utc)).total_seconds())
        freshness_reasons = _entry_freshness_reason_codes(self.supervisor, event=event, state=state, lag_seconds=lag_seconds)
        calibrations = self.repository.read_active_confidence_calibrations(symbol=event.symbol, as_of=event.finalized_candle_timestamp)
        control = self.supervisor.resolve_runtime_control(
            broker_account_id=command.account_id,
            symbol=event.symbol,
            event=event,
            state=state,
            configuration=configuration,
            weights=weights,
            calibration_count=len(calibrations),
            health=entry_health,
            reason_codes=("wca.runtime_control.finalized_bar_resolved",),
        )
        control_block_reasons = () if control.effective_automatic_entries_enabled else control.reason_codes
        pause_new_entries = bool(freshness_reasons or health_block_reasons or control_block_reasons)
        entry_block_reasons = tuple(dict.fromkeys((*freshness_reasons, *health_block_reasons, *control_block_reasons)))
        now = _utc_now()
        pipeline_runtime_mode = _runtime_mode_for_rollout_control(control, self.supervisor.settings.runtime_mode)
        pipeline_configuration = configuration.for_runtime_mode(pipeline_runtime_mode)
        pipeline_result = run_wca_paper_pipeline_adapter(
            WcaExecutionPipelineInput(
                run_id=command.run_id,
                decision_id=command.decision_id,
                order_intent_id=f"wca-intent-{event.event_id}",
                snapshot=event.snapshot,
                configuration_version=configuration.configuration_version,
                runtime_mode=pipeline_runtime_mode,
                configuration=pipeline_configuration,
                weight_snapshot=weights,
                calibration_tables=calibrations,
                account_id=state.broker_account_id,
                trades_today=_required_int(state.daily_trade_count, "daily_trade_count"),
                open_position=state.to_open_position(),
                realized_daily_loss=_required_float(state.daily_loss, "daily_loss"),
                account_equity=_required_float(state.equity, "equity"),
                available_buying_power=_required_float(state.buying_power, "buying_power"),
                authoritative_account_values=True,
                remaining_allocated_risk_budget=state.remaining_portfolio_risk,
                global_gate_quantity_cap=0 if pause_new_entries else state.maximum_approved_quantity,
                approved_risk_budget=state.remaining_portfolio_risk,
                defer_global_risk_approval=True,
                total_account_exposure_snapshot=state.global_risk.get("riskState", {}),
                current_wca_attributed_exposure=(state.current_quantity or 0) * (state.average_entry_price or 0.0),
                authoritative_state_version=state.state_version,
                authoritative_state_hash=state.state_hash,
                authoritative_state_reason_codes=state.reason_codes,
                latency_timestamps=WcaLatencyTimestamps(
                    candle_open=event.snapshot.candles[-1].timestamp if event.snapshot.candles else event.finalized_candle_timestamp,
                    candle_close=event.finalized_candle_timestamp,
                    bar_finalization=event.finalized_candle_timestamp,
                    event_publication=event.publication_timestamp,
                    event_queue_enqueued=event.publication_timestamp,
                    event_receipt=now,
                    event_claimed=now,
                    decision_start=now,
                    snapshot_construction_start=event.publication_timestamp,
                    snapshot_completion=event.snapshot.decision_timestamp,
                ),
            )
        )
        decision = _stamp_decision_with_runtime_control(pipeline_result.decision, control)
        self.repository.write_decision_snapshot(decision, run_id=command.run_id)
        self.runtime_repository.record_latency_snapshot(decision.latency, account_id=command.account_id, symbol=event.symbol, timestamp=decision.decision_timestamp)
        self.runtime_repository.complete_event_and_checkpoint(event, decision_id=decision.decision_id, run_id=command.run_id)
        self._enqueue_downstream_commands(event, command, decision)
        health = entry_health.model_copy(
            update={
                "status": "protective_only" if pause_new_entries else "healthy",
                "paused_new_entries": pause_new_entries,
                "last_decision_id": decision.decision_id,
                "reason_codes": entry_block_reasons or ("wca.runtime.healthy",),
                "latency_summary": self.runtime_repository.read_latency_summaries(account_id=command.account_id, symbol=event.symbol),
            }
        )
        self.runtime_repository.write_runtime_health(health)
        return {"status": "completed", "commandId": command.command_id, "decisionId": decision.decision_id, "pausedNewEntries": pause_new_entries, "reasonCodes": list(entry_block_reasons or ("wca.runtime.healthy",))}

    def _hold_decision(self, command: WcaRuntimeCommand, event: WcaFinalizedBarEvent, state: WcaAuthoritativeRuntimeState, *, configuration_hash: str, weight_version: str) -> WcaDecision:
        if event.snapshot is None:
            raise ValueError("cannot persist WCA runtime-state HOLD without a market snapshot")
        reason_codes = ("wca.runtime.authoritative_state_unavailable_hold", *state.reason_codes)
        market_status = WcaMarketStatus(
            status=WcaEvaluationStatus.DEGRADED,
            input_timestamp=event.snapshot.data_timestamp,
            reason_codes=reason_codes,
            explanation="WCA automatic decision held because authoritative runtime state was unavailable, stale, inconsistent, or not entry-permitted.",
        )
        hold_gate = WcaLocalGateResult(
            gate_id="authoritative_runtime_state",
            status=WcaGateStatus.FAIL,
            blocks_entry=True,
            reason_code="wca.runtime.authoritative_state_blocked",
            detail="Authoritative runtime state blocks new WCA entries.",
            evaluated_value=state.freshness_result,
            required_value="PASS",
            reason_codes=reason_codes,
            explanation="WCA automatic entries require fresh broker, inventory, daily-state, and reconciliation state.",
        )
        aggregation = WcaAggregationResult(
            signal=WcaSide.HOLD,
            decision_label="HOLD",
            pre_gate_decision=WcaSide.HOLD,
            post_local_gate_decision=WcaSide.HOLD,
            buy_score=0,
            sell_score=0,
            net_score=0,
            active_weight=0,
            normalized_net_score=0,
            active_strategy_count=0,
            winner_edge=0,
            buy_agreement=0,
            sell_agreement=0,
            buy_average_confidence=0,
            sell_average_confidence=0,
            strategy_evaluations=(),
            reason_codes=reason_codes,
        )
        sizing = WcaSizingResult(
            final_quantity=0,
            risk_dollars=0,
            stop_distance=0,
            shares_by_risk=0,
            shares_by_order=0,
            shares_by_capital=0,
            shares_by_buying_power=0,
            shares_by_liquidity=0,
            limiting_factor="authoritative_runtime_state",
            blocked_reason="wca.runtime.authoritative_state_blocked",
            side=WcaSide.HOLD,
            reason_codes=reason_codes,
        )
        gate = GlobalGateResult(
            status=WcaGateStatus.FAIL,
            proposed_quantity=0,
            allowed_quantity=0,
            entry_permitted=False,
            risk_reducing_exit_permitted=state.account_wide_exit_permission,
            reason_codes=reason_codes,
            explanation="Global entry permission is denied until authoritative WCA runtime state is fresh.",
        )
        decision = WcaDecision(
            decision_id=command.decision_id,
            configuration_version=state.wca_configuration_version,
            configuration_hash=configuration_hash,
            weight_version=weight_version,
            data_timestamp=event.snapshot.data_timestamp,
            decision_timestamp=event.snapshot.decision_timestamp,
            market_snapshot=event.snapshot,
            market_status=market_status,
            runtime_mode=self.supervisor.settings.runtime_mode,
            called_module_versions={"authoritative_runtime_state": state.state_version},
            hard_filter_results=(hold_gate,),
            aggregation=aggregation,
            local_gates=(hold_gate,),
            sizing=sizing,
            global_gate_result=gate,
            authoritative_state_version=state.state_version,
            authoritative_state_hash=state.state_hash,
            authoritative_state_reason_codes=state.reason_codes,
            reason_codes=reason_codes,
        )
        return decision.model_copy(update={"decision_hash": decision.deterministic_hash()})

    def _enqueue_downstream_commands(self, event: WcaFinalizedBarEvent, command: WcaRuntimeCommand, decision: WcaDecision) -> None:
        latest_close = decision.market_snapshot.candles[-1].close if decision.market_snapshot.candles else None
        common = {"event_id": event.event_id, "account_id": command.account_id, "decision_id": decision.decision_id, "run_id": command.run_id, "symbol": event.symbol}
        position_payload = {**common, "mark_price": latest_close, "data_timestamp": decision.data_timestamp.isoformat()}
        self.runtime_repository.enqueue_command(
            runtime_command(WcaRuntimeCommandType.POSITION_PROTECTIVE_EXIT, payload=position_payload, **common),
            max_queue_depth=self.supervisor.settings.max_command_queue_depth,
        )
        self.runtime_repository.enqueue_command(
            runtime_command(WcaRuntimeCommandType.BROKER_RECONCILIATION, payload=common, priority=80, **common),
            max_queue_depth=self.supervisor.settings.max_command_queue_depth,
        )
        if decision.proposed_order is not None:
            self.runtime_repository.enqueue_command(
                runtime_command(WcaRuntimeCommandType.GLOBAL_RISK_REQUEST, payload={"decision": decision.model_dump(mode="json"), **common}, priority=30, **common),
                max_queue_depth=self.supervisor.settings.max_command_queue_depth,
            )


class ManualPaperCommandWorker(RuntimeWorker):
    worker_name = "manual_paper_command_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(
            WcaRuntimeCommandType.MANUAL_PAPER_COMMAND,
            owner_id=self.supervisor.owner_id,
            lease_seconds=self.supervisor.settings.lease_seconds,
        )
        if command is None:
            return {"status": "idle", "reasonCodes": ["wca.runtime.manual_paper_worker.idle"]}
        try:
            return self._process(command)
        except Exception as exc:
            reasons = ("wca.runtime.manual_paper_command.failed", type(exc).__name__)
            self.runtime_repository.fail_command(command.command_id, reason_codes=reasons)
            self.runtime_repository.write_runtime_health(self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons))
            return {"status": "failed", "commandId": command.command_id, "reasonCodes": list(reasons)}

    def _process(self, command: WcaRuntimeCommand) -> dict[str, Any]:
        request_payload = command.payload.get("request")
        if not isinstance(request_payload, dict):
            reasons = ("wca.runtime.manual_paper_command.missing_request",)
            self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": list(reasons)}
        request = WcaPaperExecutionRequest.model_validate(request_payload)
        if request.mode == "automatic":
            reasons = (
                "wca.runtime.manual_paper_command.automatic_api_payload_blocked",
                "wca.runtime.automatic_entries_only_from_finalized_bar_worker",
            )
            self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": list(reasons)}
        timestamp = request.candles[-1].timestamp.astimezone(timezone.utc)
        state = load_wca_authoritative_runtime_state(
            self.repository,
            broker_account_id=command.account_id,
            symbol=command.symbol,
            state_timestamp=timestamp,
            maximum_permitted_state_age_seconds=int(self.supervisor.settings.max_authoritative_account_state_age_seconds or self.supervisor.settings.max_state_age_seconds),
        )
        if not state.fresh:
            reasons = ("wca.runtime.manual_paper_command.authoritative_state_unavailable", *state.reason_codes)
            self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
            self.runtime_repository.write_runtime_health(self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons))
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": list(reasons)}
        hydrated = request.model_copy(
            update={
                "mode": "manual",
                "run_id": command.run_id or request.run_id,
                "account_id": command.account_id,
                "symbol": command.symbol,
                "account_equity": _required_float(state.equity, "equity"),
                "available_buying_power": _required_float(state.buying_power, "buying_power"),
                "global_gate_quantity_cap": state.maximum_approved_quantity,
                "approved_risk_budget": state.remaining_portfolio_risk,
                "remaining_allocated_risk_budget": state.remaining_portfolio_risk,
                "trades_today": _required_int(state.daily_trade_count, "daily_trade_count"),
                "realized_daily_loss": _required_float(state.daily_loss, "daily_loss"),
                "current_position_quantity": abs(int(state.current_quantity or 0)),
                "current_position_side": WcaSide.BUY if (state.current_quantity or 0) > 0 else (WcaSide.SELL if (state.current_quantity or 0) < 0 else None),
                "current_position_entry_price": state.average_entry_price if state.average_entry_price and state.current_quantity else None,
            }
        )
        from backend.app.algorithms.wca.service import WcaService

        service = WcaService(repository=self.repository)
        result = service.execute_manual_paper(hydrated)
        reasons = ("wca.runtime.manual_paper_command.completed", *result.reason_codes)
        self.runtime_repository.complete_command(command.command_id, reason_codes=reasons)
        self.runtime_repository.write_runtime_health(
            self.supervisor.health_snapshot(
                paused_new_entries=True,
                reason_codes=("wca.runtime.manual_paper_command.entries_remain_backend_controlled",),
            )
        )
        return {
            "status": "completed",
            "commandId": command.command_id,
            "actionStatus": result.action_status,
            "submitted": result.submitted,
            "decisionId": result.decision.decision_id,
            "orderIntentId": result.proposed_order.order_intent_id if result.proposed_order is not None else None,
            "reasonCodes": list(reasons),
        }


class PositionProtectiveExitWorker(RuntimeWorker):
    worker_name = "position_and_protective_exit_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(WcaRuntimeCommandType.POSITION_PROTECTIVE_EXIT, owner_id=self.supervisor.owner_id)
        if command is None:
            return {"status": "idle", "reasonCodes": ["wca.runtime.protective_exit_worker.idle"]}
        mark_price = command.payload.get("mark_price")
        if mark_price is None:
            self.runtime_repository.complete_command(command.command_id, reason_codes=("wca.runtime.protective_exit_management.no_mark_price",))
            return {"status": "completed", "commandId": command.command_id, "reasonCodes": ["wca.runtime.protective_exit_management.no_mark_price"]}
        position = manage_wca_position(
            repository=self.repository,
            account_id=command.account_id,
            symbol=command.symbol,
            mark_price=float(mark_price),
            evaluated_at=_utc_now(),
            emergency_exit=bool(command.payload.get("emergency_exit", False)),
            global_emergency_risk_reduction=bool(command.payload.get("global_emergency_risk_reduction", False)),
        )
        self.runtime_repository.complete_command(command.command_id, reason_codes=("wca.runtime.protective_exit_management.completed", *position.reason_codes))
        return {
            "status": "completed",
            "commandId": command.command_id,
            "openQuantity": position.open_quantity,
            "pendingExitOrders": len(position.pending_exit_orders),
            "circuitBreakerOpen": position.circuit_breaker_open,
            "reasonCodes": ["wca.runtime.protective_exit_management.completed", *position.reason_codes],
        }


class GlobalRiskRequestWorker(RuntimeWorker):
    worker_name = "global_risk_request_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(
            WcaRuntimeCommandType.GLOBAL_RISK_REQUEST,
            owner_id=self.supervisor.owner_id,
            lease_seconds=self.supervisor.settings.lease_seconds,
        )
        if command is None:
            return {"status": "idle", "reasonCodes": ["wca.runtime.global_risk_worker.idle"]}
        try:
            return self._process(command)
        except Exception as exc:
            reasons = ("wca.runtime.global_risk_worker.failed", type(exc).__name__)
            self.runtime_repository.fail_command(command.command_id, reason_codes=reasons)
            self.runtime_repository.write_runtime_health(self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons))
            return {"status": "failed", "commandId": command.command_id, "reasonCodes": list(reasons)}

    def _process(self, command: WcaRuntimeCommand) -> dict[str, Any]:
        if _command_deadline_expired(command):
            reasons = ("wca.runtime.global_risk_request.command_deadline_expired",)
            self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": list(reasons)}
        decision_payload = command.payload.get("decision")
        if not isinstance(decision_payload, dict):
            reasons = ("wca.runtime.global_risk_request.missing_decision",)
            self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": list(reasons)}
        decision = WcaDecision.model_validate(decision_payload)
        if decision.proposed_order is None or decision.proposed_order.quantity <= 0:
            reasons = ("wca.runtime.global_risk_request.no_entry_order",)
            self.runtime_repository.complete_command(command.command_id, reason_codes=reasons)
            return {"status": "completed", "commandId": command.command_id, "reasonCodes": list(reasons)}
        state = load_wca_authoritative_runtime_state(
            self.repository,
            broker_account_id=command.account_id,
            symbol=command.symbol,
            state_timestamp=decision.decision_timestamp,
            maximum_permitted_state_age_seconds=int(self.supervisor.settings.max_authoritative_account_state_age_seconds or self.supervisor.settings.max_state_age_seconds),
        )
        if not state.fresh or not state.account_wide_entry_permission:
            reasons = ("wca.runtime.global_risk_request.authoritative_state_blocked", *state.reason_codes)
            self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
            self.runtime_repository.write_runtime_health(self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons))
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": list(reasons)}
        now = _utc_now()
        requested_quantity = int(decision.proposed_order.quantity)
        stop_distance = float(decision.sizing.stop_distance)
        requested_risk = requested_quantity * stop_distance
        exposure_snapshot = dict(state.global_risk.get("riskState", {}))
        exposure_snapshot.setdefault("global_gate_quantity_cap", state.maximum_approved_quantity)
        if state.remaining_portfolio_risk is not None:
            exposure_snapshot.setdefault("approved_risk_budget", state.remaining_portfolio_risk)
        global_state_hash = _global_state_hash(state)
        proposal = build_wca_global_risk_proposal(
            account_id=command.account_id,
            symbol=command.symbol,
            side=decision.proposed_order.side,
            requested_quantity=requested_quantity,
            requested_risk=requested_risk,
            stop_distance=stop_distance,
            expected_holding_period_seconds=3600,
            current_wca_attributed_exposure=(state.current_quantity or 0) * (state.average_entry_price or 0.0),
            total_account_exposure_snapshot=exposure_snapshot,
            configuration_version=decision.configuration_version,
            configuration_hash=decision.configuration_hash,
            decision_id=decision.decision_id,
            idempotency_key=decision.proposed_order.idempotency_key or f"wca-global-risk-{decision.proposed_order.order_intent_id}",
            risk_reducing_exit=False,
        )
        risk_decision = self.supervisor.global_risk_client.evaluate_wca_proposal(proposal)
        allowed_quantity = min(requested_quantity, int(risk_decision.approved_quantity))
        gate_status = WcaGateStatus.FAIL
        if risk_decision.entry_permitted and allowed_quantity == requested_quantity:
            gate_status = WcaGateStatus.PASS
        elif risk_decision.entry_permitted and allowed_quantity > 0:
            gate_status = WcaGateStatus.WARN
        expires_at = now + timedelta(seconds=int(self.supervisor.settings.max_state_age_seconds))
        gate = GlobalGateResult(
            status=gate_status,
            proposed_quantity=requested_quantity,
            allowed_quantity=allowed_quantity,
            requested_risk=requested_risk,
            approved_risk=float(risk_decision.approved_risk),
            entry_permitted=bool(risk_decision.entry_permitted and allowed_quantity > 0),
            risk_reducing_exit_permitted=risk_decision.risk_reducing_exit_permitted,
            idempotency_key=risk_decision.idempotency_key,
            global_risk_decision_id=f"wca-global-risk-{decision.decision_id}-{now.strftime('%Y%m%d%H%M%S')}",
            evaluated_at=now,
            expires_at=expires_at,
            global_state_hash=global_state_hash,
            global_state_revision=state.inventory_state_version,
            reason_codes=_global_risk_worker_reason_codes(risk_decision.reason_codes, allowed_quantity, requested_quantity),
            explanation=risk_decision.explanation,
        )
        approved_decision = _apply_durable_global_risk_approval(decision, gate, account_id=command.account_id)
        self.repository.write_decision_snapshot(approved_decision, run_id=command.run_id)
        if approved_decision.proposed_order is None:
            reasons = ("wca.runtime.global_risk_request.rejected", *gate.reason_codes)
            self.runtime_repository.complete_command(command.command_id, reason_codes=reasons)
            return {"status": "completed", "commandId": command.command_id, "decisionId": decision.decision_id, "entryPermitted": False, "reasonCodes": list(reasons)}
        self.runtime_repository.enqueue_command(
            runtime_command(
                WcaRuntimeCommandType.EXECUTION_OUTBOX,
                payload={"decision": approved_decision.model_dump(mode="json"), "globalRiskApproval": gate.model_dump(mode="json")},
                event_id=command.event_id,
                account_id=command.account_id,
                symbol=command.symbol,
                decision_id=approved_decision.decision_id,
                run_id=command.run_id,
                priority=20,
                deadline_seconds=max(1, int((command.deadline_at - _utc_now()).total_seconds())),
                reason_codes=("wca.runtime.global_risk_request.approval_persisted",),
            ),
            max_queue_depth=self.supervisor.settings.max_command_queue_depth,
        )
        reasons = ("wca.runtime.global_risk_request.completed", *gate.reason_codes)
        self.runtime_repository.complete_command(command.command_id, reason_codes=reasons)
        return {
            "status": "completed",
            "commandId": command.command_id,
            "decisionId": approved_decision.decision_id,
            "globalRiskDecisionId": gate.global_risk_decision_id,
            "allowedQuantity": gate.allowed_quantity,
            "reasonCodes": list(reasons),
        }


class ExecutionOutboxWorker(RuntimeWorker):
    worker_name = "execution_outbox_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(WcaRuntimeCommandType.EXECUTION_OUTBOX, owner_id=self.supervisor.owner_id)
        if command is None:
            return {"status": "idle", "reasonCodes": ["wca.runtime.execution_outbox_worker.idle"]}
        if _command_deadline_expired(command):
            reasons = ("wca.runtime.execution_outbox.command_deadline_expired",)
            self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
            return {"status": "blocked", "commandId": command.command_id, "submitted": False, "reasonCodes": list(reasons)}
        decision = WcaDecision.model_validate(command.payload["decision"])
        if decision.proposed_order is None:
            self.runtime_repository.block_command(command.command_id, reason_codes=("wca.runtime.execution_outbox.no_order",))
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": ["wca.runtime.execution_outbox.no_order"]}
        current_control = self.repository.read_runtime_control(broker_account_id=command.account_id, symbol=command.symbol)
        control_block_reasons = _runtime_control_submission_block_reasons(decision, current_control)
        if control_block_reasons and not _is_risk_reducing_exit(decision):
            self.runtime_repository.block_command(command.command_id, reason_codes=control_block_reasons)
            return {
                "status": "blocked",
                "commandId": command.command_id,
                "submitted": False,
                "runtimeControl": current_control.api_dict(),
                "reasonCodes": list(control_block_reasons),
            }
        approval_reasons, approval_state = _global_risk_submission_block_reasons(self.supervisor, command, decision)
        if approval_reasons and not _is_risk_reducing_exit(decision):
            self.runtime_repository.block_command(command.command_id, reason_codes=approval_reasons)
            self.runtime_repository.write_runtime_health(self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=approval_reasons))
            return {
                "status": "blocked",
                "commandId": command.command_id,
                "submitted": False,
                "reasonCodes": list(approval_reasons),
            }
        global_risk_approval = self.repository.read_global_risk_approval(decision_id=decision.decision_id)
        idempotency_key = f"wca-runtime-outbox-{decision.proposed_order.order_intent_id}"
        proposed = decision.proposed_order.model_copy(update={"idempotency_key": decision.proposed_order.idempotency_key or idempotency_key, "account_id": command.account_id})
        decision = decision.model_copy(update={"proposed_order": proposed})
        request = build_wca_paper_broker_request(proposed)
        paper_account = validate_wca_automatic_paper_account(account_id=command.account_id)
        if not paper_account.verified:
            self.runtime_repository.block_command(command.command_id, reason_codes=("wca.runtime.execution_outbox.automatic_paper_account_blocked", *paper_account.reason_codes))
            self.runtime_repository.write_runtime_health(
                self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=("wca.runtime.execution_outbox.automatic_paper_account_blocked", *paper_account.reason_codes))
            )
            return {
                "status": "blocked",
                "commandId": command.command_id,
                "submitted": False,
                "reasonCodes": ["wca.runtime.execution_outbox.automatic_paper_account_blocked", *paper_account.reason_codes],
            }
        broker: WcaAlpacaPaperBroker | None = None
        broker_clock: WcaBrokerClock | None = None
        try:
            broker = WcaAlpacaPaperBroker.from_env(account_id=command.account_id)
            verified, broker_reason_codes = broker.verify_account_and_endpoint_identity()
            if not verified:
                reasons = ("wca.runtime.execution_outbox.alpaca_paper_broker_blocked", *broker_reason_codes)
                self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
                self.runtime_repository.write_runtime_health(
                    self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons)
                )
                return {
                    "status": "blocked",
                    "commandId": command.command_id,
                    "submitted": False,
                    "reasonCodes": list(reasons),
                }
            broker_clock = broker.read_clock()
        except WcaAlpacaPaperBrokerConfigurationError as exc:
            reason_text = str(exc)
            broker_reason_codes = tuple(code for code in reason_text.split(";") if code)
            reasons = ("wca.runtime.execution_outbox.alpaca_paper_broker_blocked", *broker_reason_codes)
            self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
            self.runtime_repository.write_runtime_health(
                self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons)
            )
            if broker is not None:
                broker.close()
            return {
                "status": "blocked",
                "commandId": command.command_id,
                "submitted": False,
                "reasonCodes": list(reasons),
            }
        except Exception as exc:
            reasons = ("wca.runtime.execution_outbox.alpaca_paper_clock_blocked", type(exc).__name__)
            self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
            self.runtime_repository.write_runtime_health(
                self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons)
            )
            if broker is not None:
                broker.close()
            return {
                "status": "blocked",
                "commandId": command.command_id,
                "submitted": False,
                "reasonCodes": list(reasons),
            }
        runtime_health = self.runtime_repository.read_latest_runtime_health()
        if runtime_health is not None and runtime_health.block_new_entries:
            if broker is not None:
                broker.close()
            reasons = (
                "wca.runtime.execution_outbox.global_paper_control_blocked",
                *runtime_health.reason_codes,
            )
            self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
            self.runtime_repository.write_runtime_health(
                self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons)
            )
            return {
                "status": "blocked",
                "commandId": command.command_id,
                "submitted": False,
                "reasonCodes": list(reasons),
            }
        reservation = self.repository.reserve_decision_order_and_outbox(
            decision,
            run_id=command.run_id,
            account_id=command.account_id,
            idempotency_key=proposed.idempotency_key or idempotency_key,
            client_order_id=request.client_order_id,
            request_payload=request.model_dump(mode="json"),
            final_validation_context=_runtime_order_validation_context(
                command,
                decision,
                request,
                supervisor=self.supervisor,
                runtime_control=current_control,
                broker_clock=broker_clock,
                automatic_paper_enabled=paper_account.verified and current_control.effective_automatic_entries_enabled,
                authoritative_state=approval_state,
            ),
            global_risk_approval=global_risk_approval,
            authoritative_state_hash=approval_state.state_hash if approval_state is not None else decision.authoritative_state_hash,
        )
        current_control = self.repository.read_runtime_control(broker_account_id=command.account_id, symbol=command.symbol)
        control_block_reasons = _runtime_control_submission_block_reasons(decision, current_control)
        if control_block_reasons and not _is_risk_reducing_exit(decision):
            if broker is not None:
                broker.close()
            self.repository.update_execution_outbox_state(
                outbox_id=reservation.outbox_id,
                status=WcaOrderStatus.CANCELLED,
                response_payload={
                    "reason": "wca.runtime_control.revalidation_before_broker_submission_blocked",
                    "controlRevision": current_control.control_revision,
                    "controlHash": current_control.control_hash,
                    "reasonCodes": list(control_block_reasons),
                },
            )
            self.repository.record_order_terminal_inventory_event(
                decision,
                account_id=command.account_id,
                client_order_id=request.client_order_id,
                event_type="ORDER_CANCELLED",
                event_timestamp=_utc_now(),
                payload={"runtime_control": current_control.api_dict(), "reasonCodes": list(control_block_reasons)},
            )
            self.runtime_repository.block_command(command.command_id, reason_codes=control_block_reasons)
            return {
                "status": "blocked",
                "commandId": command.command_id,
                "outboxId": reservation.outbox_id,
                "submitted": False,
                "runtimeControl": current_control.api_dict(),
                "reasonCodes": list(control_block_reasons),
            }
        try:
            submission = WcaPaperBrokerOutboxAdapter().process_next_outbox(
                self.repository,
                broker,
                owner_id=self.supervisor.owner_id,
                pre_submit_check=lambda record, broker_request: _pre_submit_market_session_check(
                    self.supervisor,
                    command,
                    record,
                    broker_request,
                    broker=broker,
                    runtime_control=current_control,
                ),
            )
        except WcaAlpacaPaperBrokerConfigurationError as exc:
            reason_text = str(exc)
            broker_reason_codes = tuple(code for code in reason_text.split(";") if code)
            reasons = ("wca.runtime.execution_outbox.alpaca_paper_broker_blocked", *broker_reason_codes)
            self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
            self.runtime_repository.write_runtime_health(
                self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons)
            )
            return {
                "status": "blocked",
                "commandId": command.command_id,
                "outboxId": reservation.outbox_id,
                "submitted": False,
                "reasonCodes": list(reasons),
            }
        finally:
            if broker is not None:
                broker.close()
        self.runtime_repository.complete_command(command.command_id, reason_codes=("wca.runtime.execution_outbox.created", *submission.reason_codes))
        self.runtime_repository.enqueue_command(
            runtime_command(
                WcaRuntimeCommandType.BROKER_RECONCILIATION,
                account_id=command.account_id,
                symbol=command.symbol,
                decision_id=decision.decision_id,
                run_id=command.run_id,
                payload={"after": "submission", "outbox_id": reservation.outbox_id, "submitted": submission.submitted},
                priority=30,
                reason_codes=("wca.runtime.reconciliation.scheduled_after_submission",),
            ),
            max_queue_depth=self.supervisor.settings.max_command_queue_depth,
        )
        return {
            "status": "completed",
            "commandId": command.command_id,
            "outboxId": reservation.outbox_id,
            "orderState": str(submission.state),
            "submitted": submission.submitted,
            "reasonCodes": ["wca.runtime.execution_outbox.created", *submission.reason_codes],
        }


class BrokerReconciliationWorker(RuntimeWorker):
    worker_name = "broker_reconciliation_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(WcaRuntimeCommandType.BROKER_RECONCILIATION, owner_id=self.supervisor.owner_id)
        if command is None:
            reconciliation_age = _latest_reconciliation_age_seconds(
                self.repository,
                account_id=self.supervisor.settings.account_id,
                symbol=self.supervisor.settings.symbol,
                now=_utc_now(),
            )
            reconciliation_due = reconciliation_age is None or reconciliation_age > self.supervisor.settings.max_reconciliation_age_seconds
            if reconciliation_due or self.repository.reconciliation_blocks_new_entries(account_id=self.supervisor.settings.account_id, symbol=self.supervisor.settings.symbol):
                paper_account = validate_wca_automatic_paper_account(account_id=self.supervisor.settings.account_id)
                if paper_account.verified:
                    return self._run_reconciliation(None, account_id=self.supervisor.settings.account_id)
            return {"status": "idle", "reasonCodes": ["wca.runtime.broker_reconciliation_worker.idle"]}
        return self._run_reconciliation(command, account_id=command.account_id)

    def _run_reconciliation(self, command: WcaRuntimeCommand | None, *, account_id: str) -> dict[str, Any]:
        broker: WcaAlpacaPaperBroker | None = None
        try:
            broker = WcaAlpacaPaperBroker.from_env(account_id=account_id)
            verified, broker_reason_codes = broker.verify_account_and_endpoint_identity()
            if not verified:
                reasons = ("wca.runtime.broker_reconciliation.alpaca_paper_broker_blocked", *broker_reason_codes)
                if command is not None:
                    self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
                self.runtime_repository.write_runtime_health(self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons))
                return {"status": "blocked", "commandId": command.command_id if command is not None else None, "reasonCodes": list(reasons)}
            polled_orders = _poll_wca_order_states(self.repository, broker)
            processed_fills = _process_observed_fills(self.repository, broker)
            result = reconcile_wca_broker(
                repository=self.repository,
                broker=broker,
                account_id=account_id,
                evaluated_at=_utc_now(),
            )
        except WcaAlpacaPaperBrokerConfigurationError as exc:
            broker_reason_codes = tuple(code for code in str(exc).split(";") if code)
            reasons = ("wca.runtime.broker_reconciliation.alpaca_paper_broker_blocked", *broker_reason_codes)
            if command is not None:
                self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
            self.runtime_repository.write_runtime_health(self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons))
            return {"status": "blocked", "commandId": command.command_id if command is not None else None, "reasonCodes": list(reasons)}
        finally:
            if broker is not None:
                broker.close()
        polling_reasons: tuple[str, ...] = ()
        if polled_orders or processed_fills:
            polling_reasons = (
                "wca.runtime.broker_polling.completed_before_reconciliation",
                *(("wca.runtime.broker_polling.order_state_changed",) if polled_orders else ()),
                *(("wca.runtime.broker_polling.fills_processed",) if processed_fills else ()),
                "wca.runtime.broker_polling.reconciled_after_updates",
            )
        if result.discrepancies:
            self.runtime_repository.write_runtime_health(
                self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=("wca.runtime.broker_reconciliation.discrepancies_block_entries", *polling_reasons, *result.reason_codes))
            )
        else:
            self.runtime_repository.write_runtime_health(
                self.supervisor.health_snapshot(paused_new_entries=False, reason_codes=("wca.runtime.broker_reconciliation.clean", *polling_reasons, *result.reason_codes))
            )
        if command is None:
            return {
                "status": "completed",
                "commandId": None,
                "ordersPolled": polled_orders,
                "fillsProcessed": processed_fills,
                "reasonCodes": ["wca.runtime.broker_reconciliation.startup_completed", *polling_reasons, *result.reason_codes],
            }
        self.runtime_repository.complete_command(command.command_id, reason_codes=("wca.runtime.broker_reconciliation.completed", *polling_reasons, *result.reason_codes))
        return {
            "status": "completed",
            "commandId": command.command_id,
            "ordersPolled": polled_orders,
            "fillsProcessed": processed_fills,
            "reasonCodes": ["wca.runtime.broker_reconciliation.completed", *polling_reasons, *result.reason_codes],
        }


class RecoveryWorker(RuntimeWorker):
    worker_name = "recovery_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(
            WcaRuntimeCommandType.RECOVERY,
            owner_id=self.supervisor.owner_id,
            lease_seconds=self.supervisor.settings.lease_seconds,
        )
        unsupported_commands = self.runtime_repository.fail_unsupported_commands(tuple(command_type.value for command_type in WcaRuntimeCommandType))
        recovered = self.runtime_repository.recover_expired_work()
        recovered["unsupported_commands_failed"] = unsupported_commands
        self.supervisor.recovery_state = "completed"
        if recovered.get("commands_requeued", 0) or recovered.get("events_requeued", 0) or unsupported_commands:
            self.runtime_repository.enqueue_command(
                runtime_command(
                    WcaRuntimeCommandType.BROKER_RECONCILIATION,
                    account_id=self.supervisor.settings.account_id,
                    symbol=self.supervisor.settings.symbol,
                    payload={"after": "worker_recovery", **recovered},
                    priority=5,
                    reason_codes=("wca.runtime.reconciliation.scheduled_after_worker_recovery",),
                ),
                max_queue_depth=self.supervisor.settings.max_command_queue_depth,
            )
        reasons = ("wca.runtime.recovery.completed", *(() if not unsupported_commands else ("wca.runtime.command.unsupported_failed",)))
        if command is not None:
            self.runtime_repository.complete_command(command.command_id, reason_codes=reasons)
        return {"status": "completed", "commandId": command.command_id if command is not None else None, **recovered, "reasonCodes": list(reasons)}


class ConfigurationActivationWorker(RuntimeWorker):
    worker_name = "configuration_activation_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(WcaRuntimeCommandType.CONFIGURATION_ACTIVATION, owner_id=self.supervisor.owner_id)
        if command is None:
            return {"status": "idle", "reasonCodes": ["wca.runtime.configuration_activation_worker.idle"]}
        version = str(command.payload.get("configuration_version") or command.payload.get("configurationVersion") or command.decision_id)
        boundary = _payload_datetime(command.payload.get("candle_timestamp") or command.payload.get("finalized_candle_timestamp") or command.payload.get("activation_timestamp")) or _utc_now()
        try:
            activated = self.repository.activate_configuration_version_at_candle_boundary(version, candle_timestamp=boundary)
        except Exception as exc:
            reasons = ("wca.runtime.configuration_activation.failed", type(exc).__name__)
            self.runtime_repository.fail_command(command.command_id, reason_codes=reasons)
            self.runtime_repository.write_runtime_health(self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons))
            return {"status": "failed", "commandId": command.command_id, "reasonCodes": list(reasons)}
        reasons = ("wca.runtime.configuration_activation.applied_at_candle_boundary",)
        self.runtime_repository.complete_command(command.command_id, reason_codes=reasons)
        return {
            "status": "completed",
            "commandId": command.command_id,
            "configurationVersion": activated.configuration_version,
            "activationTimestamp": activated.activation_timestamp.isoformat() if activated.activation_timestamp else "",
            "reasonCodes": list(reasons),
        }


class ConfigurationRollbackWorker(RuntimeWorker):
    worker_name = "configuration_rollback_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(
            WcaRuntimeCommandType.CONFIGURATION_ROLLBACK,
            owner_id=self.supervisor.owner_id,
            lease_seconds=self.supervisor.settings.lease_seconds,
        )
        if command is None:
            return {"status": "idle", "reasonCodes": ["wca.runtime.configuration_rollback_worker.idle"]}
        version = str(command.payload.get("configuration_version") or command.payload.get("configurationVersion") or "")
        boundary = _payload_datetime(command.payload.get("candle_timestamp") or command.payload.get("finalized_candle_timestamp") or command.payload.get("activation_timestamp")) or _utc_now()
        before = self.repository.read_active_configuration()
        target = self.repository.read_configuration_by_version(version) if version else None
        if target is None:
            reasons = ("wca.runtime.configuration_rollback.unknown_target",)
            self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
            self.runtime_repository.write_runtime_health(self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons))
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": list(reasons)}
        try:
            self.repository.validate_configuration_revision(target)
        except Exception as exc:
            reasons = ("wca.runtime.configuration_rollback.invalid_target", type(exc).__name__)
            self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
            self.runtime_repository.write_runtime_health(self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons))
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": list(reasons)}
        control = self._pause_for_rollback(command, before_version=before.configuration_version if before is not None else "", target_version=target.configuration_version)
        evidence = {
            "command_id": command.command_id,
            "before_configuration_version": before.configuration_version if before is not None else "",
            "before_configuration_hash": before.content_hash if before is not None else "",
            "target_configuration_version": target.configuration_version,
            "target_configuration_hash": target.content_hash,
            "boundary_timestamp": boundary.isoformat(),
            "runtime_control_revision": control.control_revision,
            "runtime_control_hash": control.control_hash,
            "new_entries_paused": True,
            "protective_management_active": True,
        }
        self.repository.compare_and_swap_runtime_checkpoint(
            checkpoint_key=f"wca.runtime.configuration_rollback.{command.command_id}.before",
            expected_version=None,
            payload=evidence,
            account_id=command.account_id,
            symbol=command.symbol,
            configuration_version=before.configuration_version if before is not None else target.configuration_version,
            run_id=command.run_id,
        )
        try:
            activated = self.repository.activate_configuration_version_at_candle_boundary(target.configuration_version, candle_timestamp=boundary)
        except Exception as exc:
            reasons = ("wca.runtime.configuration_rollback.failed", type(exc).__name__)
            self.runtime_repository.fail_command(command.command_id, reason_codes=reasons)
            self.runtime_repository.write_runtime_health(self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons))
            return {"status": "failed", "commandId": command.command_id, "reasonCodes": list(reasons)}
        after_evidence = {
            **evidence,
            "after_configuration_version": activated.configuration_version,
            "after_configuration_hash": activated.content_hash,
            "after_activation_timestamp": activated.activation_timestamp.isoformat() if activated.activation_timestamp else "",
            "requires_reconciliation_before_resume": True,
            "requires_healthy_state_validation_before_resume": True,
        }
        self.repository.compare_and_swap_runtime_checkpoint(
            checkpoint_key=f"wca.runtime.configuration_rollback.{command.command_id}.after",
            expected_version=None,
            payload=after_evidence,
            account_id=command.account_id,
            symbol=command.symbol,
            configuration_version=activated.configuration_version,
            run_id=command.run_id,
        )
        _enqueue_reconciliation(self.runtime_repository, command, max_queue_depth=self.supervisor.settings.max_command_queue_depth, marker="after_configuration_rollback", priority=1)
        reasons = (
            "wca.runtime.configuration_rollback.applied_at_candle_boundary",
            "wca.runtime.configuration_rollback.entries_paused_until_reconciliation",
            "wca.runtime.configuration_rollback.protective_management_preserved",
        )
        self.runtime_repository.complete_command(command.command_id, reason_codes=reasons)
        self.runtime_repository.write_runtime_health(self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons))
        return {
            "status": "completed",
            "commandId": command.command_id,
            "beforeConfigurationVersion": before.configuration_version if before is not None else "",
            "configurationVersion": activated.configuration_version,
            "runtimeControl": self.repository.read_runtime_control(broker_account_id=command.account_id, symbol=command.symbol).api_dict(),
            "reasonCodes": list(reasons),
        }

    def _pause_for_rollback(self, command: WcaRuntimeCommand, *, before_version: str, target_version: str) -> WcaRuntimeControl:
        prior = self.repository.read_runtime_control(broker_account_id=command.account_id, symbol=command.symbol)
        reasons = (
            "wca.runtime.configuration_rollback.pause_new_entries",
            "wca.runtime.configuration_rollback.requested",
        )
        requested = prior.model_copy(
            update={
                "pause_new_entries": True,
                "effective_automatic_entries_enabled": False,
                "automatic_entry_currently_permitted": False,
                "control_revision": prior.control_revision + 1,
                "updated_at": _utc_now(),
                "updated_by": str(command.payload.get("actor") or "configuration_rollback_worker"),
                "reason": f"rollback {before_version} -> {target_version}",
                "reason_codes": reasons,
            }
        ).with_hash()
        self.repository.write_runtime_control(requested)
        return self.supervisor.resolve_runtime_control(
            broker_account_id=command.account_id,
            symbol=command.symbol,
            updated_by=requested.updated_by,
            reason=requested.reason,
            reason_codes=(*reasons, "wca.runtime.configuration_rollback.effective_state_resolved"),
        )


class RuntimeControlWorker(RuntimeWorker):
    worker_name = "runtime_control_worker"

    def run_once(self) -> dict[str, Any]:
        for command_type in (
            WcaRuntimeCommandType.SET_AUTOMATIC_PAPER,
            WcaRuntimeCommandType.PAUSE_NEW_ENTRIES,
            WcaRuntimeCommandType.RESUME_NEW_ENTRIES,
        ):
            command = self.runtime_repository.claim_next_command(command_type, owner_id=self.supervisor.owner_id)
            if command is not None:
                return self._apply_control(command)
        return {"status": "idle", "reasonCodes": ["wca.runtime.control_worker.idle"]}

    def _apply_control(self, command: WcaRuntimeCommand) -> dict[str, Any]:
        if command.command_type == WcaRuntimeCommandType.SET_AUTOMATIC_PAPER:
            enabled = bool(command.payload.get("enabled"))
            reason = str(command.payload.get("reason") or ("global_paper_toggle_on" if enabled else "global_paper_toggle_off"))
            actor = str(command.payload.get("actor") or "runtime_control_worker")
            reasons = (
                "wca.runtime_control.paper_trading_requested_on"
                if enabled
                else "wca.runtime_control.paper_trading_requested_off",
                "wca.runtime.control.applied_by_background_worker",
            )
            prior = self.repository.read_runtime_control(broker_account_id=command.account_id, symbol=command.symbol)
            requested = prior.model_copy(
                update={
                    "paper_trading_requested": enabled,
                    "automatic_entries_requested": enabled,
                    "pause_new_entries": not enabled,
                    "effective_paper_trading_enabled": False if not enabled else prior.effective_paper_trading_enabled,
                    "effective_automatic_entries_enabled": False,
                    "control_revision": prior.control_revision + 1,
                    "updated_at": _utc_now(),
                    "updated_by": actor,
                    "reason": reason,
                    "reason_codes": (*reasons, reason, "wca.runtime_control.requested_state_recorded"),
                }
            ).with_hash()
            self.repository.write_runtime_control(requested)
            control = self.supervisor.resolve_runtime_control(
                broker_account_id=command.account_id,
                symbol=command.symbol,
                updated_by=actor,
                reason=reason,
                reason_codes=(*requested.reason_codes, "wca.runtime_control.effective_state_resolved"),
            )
            transition = _apply_runtime_control_transition(self.repository, control)
            paused = not control.effective_automatic_entries_enabled
            self.runtime_repository.write_runtime_health(
                self.supervisor.health_snapshot(paused_new_entries=paused, reason_codes=control.reason_codes)
            )
            self.runtime_repository.complete_command(command.command_id, reason_codes=(*reasons, reason))
            return {
                "status": "completed",
                "commandId": command.command_id,
                "runtimeControl": control.api_dict(),
                "transition": transition,
                "automaticPaperTradingEnabled": control.effective_automatic_entries_enabled,
                "automaticPaperRequested": control.automatic_entries_requested,
                "globalPaperControl": True,
                "paperOnly": True,
                "liveTradingEnabled": False,
                "reasonCodes": list(control.reason_codes),
            }
        if command.command_type == WcaRuntimeCommandType.PAUSE_NEW_ENTRIES:
            reason = str(command.payload.get("reason") or "api_pause")
            reasons = ("wca.runtime.new_entries.paused", "wca.runtime.control.applied_by_background_worker", reason)
            prior = self.repository.read_runtime_control(broker_account_id=command.account_id, symbol=command.symbol)
            requested = prior.model_copy(
                update={
                    "pause_new_entries": True,
                    "effective_automatic_entries_enabled": False,
                    "control_revision": prior.control_revision + 1,
                    "updated_at": _utc_now(),
                    "updated_by": str(command.payload.get("actor") or "runtime_control_worker"),
                    "reason": reason,
                    "reason_codes": reasons,
                }
            ).with_hash()
            self.repository.write_runtime_control(requested)
            control = self.supervisor.resolve_runtime_control(broker_account_id=command.account_id, symbol=command.symbol, reason=reason, reason_codes=reasons)
            transition = _apply_runtime_control_transition(self.repository, control)
            self.runtime_repository.write_runtime_health(self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=control.reason_codes))
            self.runtime_repository.complete_command(command.command_id, reason_codes=reasons)
            return {"status": "completed", "commandId": command.command_id, "pausedNewEntries": True, "runtimeControl": control.api_dict(), "transition": transition, "reasonCodes": list(control.reason_codes)}
        reason = str(command.payload.get("reason") or "api_resume")
        reasons = ("wca.runtime.new_entries.resume_requested", "wca.runtime.control.applied_by_background_worker", reason)
        prior = self.repository.read_runtime_control(broker_account_id=command.account_id, symbol=command.symbol)
        requested = prior.model_copy(
            update={
                "pause_new_entries": False,
                "control_revision": prior.control_revision + 1,
                "updated_at": _utc_now(),
                "updated_by": str(command.payload.get("actor") or "runtime_control_worker"),
                "reason": reason,
                "reason_codes": reasons,
            }
        ).with_hash()
        self.repository.write_runtime_control(requested)
        control = self.supervisor.resolve_runtime_control(broker_account_id=command.account_id, symbol=command.symbol, reason=reason, reason_codes=reasons)
        self.runtime_repository.write_runtime_health(self.supervisor.health_snapshot(paused_new_entries=not control.effective_automatic_entries_enabled, reason_codes=control.reason_codes))
        self.runtime_repository.complete_command(command.command_id, reason_codes=reasons)
        return {"status": "completed", "commandId": command.command_id, "pausedNewEntries": False, "runtimeControl": control.api_dict(), "reasonCodes": list(control.reason_codes)}


class EmergencyRiskReductionWorker(RuntimeWorker):
    worker_name = "emergency_risk_reduction_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(
            WcaRuntimeCommandType.EMERGENCY_RISK_REDUCTION,
            owner_id=self.supervisor.owner_id,
            lease_seconds=self.supervisor.settings.lease_seconds,
        )
        if command is None:
            return {"status": "idle", "reasonCodes": ["wca.runtime.emergency_risk_reduction_worker.idle"]}
        try:
            return self._process(command)
        except Exception as exc:
            reasons = ("wca.runtime.emergency_risk_reduction.failed", type(exc).__name__)
            self.runtime_repository.fail_command(command.command_id, reason_codes=reasons)
            self.runtime_repository.write_runtime_health(
                WcaRuntimeHealthSnapshot(
                    health_id="wca-runtime-health",
                    account_id=command.account_id,
                    symbol=command.symbol,
                    status="critical",
                    paused_new_entries=True,
                    protective_management_active=True,
                    reason_codes=reasons,
                    recovery_state=self.supervisor.recovery_state,
                    last_decision_id=command.decision_id,
                )
            )
            _enqueue_reconciliation(self.runtime_repository, command, max_queue_depth=self.supervisor.settings.max_command_queue_depth, marker="after_failed_emergency_risk_reduction", priority=1)
            return {"status": "failed", "commandId": command.command_id, "reasonCodes": list(reasons)}

    def _process(self, command: WcaRuntimeCommand) -> dict[str, Any]:
        reason = str(command.payload.get("reason") or "emergency_risk_reduction")
        control = self._open_circuit(command, reason=reason)
        transition = _apply_runtime_control_transition(self.repository, control)
        local_cancelled = _mark_local_entry_orders_cancelled(self.repository, account_id=command.account_id, symbol=command.symbol, evidence={"phase": "emergency_risk_reduction", "runtime_control_revision": control.control_revision})
        breaker_event_id = self._record_circuit_breaker_event(command, control=control, reason=reason)
        mark_price = _emergency_mark_price(self.repository, command=command)
        position = manage_wca_position(
            repository=self.repository,
            account_id=command.account_id,
            symbol=command.symbol,
            mark_price=mark_price,
            evaluated_at=_utc_now(),
            emergency_exit=True,
            global_emergency_risk_reduction=True,
        )
        self.runtime_repository.enqueue_command(
            runtime_command(
                WcaRuntimeCommandType.POSITION_PROTECTIVE_EXIT,
                account_id=command.account_id,
                symbol=command.symbol,
                decision_id=command.decision_id,
                run_id=command.run_id,
                payload={
                    "mark_price": mark_price,
                    "emergency_exit": True,
                    "global_emergency_risk_reduction": True,
                    "source_command_id": command.command_id,
                },
                priority=1,
                reason_codes=("wca.runtime.emergency_risk_reduction.protective_exit_scheduled",),
            ),
            max_queue_depth=self.supervisor.settings.max_command_queue_depth,
        )
        _enqueue_reconciliation(self.runtime_repository, command, max_queue_depth=self.supervisor.settings.max_command_queue_depth, marker="after_emergency_risk_reduction", priority=1)
        reasons: list[str] = [
            "wca.runtime.emergency_risk_reduction.entries_blocked",
            "wca.runtime.emergency_risk_reduction.circuit_breaker_open",
            "wca.runtime.emergency_risk_reduction.local_entry_orders_cancelled",
            "wca.runtime.emergency_risk_reduction.protective_management_preserved",
            "wca.runtime.emergency_risk_reduction.reconciliation_scheduled",
        ]
        broker_evidence: dict[str, Any] = {}
        broker: WcaAlpacaPaperBroker | None = None
        terminal = "completed"
        try:
            broker = WcaAlpacaPaperBroker.from_env(account_id=command.account_id)
            verified, broker_reason_codes = broker.verify_account_and_endpoint_identity()
            if not verified:
                reasons.extend(("wca.runtime.emergency_risk_reduction.alpaca_paper_broker_blocked", *broker_reason_codes))
                terminal = "blocked"
            else:
                broker_cancelled = broker.cancel_all_wca_entry_orders() if hasattr(broker, "cancel_all_wca_entry_orders") else ()
                broker_evidence["broker_cancelled_entry_orders"] = len(broker_cancelled or ())
                flatten = _flatten_local_wca_position(self.repository, broker, command=command, evaluated_at=_utc_now())
                broker_evidence["flatten"] = flatten
                reasons.append("wca.runtime.emergency_risk_reduction.broker_entry_orders_cancelled")
                if flatten["status"] in {"filled", "submitted", "already_flat"}:
                    reasons.append("wca.runtime.emergency_risk_reduction.wca_exposure_reduce_or_flatten_submitted")
                else:
                    reasons.append(f"wca.runtime.emergency_risk_reduction.flatten_{flatten['status']}")
                    terminal = "blocked"
        except WcaAlpacaPaperBrokerConfigurationError as exc:
            reasons.extend(("wca.runtime.emergency_risk_reduction.alpaca_paper_broker_blocked", *(code for code in str(exc).split(";") if code)))
            terminal = "blocked"
        finally:
            if broker is not None:
                broker.close()
        reason_codes = tuple(dict.fromkeys(reasons))
        self.runtime_repository.write_runtime_health(
            WcaRuntimeHealthSnapshot(
                health_id="wca-runtime-health",
                account_id=command.account_id,
                symbol=command.symbol,
                status="critical" if terminal == "blocked" else "protective_only",
                paused_new_entries=True,
                protective_management_active=True,
                reason_codes=reason_codes,
                recovery_state=self.supervisor.recovery_state,
                last_decision_id=command.decision_id,
            )
        )
        if terminal == "blocked":
            self.runtime_repository.block_command(command.command_id, reason_codes=reason_codes)
        else:
            self.runtime_repository.complete_command(command.command_id, reason_codes=reason_codes)
        return {
            "status": terminal,
            "commandId": command.command_id,
            "runtimeControl": control.api_dict(),
            "transition": transition,
            "localCancelledEntryOrders": local_cancelled,
            "circuitBreakerEventId": breaker_event_id,
            "openQuantity": position.open_quantity,
            "pendingExitOrders": len(position.pending_exit_orders),
            "brokerEvidence": broker_evidence,
            "reasonCodes": list(reason_codes),
        }

    def _open_circuit(self, command: WcaRuntimeCommand, *, reason: str) -> WcaRuntimeControl:
        prior = self.repository.read_runtime_control(broker_account_id=command.account_id, symbol=command.symbol)
        reasons = (
            "wca.runtime.emergency_risk_reduction.requested",
            "wca.runtime.emergency_risk_reduction.kill_switch_open",
            reason,
        )
        requested = prior.model_copy(
            update={
                "pause_new_entries": True,
                "kill_switch_open": True,
                "effective_automatic_entries_enabled": False,
                "automatic_entry_currently_permitted": False,
                "control_revision": prior.control_revision + 1,
                "updated_at": _utc_now(),
                "updated_by": str(command.payload.get("actor") or "emergency_risk_reduction_worker"),
                "reason": reason,
                "reason_codes": reasons,
            }
        ).with_hash()
        self.repository.write_runtime_control(requested)
        return self.supervisor.resolve_runtime_control(
            broker_account_id=command.account_id,
            symbol=command.symbol,
            updated_by=requested.updated_by,
            reason=reason,
            reason_codes=(*reasons, "wca.runtime.emergency_risk_reduction.effective_state_resolved"),
        )

    def _record_circuit_breaker_event(self, command: WcaRuntimeCommand, *, control: WcaRuntimeControl, reason: str) -> str:
        timestamp = _utc_now()
        event_id = f"wca-emergency-circuit-breaker-{command.command_id}"
        self.repository.record_inventory_event(
            WcaInventoryLedgerEvent(
                inventory_event_id=event_id,
                event_type="RECONCILIATION_CORRECTION",
                broker_account_id=command.account_id,
                symbol=command.symbol,
                event_timestamp=timestamp.isoformat(),
                trade_date=timestamp.date().isoformat(),
                source_authority="wca_emergency_risk_reduction",
                configuration_version="wca_emergency_risk_reduction",
                decision_id=command.decision_id or command.command_id,
                run_id=command.run_id,
                payload={
                    "critical": True,
                    "circuit_breaker_state": "open",
                    "runtime_control_revision": control.control_revision,
                    "runtime_control_hash": control.control_hash,
                    "reason": reason,
                },
            )
        )
        return event_id


class HeartbeatHealthWorker(RuntimeWorker):
    worker_name = "heartbeat_and_health_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(
            WcaRuntimeCommandType.HEARTBEAT,
            owner_id=self.supervisor.owner_id,
            lease_seconds=self.supervisor.settings.lease_seconds,
        )
        prior = self.runtime_repository.read_latest_runtime_health()
        prior_critical = _prior_critical_health_reasons(prior)
        prior_pause = prior is not None and prior.paused_new_entries
        config_ready = self.repository.read_active_configuration() is not None and self.repository.read_active_weights() is not None
        recon_block = self.repository.reconciliation_blocks_new_entries(account_id=self.supervisor.settings.account_id, symbol=self.supervisor.settings.symbol)
        circuit_breaker = self.repository.wca_position_circuit_breaker_open(account_id=self.supervisor.settings.account_id, symbol=self.supervisor.settings.symbol)
        last_bar = self.runtime_repository.last_processed_bar(symbol=self.supervisor.settings.symbol)
        lag_pause = last_bar is not None and (_utc_now() - last_bar).total_seconds() > int(self.supervisor.settings.max_finalized_bar_age_seconds or self.supervisor.settings.max_lag_seconds)
        paused = (not config_ready) or recon_block or lag_pause or circuit_breaker or bool(prior_critical) or prior_pause
        reason = "wca.runtime.healthy"
        if prior_critical or prior_pause:
            reason = (prior_critical or prior.reason_codes if prior is not None else ("wca.runtime.health.prior_pause",))[0]
        elif lag_pause:
            reason = "wca.runtime.lag_entry_pause"
        elif recon_block:
            reason = "wca.runtime.reconciliation_blocks_entries"
        elif circuit_breaker:
            reason = "wca.runtime.position_circuit_breaker"
        elif not config_ready:
            reason = "wca.runtime.starting_fail_closed"
        if prior is not None and (prior_critical or prior_pause):
            now = _utc_now()
            heartbeats = {**prior.worker_heartbeats, self.worker_name: now}
            health = prior.model_copy(
                update={
                    "heartbeat_at": now,
                    "status": "protective_only",
                    "paused_new_entries": True,
                    "protective_management_active": True,
                    "worker_heartbeats": heartbeats,
                    "reason_codes": prior.reason_codes,
                    "latency_summary": self.runtime_repository.read_latency_summaries(account_id=self.supervisor.settings.account_id, symbol=self.supervisor.settings.symbol),
                }
            )
        else:
            health = self.supervisor.health_snapshot(paused_new_entries=paused, reason_codes=(reason,))
        self.runtime_repository.write_runtime_health(health)
        reasons = ("wca.runtime.heartbeat.completed", reason) if command is not None else (reason,)
        if command is not None:
            self.runtime_repository.complete_command(command.command_id, reason_codes=reasons)
        return {"status": "completed", "commandId": command.command_id if command is not None else None, "pausedNewEntries": paused, "reasonCodes": list(reasons)}


class EndOfSessionWorker(RuntimeWorker):
    worker_name = "end_of_session_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(WcaRuntimeCommandType.END_OF_SESSION, owner_id=self.supervisor.owner_id)
        if command is None:
            return {"status": "idle", "reasonCodes": ["wca.runtime.end_of_session_worker.idle"]}
        broker: WcaAlpacaPaperBroker | None = None
        try:
            broker = WcaAlpacaPaperBroker.from_env(account_id=command.account_id)
            verified, broker_reason_codes = broker.verify_account_and_endpoint_identity()
            if not verified:
                reasons = ("wca.runtime.end_of_session.alpaca_paper_broker_blocked", *broker_reason_codes)
                return _fail_end_of_session(self.supervisor, command, reasons, evidence={"stage": "broker_identity"})
            result = process_wca_end_of_session(
                repository=self.repository,
                runtime_repository=self.runtime_repository,
                broker=broker,
                command=command,
                max_queue_depth=self.supervisor.settings.max_command_queue_depth,
                now=_payload_datetime(command.payload.get("evaluated_at")) or _utc_now(),
            )
        except WcaAlpacaPaperBrokerConfigurationError as exc:
            reasons = ("wca.runtime.end_of_session.alpaca_paper_broker_blocked", *(code for code in str(exc).split(";") if code))
            return _fail_end_of_session(self.supervisor, command, reasons, evidence={"stage": "broker_configuration"})
        finally:
            if broker is not None:
                broker.close()
        if result["verified"]:
            self.runtime_repository.complete_command(command.command_id, reason_codes=tuple(result["reasonCodes"]))
            self.runtime_repository.write_runtime_health(
                self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=tuple(result["reasonCodes"]))
            )
            return {"status": "completed", "commandId": command.command_id, **result}
        return _fail_end_of_session(self.supervisor, command, tuple(result["reasonCodes"]), evidence=result)


def process_wca_end_of_session(
    *,
    repository: WcaSqliteRepository,
    runtime_repository: WcaRuntimeRepository,
    broker: Any,
    command: WcaRuntimeCommand,
    max_queue_depth: int,
    now: datetime,
    calendar: WcaMarketCalendar | None = None,
) -> dict[str, Any]:
    evaluated = now.astimezone(timezone.utc)
    market_calendar = calendar or WcaMarketCalendar()
    session = market_calendar.session_for(evaluated)
    config = repository.read_active_configuration()
    cutoff_minutes = config.execution.entry_cutoff_minutes if config is not None else 15 * 60 + 30
    if session is None:
        cutoff_reached = True
    else:
        local_evaluated = evaluated.astimezone(session.market_close.tzinfo)
        cutoff_reached = (local_evaluated.hour * 60 + local_evaluated.minute) >= cutoff_minutes or market_calendar.should_flatten(evaluated, buffer_minutes=5)
    reasons: list[str] = ["wca.runtime.end_of_session.started", "wca.runtime.end_of_session.entries_paused"]
    evidence: dict[str, Any] = {
        "command_id": command.command_id,
        "account_id": command.account_id,
        "symbol": command.symbol,
        "evaluated_at": evaluated.isoformat(),
        "session_date": session.session_date.isoformat() if session is not None else evaluated.date().isoformat(),
        "early_close": bool(session and session.is_early_close),
        "market_holiday_or_closed": session is None,
        "entry_cutoff_minutes": cutoff_minutes,
        "entry_cutoff_reached": cutoff_reached,
    }
    _enqueue_reconciliation(runtime_repository, command, max_queue_depth=max_queue_depth, marker="before_end_of_session_flatten", priority=1)
    first_reconciliation = reconcile_wca_broker(repository=repository, broker=broker, account_id=command.account_id, evaluated_at=evaluated)
    evidence["first_reconciliation_id"] = first_reconciliation.reconciliation_id
    evidence["first_discrepancies"] = [row.model_dump(mode="json") for row in first_reconciliation.discrepancies]
    try:
        cancelled = broker.cancel_all_wca_entry_orders() if hasattr(broker, "cancel_all_wca_entry_orders") else ()
    except WcaPaperBrokerTimeout as exc:
        reasons.append("wca.runtime.end_of_session.entry_cancel_timeout")
        evidence["cancel_error"] = str(exc)
        cancelled = ()
    except Exception as exc:
        reasons.append("wca.runtime.end_of_session.entry_cancel_failed")
        evidence["cancel_error"] = str(exc)
        cancelled = ()
    evidence["cancelled_entry_orders"] = len(cancelled or ())
    _mark_local_entry_orders_cancelled(repository, account_id=command.account_id, symbol=command.symbol, evidence={"phase": "end_of_session"})
    processed_fills = _process_observed_fills(repository, broker)
    evidence["processed_fills"] = processed_fills
    flattened = _flatten_local_wca_position(repository, broker, command=command, evaluated_at=evaluated, record_inventory_event=True)
    evidence["flatten"] = flattened
    if flattened["status"] == "filled":
        _record_end_of_session_flatten_event(repository, command=command, evaluated_at=evaluated, flatten={**flattened, "ledger_quantity": 0})
    if flattened["status"] == "rejected":
        reasons.append("wca.runtime.end_of_session.flatten_rejected")
    elif flattened["status"] == "timeout":
        reasons.append("wca.runtime.end_of_session.flatten_timeout")
    elif flattened["status"] == "failed":
        reasons.append("wca.runtime.end_of_session.flatten_failed")
    elif flattened["status"] == "already_flat":
        reasons.append("wca.runtime.end_of_session.already_flat")
    else:
        reasons.append("wca.runtime.end_of_session.flatten_submitted")
    second_reconciliation = reconcile_wca_broker(repository=repository, broker=broker, account_id=command.account_id, evaluated_at=evaluated + timedelta(microseconds=1))
    evidence["final_reconciliation_id"] = second_reconciliation.reconciliation_id
    evidence["final_discrepancies"] = [row.model_dump(mode="json") for row in second_reconciliation.discrepancies]
    verification = _verify_end_of_session(repository, broker, command=command)
    evidence["verification"] = verification
    if verification["verified"]:
        _record_end_of_session_evidence(repository, command=command, evaluated_at=evaluated, evidence=evidence, verified=True)
        reasons.append("wca.runtime.end_of_session.verified_flat")
        reasons.append("wca.runtime.end_of_session.completed")
        return {"verified": True, "reasonCodes": reasons, "evidence": evidence}
    reasons.extend(verification["blocking_reasons"])
    reasons.append("wca.runtime.end_of_session.failed")
    _record_end_of_session_evidence(repository, command=command, evaluated_at=evaluated, evidence=evidence, verified=False)
    _enqueue_reconciliation(runtime_repository, command, max_queue_depth=max_queue_depth, marker="after_failed_end_of_session", priority=1)
    return {"verified": False, "reasonCodes": tuple(dict.fromkeys(reasons)), "evidence": evidence}


def _poll_wca_order_states(repository: WcaSqliteRepository, broker: Any) -> int:
    poll = getattr(broker, "poll_order_updates", None) or getattr(broker, "find_order_by_client_order_id", None)
    if poll is None:
        return 0
    changed = 0
    terminal = {
        WcaOrderStatus.FILLED.value,
        WcaOrderStatus.CANCELLED.value,
        WcaOrderStatus.REJECTED.value,
        WcaOrderStatus.RECONCILED.value,
        WcaOrderStatus.DEAD_LETTER.value,
        WcaOrderStatus.EXPIRED.value,
    }
    for record in repository.list_execution_outbox_records():
        if coerce_wca_order_status(record.status) in terminal:
            continue
        if not str(record.client_order_id or "").startswith("wca-"):
            continue
        try:
            update = poll(record.client_order_id)
        except Exception:
            continue
        target = _order_status_from_broker_update(update)
        if target is None:
            continue
        if _transition_execution_outbox_state(repository, record, target, update):
            changed += 1
            if target in {WcaOrderStatus.REJECTED, WcaOrderStatus.CANCELLED, WcaOrderStatus.EXPIRED}:
                _record_terminal_order_event_from_poll(repository, record=record, target=target, update=update)
    return changed


def _order_status_from_broker_update(update: Any) -> WcaOrderStatus | None:
    if update is None:
        return None
    status = str(_broker_update_value(update, "status") or "").lower()
    filled = _broker_update_int(update, "filled_qty", "filledQuantity", "filled_quantity")
    quantity = _broker_update_int(update, "qty", "quantity")
    if status in {"filled", "done_for_day"} or (quantity > 0 and filled >= quantity):
        return WcaOrderStatus.FILLED
    if status in {"partially_filled", "partial_fill"} or filled > 0:
        return WcaOrderStatus.PARTIALLY_FILLED
    if status == "rejected":
        return WcaOrderStatus.REJECTED
    if status in {"canceled", "cancelled"}:
        return WcaOrderStatus.CANCELLED
    if status == "expired":
        return WcaOrderStatus.EXPIRED
    if status in {"accepted", "new", "pending", "open", "accepted_for_bidding", "pending_new"}:
        return WcaOrderStatus.ACKNOWLEDGED
    return None


def _transition_execution_outbox_state(repository: WcaSqliteRepository, record: Any, target: WcaOrderStatus, update: Any) -> bool:
    current = coerce_wca_order_status(record.status)
    if current == target.value:
        return False
    response = {
        "broker_order_poll": _broker_update_payload(update),
        "reason_codes": ("wca.runtime.broker_polling.order_state_observed",),
    }
    try:
        if current == WcaOrderStatus.RESERVED.value and target not in {WcaOrderStatus.CANCELLED, WcaOrderStatus.DEAD_LETTER}:
            if repository.update_execution_outbox_state(outbox_id=record.outbox_id, status=WcaOrderStatus.SUBMITTING, response_payload=response):
                current = WcaOrderStatus.SUBMITTING.value
        repository.update_execution_outbox_state(outbox_id=record.outbox_id, status=target, response_payload=response)
        return True
    except ValueError:
        try:
            repository.update_execution_outbox_state(
                outbox_id=record.outbox_id,
                status=WcaOrderStatus.RECONCILING,
                error_payload={**response, "target_status": target.value, "prior_status": current},
            )
            return True
        except ValueError:
            return False


def _record_terminal_order_event_from_poll(repository: WcaSqliteRepository, *, record: Any, target: WcaOrderStatus, update: Any) -> None:
    if _record_is_risk_reducing_exit(record):
        return
    event_type = "ORDER_CANCELLED" if target in {WcaOrderStatus.CANCELLED, WcaOrderStatus.EXPIRED} else "ORDER_REJECTED"
    try:
        repository.record_order_terminal_inventory_event(
            record.decision.model_copy(update={"proposed_order": record.proposed_order}),
            account_id=record.account_id,
            client_order_id=record.client_order_id,
            broker_order_id=str(_broker_update_value(update, "id") or _broker_update_value(update, "broker_order_id") or ""),
            event_type=event_type,
            event_timestamp=_utc_now(),
            payload={"broker_order_poll": _broker_update_payload(update), "reason_codes": ("wca.runtime.broker_polling.terminal_order_observed",)},
        )
    except Exception:
        pass


def _process_observed_fills(repository: WcaSqliteRepository, broker: Any) -> int:
    if not hasattr(broker, "read_fills_and_activities"):
        return 0
    records = {record.client_order_id: record for record in repository.list_execution_outbox_records()}
    processed = 0
    for fill in broker.read_fills_and_activities(after=_utc_now() - timedelta(days=1)):
        record = records.get(fill.client_order_id)
        if record is None:
            if _process_protective_order_fill(repository, broker, fill):
                processed += 1
            continue
        if fill.filled_quantity <= 0:
            continue
        payload = {
            "fill": fill.model_dump(mode="json") if hasattr(fill, "model_dump") else {},
            "client_order_id": fill.client_order_id,
            "entry_price": fill.average_fill_price or record.proposed_order.limit_price or record.proposed_order.trigger_price,
            "stop_price": record.proposed_order.stop_price,
            "target_price": record.proposed_order.target_price,
            "opened_at": fill.filled_at.astimezone(timezone.utc).isoformat(),
            "remaining_quantity": fill.remaining_quantity,
            "position_effect": "exit" if _record_is_risk_reducing_exit(record) else "entry",
        }
        if repository.apply_fill_and_update_position(
            record.decision.model_copy(update={"proposed_order": record.proposed_order}),
            fill_id=fill.fill_id,
            account_id=record.account_id,
            quantity=fill.filled_quantity,
            broker_order_id=fill.broker_order_id,
            payload=payload,
        ):
            processed += 1
            if not _record_is_risk_reducing_exit(record):
                place_or_replace_wca_protective_orders(repository, broker=broker, record=record, fill=fill)
            target_status = WcaOrderStatus.FILLED if fill.remaining_quantity == 0 else WcaOrderStatus.PARTIALLY_FILLED
            try:
                repository.update_execution_outbox_state(outbox_id=record.outbox_id, status=target_status, response_payload={"end_of_session_fill": payload})
            except ValueError:
                pass
    return processed


def _process_protective_order_fill(repository: WcaSqliteRepository, broker: Any, fill: Any) -> bool:
    client_order_id = str(getattr(fill, "client_order_id", "") or "")
    if not client_order_id.startswith("wca-protection-") or int(getattr(fill, "filled_quantity", 0) or 0) <= 0:
        return False
    with repository.connect() as conn:
        row = conn.execute(
            """
            SELECT account_id, symbol, side, quantity, payload_json
            FROM wca_broker_orders
            WHERE algorithm_id = ? AND client_order_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            ("wca", client_order_id),
        ).fetchone()
    if row is None:
        return False
    payload = json.loads(row["payload_json"] or "{}")
    response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
    protection_kind = str(payload.get("protection_kind") or "protective_exit")
    fill_price = float(getattr(fill, "average_fill_price", None) or response.get("filled_avg_price") or response.get("limit_price") or 0)
    if fill_price <= 0:
        return False
    closed = repository.close_wca_attributed_position_quantity(
        account_id=row["account_id"],
        symbol=row["symbol"],
        quantity=min(int(getattr(fill, "filled_quantity", 0) or 0), int(row["quantity"] or 0)),
        exit_price=fill_price,
        exit_reason=f"{protection_kind}_executed",
        evaluated_at=getattr(fill, "filled_at", None) or _utc_now(),
        client_order_id=client_order_id,
        broker_order_id=str(getattr(fill, "broker_order_id", "") or ""),
        fill_id=str(getattr(fill, "fill_id", "") or ""),
        payload={"fill": fill.model_dump(mode="json") if hasattr(fill, "model_dump") else {}, "protection_kind": protection_kind},
    )
    siblings = tuple(payload.get("sibling_client_order_ids") or ())
    for sibling_client_id in siblings:
        if sibling_client_id == client_order_id:
            continue
        _cancel_broker_order_by_client_id(broker, str(sibling_client_id))
    return bool(closed)


def _broker_update_value(update: Any, *names: str) -> Any:
    if update is None:
        return None
    for name in names:
        if isinstance(update, dict) and name in update:
            return update.get(name)
        if hasattr(update, name):
            return getattr(update, name)
        snake = []
        for char in name:
            if char.isupper():
                snake.append("_")
                snake.append(char.lower())
            else:
                snake.append(char)
        snake_name = "".join(snake).lstrip("_")
        if isinstance(update, dict) and snake_name in update:
            return update.get(snake_name)
        if hasattr(update, snake_name):
            return getattr(update, snake_name)
    return None


def _broker_update_int(update: Any, *names: str) -> int:
    value = _broker_update_value(update, *names)
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _broker_update_payload(update: Any) -> dict[str, Any]:
    if update is None:
        return {}
    if isinstance(update, dict):
        return dict(update)
    if hasattr(update, "model_dump"):
        return update.model_dump(mode="json")
    payload: dict[str, Any] = {}
    for name in ("id", "clientOrderId", "client_order_id", "status", "quantity", "qty", "filledQuantity", "filled_qty"):
        value = _broker_update_value(update, name)
        if value is not None:
            payload[name] = value
    return payload


def _cancel_broker_order_by_client_id(broker: Any, client_order_id: str) -> bool:
    finder = getattr(broker, "find_order_by_client_order_id", None)
    cancel = getattr(broker, "cancel_order", None)
    if finder is None or cancel is None:
        return False
    try:
        found = finder(client_order_id)
    except Exception:
        return False
    broker_order_id = str((found or {}).get("id") or (found or {}).get("broker_order_id") or "")
    if not broker_order_id:
        return False
    cancel(broker_order_id)
    return True


def _flatten_local_wca_position(repository: WcaSqliteRepository, broker: Any, *, command: WcaRuntimeCommand, evaluated_at: datetime, record_inventory_event: bool = True) -> dict[str, Any]:
    lots = repository.list_open_wca_lots(account_id=command.account_id, symbol=command.symbol)
    quantity = sum(int(lot["quantity"]) for lot in lots)
    if quantity <= 0:
        return {"status": "already_flat", "closed_quantity": 0}
    side = lots[0]["side"]
    client_order_id = f"wca-eos-{command.account_id}-{command.symbol}-{command.command_id}"[:48]
    try:
        ack = broker.close_or_reduce_wca_position(symbol=command.symbol, quantity=quantity, side=side, client_order_id=client_order_id)
    except WcaPaperBrokerTimeout as exc:
        return {"status": "timeout", "closed_quantity": 0, "error": str(exc)}
    except Exception as exc:
        return {"status": "failed", "closed_quantity": 0, "error": str(exc)}
    if ack.status == "REJECTED":
        return {"status": "rejected", "closed_quantity": 0, "broker_order_id": ack.broker_order_id, "message": ack.message}
    cancelled_protective = ()
    cancel_protective = getattr(broker, "cancel_all_wca_protective_orders", None)
    if cancel_protective is not None:
        try:
            cancelled_protective = tuple(cancel_protective(symbol=command.symbol) or ())
        except Exception:
            cancelled_protective = ()
    fill_quantity = ack.fill.filled_quantity if ack.fill is not None else 0
    fill_price = ack.fill.average_fill_price if ack.fill is not None and ack.fill.average_fill_price is not None else _average_mark_from_lots(lots)
    if fill_quantity > 0:
        repository.close_wca_attributed_position_quantity(
            account_id=command.account_id,
            symbol=command.symbol,
            quantity=min(quantity, fill_quantity),
            exit_price=fill_price,
            exit_reason="end_of_session_flatten",
            evaluated_at=evaluated_at,
            client_order_id=client_order_id,
            broker_order_id=ack.broker_order_id,
            fill_id=ack.fill.fill_id if ack.fill is not None else None,
            payload={"flatten": str(command.command_type.value if hasattr(command.command_type, "value") else command.command_type), "position_effect": "exit"},
            record_inventory_event=record_inventory_event,
        )
    return {
        "status": "submitted" if fill_quantity < quantity else "filled",
        "closed_quantity": fill_quantity,
        "remaining_quantity": max(0, quantity - fill_quantity),
        "fill_price": fill_price,
        "broker_order_id": ack.broker_order_id,
        "client_order_id": ack.client_order_id,
        "cancelled_protective_orders": len(cancelled_protective),
    }


def _verify_end_of_session(repository: WcaSqliteRepository, broker: Any, *, command: WcaRuntimeCommand) -> dict[str, Any]:
    local_quantity = repository.open_wca_position_quantity(account_id=command.account_id, symbol=command.symbol)
    projection = repository.read_inventory_projection(algorithm_id="wca", broker_account_id=command.account_id, symbol=command.symbol)
    snapshot = broker.refresh_account_snapshot()
    broker_quantity = sum(_signed_broker_quantity(position.side, position.quantity) for position in snapshot.positions if position.symbol.upper() == command.symbol.upper() and (position.algorithmId == "wca" or position.positionOwner == "wca"))
    orders = [*snapshot.pendingOrders, *snapshot.partiallyFilledOrders]
    entry_orders = [order for order in orders if _broker_order_is_entry(order)]
    orphan_protection = [order for order in orders if _broker_order_is_protective(order) and local_quantity == 0 and broker_quantity == 0]
    local_entry_orders = [record for record in repository.list_execution_outbox_records(account_id=command.account_id) if record.symbol == command.symbol and _record_is_open_entry(record)]
    if local_quantity == 0 and broker_quantity == 0 and projection.open_quantity == 0 and not entry_orders and not local_entry_orders and not orphan_protection:
        _release_reserved_risk_for_eos(repository, command=command)
        projection = repository.read_inventory_projection(algorithm_id="wca", broker_account_id=command.account_id, symbol=command.symbol)
    blocking: list[str] = []
    if local_quantity != 0 or broker_quantity != 0 or projection.open_quantity != 0:
        blocking.append("wca.runtime.end_of_session.position_not_flat")
    if entry_orders or local_entry_orders:
        blocking.append("wca.runtime.end_of_session.entry_orders_remain")
    if orphan_protection:
        blocking.append("wca.runtime.end_of_session.orphan_protective_orders")
    if projection.reserved_risk > 0:
        blocking.append("wca.runtime.end_of_session.unreleased_risk_reservation")
    return {
        "verified": not blocking,
        "blocking_reasons": tuple(blocking),
        "local_quantity": local_quantity,
        "broker_quantity": broker_quantity,
        "projection_quantity": projection.open_quantity,
        "reserved_risk": projection.reserved_risk,
        "broker_entry_orders": len(entry_orders),
        "local_entry_orders": len(local_entry_orders),
        "orphan_protective_orders": len(orphan_protection),
    }


def _mark_local_entry_orders_cancelled(repository: WcaSqliteRepository, *, account_id: str, symbol: str, evidence: dict[str, Any]) -> int:
    cancelled = 0
    for record in repository.list_execution_outbox_records(account_id=account_id):
        if record.symbol != symbol or not _record_is_open_entry(record):
            continue
        try:
            repository.update_execution_outbox_state(outbox_id=record.outbox_id, status=WcaOrderStatus.CANCELLED, response_payload=evidence)
        except ValueError:
            try:
                repository.update_execution_outbox_state(outbox_id=record.outbox_id, status=WcaOrderStatus.RECONCILING, response_payload=evidence)
            except ValueError:
                continue
        repository.record_order_terminal_inventory_event(
            record.decision.model_copy(update={"proposed_order": record.proposed_order}),
            account_id=record.account_id,
            client_order_id=record.client_order_id,
            event_type="ORDER_CANCELLED",
            event_timestamp=_utc_now(),
            payload={"reason": "end_of_session_entry_cancel", **evidence},
        )
        cancelled += 1
    return cancelled


def _release_reserved_risk_for_eos(repository: WcaSqliteRepository, *, command: WcaRuntimeCommand) -> None:
    projection = repository.read_inventory_projection(algorithm_id="wca", broker_account_id=command.account_id, symbol=command.symbol)
    if projection.reserved_risk <= 0:
        return
    now = _utc_now().isoformat()
    repository.record_inventory_event(
        WcaInventoryLedgerEvent(
            inventory_event_id=f"wca-eos-risk-released-{command.command_id}",
            event_type="RISK_RELEASED",
            broker_account_id=command.account_id,
            symbol=command.symbol,
            event_timestamp=now,
            trade_date=now[:10],
            reserved_risk=projection.reserved_risk,
            source_authority="wca_end_of_session",
            configuration_version="wca_end_of_session",
            decision_id=command.decision_id or command.command_id,
            run_id=command.run_id,
            payload={"reason": "end_of_session_final_risk_release"},
        )
    )


def _record_end_of_session_flatten_event(repository: WcaSqliteRepository, *, command: WcaRuntimeCommand, evaluated_at: datetime, flatten: dict[str, Any]) -> None:
    closed_quantity = int(flatten.get("ledger_quantity") if flatten.get("ledger_quantity") is not None else flatten.get("closed_quantity") or 0)
    repository.record_inventory_event(
        WcaInventoryLedgerEvent(
            inventory_event_id=f"wca-eos-flatten-{command.command_id}",
            event_type="END_OF_SESSION_FLATTEN",
            broker_account_id=command.account_id,
            symbol=command.symbol,
            event_timestamp=evaluated_at.isoformat(),
            trade_date=evaluated_at.date().isoformat(),
            side="SELL",
            quantity=closed_quantity,
            filled_quantity=closed_quantity,
            fill_price=float(flatten.get("fill_price") or 0),
            source_authority="wca_end_of_session",
            configuration_version="wca_end_of_session",
            decision_id=command.decision_id or command.command_id,
            run_id=command.run_id,
            payload={"reason": "end_of_session_flatten", **flatten},
        )
    )


def _record_end_of_session_evidence(repository: WcaSqliteRepository, *, command: WcaRuntimeCommand, evaluated_at: datetime, evidence: dict[str, Any], verified: bool) -> None:
    flatten = evidence.get("flatten") if isinstance(evidence.get("flatten"), dict) else {}
    payload = {**evidence, "verified": verified, "critical": not verified, "circuit_breaker_state": "closed" if verified else "open"}
    repository.record_inventory_event(
        WcaInventoryLedgerEvent(
            inventory_event_id=f"wca-eos-evidence-{command.command_id}",
            event_type="RECONCILIATION_CORRECTION",
            broker_account_id=command.account_id,
            symbol=command.symbol,
            event_timestamp=evaluated_at.isoformat(),
            trade_date=evaluated_at.date().isoformat(),
            side="SELL",
            quantity=int(flatten.get("closed_quantity") or 0),
            filled_quantity=int(flatten.get("closed_quantity") or 0),
            fill_price=float(flatten.get("fill_price") or 0),
            source_authority="wca_end_of_session",
            configuration_version="wca_end_of_session",
            decision_id=command.decision_id or command.command_id,
            run_id=command.run_id,
            payload=payload,
        )
    )


def _enqueue_reconciliation(runtime_repository: WcaRuntimeRepository, command: WcaRuntimeCommand, *, max_queue_depth: int, marker: str, priority: int) -> None:
    runtime_repository.enqueue_command(
        runtime_command(
            WcaRuntimeCommandType.BROKER_RECONCILIATION,
            account_id=command.account_id,
            symbol=command.symbol,
            decision_id=command.decision_id,
            run_id=command.run_id,
            payload={marker: True, "source_command_id": command.command_id},
            priority=priority,
            reason_codes=(f"wca.runtime.reconciliation.scheduled_{marker}",),
        ),
        max_queue_depth=max_queue_depth,
    )


def _fail_end_of_session(supervisor: WcaRuntimeSupervisor, command: WcaRuntimeCommand, reasons: tuple[str, ...], *, evidence: dict[str, Any]) -> dict[str, Any]:
    reason_codes = tuple(dict.fromkeys((*reasons, "wca.runtime.end_of_session.critical_health", "wca.runtime.end_of_session.failed")))
    supervisor.runtime_repository.fail_command(command.command_id, reason_codes=reason_codes)
    supervisor.runtime_repository.write_runtime_health(
        WcaRuntimeHealthSnapshot(
            health_id="wca-runtime-health",
            account_id=command.account_id,
            symbol=command.symbol,
            status="critical",
            paused_new_entries=True,
            protective_management_active=True,
            reason_codes=reason_codes,
            recovery_state=supervisor.recovery_state,
            last_decision_id=command.decision_id,
        )
    )
    _enqueue_reconciliation(supervisor.runtime_repository, command, max_queue_depth=supervisor.settings.max_command_queue_depth, marker="after_failed_end_of_session", priority=1)
    return {"status": "failed", "commandId": command.command_id, "verified": False, "reasonCodes": list(reason_codes), "evidence": evidence}


def _evaluate_entry_health(
    supervisor: WcaRuntimeSupervisor,
    *,
    event: WcaFinalizedBarEvent,
    state: WcaAuthoritativeRuntimeState,
    configuration_ready: bool,
    weight_calibration_ready: bool,
) -> WcaRuntimeHealthSnapshot:
    now = _utc_now()
    depths = supervisor.runtime_repository.queue_depths()
    ages = supervisor.runtime_repository.queue_ages(now=now)
    prior = supervisor.runtime_repository.read_latest_runtime_health()
    heartbeat_ok = _worker_heartbeats_fresh(prior, now=now, maximum_age_seconds=supervisor.settings.max_worker_heartbeat_age_seconds)
    reconciliation_age = _latest_reconciliation_age_seconds(supervisor.repository, account_id=supervisor.settings.account_id, symbol=event.symbol, now=now)
    clock_skew = prior.clock_skew_seconds if prior is not None else 0.0
    health_checks = {
        "worker_heartbeat": heartbeat_ok,
        "queue_depth": depths["events"] <= supervisor.settings.max_event_queue_depth and depths["commands"] <= supervisor.settings.max_command_queue_depth,
        "queue_age": ages["maximum"] <= supervisor.settings.max_queue_delay_seconds,
        "database_available": supervisor.runtime_repository.database_available(),
        "broker_available": prior.broker_available if prior is not None else True,
        "market_data_available": (event.snapshot is None or event.snapshot.data_ready) and (prior.market_data_available if prior is not None else True),
        "clock_skew": abs(clock_skew) <= supervisor.settings.max_clock_skew_seconds,
        "reconciliation_fresh": reconciliation_age is None or reconciliation_age <= supervisor.settings.max_reconciliation_age_seconds,
        "unprotected_position_clear": not (prior.unprotected_position if prior is not None else False),
        "duplicate_order_evidence_clear": not (prior.duplicate_order_evidence if prior is not None else False),
        "configuration_ready": configuration_ready and (prior.configuration_ready if prior is not None else True),
        "weight_calibration_ready": weight_calibration_ready and (prior.weight_calibration_ready if prior is not None else True),
        "circuit_breaker_closed": state.circuit_breaker_state != "open" and not (prior.circuit_breaker_open if prior is not None else False),
    }
    reason_codes = tuple(f"wca.runtime.health.{name}" for name, passed in health_checks.items() if not passed)
    return healthy_runtime_snapshot(
        queue_depth=depths["events"],
        command_depth=depths["commands"],
        max_queue_age_seconds=ages["maximum"],
        last_processed_bar=supervisor.runtime_repository.last_processed_bar(symbol=event.symbol),
        lag_seconds=max(0.0, (now - event.finalized_candle_timestamp.astimezone(timezone.utc)).total_seconds()),
        last_decision_id=supervisor.runtime_repository.last_decision_id(),
        recovery_state=supervisor.recovery_state,
        paused_new_entries=bool(reason_codes),
        reason_codes=reason_codes or ("wca.runtime.health.ok",),
        worker_heartbeats=prior.worker_heartbeats if prior is not None else {},
        database_available=health_checks["database_available"],
        broker_available=health_checks["broker_available"],
        market_data_available=health_checks["market_data_available"],
        clock_skew_seconds=abs(clock_skew),
        reconciliation_age_seconds=reconciliation_age,
        unprotected_position=not health_checks["unprotected_position_clear"],
        duplicate_order_evidence=not health_checks["duplicate_order_evidence_clear"],
        configuration_ready=health_checks["configuration_ready"],
        weight_calibration_ready=health_checks["weight_calibration_ready"],
        circuit_breaker_open=not health_checks["circuit_breaker_closed"],
        latency_summary=supervisor.runtime_repository.read_latency_summaries(account_id=supervisor.settings.account_id, symbol=event.symbol),
        health_checks=health_checks,
    )


def _prior_critical_health_reasons(health: WcaRuntimeHealthSnapshot | None) -> tuple[str, ...]:
    return tuple(
        code
        for code in (health.reason_codes if health is not None else ())
        if code.startswith("wca.runtime.health.") and code != "wca.runtime.health.ok"
    )


def _clean_reconciliation_clears_prior_pause(health: WcaRuntimeHealthSnapshot | None, new_reason_codes: tuple[str, ...]) -> bool:
    if health is None or "wca.runtime.broker_reconciliation.clean" not in new_reason_codes:
        return False
    return any("reconciliation" in code for code in health.reason_codes)


def _entry_freshness_reason_codes(
    supervisor: WcaRuntimeSupervisor,
    *,
    event: WcaFinalizedBarEvent,
    state: WcaAuthoritativeRuntimeState,
    lag_seconds: float,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if lag_seconds > int(supervisor.settings.max_finalized_bar_age_seconds or supervisor.settings.max_lag_seconds):
        reasons.append("wca.runtime.health.finalized_bar_age_exceeded")
    if event.snapshot is not None and event.snapshot.quote is not None:
        quote_age = max(0.0, (_utc_now() - event.snapshot.quote.timestamp.astimezone(timezone.utc)).total_seconds())
        if quote_age > supervisor.settings.max_quote_age_seconds:
            reasons.append("wca.runtime.health.quote_age_exceeded")
    if state.broker_observation_timestamp is not None:
        account_age = max(0.0, (_utc_now() - state.broker_observation_timestamp.astimezone(timezone.utc)).total_seconds())
        if account_age > int(supervisor.settings.max_authoritative_account_state_age_seconds or supervisor.settings.max_state_age_seconds):
            reasons.append("wca.runtime.health.authoritative_account_state_age_exceeded")
    return tuple(dict.fromkeys(reasons))


def _worker_heartbeats_fresh(health: WcaRuntimeHealthSnapshot | None, *, now: datetime, maximum_age_seconds: int) -> bool:
    if health is None or not health.worker_heartbeats:
        return True
    return all((now - heartbeat.astimezone(timezone.utc)).total_seconds() <= maximum_age_seconds for heartbeat in health.worker_heartbeats.values())


def _latest_reconciliation_age_seconds(repository: WcaSqliteRepository, *, account_id: str, symbol: str, now: datetime) -> float | None:
    with repository.connect() as conn:
        row = conn.execute(
            """
            SELECT timestamp
            FROM wca_broker_reconciliations
            WHERE algorithm_id = 'wca' AND account_id = ? AND symbol = ?
            ORDER BY timestamp DESC, created_at DESC
            LIMIT 1
            """,
            (account_id, symbol),
        ).fetchone()
    if row is None:
        return None
    return max(0.0, (now - _payload_datetime(row["timestamp"])).total_seconds())


def _record_is_open_entry(record: Any) -> bool:
    return record.status not in {
        WcaOrderStatus.FILLED.value,
        WcaOrderStatus.CANCELLED.value,
        WcaOrderStatus.REJECTED.value,
        WcaOrderStatus.RECONCILED.value,
        WcaOrderStatus.DEAD_LETTER.value,
        WcaOrderStatus.EXPIRED.value,
    } and not _record_is_risk_reducing_exit(record)


def _record_is_risk_reducing_exit(record: Any) -> bool:
    codes = tuple(str(code) for code in record.proposed_order.reason_codes)
    return any("risk_reducing_exit" in code or ".exit" in code for code in codes)


def _broker_order_is_entry(order: Any) -> bool:
    return not _broker_order_is_protective(order) and str(getattr(order, "symbol", "")).upper() == "SPY"


def _broker_order_is_protective(order: Any) -> bool:
    order_type = str(getattr(order, "orderType", "") or "").upper()
    client_order_id = str(getattr(order, "clientOrderId", "") or "").lower()
    return order_type in {"STOP", "STOP_LIMIT", "TRAILING_STOP"} or getattr(order, "exitOwner", None) == "wca" or "-exit-" in client_order_id or "-protection-" in client_order_id


def _signed_broker_quantity(side: Any, quantity: int) -> int:
    return int(quantity) if _side_text(side) == "BUY" else -int(quantity)


def _side_text(side: Any) -> str:
    return side.value if hasattr(side, "value") else str(side)


def _average_mark_from_lots(lots: tuple[dict[str, Any], ...]) -> float:
    quantity = sum(int(lot["quantity"]) for lot in lots)
    if quantity <= 0:
        return 0.01
    return max(0.01, sum(float(lot["entry_price"]) * int(lot["quantity"]) for lot in lots) / quantity)


def _emergency_mark_price(repository: WcaSqliteRepository, *, command: WcaRuntimeCommand) -> float:
    payload_price = command.payload.get("mark_price") or command.payload.get("markPrice")
    if payload_price is not None:
        return max(0.01, float(payload_price))
    lots = repository.list_open_wca_lots(account_id=command.account_id, symbol=command.symbol)
    if lots:
        return _average_mark_from_lots(lots)
    projection = repository.read_inventory_projection(algorithm_id="wca", broker_account_id=command.account_id, symbol=command.symbol)
    if projection.average_entry_price > 0:
        return max(0.01, float(projection.average_entry_price))
    return 0.01


def _payload_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _command_deadline_expired(command: WcaRuntimeCommand, *, now: datetime | None = None) -> bool:
    return (now or _utc_now()) > command.deadline_at.astimezone(timezone.utc)


def _required_float(value: float | None, field_name: str) -> float:
    if value is None:
        raise ValueError(f"WCA authoritative runtime state missing {field_name}")
    return float(value)


def _required_int(value: int | None, field_name: str) -> int:
    if value is None:
        raise ValueError(f"WCA authoritative runtime state missing {field_name}")
    return int(value)


def _runtime_mode_for_rollout_control(control: WcaRuntimeControl, fallback: WcaRuntimeMode | str) -> WcaRuntimeMode:
    if control.rollout_stage == "LIMITED_AUTOMATIC_PAPER":
        return WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER
    if control.rollout_stage == "AUTOMATIC_PAPER":
        return WcaRuntimeMode.AUTOMATIC_PAPER
    return coerce_wca_runtime_mode(fallback)


def _rollout_caps_from_configuration(configuration: Any | None) -> WcaLimitedAutomaticPaperCaps:
    if configuration is None:
        return WcaLimitedAutomaticPaperCaps()
    limited = getattr(configuration, "limited_automatic_paper", None)
    if limited is None:
        return WcaLimitedAutomaticPaperCaps()
    return WcaLimitedAutomaticPaperCaps(
        symbols=(str(getattr(limited, "symbol", "SPY") or "SPY").upper(),),
        max_quantity=int(getattr(limited, "max_quantity", 10) or 10),
        max_daily_trades=int(getattr(limited, "max_daily_trades", 3) or 3),
        max_daily_loss_dollars=float(getattr(limited, "max_daily_loss_dollars", 100.0) or 100.0),
        session_windows=tuple(str(window) for window in (getattr(limited, "entry_windows", ()) or ())),
        allowed_strategies=tuple(str(strategy_id) for strategy_id in (getattr(limited, "permitted_strategy_ids", ()) or ())),
    )


def _load_persisted_rollout_evidence(repository: WcaSqliteRepository) -> WcaRolloutEvidence:
    payloads: list[dict[str, Any]] = []
    persisted_ids: set[str] = set()
    try:
        with repository.connect() as conn:
            rows = conn.execute(
                """
                SELECT evidence_id, payload_json
                FROM wca_rollout_evidence
                WHERE algorithm_id = 'wca'
                ORDER BY created_at ASC
                """
            ).fetchall()
    except Exception:
        return WcaRolloutEvidence()
    for row in rows:
        evidence_id = str(row["evidence_id"] or "")
        if evidence_id:
            persisted_ids.add(evidence_id)
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        payloads.append(payload)
        payload_id = payload.get("evidence_id") or payload.get("evidenceId")
        if payload_id:
            persisted_ids.add(str(payload_id))
        persisted_ids.update(str(value) for value in (payload.get("persisted_evidence_ids") or payload.get("persistedEvidenceIds") or ()) if value)
    if not payloads and not persisted_ids:
        return WcaRolloutEvidence()

    def _bool(name: str) -> bool:
        camel = _camel(name)
        return any(bool(payload.get(name, payload.get(camel, False))) for payload in payloads)

    def _max_float(name: str) -> float | None:
        values = []
        camel = _camel(name)
        for payload in payloads:
            value = payload.get(name, payload.get(camel))
            if value is not None:
                values.append(float(value))
        return max(values) if values else None

    def _max_int(name: str) -> int:
        value = _max_float(name)
        return int(value) if value is not None else 0

    def _union(name: str) -> tuple[str, ...]:
        values: set[str] = set()
        camel = _camel(name)
        for payload in payloads:
            raw = payload.get(name, payload.get(camel)) or ()
            if isinstance(raw, str):
                values.add(raw)
            else:
                values.update(str(item) for item in raw if item)
        return tuple(sorted(values))

    return WcaRolloutEvidence(
        persisted_evidence_ids=frozenset(persisted_ids),
        prior_steps_passed=_bool("prior_steps_passed"),
        deterministic_replay_parity=_bool("deterministic_replay_parity"),
        unexplained_decision_mismatches=_max_int("unexplained_decision_mismatches"),
        duplicate_broker_orders=_max_int("duplicate_broker_orders"),
        cross_algorithm_inventory_mutations=_max_int("cross_algorithm_inventory_mutations"),
        restart_recovery_passed=_bool("restart_recovery_passed"),
        reconciliation_passed=_bool("reconciliation_passed"),
        unprotected_positions=_max_int("unprotected_positions"),
        max_event_lag_seconds=_max_float("max_event_lag_seconds"),
        max_decision_latency_seconds=_max_float("max_decision_latency_seconds"),
        max_broker_latency_seconds=_max_float("max_broker_latency_seconds"),
        average_realised_slippage_per_share=_max_float("average_realised_slippage_per_share"),
        market_conditions=_union("market_conditions"),
        session_periods=_union("session_periods"),
        high_volatility_sessions=_max_int("high_volatility_sessions"),
        economic_event_sessions=_max_int("economic_event_sessions"),
        paper_observation_days=float(_max_float("paper_observation_days") or 0.0),
        paper_trade_count=_max_int("paper_trade_count"),
        rollback_tested=_bool("rollback_tested"),
        rollback_restored_safe_state=_bool("rollback_restored_safe_state"),
        critical_failure_open=_bool("critical_failure_open"),
        reconciliation_after_failure_passed=_bool("reconciliation_after_failure_passed"),
        healthy_state_validation_passed=_bool("healthy_state_validation_passed"),
        live_trading_enabled=_bool("live_trading_enabled"),
    )


def _camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


def _runtime_order_validation_context(
    command: WcaRuntimeCommand,
    decision: WcaDecision,
    request: WcaPaperBrokerOrderRequest,
    *,
    supervisor: WcaRuntimeSupervisor,
    runtime_control: WcaRuntimeControl,
    broker_clock: WcaBrokerClock | None,
    automatic_paper_enabled: bool,
    authoritative_state: WcaAuthoritativeRuntimeState | None = None,
) -> WcaOrderValidationContext:
    quote_required = decision.proposed_order is not None and not _is_risk_reducing_exit(decision)
    settings = decision.effective_settings
    rollout_stage = decision.rollout_stage or runtime_control.rollout_stage
    limited_stage = rollout_stage == "LIMITED_AUTOMATIC_PAPER"
    now = _utc_now()
    entry_cutoff = settings.final_entry_cutoff_minutes if settings is not None else 15 * 60 + 30
    session = validate_wca_entry_session(
        timestamp=now,
        entry_cutoff_minutes=entry_cutoff,
        broker_clock=broker_clock,
        require_broker_clock=quote_required,
    )
    inventory_consistent = bool(
        authoritative_state is not None
        and authoritative_state.fresh
        and not authoritative_state.position_inconsistent
        and not authoritative_state.position_unprotected
    )
    current_side = None
    if authoritative_state is not None and authoritative_state.current_position_direction:
        current_side = authoritative_state.current_position_direction
    max_daily_loss = settings.final_max_daily_loss_dollars if settings is not None else None
    max_daily_trades = settings.final_max_daily_trades if settings is not None else None
    return WcaOrderValidationContext(
        evaluation_timestamp=now,
        paper_only_mode=True,
        account_id=command.account_id,
        broker_endpoint="paper",
        runtime_mode=decision.runtime_mode,
        rollout_stage=rollout_stage,
        rollout_evidence_revision=decision.rollout_evidence_revision or runtime_control.rollout_evidence_revision,
        rollout_evidence_hash=decision.rollout_evidence_hash or runtime_control.rollout_evidence_hash,
        rollout_allowed_symbols=(command.symbol.upper(),) if limited_stage else (),
        rollout_allowed_strategy_ids=settings.final_permitted_strategy_ids if limited_stage and settings is not None else (),
        rollout_allowed_entry_windows=settings.final_entry_windows if limited_stage and settings is not None else (),
        rollout_max_quantity=settings.final_max_allowed_shares if limited_stage and settings is not None and settings.final_max_allowed_shares > 0 else None,
        rollout_max_daily_trades=settings.final_max_daily_trades if limited_stage and settings is not None else None,
        rollout_max_daily_loss=settings.final_max_daily_loss_dollars if limited_stage and settings is not None else None,
        rollout_policy_required=True,
        requires_executable_paper_stage=True,
        automatic_paper_enabled=automatic_paper_enabled,
        market_is_open=session.market_is_open,
        allowed_session_window=session.allowed_session_window,
        market_session_reason_codes=session.reason_codes,
        candle_freshness_seconds=int(supervisor.settings.max_finalized_bar_age_seconds or supervisor.settings.max_lag_seconds),
        data_ready=decision.market_snapshot.data_ready,
        inventory_consistent=inventory_consistent,
        max_approved_quantity=decision.global_gate_result.allowed_quantity if decision.global_gate_result is not None else None,
        order_type=request.order_type,
        time_in_force=request.time_in_force,
        protective_exit_plan_present=decision.proposed_order is None or (decision.proposed_order.stop_price is not None and decision.proposed_order.target_price is not None),
        current_position_quantity=abs(int(authoritative_state.current_quantity)) if authoritative_state is not None else 0,
        current_position_side=current_side,
        position_owned_by_wca=True,
        quote_freshness_seconds=supervisor.settings.max_quote_age_seconds if quote_required else None,
        decision_expiration_seconds=int(supervisor.settings.max_finalized_bar_age_seconds or supervisor.settings.max_lag_seconds),
        command_deadline_at=command.deadline_at,
        available_buying_power=authoritative_state.buying_power if authoritative_state is not None else None,
        account_equity=authoritative_state.equity if authoritative_state is not None else None,
        max_position_value=authoritative_state.buying_power if authoritative_state is not None else None,
        realized_daily_loss=authoritative_state.daily_loss if authoritative_state is not None else None,
        max_daily_loss=max_daily_loss,
        trades_today=authoritative_state.daily_trade_count if authoritative_state is not None else None,
        max_daily_trades=max_daily_trades,
        max_spread_percent=decision.effective_settings.final_max_spread_percent if decision.effective_settings is not None else None,
        average_one_minute_volume=None,
        max_participation_percent=decision.effective_settings.final_max_participation_percent if decision.effective_settings is not None else None,
        expected_net_edge=decision.cost_estimate.conservative_net_edge_per_share if decision.cost_estimate is not None else None,
        minimum_net_edge=decision.effective_settings.final_minimum_net_edge_per_share if decision.effective_settings is not None else 0,
        idempotency_required=True,
        pending_wca_entry=bool(
            authoritative_state is not None
            and _has_pending_entry_other_than(authoritative_state, decision.proposed_order.order_intent_id if decision.proposed_order is not None else "")
        ),
        cooldown_active=bool(authoritative_state is not None and authoritative_state.cooldown_state.get("active")),
        circuit_breaker_open=bool(authoritative_state is not None and str(authoritative_state.circuit_breaker_state or "").lower() in {"open", "halted", "tripped"}),
        new_entry_permitted=bool((decision.global_gate_result.entry_permitted if decision.global_gate_result is not None else True) and (authoritative_state.account_wide_entry_permission if authoritative_state is not None else True)),
        risk_reducing_exit_permitted=decision.global_gate_result.risk_reducing_exit_permitted if decision.global_gate_result is not None else True,
        is_risk_reducing_exit=not quote_required,
    )


def _has_pending_entry_other_than(state: WcaAuthoritativeRuntimeState, order_intent_id: str) -> bool:
    for order in state.pending_entry_orders:
        if str(order.get("order_intent_id") or "") != order_intent_id:
            return True
    return False


def _apply_durable_global_risk_approval(decision: WcaDecision, gate: GlobalGateResult, *, account_id: str) -> WcaDecision:
    proposed = decision.proposed_order
    sizing = decision.sizing.model_copy(
        update={
            "final_quantity": gate.allowed_quantity,
            "stop_risk_dollars": gate.allowed_quantity * decision.sizing.stop_distance,
            "shares_by_global_gate": gate.allowed_quantity,
            "approved_risk_budget": gate.approved_risk,
            "reason_codes": (*decision.sizing.reason_codes, *gate.reason_codes),
        }
    )
    order = None
    if proposed is not None and gate.entry_permitted and gate.allowed_quantity > 0:
        order = proposed.model_copy(
            update={
                "quantity": gate.allowed_quantity,
                "account_id": account_id,
                "idempotency_key": gate.idempotency_key or proposed.idempotency_key,
                "reason_codes": (*proposed.reason_codes, *gate.reason_codes),
            }
        )
    approved = decision.model_copy(
        update={
            "sizing": sizing,
            "proposed_order": order,
            "global_gate_result": gate,
            "reason_codes": tuple(dict.fromkeys((*decision.reason_codes, *gate.reason_codes))),
        }
    )
    return approved.model_copy(update={"decision_hash": approved.deterministic_hash()})


def _global_state_hash(state: WcaAuthoritativeRuntimeState) -> str:
    payload = {
        "global_risk": state.global_risk,
        "account_wide_entry_permission": state.account_wide_entry_permission,
        "account_wide_exit_permission": state.account_wide_exit_permission,
        "maximum_approved_quantity": state.maximum_approved_quantity,
        "remaining_portfolio_risk": state.remaining_portfolio_risk,
        "global_circuit_breaker_status": state.global_circuit_breaker_status,
        "inventory_state_version": state.inventory_state_version,
        "broker_observation_timestamp": state.broker_observation_timestamp.isoformat() if state.broker_observation_timestamp else None,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _global_risk_worker_reason_codes(reason_codes: tuple[str, ...], allowed_quantity: int, requested_quantity: int) -> tuple[str, ...]:
    compatibility = "wca.global_risk.approved" if allowed_quantity == requested_quantity and allowed_quantity > 0 else "wca.global_risk.reduced_or_rejected"
    return tuple(dict.fromkeys((WCA_GLOBAL_RISK_ADAPTER_VERSION, *reason_codes, compatibility, "wca.global_risk.durable_approval_persisted")))


def _global_risk_submission_block_reasons(
    supervisor: WcaRuntimeSupervisor,
    command: WcaRuntimeCommand,
    decision: WcaDecision,
) -> tuple[tuple[str, ...], WcaAuthoritativeRuntimeState | None]:
    if decision.proposed_order is None:
        return (), None
    approval = supervisor.repository.read_global_risk_approval(decision_id=decision.decision_id)
    if approval is None:
        return ("wca.runtime.execution_outbox.global_risk_approval_missing",), None
    now = _utc_now()
    reasons: list[str] = []
    if approval.expires_at is None or approval.expires_at <= now:
        reasons.append("wca.runtime.execution_outbox.global_risk_approval_expired")
    if not approval.entry_permitted:
        reasons.append("wca.runtime.execution_outbox.global_risk_entry_rejected")
    if decision.proposed_order.quantity > approval.allowed_quantity:
        reasons.append("wca.runtime.execution_outbox.quantity_exceeds_global_risk_approval")
    state = load_wca_authoritative_runtime_state(
        supervisor.repository,
        broker_account_id=command.account_id,
        symbol=command.symbol,
        state_timestamp=now,
        maximum_permitted_state_age_seconds=int(supervisor.settings.max_authoritative_account_state_age_seconds or supervisor.settings.max_state_age_seconds),
    )
    if not state.fresh:
        reasons.extend(("wca.runtime.execution_outbox.authoritative_state_blocked", *state.reason_codes))
    if not state.account_wide_entry_permission:
        reasons.append("wca.runtime.execution_outbox.account_wide_circuit_breaker_open")
    current_global_hash = _global_state_hash(state)
    approved_reservation = _state_contains_only_current_approved_reservation(state, decision, approval)
    if approval.global_state_hash and approval.global_state_hash != current_global_hash and not approved_reservation:
        reasons.append("wca.runtime.execution_outbox.global_risk_revision_stale")
    if approval.global_state_revision and approval.global_state_revision != state.inventory_state_version and not approved_reservation:
        reasons.append("wca.runtime.execution_outbox.global_risk_state_revision_stale")
    return tuple(dict.fromkeys(reasons)), state


def _state_contains_only_current_approved_reservation(
    state: WcaAuthoritativeRuntimeState,
    decision: WcaDecision,
    approval: WcaGlobalRiskApprovalRecord,
) -> bool:
    proposed = decision.proposed_order
    if proposed is None:
        return False
    pending_entries = tuple(state.pending_entry_orders or ())
    if not pending_entries:
        return False
    if any(str(order.get("order_intent_id") or "") != proposed.order_intent_id for order in pending_entries):
        return False
    if proposed.quantity > approval.allowed_quantity:
        return False
    reserved_risk = float(state.reserved_risk or 0.0)
    if approval.approved_risk_dollars > 0 and reserved_risk > approval.approved_risk_dollars + 1e-6:
        return False
    return True


def _is_risk_reducing_exit(decision: WcaDecision) -> bool:
    return decision.global_gate_result is not None and not decision.global_gate_result.entry_permitted and decision.global_gate_result.risk_reducing_exit_permitted


def _runtime_control_evidence(
    supervisor: WcaRuntimeSupervisor,
    *,
    prior: WcaRuntimeControl,
    event: WcaFinalizedBarEvent | None,
    state: WcaAuthoritativeRuntimeState | None,
    configuration: Any | None,
    weights: Any | None,
    calibration_count: int | None,
    health: WcaRuntimeHealthSnapshot | None,
) -> WcaRuntimeControlEvidence:
    checked_at = event.publication_timestamp if event is not None else _utc_now()
    paper_account = validate_wca_automatic_paper_account(account_id=prior.broker_account_id)
    rollout_flags = wca_rollout_feature_flags()
    rollout_decision = evaluate_wca_automatic_paper_rollout(
        flags=rollout_flags,
        evidence=_load_persisted_rollout_evidence(supervisor.repository),
        caps=_rollout_caps_from_configuration(configuration),
        configured_stage=getattr(getattr(configuration, "limited_automatic_paper", None), "rollout_stage", None),
    )
    rollout_allowed = rollout_decision.permitted
    timestamp = event.finalized_candle_timestamp if event is not None else checked_at
    session = WcaMarketCalendar().session_for(timestamp)
    market_open = bool(session and session.market_open <= timestamp.astimezone(session.market_open.tzinfo) < session.market_close)
    try:
        effective_settings = default_effective_settings(configuration)
        inside_entry_window = _inside_wca_entry_window(timestamp, tuple(effective_settings.final_entry_windows or ()))
    except Exception:
        inside_entry_window = False
    max_age = int(supervisor.settings.max_finalized_bar_age_seconds or supervisor.settings.max_state_age_seconds)
    if event is None:
        market_data_fresh = bool(health and health.last_processed_bar and health.lag_seconds <= max_age)
    else:
        market_data_fresh = bool(event.snapshot and event.snapshot.data_ready and max(0.0, (checked_at - event.finalized_candle_timestamp).total_seconds()) <= max_age)
    health_critical = critical_health_reason_codes(health) if health is not None else ("wca.runtime_control.runtime_health_missing",)
    runtime_healthy = bool(health is not None and not health_critical and health.status in {"healthy", "idle", "protective_only"})
    inventory_reconciled = bool(state and state.fresh and not supervisor.repository.reconciliation_blocks_new_entries(account_id=prior.broker_account_id, symbol=prior.symbol))
    wca_breaker_closed = bool(
        not supervisor.repository.wca_position_circuit_breaker_open(account_id=prior.broker_account_id, symbol=prior.symbol)
        and str(state.circuit_breaker_state if state is not None else "").lower() not in {"open", "halted", "tripped"}
    )
    global_status = str(state.global_circuit_breaker_status if state is not None else "unknown").lower()
    global_breaker_closed = bool(global_status not in {"open", "halted", "tripped", "unknown"})
    global_gate_available = bool(state and state.account_wide_entry_permission and state.maximum_approved_quantity >= 0 and global_breaker_closed)
    calibration_ready = bool(configuration is not None and (not getattr(configuration.calibration, "enabled", False) or (calibration_count or 0) > 0))

    dependency_health = _dependency_health(
        checked_at=checked_at,
        checks={
            "user_paper_requested": (prior.paper_trading_requested, "wca.runtime_control.paper_trading_not_requested"),
            "wca_automatic_requested": (prior.automatic_entries_requested, "wca.runtime_control.automatic_entries_not_requested"),
            "pause_new_entries_clear": (not prior.pause_new_entries, "wca.runtime_control.pause_new_entries"),
            "kill_switch_clear": (not prior.kill_switch_open, "wca.runtime_control.kill_switch_open"),
            "wca_paper_execution_enabled": (rollout_flags.paper_execution_enabled, "wca.runtime_control.paper_execution_env_disabled"),
            "rollout_stage_permits_automatic_paper": (rollout_allowed, "wca.runtime_control.rollout_automatic_paper_blocked"),
            "global_gate_available": (global_gate_available, "wca.runtime_control.global_gate_unavailable"),
            "runtime_healthy": (runtime_healthy, "wca.runtime_control.runtime_unhealthy"),
            "paper_account_verified": (paper_account.verified, "wca.runtime_control.paper_account_unverified"),
            "market_open": (market_open, "wca.runtime_control.market_closed"),
            "inside_entry_window": (inside_entry_window, "wca.runtime_control.outside_entry_window"),
            "market_data_fresh": (market_data_fresh, "wca.runtime_control.market_data_stale"),
            "inventory_reconciled": (inventory_reconciled, "wca.runtime_control.inventory_not_reconciled"),
            "wca_circuit_breaker_closed": (wca_breaker_closed, "wca.runtime_control.wca_circuit_breaker_open"),
            "global_circuit_breaker_closed": (global_breaker_closed, "wca.runtime_control.global_circuit_breaker_open"),
            "configuration_ready": (configuration is not None, "wca.runtime_control.configuration_not_ready"),
            "weight_ready": (weights is not None, "wca.runtime_control.weight_snapshot_not_ready"),
            "calibration_ready": (calibration_ready, "wca.runtime_control.calibration_not_ready"),
        },
    )
    return WcaRuntimeControlEvidence(
        paper_execution_env_enabled=bool(rollout_flags.paper_execution_enabled),
        rollout_automatic_paper_permitted=rollout_allowed,
        global_gate_available=global_gate_available,
        runtime_healthy=runtime_healthy,
        paper_account_verified=paper_account.verified,
        market_open=market_open,
        inside_entry_window=inside_entry_window,
        market_data_fresh=market_data_fresh,
        inventory_reconciled=inventory_reconciled,
        wca_circuit_breaker_closed=wca_breaker_closed,
        global_circuit_breaker_closed=global_breaker_closed,
        configuration_ready=configuration is not None,
        weight_ready=weights is not None,
        calibration_ready=calibration_ready,
        rollout_stage=rollout_decision.stage,
        rollout_evidence_revision=rollout_decision.evidence_revision,
        rollout_evidence_hash=rollout_decision.evidence_hash,
        rollout_reason_codes=rollout_decision.reason_codes,
        limited_automatic_paper_caps=rollout_decision.caps.model_dump(),
        cancel_unfilled_entry_orders_required=True,
        dependency_health=dependency_health,
        reason_codes=tuple(dict.fromkeys((*paper_account.reason_codes, *rollout_decision.reason_codes))),
    )


def _stamp_decision_with_runtime_control(decision: WcaDecision, control: WcaRuntimeControl) -> WcaDecision:
    proposed = decision.proposed_order
    if proposed is not None:
        proposed = proposed.model_copy(
            update={
                "runtime_control_revision": control.control_revision,
                "runtime_control_hash": control.control_hash,
                "rollout_stage": control.rollout_stage,
                "rollout_evidence_revision": control.rollout_evidence_revision,
                "rollout_evidence_hash": control.rollout_evidence_hash,
                "weight_version": decision.weight_version,
                "reason_codes": (
                    *proposed.reason_codes,
                    f"wca.runtime_control.revision.{control.control_revision}",
                    f"wca.rollout.stage.{control.rollout_stage.lower()}",
                ),
            }
        )
    stamped = decision.model_copy(
        update={
            "proposed_order": proposed,
            "runtime_control_revision": control.control_revision,
            "runtime_control_hash": control.control_hash,
            "runtime_control_reason_codes": control.reason_codes,
            "rollout_stage": control.rollout_stage,
            "rollout_evidence_revision": control.rollout_evidence_revision,
            "rollout_evidence_hash": control.rollout_evidence_hash,
            "rollout_reason_codes": control.rollout_reason_codes,
            "reason_codes": (
                *decision.reason_codes,
                "wca.runtime_control.stamped",
                *control.reason_codes,
            ),
        }
    )
    return stamped.model_copy(update={"decision_hash": stamped.deterministic_hash()})


def _apply_runtime_control_transition(repository: WcaSqliteRepository, control: WcaRuntimeControl) -> dict[str, Any]:
    transition = {
        "newEntriesBlocked": not control.effective_automatic_entries_enabled,
        "riskReducingExitsEnabled": True,
        "protectiveOrdersEnabled": True,
        "reconciliationContinues": True,
        "cancelledUnsubmittedEntryOrderIntentIds": [],
        "reasonCodes": list(control.reason_codes),
    }
    if control.paper_trading_requested or not control.cancel_unfilled_entry_orders_required:
        return transition
    for record in repository.list_execution_outbox_records(account_id=control.broker_account_id):
        if record.symbol.upper() != control.symbol.upper() or str(record.status).upper() != WcaOrderStatus.RESERVED.value:
            continue
        if not _record_is_open_entry(record):
            continue
        if repository.update_execution_outbox_state(
            outbox_id=record.outbox_id,
            status=WcaOrderStatus.CANCELLED,
            response_payload={
                "reason": "wca.runtime_control.paper_off_cancel_unsubmitted_entry",
                "controlRevision": control.control_revision,
                "controlHash": control.control_hash,
            },
        ):
            repository.record_order_terminal_inventory_event(
                record.decision,
                account_id=record.account_id,
                client_order_id=record.client_order_id,
                event_type="ORDER_CANCELLED",
                event_timestamp=control.updated_at,
                payload={"runtime_control": control.api_dict(), "reason": "paper_off"},
            )
            transition["cancelledUnsubmittedEntryOrderIntentIds"].append(record.order_intent_id)
    return transition


def _runtime_control_submission_block_reasons(decision: WcaDecision, control: WcaRuntimeControl) -> tuple[str, ...]:
    reasons: list[str] = []
    if decision.runtime_control_revision is None or not decision.runtime_control_hash:
        reasons.append("wca.runtime_control.decision_missing_control_revision")
    elif int(decision.runtime_control_revision) != int(control.control_revision) or decision.runtime_control_hash != control.control_hash:
        reasons.append("wca.runtime_control.decision_control_revision_stale")
    if not control.effective_automatic_entries_enabled:
        reasons.extend(("wca.runtime_control.effective_automatic_entries_disabled", *control.reason_codes))
    if control.rollout_stage not in WCA_AUTOMATIC_PAPER_ROLLOUT_STAGES:
        reasons.extend(("wca.runtime_control.rollout_stage_not_automatic_paper", *control.rollout_reason_codes))
    if decision.rollout_evidence_revision and decision.rollout_evidence_revision != control.rollout_evidence_revision:
        reasons.append("wca.runtime_control.rollout_evidence_revision_stale")
    if decision.rollout_evidence_hash and decision.rollout_evidence_hash != control.rollout_evidence_hash:
        reasons.append("wca.runtime_control.rollout_evidence_hash_stale")
    return tuple(dict.fromkeys(reasons))


def _pre_submit_market_session_check(
    supervisor: WcaRuntimeSupervisor,
    command: WcaRuntimeCommand,
    record: Any,
    broker_request: WcaPaperBrokerOrderRequest,
    *,
    broker: WcaAlpacaPaperBroker,
    runtime_control: WcaRuntimeControl,
) -> tuple[bool, tuple[str, ...]]:
    if _is_risk_reducing_exit(record.decision):
        return True, ("wca.runtime.pre_submit.risk_reducing_exit_session_bypass",)
    if _command_deadline_expired(command):
        return False, ("wca.runtime.pre_submit.command_deadline_expired",)
    latest_control = supervisor.repository.read_runtime_control(broker_account_id=command.account_id, symbol=command.symbol)
    control_reasons = _runtime_control_submission_block_reasons(record.decision, latest_control)
    if control_reasons:
        return False, ("wca.runtime.pre_submit.runtime_control_blocked", *control_reasons)
    approval_reasons, approval_state = _global_risk_submission_block_reasons(supervisor, command, record.decision.model_copy(update={"proposed_order": record.proposed_order}))
    if approval_reasons:
        return False, ("wca.runtime.pre_submit.global_risk_blocked", *approval_reasons)
    try:
        broker_clock = broker.read_clock()
    except Exception as exc:
        return False, ("wca.runtime.pre_submit.broker_clock_unavailable", type(exc).__name__)
    from backend.app.algorithms.wca.order_validation import validate_wca_final_order

    context = _runtime_order_validation_context(
        command,
        record.decision.model_copy(update={"proposed_order": record.proposed_order}),
        broker_request,
        supervisor=supervisor,
        runtime_control=latest_control if latest_control is not None else runtime_control,
        broker_clock=broker_clock,
        automatic_paper_enabled=latest_control.effective_automatic_entries_enabled,
        authoritative_state=approval_state,
    )
    validation = validate_wca_final_order(record.decision.model_copy(update={"proposed_order": record.proposed_order}), context)
    if validation.valid:
        return True, ("wca.runtime.pre_submit.market_session_validated", *context.market_session_reason_codes)
    return False, ("wca.runtime.pre_submit.market_session_blocked", *validation.reason_codes)


def _dependency_health(*, checked_at: datetime, checks: dict[str, tuple[bool, str]]) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "healthy": bool(passed),
            "reasonCodes": [] if passed else [reason],
            "checkedAt": checked_at.isoformat(),
        }
        for name, (passed, reason) in checks.items()
    }


def _inside_wca_entry_window(timestamp: datetime, windows: tuple[str, ...]) -> bool:
    if not windows:
        return True
    session = WcaMarketCalendar().session_for(timestamp)
    local = timestamp.astimezone(session.market_open.tzinfo) if session is not None else timestamp
    current = local.hour * 60 + local.minute
    for window in windows:
        times = str(window).rsplit(" ", 1)[0]
        start, end = times.split("-", 1)
        if _minutes(start) <= current <= _minutes(end):
            return True
    return False


def _minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


class _RuntimeEmptyPaperBroker:
    def __init__(self, *, account_id: str) -> None:
        self.account_id = account_id

    def refresh_account_snapshot(self) -> BrokerAccountSnapshot:
        now = _utc_now()
        return BrokerAccountSnapshot(
            accountId=self.account_id,
            equity=0,
            buyingPower=0,
            realizedPnlToday=0,
            positions=[],
            pendingOrders=[],
            partiallyFilledOrders=[],
            observedAt=now,
            sessionDate=now.date(),
            sourceAuthority="unknown",
        )

    def refresh_order(self, client_order_id: str) -> BrokerFillUpdate | None:
        return None


_WCA_RUNTIME_SUPERVISOR: WcaRuntimeSupervisor | None = None


def get_wca_runtime_supervisor() -> WcaRuntimeSupervisor:
    global _WCA_RUNTIME_SUPERVISOR
    if _WCA_RUNTIME_SUPERVISOR is None:
        _WCA_RUNTIME_SUPERVISOR = WcaRuntimeSupervisor()
    return _WCA_RUNTIME_SUPERVISOR


__all__ = [
    "WCA_RUNTIME_REQUIRES_OS_PROCESS",
    "WCA_RUNTIME_COMMAND_CONSUMERS",
    "WCA_RUNTIME_COMMAND_RETRY_POLICY",
    "WCA_RUNTIME_SUPERVISOR_VERSION",
    "WCA_RUNTIME_WORKERS",
    "WcaRuntimeSettings",
    "WcaRuntimeSupervisor",
    "get_wca_runtime_supervisor",
]
