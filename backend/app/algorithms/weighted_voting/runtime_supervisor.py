"""Background one-minute runtime supervisor for Weighted Voting."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.broker_reconciliation import (
    WeightedVotingBrokerFillObservation,
    WeightedVotingBrokerPositionObservation,
    reconcile_weighted_voting_broker_observations,
)
from backend.app.algorithms.weighted_voting.decision_gates import WeightedVotingGatePipelineResult
from backend.app.algorithms.weighted_voting.dynamic_settings import resolve_effective_settings
from backend.app.algorithms.weighted_voting.execution_gateway import (
    WEIGHTED_VOTING_EXECUTION_NAMESPACE,
    WeightedVotingExecutionQueueItem,
    enqueue_weighted_voting_execution_order,
    submit_queued_weighted_voting_paper_order,
)
from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.inventory import CURRENT_SNAPSHOT_KEY, WeightedVotingInventoryRepository
from backend.app.algorithms.weighted_voting.market_condition import classify_market_condition
from backend.app.algorithms.weighted_voting.market_snapshot import build_weighted_voting_market_snapshot
from backend.app.algorithms.weighted_voting.models import WeightedEffectiveSettings, WeightedMarketSnapshot, WeightedWeightState
from backend.app.algorithms.weighted_voting.persistence import (
    WEIGHTED_VOTING_SETTINGS_KEY,
    WeightedVotingFilesystemStateStore,
    WeightedVotingStateStore,
    load_effective_settings,
    persist_effective_settings,
)
from backend.app.algorithms.weighted_voting.position_manager import WeightedVotingPositionManagerService
from backend.app.algorithms.weighted_voting.rollout import WeightedVotingRolloutFlags, WeightedVotingRolloutValidation, automatic_submission_allowed
from backend.app.algorithms.weighted_voting.runtime_context import (
    WeightedVotingAccountObservationPort,
    WeightedVotingExecutionCostEstimate,
    WeightedVotingGlobalRiskPort,
    WeightedVotingRuntimeContext,
    WeightedVotingRuntimeContextBuilder,
    WeightedVotingStaticMarketDataPort,
    WeightedVotingUnavailableAccountPort,
    WeightedVotingUnavailableGlobalRiskPort,
)
from backend.app.algorithms.weighted_voting.scheduler import ACTIVE_WEIGHT_STATE_KEY
from backend.app.algorithms.weighted_voting.service import WeightedVotingService
from backend.app.algorithms.weighted_voting.strategy_lifecycle import WEIGHTED_VOTING_STRATEGY_LIFECYCLE_LATEST_KEY
from backend.app.execution import PaperOrderGateway
from backend.app.gates import AppliedGlobalGateDecision, GlobalOrderProposal


WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION = "weighted_voting_runtime_supervisor_v1"
RUNTIME_STATUS_KEY = "weighted_voting.runtime.status"
RUNTIME_HEARTBEAT_KEY = "weighted_voting.runtime.heartbeat"
RUNTIME_EVENT_PREFIX = "weighted_voting.runtime.events."
RUNTIME_CHECKPOINT_PREFIX = "weighted_voting.runtime.checkpoints."
RUNTIME_EXECUTION_PREFIX = "weighted_voting.runtime.executions."
RUNTIME_ADMIN_AUDIT_PREFIX = "weighted_voting.runtime.admin_audit."
RUNTIME_STRATEGY_CONTROL_PREFIX = "weighted_voting.runtime.strategy_controls."
RUNTIME_EMERGENCY_FLATTEN_PREFIX = "weighted_voting.runtime.emergency_flatten."
RUNTIME_RECOVERY_STATE_KEY = "weighted_voting.runtime.recovery.state"
RUNTIME_RECOVERY_EVENT_PREFIX = "weighted_voting.runtime.recovery.events."
RUNTIME_QUARANTINE_PREFIX = "weighted_voting.runtime.quarantine."
RUNTIME_HEALTHY_STATE_KEY = "weighted_voting.runtime.recovery.healthy_state"
LAST_APPROVED_SETTINGS_KEY = "weighted_voting.settings.last_approved"
LAST_APPROVED_WEIGHT_STATE_KEY = "weighted_voting.weights.last_approved"


@dataclass(frozen=True)
class WeightedVotingFinalisedBarEvent:
    algorithm_id: Literal["weighted_voting"]
    symbol: str
    finalised_candle_timestamp: datetime
    data_manifest_hash: str
    market_payload: dict[str, Any]
    published_at: datetime
    event_id: str = ""
    replay_recovery: bool = False

    def __post_init__(self) -> None:
        if self.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("Weighted Voting finalised-bar events require algorithm_id weighted_voting")
        if self.finalised_candle_timestamp.tzinfo is None:
            object.__setattr__(self, "finalised_candle_timestamp", self.finalised_candle_timestamp.replace(tzinfo=timezone.utc))
        if self.published_at.tzinfo is None:
            object.__setattr__(self, "published_at", self.published_at.replace(tzinfo=timezone.utc))
        if not self.event_id:
            object.__setattr__(self, "event_id", _hash_payload(self.as_dict(exclude_event_id=True)))

    def as_dict(self, *, exclude_event_id: bool = False) -> dict[str, Any]:
        payload = {
            "algorithm_id": self.algorithm_id,
            "symbol": self.symbol,
            "finalised_candle_timestamp": self.finalised_candle_timestamp.isoformat(),
            "data_manifest_hash": self.data_manifest_hash,
            "market_payload": self.market_payload,
            "published_at": self.published_at.isoformat(),
            "event_id": self.event_id,
            "replay_recovery": self.replay_recovery,
        }
        if exclude_event_id:
            payload.pop("event_id", None)
        return payload


@dataclass(frozen=True)
class WeightedVotingRuntimeConfig:
    symbols: tuple[str, ...] = ("SPY",)
    queue_maxsize: int = 256
    max_queue_lag_seconds: int = 75
    worker_restart_failure_threshold: int = 3
    heartbeat_interval_seconds: float = 30.0
    maintenance_interval_seconds: float = 60.0


@dataclass
class WeightedVotingRuntimeMetrics:
    supervisor_started: bool = False
    automatic_order_creation_paused: bool = True
    paused: bool = False
    queue_depth: int = 0
    execution_queue_depth: int = 0
    processed_events: int = 0
    duplicate_events: int = 0
    rejected_events: int = 0
    stale_events: int = 0
    out_of_order_events: int = 0
    persisted_decisions: int = 0
    enqueued_orders: int = 0
    submitted_orders: int = 0
    rejected_execution_events: int = 0
    entry_creation_paused_for_reconciliation: bool = False
    inventory_reconciled: bool = False
    risk_reducing_exits_allowed: bool = True
    worker_failures: dict[str, int] = field(default_factory=dict)
    worker_restarts: dict[str, int] = field(default_factory=dict)
    last_event_timestamp_by_symbol: dict[str, str] = field(default_factory=dict)
    last_checkpoint_by_symbol: dict[str, str] = field(default_factory=dict)
    last_decision_id: str | None = None
    last_finalised_bar_received: dict[str, Any] | None = None
    last_bar_processed: dict[str, Any] | None = None
    processing_lag_seconds: float | None = None
    last_accepted_proposal: dict[str, Any] | None = None
    last_global_risk_response: dict[str, Any] | None = None
    last_order_submission: dict[str, Any] | None = None
    last_fill: dict[str, Any] | None = None
    last_reconciliation: dict[str, Any] | None = None
    pause_reason: str | None = None
    decision_latency_ms: float | None = None
    risk_service_latency_ms: float | None = None
    broker_latency_ms: float | None = None
    gate_rejection_counts: dict[str, int] = field(default_factory=dict)
    strategy_opportunity_counts: dict[str, int] = field(default_factory=dict)
    strategy_signal_counts: dict[str, dict[str, int]] = field(default_factory=lambda: {"active": {}, "shadow": {}})
    proposed_vs_allowed_quantity: dict[str, Any] = field(default_factory=dict)
    fill_quality: dict[str, Any] = field(default_factory=dict)
    slippage: dict[str, Any] = field(default_factory=dict)
    reconciliation_discrepancies: int = 0
    recovery_required: bool = False
    recovery_state: dict[str, Any] = field(default_factory=dict)
    quarantined_snapshots: int = 0
    circuit_breaker_open: bool = False
    last_error: str | None = None


class WeightedVotingEventBus:
    def __init__(self, *, maxsize: int = 256) -> None:
        self.queue: asyncio.Queue[WeightedVotingFinalisedBarEvent] = asyncio.Queue(maxsize=maxsize)
        self.dropped_events = 0

    async def publish(self, event: WeightedVotingFinalisedBarEvent) -> bool:
        try:
            self.queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            self.dropped_events += 1
            return False

    async def next_event(self) -> WeightedVotingFinalisedBarEvent:
        return await self.queue.get()

    def task_done(self) -> None:
        self.queue.task_done()

    def depth(self) -> int:
        return self.queue.qsize()


class WeightedVotingMarketCalendar:
    def is_trading_session(self, timestamp: datetime, session_phase: str | None = None) -> bool:
        local_date = timestamp.date()
        if local_date.weekday() >= 5:
            return False
        if local_date in _market_holidays(local_date.year):
            return False
        if session_phase in {"outside_session", "unknown"}:
            return False
        return True


class WeightedVotingRuntimeWorker:
    def __init__(self, supervisor: "WeightedVotingRuntimeSupervisor", worker_id: str) -> None:
        self.supervisor = supervisor
        self.worker_id = worker_id

    async def run(self) -> None:
        while not self.supervisor.stop_event.is_set():
            await asyncio.sleep(self.supervisor.config.maintenance_interval_seconds)


class WeightedVotingBarEventWorker(WeightedVotingRuntimeWorker):
    pass


class WeightedVotingDecisionWorker(WeightedVotingRuntimeWorker):
    async def run(self) -> None:
        while not self.supervisor.stop_event.is_set():
            event = await self.supervisor.event_bus.next_event()
            try:
                await self.supervisor.process_finalised_bar_event(event)
            finally:
                self.supervisor.event_bus.task_done()


class WeightedVotingRiskWorker(WeightedVotingRuntimeWorker):
    pass


class WeightedVotingExecutionWorker(WeightedVotingRuntimeWorker):
    async def run(self) -> None:
        while not self.supervisor.stop_event.is_set():
            item = await self.supervisor.execution_queue.get()
            try:
                self.supervisor.process_execution_queue_item(item)
            finally:
                self.supervisor.execution_queue.task_done()


class WeightedVotingReconciliationWorker(WeightedVotingRuntimeWorker):
    async def run(self) -> None:
        while not self.supervisor.stop_event.is_set():
            self.supervisor.reconcile_broker_inventory()
            await asyncio.sleep(self.supervisor.config.maintenance_interval_seconds)


class WeightedVotingPositionManager(WeightedVotingRuntimeWorker):
    async def run(self) -> None:
        self.supervisor.restore_position_management()
        await super().run()


class WeightedVotingDailyUpdateWorker(WeightedVotingRuntimeWorker):
    pass


class WeightedVotingRecoveryWorker(WeightedVotingRuntimeWorker):
    async def run(self) -> None:
        self.supervisor.recover_from_checkpoints()
        await super().run()


class WeightedVotingHeartbeatWorker(WeightedVotingRuntimeWorker):
    async def run(self) -> None:
        while not self.supervisor.stop_event.is_set():
            self.supervisor.write_heartbeat()
            await asyncio.sleep(self.supervisor.config.heartbeat_interval_seconds)


class WeightedVotingRuntimeSupervisor:
    def __init__(
        self,
        *,
        service: WeightedVotingService | None = None,
        store: WeightedVotingStateStore | None = None,
        config: WeightedVotingRuntimeConfig | None = None,
        weighted_config: WeightedVotingConfig | None = None,
        event_bus: WeightedVotingEventBus | None = None,
        calendar: WeightedVotingMarketCalendar | None = None,
        paper_gateway: PaperOrderGateway | None = None,
        inventory_repository: WeightedVotingInventoryRepository | None = None,
        account_port: WeightedVotingAccountObservationPort | None = None,
        global_risk_port: WeightedVotingGlobalRiskPort | None = None,
        rollout_flags: WeightedVotingRolloutFlags | None = None,
        rollout_validation: WeightedVotingRolloutValidation | None = None,
        position_manager: WeightedVotingPositionManagerService | None = None,
    ) -> None:
        self.store = store or WeightedVotingFilesystemStateStore()
        self.weighted_config = weighted_config or WeightedVotingConfig()
        self.service = service or WeightedVotingService(config=self.weighted_config, store=self.store)
        self.config = config or WeightedVotingRuntimeConfig()
        self.event_bus = event_bus or WeightedVotingEventBus(maxsize=self.config.queue_maxsize)
        self.execution_queue: asyncio.Queue[WeightedVotingExecutionQueueItem] = asyncio.Queue(maxsize=self.config.queue_maxsize)
        self.calendar = calendar or WeightedVotingMarketCalendar()
        self.paper_gateway = paper_gateway
        self.inventory_repository = inventory_repository or WeightedVotingInventoryRepository(self.store, allocated_capital=0.0)
        self.account_port = account_port or WeightedVotingUnavailableAccountPort()
        self.global_risk_port = global_risk_port or WeightedVotingUnavailableGlobalRiskPort()
        self.rollout_flags = rollout_flags
        self.rollout_validation = rollout_validation
        self.position_manager = position_manager or WeightedVotingPositionManagerService(store=self.store, inventory_repository=self.inventory_repository)
        self.metrics = WeightedVotingRuntimeMetrics()
        self.stop_event = asyncio.Event()
        self.tasks: dict[str, asyncio.Task] = {}
        self.symbol_locks: dict[str, asyncio.Lock] = {symbol.upper(): asyncio.Lock() for symbol in self.config.symbols}
        self.workers = (
            WeightedVotingBarEventWorker(self, "WeightedVotingBarEventWorker"),
            WeightedVotingDecisionWorker(self, "WeightedVotingDecisionWorker"),
            WeightedVotingRiskWorker(self, "WeightedVotingRiskWorker"),
            WeightedVotingExecutionWorker(self, "WeightedVotingExecutionWorker"),
            WeightedVotingReconciliationWorker(self, "WeightedVotingReconciliationWorker"),
            WeightedVotingPositionManager(self, "WeightedVotingPositionManager"),
            WeightedVotingDailyUpdateWorker(self, "WeightedVotingDailyUpdateWorker"),
            WeightedVotingRecoveryWorker(self, "WeightedVotingRecoveryWorker"),
            WeightedVotingHeartbeatWorker(self, "WeightedVotingHeartbeatWorker"),
        )

    async def start(self) -> None:
        if self.metrics.supervisor_started:
            return
        self.stop_event.clear()
        self.metrics.supervisor_started = True
        self.metrics.automatic_order_creation_paused = True
        self.metrics.pause_reason = "weighted_voting.runtime.restart_fail_closed_until_recovery"
        self.recover_from_checkpoints()
        self.reconcile_broker_inventory(startup=True)
        for worker in self.workers:
            self._start_worker(worker)
        self._write_status("started", ("weighted_voting.runtime.supervisor.started",))

    async def shutdown(self) -> None:
        self.stop_event.set()
        for task in self.tasks.values():
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()
        self.metrics.supervisor_started = False
        self._write_status("stopped", ("weighted_voting.runtime.supervisor.stopped",))

    async def publish_finalised_bar(self, event: WeightedVotingFinalisedBarEvent) -> bool:
        if event.symbol.upper() not in {symbol.upper() for symbol in self.config.symbols}:
            self.metrics.rejected_events += 1
            self._write_event_record(event, "rejected_unconfigured_symbol", None, ("weighted_voting.runtime.unconfigured_symbol",))
            return False
        self.metrics.last_finalised_bar_received = _bar_summary(event)
        published = await self.event_bus.publish(event)
        if not published:
            self.metrics.rejected_events += 1
            self._write_event_record(event, "rejected_backpressure", None, ("weighted_voting.runtime.queue_full",))
        self.metrics.queue_depth = self.event_bus.depth()
        return published

    async def process_finalised_bar_event(self, event: WeightedVotingFinalisedBarEvent) -> dict[str, Any]:
        symbol = event.symbol.upper()
        lock = self.symbol_locks.setdefault(symbol, asyncio.Lock())
        async with lock:
            try:
                return self._process_finalised_bar_event_locked(event)
            except Exception as exc:
                self.metrics.rejected_events += 1
                self.metrics.automatic_order_creation_paused = True
                self.metrics.recovery_required = True
                self.metrics.last_error = f"WeightedVotingRuntimeEvent: {exc}"
                failure = {
                    "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
                    "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
                    "status": "runtime_exception_safe_degradation",
                    "event_id": event.event_id,
                    "symbol": event.symbol,
                    "finalised_candle_timestamp": event.finalised_candle_timestamp.isoformat(),
                    "recorded_at": _now().isoformat(),
                    "reason_codes": ("weighted_voting.runtime.persistence_or_processing_exception_blocks_new_entries",),
                    "error": str(exc),
                }
                self.metrics.recovery_state = {
                    "recoveryRequired": True,
                    "unresolvedBoundaries": [{"boundary": "persistence_outage", "error": str(exc)}],
                    "newEntriesBlocked": True,
                }
                return failure

    def pause(self, *, actor: str = "system", reason: str = "weighted_voting.runtime.supervisor.paused") -> None:
        prior = self._admin_state()
        self.metrics.paused = True
        self.metrics.automatic_order_creation_paused = True
        self.metrics.pause_reason = reason
        self._write_admin_audit(
            "pause_runtime",
            actor=actor,
            prior_state=prior,
            new_state=self._admin_state(),
            reason_codes=("weighted_voting.runtime.admin.pause", reason),
        )
        self._write_status("paused", ("weighted_voting.runtime.supervisor.paused",))

    def resume(self, *, actor: str = "system", reason: str = "weighted_voting.runtime.supervisor.resumed") -> None:
        prior = self._admin_state()
        self.metrics.paused = False
        if not self.metrics.entry_creation_paused_for_reconciliation:
            self.metrics.pause_reason = None
        self._write_admin_audit(
            "resume_runtime",
            actor=actor,
            prior_state=prior,
            new_state=self._admin_state(),
            reason_codes=("weighted_voting.runtime.admin.resume", reason),
        )
        self._write_status("running", ("weighted_voting.runtime.supervisor.resumed",))

    def pause_new_entries(self, *, actor: str = "system", reason: str = "weighted_voting.runtime.entries_paused_by_admin") -> dict[str, Any]:
        prior = self._admin_state()
        self.metrics.automatic_order_creation_paused = True
        self.metrics.pause_reason = reason
        audit = self._write_admin_audit(
            "pause_new_entries",
            actor=actor,
            prior_state=prior,
            new_state=self._admin_state(),
            reason_codes=("weighted_voting.runtime.admin.pause_new_entries", reason),
        )
        self._write_status("entries_paused", ("weighted_voting.runtime.entries_paused", reason))
        return audit

    def resume_new_entries(
        self,
        *,
        actor: str = "system",
        reason: str = "weighted_voting.runtime.entries_resumed_by_admin",
        validation_passed: bool = True,
    ) -> dict[str, Any]:
        prior = self._admin_state()
        healthy = self.healthy_state_check(actor=actor, reason=reason)
        if not validation_passed or self.metrics.entry_creation_paused_for_reconciliation or not healthy["healthy"]:
            self.metrics.automatic_order_creation_paused = True
            status_reason = "weighted_voting.runtime.entries_resume_rejected_validation_or_reconciliation"
        else:
            self.metrics.automatic_order_creation_paused = False
            self.metrics.pause_reason = None
            status_reason = "weighted_voting.runtime.entries_resumed"
        audit = self._write_admin_audit(
            "resume_new_entries",
            actor=actor,
            prior_state=prior,
            new_state=self._admin_state(),
            reason_codes=("weighted_voting.runtime.admin.resume_new_entries", reason, status_reason),
        )
        self._write_status("entries_resume_checked", (status_reason, reason))
        return audit

    def force_reconciliation(self, *, actor: str = "system", reason: str = "weighted_voting.runtime.admin.force_reconciliation") -> dict[str, Any]:
        prior = self._admin_state()
        self.reconcile_broker_inventory(startup=False)
        return self._write_admin_audit(
            "force_reconciliation",
            actor=actor,
            prior_state=prior,
            new_state=self._admin_state(),
            reason_codes=("weighted_voting.runtime.admin.force_reconciliation", reason),
        )

    def set_strategy_runtime_state(
        self,
        strategy_id: str,
        state: Literal["shadow", "disabled"],
        *,
        actor: str = "system",
        reason: str = "weighted_voting.runtime.admin.strategy_state_changed",
    ) -> dict[str, Any]:
        if state not in {"shadow", "disabled"}:
            raise ValueError("Weighted Voting runtime strategy control supports only shadow or disabled")
        strategy_id = strategy_id.upper()
        prior_state = _read_optional(self.store, f"{RUNTIME_STRATEGY_CONTROL_PREFIX}{strategy_id}") or {"strategyId": strategy_id, "runtimeState": None}
        record = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "strategyId": strategy_id,
            "runtimeState": state,
            "updatedAt": _now().isoformat(),
            "updatedBy": actor,
            "reasonCodes": ("weighted_voting.runtime.strategy_control.updated", reason),
        }
        self.store.write_snapshot(f"{RUNTIME_STRATEGY_CONTROL_PREFIX}{strategy_id}", record)
        return self._write_admin_audit(
            "strategy_runtime_state",
            actor=actor,
            prior_state=prior_state,
            new_state=record,
            reason_codes=("weighted_voting.runtime.admin.strategy_state", reason),
        )

    def emergency_flatten(self, *, actor: str = "system", reason: str = "weighted_voting.runtime.admin.emergency_flatten_requested") -> dict[str, Any]:
        prior = self._admin_state()
        self.metrics.automatic_order_creation_paused = True
        self.metrics.pause_reason = reason
        request = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "status": "requested",
            "centralRiskProcessRequired": True,
            "liveMoneyTradingEnabled": False,
            "requestedBy": actor,
            "requestedAt": _now().isoformat(),
            "reasonCodes": ("weighted_voting.runtime.emergency_flatten.central_risk_required", reason),
        }
        self.store.write_snapshot(f"{RUNTIME_EMERGENCY_FLATTEN_PREFIX}{_hash_payload(request)}", request)
        audit = self._write_admin_audit(
            "emergency_flatten",
            actor=actor,
            prior_state=prior,
            new_state={**self._admin_state(), "emergencyFlatten": request},
            reason_codes=("weighted_voting.runtime.admin.emergency_flatten", reason),
        )
        self._write_status("emergency_flatten_requested", ("weighted_voting.runtime.emergency_flatten.requested", reason))
        return audit

    def recover_from_checkpoints(self) -> None:
        for symbol in self.config.symbols:
            checkpoint = _read_optional(self.store, _checkpoint_key(symbol))
            if checkpoint:
                self.metrics.last_checkpoint_by_symbol[symbol.upper()] = str(checkpoint.get("idempotency_key", ""))
                if checkpoint.get("finalised_candle_timestamp"):
                    self.metrics.last_event_timestamp_by_symbol[symbol.upper()] = str(checkpoint["finalised_candle_timestamp"])
        self.perform_recovery_safety_check(reason="weighted_voting.runtime.recovery.checkpoints_scanned")
        self.restore_position_management()

    def perform_recovery_safety_check(self, *, reason: str = "weighted_voting.runtime.recovery.safety_check") -> dict[str, Any]:
        reasons: list[str] = [reason]
        unresolved: list[dict[str, Any]] = []
        quarantined: list[dict[str, Any]] = []
        restored: list[str] = []
        now = _now()

        try:
            restored.extend(self._validate_or_restore_authoritative_snapshots(now=now, quarantined=quarantined, reasons=reasons))
            unresolved.extend(self._detect_unresolved_execution_crash_points())
            unresolved.extend(self._detect_unprotected_positions())
            if self.event_bus.depth() >= self.config.queue_maxsize:
                unresolved.append({"boundary": "event_backlog", "reasonCode": "weighted_voting.runtime.recovery.event_backlog"})
        except Exception as exc:
            reasons.append("weighted_voting.runtime.recovery.persistence_or_validation_outage")
            unresolved.append({"boundary": "persistence_outage", "error": str(exc), "reasonCode": "weighted_voting.runtime.recovery.persistence_outage"})
            self.metrics.last_error = f"WeightedVotingRecovery: {exc}"

        recovery_required = bool(unresolved or quarantined)
        self.metrics.recovery_required = recovery_required
        self.metrics.quarantined_snapshots += len(quarantined)
        if recovery_required:
            self.metrics.automatic_order_creation_paused = True
            self.metrics.pause_reason = "weighted_voting.runtime.recovery.unresolved_blocks_new_entries"
        state = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "recoveryRequired": recovery_required,
            "newEntriesBlocked": recovery_required or self.metrics.automatic_order_creation_paused,
            "protectiveExitsMayContinue": True,
            "unresolvedBoundaries": unresolved,
            "quarantinedSnapshots": quarantined,
            "restoredAuthoritativeSnapshots": restored,
            "checkedAt": now.isoformat(),
            "reasonCodes": tuple(dict.fromkeys(reasons)),
        }
        self.metrics.recovery_state = state
        self._write_recovery_state(state)
        return state

    def healthy_state_check(self, *, actor: str = "system", reason: str = "weighted_voting.runtime.recovery.healthy_check") -> dict[str, Any]:
        state = self.perform_recovery_safety_check(reason=reason)
        healthy = (
            not state["recoveryRequired"]
            and self.metrics.inventory_reconciled
            and not self.metrics.entry_creation_paused_for_reconciliation
            and not self.metrics.circuit_breaker_open
            and not self.metrics.last_error
        )
        record = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "healthy": healthy,
            "actor": actor,
            "checkedAt": _now().isoformat(),
            "recoveryStateHash": _hash_payload(state),
            "inventoryReconciled": self.metrics.inventory_reconciled,
            "circuitBreakerOpen": self.metrics.circuit_breaker_open,
            "reasonCodes": (
                "weighted_voting.runtime.recovery.healthy_state_ready"
                if healthy
                else "weighted_voting.runtime.recovery.healthy_state_rejected"
            ),
        }
        try:
            self.store.write_snapshot(RUNTIME_HEALTHY_STATE_KEY, record)
        except Exception as exc:
            self.metrics.last_error = f"WeightedVotingRecoveryHealthyCheck: {exc}"
            record["healthy"] = False
            record["reasonCodes"] = ("weighted_voting.runtime.recovery.healthy_state_persistence_failed",)
        return record

    def _validate_or_restore_authoritative_snapshots(
        self,
        *,
        now: datetime,
        quarantined: list[dict[str, Any]],
        reasons: list[str],
    ) -> list[str]:
        restored: list[str] = []
        settings = _read_optional(self.store, WEIGHTED_VOTING_SETTINGS_KEY)
        if settings is not None:
            try:
                WeightedEffectiveSettings.model_validate(settings)
                if not (settings.get("configuration_hash") or settings.get("configurationHash")):
                    raise ValueError("missing Weighted Voting settings hash")
            except Exception as exc:
                self._quarantine_snapshot(WEIGHTED_VOTING_SETTINGS_KEY, settings, "weighted_voting.runtime.recovery.settings_corruption", str(exc), now=now, quarantined=quarantined)
                approved = _read_optional(self.store, LAST_APPROVED_SETTINGS_KEY)
                if approved is not None:
                    WeightedEffectiveSettings.model_validate(approved)
                    if not (approved.get("configuration_hash") or approved.get("configurationHash")):
                        raise ValueError("last approved Weighted Voting settings hash missing")
                    self.store.write_snapshot(WEIGHTED_VOTING_SETTINGS_KEY, approved)
                    restored.append(WEIGHTED_VOTING_SETTINGS_KEY)
                reasons.append("weighted_voting.runtime.recovery.settings_corruption_quarantined")

        weights = _read_optional(self.store, ACTIVE_WEIGHT_STATE_KEY)
        if weights is not None:
            try:
                WeightedWeightState.model_validate(weights)
                if not (weights.get("output_hash") or weights.get("outputHash") or weights.get("input_data_hash") or weights.get("inputDataHash")):
                    raise ValueError("missing Weight Voting weight-state hash evidence")
            except Exception as exc:
                self._quarantine_snapshot(ACTIVE_WEIGHT_STATE_KEY, weights, "weighted_voting.runtime.recovery.weight_state_corruption", str(exc), now=now, quarantined=quarantined)
                approved = _read_optional(self.store, LAST_APPROVED_WEIGHT_STATE_KEY)
                if approved is not None:
                    WeightedWeightState.model_validate(approved)
                    if not (approved.get("output_hash") or approved.get("outputHash") or approved.get("input_data_hash") or approved.get("inputDataHash")):
                        raise ValueError("last approved Weighted Voting weight-state hash evidence missing")
                    self.store.write_snapshot(ACTIVE_WEIGHT_STATE_KEY, approved)
                    restored.append(ACTIVE_WEIGHT_STATE_KEY)
                reasons.append("weighted_voting.runtime.recovery.weight_state_corruption_quarantined")

        inventory = _read_optional(self.store, CURRENT_SNAPSHOT_KEY)
        if inventory is not None:
            try:
                self.inventory_repository.current_snapshot(now=now)
            except Exception as exc:
                self._quarantine_snapshot(CURRENT_SNAPSHOT_KEY, inventory, "weighted_voting.runtime.recovery.inventory_snapshot_corruption", str(exc), now=now, quarantined=quarantined)
                reasons.append("weighted_voting.runtime.recovery.inventory_corruption_quarantined")
        return restored

    def _detect_unresolved_execution_crash_points(self) -> list[dict[str, Any]]:
        unresolved: list[dict[str, Any]] = []
        automatic_results = {
            key.removeprefix(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.automatic_result.")
            for key, _ in _store_items(self.store)
            if key.startswith(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.automatic_result.")
        }
        reconciliations = {
            key.removeprefix(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.reconciliation.")
            for key, _ in _store_items(self.store)
            if key.startswith(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.reconciliation.")
        }
        for key, payload in _store_items(self.store):
            if key.startswith(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.queue."):
                client_order_id = str(payload.get("clientOrderId") or payload.get("client_order_id") or key.rsplit(".", 1)[-1])
                status = str(payload.get("status") or "PENDING")
                if client_order_id not in automatic_results:
                    unresolved.append(_unresolved("risk_approval_before_broker_submission", key, status, "weighted_voting.runtime.recovery.execution_queue_unresolved"))
            elif key.startswith(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.lifecycle.") and key.endswith(".latest"):
                client_order_id = str(payload.get("clientOrderId") or payload.get("client_order_id") or "")
                status = str(payload.get("status") or "")
                if status in {"PENDING", "PENDING_SUBMISSION", "SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED"} and client_order_id not in automatic_results and client_order_id not in reconciliations:
                    unresolved.append(_unresolved("submission_or_acknowledgement_incomplete", key, status, "weighted_voting.runtime.recovery.lifecycle_unresolved"))
            elif key.startswith("weighted_voting.decisions."):
                decision_id = str(payload.get("decision_id") or payload.get("decisionId") or key.rsplit(".", 1)[-1])
                if not any(isinstance(item, dict) and decision_id in str(item) for _, item in _store_items(self.store) if _.startswith(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.")):
                    unresolved.append(_unresolved("decision_before_risk_response", key, "DECISION_PERSISTED", "weighted_voting.runtime.recovery.decision_without_risk_evidence"))
        for key, payload in _store_items(self.store):
            if key.startswith(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.automatic_result."):
                client_order_id = key.removeprefix(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.automatic_result.")
                fill = payload.get("fill") if isinstance(payload.get("fill"), dict) else None
                if fill and int(fill.get("filledQuantity") or 0) > 0 and client_order_id not in reconciliations:
                    unresolved.append(_unresolved("fill_before_inventory_update", key, str(payload.get("status") or "FILLED"), "weighted_voting.runtime.recovery.fill_requires_inventory_reconciliation"))
        return _dedupe_unresolved(unresolved)

    def _detect_unprotected_positions(self) -> list[dict[str, Any]]:
        try:
            snapshot = self.inventory_repository.current_snapshot(now=_now())
        except Exception as exc:
            return [_unresolved("inventory_version_conflict", CURRENT_SNAPSHOT_KEY, "CORRUPT", "weighted_voting.runtime.recovery.inventory_unavailable", error=str(exc))]
        unresolved: list[dict[str, Any]] = []
        for position in snapshot.open_positions:
            protection_key = f"weighted_voting.position_manager.protection.{position.client_order_id}"
            if _read_optional(self.store, protection_key) is None:
                unresolved.append(_unresolved("protective_orders_being_created", protection_key, "MISSING", "weighted_voting.runtime.recovery.protective_order_restore_required"))
        return unresolved

    def _quarantine_snapshot(
        self,
        key: str,
        payload: dict[str, Any],
        reason_code: str,
        error: str,
        *,
        now: datetime,
        quarantined: list[dict[str, Any]],
    ) -> None:
        record = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "originalKey": key,
            "payload": _json_ready(payload),
            "error": error,
            "quarantinedAt": now.isoformat(),
            "reasonCodes": (reason_code,),
        }
        self.store.write_snapshot(f"{RUNTIME_QUARANTINE_PREFIX}{_hash_payload({'key': key, 'payload': payload, 'at': now.isoformat()})}", record)
        quarantined.append({"key": key, "reasonCode": reason_code, "error": error})

    def _write_recovery_state(self, state: dict[str, Any]) -> None:
        try:
            self.store.write_snapshot(RUNTIME_RECOVERY_STATE_KEY, state)
            self.store.write_snapshot(f"{RUNTIME_RECOVERY_EVENT_PREFIX}{_hash_payload(state)}", state)
        except Exception as exc:
            self.metrics.last_error = f"WeightedVotingRecoveryPersistence: {exc}"
            self.metrics.automatic_order_creation_paused = True
            self.metrics.recovery_required = True

    def write_heartbeat(self) -> None:
        payload = {
            "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "recorded_at": _now().isoformat(),
            "queue_depth": self.event_bus.depth(),
            "workers": sorted(self.tasks),
            "automatic_order_creation_paused": self.metrics.automatic_order_creation_paused,
            "paused": self.metrics.paused,
            "reason_codes": ("weighted_voting.runtime.heartbeat",),
        }
        self.store.write_snapshot(RUNTIME_HEARTBEAT_KEY, payload)

    def health(self) -> dict[str, Any]:
        self.metrics.queue_depth = self.event_bus.depth()
        self.metrics.execution_queue_depth = self.execution_queue.qsize()
        inventory = None
        inventory_error = None
        try:
            inventory = self.inventory_repository.current_snapshot(now=_now())
        except Exception as exc:
            inventory_error = str(exc)
        active_weight = _read_optional(self.store, ACTIVE_WEIGHT_STATE_KEY) or {}
        settings = _read_optional(self.store, WEIGHTED_VOTING_SETTINGS_KEY) or {}
        lifecycle = _read_optional(self.store, WEIGHTED_VOTING_STRATEGY_LIFECYCLE_LATEST_KEY) or {}
        operational_status = {
            "supervisorState": "paused" if self.metrics.paused else ("running" if self.metrics.supervisor_started else "stopped"),
            "workerState": self._worker_state(),
            "queueDepth": self.metrics.queue_depth,
            "executionQueueDepth": self.metrics.execution_queue_depth,
            "lastFinalisedBarReceived": self.metrics.last_finalised_bar_received,
            "lastBarProcessed": self.metrics.last_bar_processed,
            "processingLagSeconds": self.metrics.processing_lag_seconds,
            "lastDecision": self.metrics.last_decision_id,
            "lastAcceptedProposal": self.metrics.last_accepted_proposal,
            "lastGlobalRiskResponse": self.metrics.last_global_risk_response,
            "lastOrderSubmission": self.metrics.last_order_submission,
            "lastFill": self.metrics.last_fill,
            "lastReconciliation": self.metrics.last_reconciliation,
            "openPositions": [_json_ready(asdict(position)) for position in inventory.open_positions] if inventory else [],
            "pendingOrders": [_json_ready(asdict(order)) for order in inventory.pending_orders] if inventory else [],
            "inventoryVersion": inventory.inventory_version if inventory else None,
            "inventorySnapshotVersion": inventory.snapshot_version if inventory else None,
            "settingsVersion": settings.get("settings_version") or settings.get("settingsVersion"),
            "weightVersion": active_weight.get("weight_version") or active_weight.get("weightVersion"),
            "catalogueVersion": lifecycle.get("catalog_version") or lifecycle.get("catalogVersion"),
            "dailyTradeCount": inventory.daily_trade_count if inventory else None,
            "dailyPnL": round(inventory.daily_realised_pnl + inventory.daily_unrealised_pnl, 10) if inventory else None,
            "remainingDailyRisk": inventory.remaining_daily_risk if inventory else None,
            "automaticSubmissionRolloutState": _rollout_state(self.rollout_flags, self.rollout_validation),
            "pauseReason": self.metrics.pause_reason,
            "lastError": self.metrics.last_error or inventory_error,
            "recoveryRequired": self.metrics.recovery_required,
            "recoveryState": dict(self.metrics.recovery_state),
            "circuitBreakerOpen": self.metrics.circuit_breaker_open,
        }
        runtime_metrics = {
            "decisionLatencyMs": self.metrics.decision_latency_ms,
            "riskServiceLatencyMs": self.metrics.risk_service_latency_ms,
            "brokerLatencyMs": self.metrics.broker_latency_ms,
            "eventBacklog": self.metrics.queue_depth,
            "staleEventDrops": self.metrics.stale_events,
            "duplicateEventDrops": self.metrics.duplicate_events,
            "gateRejectionCounts": dict(self.metrics.gate_rejection_counts),
            "strategyOpportunityCounts": dict(self.metrics.strategy_opportunity_counts),
            "strategySignalCounts": _copy_nested_counts(self.metrics.strategy_signal_counts),
            "proposedVsAllowedQuantity": dict(self.metrics.proposed_vs_allowed_quantity),
            "fillQuality": dict(self.metrics.fill_quality),
            "slippage": dict(self.metrics.slippage),
            "reconciliationDiscrepancies": self.metrics.reconciliation_discrepancies,
            "quarantinedSnapshots": self.metrics.quarantined_snapshots,
            "recoveryRequired": self.metrics.recovery_required,
            "circuitBreakerOpen": self.metrics.circuit_breaker_open,
            "workerRestarts": dict(self.metrics.worker_restarts),
        }
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "started": self.metrics.supervisor_started,
            "paused": self.metrics.paused,
            "automaticOrderCreationPaused": self.metrics.automatic_order_creation_paused,
            "queueDepth": self.metrics.queue_depth,
            "executionQueueDepth": self.metrics.execution_queue_depth,
            "queueMaxsize": self.config.queue_maxsize,
            "droppedEvents": self.event_bus.dropped_events,
            "processedEvents": self.metrics.processed_events,
            "duplicateEvents": self.metrics.duplicate_events,
            "rejectedEvents": self.metrics.rejected_events,
            "staleEvents": self.metrics.stale_events,
            "outOfOrderEvents": self.metrics.out_of_order_events,
            "persistedDecisions": self.metrics.persisted_decisions,
            "enqueuedOrders": self.metrics.enqueued_orders,
            "submittedOrders": self.metrics.submitted_orders,
            "rejectedExecutionEvents": self.metrics.rejected_execution_events,
            "entryCreationPausedForReconciliation": self.metrics.entry_creation_paused_for_reconciliation,
            "inventoryReconciled": self.metrics.inventory_reconciled,
            "riskReducingExitsAllowed": self.metrics.risk_reducing_exits_allowed,
            "workerFailures": dict(self.metrics.worker_failures),
            "workerRestarts": dict(self.metrics.worker_restarts),
            "workers": {worker_id: not task.done() for worker_id, task in self.tasks.items()},
            "lastEventTimestampBySymbol": dict(self.metrics.last_event_timestamp_by_symbol),
            "lastCheckpointBySymbol": dict(self.metrics.last_checkpoint_by_symbol),
            "lastDecisionId": self.metrics.last_decision_id,
            "lastError": self.metrics.last_error,
            "recoveryRequired": self.metrics.recovery_required,
            "circuitBreakerOpen": self.metrics.circuit_breaker_open,
            "operationalStatus": operational_status,
            "metrics": runtime_metrics,
            "reasonCodes": ("weighted_voting.runtime.health.ready",),
        }

    def _process_finalised_bar_event_locked(self, event: WeightedVotingFinalisedBarEvent) -> dict[str, Any]:
        if self.metrics.paused:
            self.metrics.rejected_events += 1
            return self._write_event_record(event, "paused", None, ("weighted_voting.runtime.paused",))
        self.metrics.last_finalised_bar_received = _bar_summary(event)
        snapshot = build_weighted_voting_market_snapshot(event.market_payload)
        if snapshot.symbol.upper() != event.symbol.upper() or snapshot.data_timestamp != event.finalised_candle_timestamp:
            self.metrics.rejected_events += 1
            return self._write_event_record(event, "rejected_conflicting_event", None, ("weighted_voting.runtime.conflicting_event_payload",))
        degradation_reasons = _event_degradation_reasons(event, snapshot, max_lag_seconds=self.config.max_queue_lag_seconds)
        if degradation_reasons:
            self.metrics.rejected_events += 1
            self.metrics.automatic_order_creation_paused = True
            self.metrics.pause_reason = "weighted_voting.runtime.degradation.new_entries_blocked"
            if any("clock_skew" in code for code in degradation_reasons):
                self.metrics.last_error = "WeightedVotingRuntime: clock skew detected"
            status = "safe_degradation_no_order"
            record = self._write_event_record(event, status, None, tuple(degradation_reasons))
            return record
        if not self.calendar.is_trading_session(snapshot.data_timestamp, str(snapshot.session_phase)) and not event.replay_recovery:
            self.metrics.rejected_events += 1
            return self._write_event_record(event, "closed_session_skipped", None, ("weighted_voting.runtime.closed_session_no_entry_decision",))
        checkpoint = _read_optional(self.store, _checkpoint_key(event.symbol))
        last_timestamp = _parse_optional_datetime(checkpoint.get("finalised_candle_timestamp") if checkpoint else None)
        if last_timestamp and snapshot.data_timestamp < last_timestamp and not event.replay_recovery:
            self.metrics.out_of_order_events += 1
            self.metrics.rejected_events += 1
            return self._write_event_record(event, "rejected_out_of_order", None, ("weighted_voting.runtime.out_of_order_event_rejected",))
        weight_state = self.service.active_weight_state()
        condition = classify_market_condition(snapshot, config=self.weighted_config)
        effective = self._active_effective_settings()
        idempotency_key = weighted_voting_bar_event_idempotency_key(
            symbol=event.symbol,
            finalised_candle_timestamp=snapshot.data_timestamp,
            data_manifest_hash=snapshot.data_manifest_hash,
            settings_version=effective.settings_version,
            weight_version=weight_state.weight_version,
        )
        if _read_optional(self.store, _event_key(idempotency_key)):
            self.metrics.duplicate_events += 1
            return self._write_event_record(event, "duplicate_noop", idempotency_key, ("weighted_voting.runtime.duplicate_event_noop",))
        queue_lag = max(0.0, (_now() - event.published_at).total_seconds())
        self.metrics.processing_lag_seconds = queue_lag
        if queue_lag > self.config.max_queue_lag_seconds:
            self.metrics.stale_events += 1
            self.metrics.rejected_events += 1
            self.metrics.automatic_order_creation_paused = True
            record = self._write_event_record(event, "stale_no_order", idempotency_key, ("weighted_voting.runtime.stale_queued_event_rejected",))
            self._write_checkpoint(event, idempotency_key, decision_id=None, status="stale_no_order")
            return record
        decision_started = _now()
        context = self.build_runtime_context_from_finalised_bar(
            snapshot=snapshot,
            active_weight_state=weight_state,
            effective_settings=effective,
            market_condition=condition,
            observed_at=snapshot.data_timestamp,
            event_payload=event.market_payload,
        )
        result = self.service.evaluate_context(context)
        self.metrics.decision_latency_ms = round((_now() - decision_started).total_seconds() * 1000, 3)
        self._capture_decision_observability_metrics(result)
        decision_id = str(result["decision"]["decision_id"])
        self._enqueue_execution_from_result(result, idempotency_key=idempotency_key, evaluated_at=snapshot.data_timestamp)
        self.metrics.processed_events += 1
        self.metrics.persisted_decisions += 1
        self.metrics.last_decision_id = decision_id
        self.metrics.last_bar_processed = _bar_summary(event)
        record = self._write_event_record(event, "decision_persisted", idempotency_key, ("weighted_voting.runtime.decision_persisted",), decision_id=decision_id)
        self._write_checkpoint(event, idempotency_key, decision_id=decision_id, status="decision_persisted")
        return record

    def _active_effective_settings(self) -> WeightedEffectiveSettings:
        try:
            return load_effective_settings(self.store)
        except KeyError:
            effective = resolve_effective_settings(
                baseline_config=self.weighted_config,
                source_evidence=("weighted_voting.runtime.stable_bootstrap_settings",),
            )
            persist_effective_settings(self.store, effective)
            return effective

    def build_runtime_context_from_finalised_bar(
        self,
        *,
        snapshot: WeightedMarketSnapshot,
        active_weight_state: WeightedWeightState,
        effective_settings: WeightedEffectiveSettings,
        market_condition: Any,
        observed_at: datetime,
        event_payload: dict[str, Any],
    ) -> WeightedVotingRuntimeContext:
        context = WeightedVotingRuntimeContextBuilder(
            market_data_port=WeightedVotingStaticMarketDataPort(snapshot),
            inventory_repository=self.inventory_repository,
            account_port=self.account_port,
            global_risk_port=self.global_risk_port,
            effective_settings=effective_settings,
            active_weight_state=active_weight_state,
            observed_at=observed_at,
            mode="production",
            cost_estimate=_runtime_cost_estimate(
                event_payload,
                effective_settings=effective_settings,
                weighted_config=self.weighted_config,
                observed_at=observed_at,
            ),
            market_condition=market_condition,
        ).build()
        self.store.write_snapshot(
            f"weighted_voting.runtime.contexts.{context.finalised_one_minute_market_snapshot.symbol.upper()}.{context.finalised_one_minute_market_snapshot.data_timestamp.isoformat()}",
            {
                "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
                "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
                "context_version": context.context_version,
                "manifest_hash": context.manifest_hash,
                "symbol": context.finalised_one_minute_market_snapshot.symbol.upper(),
                "data_timestamp": context.finalised_one_minute_market_snapshot.data_timestamp.isoformat(),
                "one_minute_candle_count": len(context.finalised_one_minute_market_snapshot.one_minute_candles),
                "five_minute_candle_count": len(context.five_minute_candles),
                "five_minute_alignment": _enum_value(context.five_minute_alignment),
                "settings_version": context.effective_settings.settings_version,
                "weight_version": context.active_weight_state.weight_version,
                "inventory_snapshot_version": context.inventory_snapshot.snapshot_version,
                "inventory_available": context.inventory_available,
                "current_position_quantity": context.current_position.quantity if context.current_position else 0,
                "pending_order_count": len(context.pending_orders),
                "algorithm_daily_pnl": context.algorithm_daily_pnl,
                "algorithm_daily_trade_count": context.algorithm_daily_trade_count,
                "remaining_algorithm_daily_risk": context.remaining_algorithm_daily_risk,
                "remaining_algorithm_capital_partition": context.remaining_algorithm_capital_partition,
                "read_only_account_equity_available": context.read_only_account_equity is not None,
                "read_only_broker_buying_power_available": context.read_only_broker_buying_power is not None,
                "current_spread": context.current_spread,
                "estimated_slippage": context.estimated_slippage,
                "estimated_fees": context.estimated_fees,
                "global_risk_service_available": context.global_risk_state.service_available,
                "global_available_risk": context.global_risk_state.global_available_risk,
                "global_max_shares": context.global_risk_state.global_max_shares,
                "reason_codes": ("weighted_voting.runtime.full_context_built_from_finalised_bar",),
            },
        )
        return context

    def process_execution_queue_item(self, item: WeightedVotingExecutionQueueItem) -> dict[str, Any]:
        self.metrics.execution_queue_depth = self.execution_queue.qsize()
        if self.metrics.recovery_required or self.metrics.circuit_breaker_open:
            self.metrics.rejected_execution_events += 1
            self.metrics.automatic_order_creation_paused = True
            return self._write_execution_record(
                item,
                status="recovery_blocked",
                reason_codes=("weighted_voting.runtime.recovery_blocks_submission",),
            )
        if self.paper_gateway is None:
            self.metrics.rejected_execution_events += 1
            self.metrics.automatic_order_creation_paused = True
            self.metrics.last_error = "WeightedVotingExecution: paper gateway unavailable"
            return self._write_execution_record(
                item,
                status="gateway_unavailable",
                reason_codes=("weighted_voting.runtime.paper_gateway_unavailable",),
            )
        broker_started = _now()
        try:
            result = submit_queued_weighted_voting_paper_order(
                gateway=self.paper_gateway,
                queue_item=item,
                inventory_repository=self.inventory_repository,
                evaluated_at=item.enqueued_at,
                rollout_flags=self.rollout_flags,
                rollout_validation=self.rollout_validation,
            )
        except Exception as exc:
            self.metrics.rejected_execution_events += 1
            self.metrics.automatic_order_creation_paused = True
            self.metrics.last_error = f"WeightedVotingExecution: {exc}"
            self.perform_recovery_safety_check(reason="weighted_voting.runtime.execution_exception_recovery_required")
            return self._write_execution_record(
                item,
                status="submission_failed_safe_degradation",
                reason_codes=("weighted_voting.runtime.execution_exception_blocks_new_entries",),
                result={"error": str(exc), "submitted": False},
            )
        self.metrics.broker_latency_ms = round((_now() - broker_started).total_seconds() * 1000, 3)
        result_payload = result.model_dump(mode="json")
        self.metrics.last_order_submission = {
            "clientOrderId": item.command.client_order_id,
            "orderIntentId": item.command.order_intent_id,
            "decisionId": item.command.decision_id,
            "submitted": bool(result.submitted),
            "status": result_payload.get("status"),
            "recordedAt": _now().isoformat(),
        }
        fill_payload = _deep_get(result_payload, ("reconciliation", "fill"))
        if isinstance(fill_payload, dict):
            self.metrics.last_fill = fill_payload
            self.metrics.fill_quality = {
                "clientOrderId": fill_payload.get("clientOrderId"),
                "filledQuantity": fill_payload.get("filledQuantity"),
                "averageFillPrice": fill_payload.get("averageFillPrice"),
                "status": fill_payload.get("status"),
            }
            if item.command.limit_price and fill_payload.get("averageFillPrice"):
                self.metrics.slippage = {
                    "clientOrderId": item.command.client_order_id,
                    "limitPrice": item.command.limit_price,
                    "averageFillPrice": fill_payload.get("averageFillPrice"),
                    "signedDifference": round(float(fill_payload["averageFillPrice"]) - float(item.command.limit_price), 10),
                }
        if result.submitted:
            self.metrics.submitted_orders += 1
        else:
            self.metrics.rejected_execution_events += 1
        return self._write_execution_record(
            item,
            status="submitted" if result.submitted else "not_submitted",
            reason_codes=tuple(result.reasonCodes),
            result=result_payload,
        )

    def reconcile_broker_inventory(self, *, startup: bool = False) -> None:
        if self.paper_gateway is None:
            self.metrics.inventory_reconciled = False
            self.metrics.entry_creation_paused_for_reconciliation = True
            self.metrics.automatic_order_creation_paused = True
            self.metrics.last_reconciliation = {
                "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
                "status": "unavailable",
                "entriesPaused": True,
                "riskReducingExitsAllowed": self.metrics.risk_reducing_exits_allowed,
                "recordedAt": _now().isoformat(),
                "reasonCodes": ("weighted_voting.runtime.reconciliation.paper_gateway_unavailable",),
            }
            self._write_status("reconciliation_unavailable", ("weighted_voting.runtime.reconciliation.paper_gateway_unavailable",))
            return
        try:
            fills, positions = _broker_observations_from_gateway(self.paper_gateway, self.store, observed_at=_now())
            result = reconcile_weighted_voting_broker_observations(
                store=self.store,
                inventory_repository=self.inventory_repository,
                fills=fills,
                positions=positions,
                reconciled_at=_now(),
            )
            self.metrics.inventory_reconciled = result.inventory_reconciled
            self.metrics.entry_creation_paused_for_reconciliation = result.entries_paused
            self.metrics.risk_reducing_exits_allowed = result.risk_reducing_exits_allowed
            self.metrics.reconciliation_discrepancies = len(result.discrepancies)
            self.metrics.last_reconciliation = result.as_dict()
            if result.entries_paused:
                self.metrics.automatic_order_creation_paused = True
            self._write_status(
                "startup_reconciled" if startup else "reconciled",
                tuple(result.reason_codes),
            )
        except Exception as exc:
            self.metrics.inventory_reconciled = False
            self.metrics.entry_creation_paused_for_reconciliation = True
            self.metrics.automatic_order_creation_paused = True
            self.metrics.last_error = f"WeightedVotingReconciliation: {exc}"
            self._write_status("reconciliation_failed", ("weighted_voting.runtime.reconciliation.failed_entries_paused",))

    def _capture_decision_observability_metrics(self, result: dict[str, Any]) -> None:
        self.metrics.last_global_risk_response = result.get("globalRiskResponse") if isinstance(result.get("globalRiskResponse"), dict) else None
        proposal = result.get("globalOrderProposal") if isinstance(result.get("globalOrderProposal"), dict) else {}
        application = result.get("globalGateApplication") if isinstance(result.get("globalGateApplication"), dict) else {}
        proposed_quantity = _safe_int(proposal.get("quantity") or proposal.get("proposedQuantity"))
        allowed_quantity = _safe_int(application.get("globallyAllowedQuantity") or application.get("maximumQuantity"))
        self.metrics.proposed_vs_allowed_quantity = {
            "proposalId": proposal.get("proposalId") or proposal.get("proposal_id"),
            "proposedQuantity": proposed_quantity,
            "allowedQuantity": allowed_quantity,
        }
        if proposed_quantity > 0 and allowed_quantity > 0:
            self.metrics.last_accepted_proposal = {
                "proposalId": proposal.get("proposalId") or proposal.get("proposal_id"),
                "decisionId": proposal.get("decisionId") or proposal.get("decision_id"),
                "symbol": proposal.get("symbol"),
                "proposedQuantity": proposed_quantity,
                "allowedQuantity": allowed_quantity,
            }
        for code in _reason_codes_from_result(result):
            if ".gate." in code or "gate" in code:
                self.metrics.gate_rejection_counts[code] = self.metrics.gate_rejection_counts.get(code, 0) + 1
        for signal in _signals_from_result(result):
            strategy_id = str(signal.get("strategyId") or signal.get("strategy_id") or "")
            if not strategy_id:
                continue
            self.metrics.strategy_opportunity_counts[strategy_id] = self.metrics.strategy_opportunity_counts.get(strategy_id, 0) + 1
            lifecycle = "shadow" if bool(signal.get("shadowRecordsOnly") or signal.get("shadow_records_only")) else "active"
            if lifecycle not in self.metrics.strategy_signal_counts:
                self.metrics.strategy_signal_counts[lifecycle] = {}
            self.metrics.strategy_signal_counts[lifecycle][strategy_id] = self.metrics.strategy_signal_counts[lifecycle].get(strategy_id, 0) + 1

    def _worker_state(self) -> dict[str, dict[str, Any]]:
        state: dict[str, dict[str, Any]] = {}
        for worker in self.workers:
            task = self.tasks.get(worker.worker_id)
            state[worker.worker_id] = {
                "running": bool(task and not task.done()),
                "done": bool(task and task.done()),
                "failures": self.metrics.worker_failures.get(worker.worker_id, 0),
                "restarts": self.metrics.worker_restarts.get(worker.worker_id, 0),
            }
        return state

    def _admin_state(self) -> dict[str, Any]:
        return {
            "paused": self.metrics.paused,
            "automaticOrderCreationPaused": self.metrics.automatic_order_creation_paused,
            "entryCreationPausedForReconciliation": self.metrics.entry_creation_paused_for_reconciliation,
            "riskReducingExitsAllowed": self.metrics.risk_reducing_exits_allowed,
            "pauseReason": self.metrics.pause_reason,
        }

    def _write_admin_audit(
        self,
        action: str,
        *,
        actor: str,
        prior_state: dict[str, Any],
        new_state: dict[str, Any],
        reason_codes: tuple[str, ...],
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        recorded_at = _now()
        record = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "action": action,
            "actor": actor,
            "recordedAt": recorded_at.isoformat(),
            "priorState": prior_state,
            "newState": new_state,
            "details": details or {},
            "reasonCodes": reason_codes,
        }
        self.store.write_snapshot(f"{RUNTIME_ADMIN_AUDIT_PREFIX}{recorded_at.isoformat()}.{_hash_payload(record)}", record)
        return record

    def restore_position_management(self) -> None:
        try:
            restored = self.position_manager.restore_protective_management(effective_settings_by_version={}, restored_at=_now())
            self.store.write_snapshot(
                "weighted_voting.runtime.position_manager.restore",
                {
                    "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
                    "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
                    "restored_count": len(restored),
                    "dashboard_required": False,
                    "updated_at": _now().isoformat(),
                    "reason_codes": ("weighted_voting.runtime.position_manager_restored",),
                },
            )
        except Exception as exc:
            self.metrics.last_error = f"WeightedVotingPositionManager: {exc}"
            self._write_status("position_manager_restore_failed", ("weighted_voting.runtime.position_manager_restore_failed",))

    def _enqueue_execution_from_result(self, result: dict[str, Any], *, idempotency_key: str, evaluated_at: datetime) -> None:
        try:
            proposal = GlobalOrderProposal.model_validate(result["globalOrderProposal"])
            global_application = AppliedGlobalGateDecision.model_validate(result["globalGateApplication"])
            local_gate_result = _local_gate_result_from_payload(result.get("gateResult") or {})
            if self.metrics.entry_creation_paused_for_reconciliation and proposal.intent == "new_entry":
                self.metrics.rejected_execution_events += 1
                self.metrics.automatic_order_creation_paused = True
                self.store.write_snapshot(
                    f"{RUNTIME_EXECUTION_PREFIX}blocked.{proposal.orderIntentId}",
                    {
                        "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
                        "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
                        "decision_id": proposal.decisionId,
                        "order_intent_id": proposal.orderIntentId,
                        "status": "entry_creation_paused_for_reconciliation",
                        "risk_reducing_exits_allowed": self.metrics.risk_reducing_exits_allowed,
                        "recorded_at": _now().isoformat(),
                        "reason_codes": ("weighted_voting.runtime.reconciliation_blocks_new_entries",),
                    },
                )
                return
            if self.metrics.automatic_order_creation_paused and proposal.intent == "new_entry":
                self.metrics.rejected_execution_events += 1
                self.store.write_snapshot(
                    f"{RUNTIME_EXECUTION_PREFIX}blocked.{proposal.orderIntentId}",
                    {
                        "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
                        "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
                        "decision_id": proposal.decisionId,
                        "order_intent_id": proposal.orderIntentId,
                        "status": "automatic_order_creation_paused",
                        "risk_reducing_exits_allowed": self.metrics.risk_reducing_exits_allowed,
                        "recorded_at": _now().isoformat(),
                        "reason_codes": ("weighted_voting.runtime.automatic_entries_paused",),
                    },
                )
                return
            item = enqueue_weighted_voting_execution_order(
                store=self.store,
                proposal=proposal,
                global_application=global_application,
                local_gate_result=local_gate_result,
                enqueued_at=evaluated_at,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            self.metrics.rejected_execution_events += 1
            self.metrics.last_error = f"WeightedVotingExecutionQueue: {exc}"
            self.metrics.automatic_order_creation_paused = True
            self.metrics.recovery_required = True
            self.metrics.pause_reason = "weighted_voting.runtime.global_risk_or_execution_queue_unavailable"
            return
        if item is None:
            return
        try:
            self.execution_queue.put_nowait(item)
            self.metrics.enqueued_orders += 1
            self.metrics.execution_queue_depth = self.execution_queue.qsize()
            self._write_execution_record(item, status="enqueued", reason_codes=("weighted_voting.runtime.execution_enqueued",))
        except asyncio.QueueFull:
            self.metrics.rejected_execution_events += 1
            self.metrics.automatic_order_creation_paused = True
            self._write_execution_record(item, status="rejected_backpressure", reason_codes=("weighted_voting.runtime.execution_queue_full",))

    def _start_worker(self, worker: WeightedVotingRuntimeWorker) -> None:
        self.tasks[worker.worker_id] = asyncio.create_task(self._run_worker(worker), name=worker.worker_id)

    async def _run_worker(self, worker: WeightedVotingRuntimeWorker) -> None:
        while not self.stop_event.is_set():
            try:
                await worker.run()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures = self.metrics.worker_failures.get(worker.worker_id, 0) + 1
                self.metrics.worker_failures[worker.worker_id] = failures
                self.metrics.last_error = f"{worker.worker_id}: {exc}"
                if failures >= self.config.worker_restart_failure_threshold:
                    self.metrics.automatic_order_creation_paused = True
                    self.metrics.circuit_breaker_open = True
                    self.metrics.pause_reason = "weighted_voting.runtime.worker_failure_circuit_breaker"
                    self._write_status("degraded", ("weighted_voting.runtime.worker_failure_threshold_pause",))
                    await asyncio.sleep(self.config.maintenance_interval_seconds)
                    return
                self.metrics.worker_restarts[worker.worker_id] = self.metrics.worker_restarts.get(worker.worker_id, 0) + 1
                await asyncio.sleep(0)

    def _write_event_record(
        self,
        event: WeightedVotingFinalisedBarEvent,
        status: str,
        idempotency_key: str | None,
        reason_codes: tuple[str, ...],
        *,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        record = {
            **event.as_dict(),
            "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "status": status,
            "idempotency_key": idempotency_key,
            "decision_id": decision_id,
            "queue_depth": self.event_bus.depth(),
            "recorded_at": _now().isoformat(),
            "automatic_order_creation_paused": self.metrics.automatic_order_creation_paused,
            "reason_codes": reason_codes,
        }
        key = _event_key(idempotency_key or event.event_id)
        self.store.write_snapshot(key, record)
        self._write_status("running" if self.metrics.supervisor_started else "stopped", reason_codes)
        return record

    def _write_execution_record(
        self,
        item: WeightedVotingExecutionQueueItem,
        *,
        status: str,
        reason_codes: tuple[str, ...],
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = {
            "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "queue_id": item.queue_id,
            "idempotency_key": item.idempotency_key,
            "decision_id": item.command.decision_id,
            "order_intent_id": item.command.order_intent_id,
            "client_order_id": item.command.client_order_id,
            "status": status,
            "result": result,
            "recorded_at": _now().isoformat(),
            "automatic_order_creation_paused": self.metrics.automatic_order_creation_paused,
            "reason_codes": reason_codes,
        }
        self.store.write_snapshot(f"{RUNTIME_EXECUTION_PREFIX}{item.command.client_order_id}.{status}", record)
        self._write_status("running" if self.metrics.supervisor_started else "stopped", reason_codes)
        return record

    def _write_checkpoint(self, event: WeightedVotingFinalisedBarEvent, idempotency_key: str, *, decision_id: str | None, status: str) -> None:
        checkpoint = {
            "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "symbol": event.symbol.upper(),
            "finalised_candle_timestamp": event.finalised_candle_timestamp.isoformat(),
            "data_manifest_hash": event.data_manifest_hash,
            "idempotency_key": idempotency_key,
            "decision_id": decision_id,
            "status": status,
            "updated_at": _now().isoformat(),
            "reason_codes": ("weighted_voting.runtime.checkpoint_persisted",),
        }
        self.store.write_snapshot(_checkpoint_key(event.symbol), checkpoint)
        self.metrics.last_event_timestamp_by_symbol[event.symbol.upper()] = checkpoint["finalised_candle_timestamp"]
        self.metrics.last_checkpoint_by_symbol[event.symbol.upper()] = idempotency_key

    def _write_status(self, status: str, reason_codes: tuple[str, ...]) -> None:
        self.store.write_snapshot(
            RUNTIME_STATUS_KEY,
            {
                "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
                "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
                "status": status,
                "health": self.health(),
                "updated_at": _now().isoformat(),
                "reason_codes": reason_codes,
            },
        )


_DEFAULT_SUPERVISOR: WeightedVotingRuntimeSupervisor | None = None


def get_weighted_voting_runtime_supervisor() -> WeightedVotingRuntimeSupervisor:
    global _DEFAULT_SUPERVISOR
    if _DEFAULT_SUPERVISOR is None:
        _DEFAULT_SUPERVISOR = WeightedVotingRuntimeSupervisor()
    return _DEFAULT_SUPERVISOR


async def publish_weighted_voting_finalised_bar_event(payload: dict[str, Any], *, replay_recovery: bool = False) -> bool:
    supervisor = get_weighted_voting_runtime_supervisor()
    snapshot = build_weighted_voting_market_snapshot(payload)
    event = WeightedVotingFinalisedBarEvent(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        symbol=snapshot.symbol,
        finalised_candle_timestamp=snapshot.data_timestamp,
        data_manifest_hash=snapshot.data_manifest_hash,
        market_payload=payload,
        published_at=_now(),
        replay_recovery=replay_recovery,
    )
    return await supervisor.publish_finalised_bar(event)


def weighted_voting_bar_event_idempotency_key(
    *,
    symbol: str,
    finalised_candle_timestamp: datetime,
    data_manifest_hash: str,
    settings_version: str,
    weight_version: str,
) -> str:
    payload = {
        "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
        "symbol": symbol.upper(),
        "finalised_candle_timestamp": finalised_candle_timestamp.isoformat(),
        "data_manifest_hash": data_manifest_hash,
        "settings_version": settings_version,
        "weight_version": weight_version,
    }
    return "weighted_voting.runtime.idempotency." + _hash_payload(payload)


def runtime_supervisor_status() -> dict[str, Any]:
    return {
        "version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "startsWithBackend": True,
        "dashboardRequired": False,
        "boundedQueues": True,
        "boundedExecutionQueue": True,
        "sequentialPerSymbol": True,
        "dashboardSubmitsOrders": False,
        "automaticSubmissionRolloutGated": True,
        "idempotencyFields": (
            "algorithm_id",
            "symbol",
            "finalised_candle_timestamp",
            "data_manifest_hash",
            "settings_version",
            "weight_version",
        ),
        "workers": (
            "WeightedVotingBarEventWorker",
            "WeightedVotingDecisionWorker",
            "WeightedVotingRiskWorker",
            "WeightedVotingExecutionWorker",
            "WeightedVotingReconciliationWorker",
            "WeightedVotingPositionManager",
            "WeightedVotingDailyUpdateWorker",
            "WeightedVotingRecoveryWorker",
            "WeightedVotingHeartbeatWorker",
        ),
        "reasonCodes": ("weighted_voting.runtime_supervisor.contract.ready",),
    }


def _market_holidays(year: int) -> set[date]:
    return {
        date(year, 1, 1),
        date(year, 7, 4),
        date(year, 12, 25),
    }


def _event_key(idempotency_key: str) -> str:
    return f"{RUNTIME_EVENT_PREFIX}{idempotency_key}"


def _checkpoint_key(symbol: str) -> str:
    return f"{RUNTIME_CHECKPOINT_PREFIX}{symbol.upper()}"


def _read_optional(store: WeightedVotingStateStore, key: str) -> dict | None:
    try:
        return store.read_snapshot(key)
    except KeyError:
        return None


def _parse_optional_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None


def _event_degradation_reasons(
    event: WeightedVotingFinalisedBarEvent,
    snapshot: Any,
    *,
    max_lag_seconds: int,
) -> tuple[str, ...]:
    reasons: list[str] = []
    freshness = event.market_payload.get("data_freshness_seconds")
    if freshness is not None and _safe_float(freshness) > max_lag_seconds:
        reasons.append("weighted_voting.runtime.recovery.stale_market_data_feed")
    quote_timestamp = _parse_optional_datetime(event.market_payload.get("quote_timestamp") or event.market_payload.get("quoteTimestamp"))
    if quote_timestamp is not None:
        quote_lag = abs((snapshot.data_timestamp - quote_timestamp).total_seconds())
        if quote_lag > max_lag_seconds:
            reasons.append("weighted_voting.runtime.recovery.stale_quote_feed")
    if event.finalised_candle_timestamp > _now() + timedelta(seconds=max_lag_seconds):
        reasons.append("weighted_voting.runtime.recovery.clock_skew_future_bar")
    if event.published_at > _now() + timedelta(seconds=max_lag_seconds):
        reasons.append("weighted_voting.runtime.recovery.clock_skew_future_publish")
    return tuple(dict.fromkeys(reasons))


def _unresolved(boundary: str, key: str, status: str, reason_code: str, *, error: str | None = None) -> dict[str, Any]:
    payload = {
        "boundary": boundary,
        "key": key,
        "status": status,
        "reasonCode": reason_code,
    }
    if error:
        payload["error"] = error
    return payload


def _dedupe_unresolved(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for item in items:
        deduped[f"{item.get('boundary')}:{item.get('key')}:{item.get('status')}"] = item
    return list(deduped.values())


def _local_gate_result_from_payload(payload: dict[str, Any]) -> WeightedVotingGatePipelineResult:
    return WeightedVotingGatePipelineResult(
        permission_granted=bool(payload.get("permission_granted", payload.get("permissionGranted", False))),
        mode=str(payload.get("mode") or "automatic"),
        gate_results=(),
        reason_codes=tuple(str(code) for code in payload.get("reason_codes", payload.get("reasonCodes", ()))),
        explanation=str(payload.get("explanation") or "Weighted Voting runtime restored persisted local gate result for automatic execution."),
    )


def _broker_observations_from_gateway(
    gateway: PaperOrderGateway,
    store: WeightedVotingStateStore,
    *,
    observed_at: datetime,
) -> tuple[tuple[WeightedVotingBrokerFillObservation, ...], tuple[WeightedVotingBrokerPositionObservation, ...]]:
    fills: list[WeightedVotingBrokerFillObservation] = []
    for key, payload in _store_items(store):
        if not key.startswith("weighted_voting.execution_gateway.command."):
            continue
        client_order_id = str(payload.get("clientOrderId") or "")
        if not client_order_id:
            continue
        fill = gateway.broker.refresh_order(client_order_id)
        if fill is None or int(fill.filledQuantity) <= 0 or fill.averageFillPrice is None:
            continue
        fills.append(
            WeightedVotingBrokerFillObservation(
                fill_id=f"{client_order_id}.{fill.status}.{fill.filledQuantity}.{fill.filledAt.isoformat()}",
                client_order_id=client_order_id,
                algorithm_id=str(fill.algorithmId),
                symbol=str(fill.symbol),
                side=str(fill.side.value if hasattr(fill.side, "value") else fill.side),
                quantity=int(fill.filledQuantity),
                average_fill_price=float(fill.averageFillPrice),
                filled_at=fill.filledAt,
            )
        )
    positions = []
    for position in gateway.broker.refresh_positions():
        if not isinstance(position, dict):
            continue
        positions.append(
            WeightedVotingBrokerPositionObservation(
                client_order_id=position.get("clientOrderId"),
                algorithm_id=position.get("algorithmId"),
                symbol=str(position.get("symbol") or "SPY"),
                quantity=int(position.get("quantity") or 0),
                average_entry_price=float(position.get("averageEntryPrice") or position.get("average_entry_price") or 0.01),
                observed_at=observed_at,
                broker_position_id=position.get("positionId") or position.get("brokerPositionId"),
            )
        )
    return tuple(fills), tuple(positions)


def _store_items(store: WeightedVotingStateStore) -> tuple[tuple[str, dict[str, Any]], ...]:
    snapshots = getattr(store, "snapshots", None)
    if not isinstance(snapshots, dict):
        return ()
    return tuple((str(key), value) for key, value in snapshots.items() if isinstance(value, dict))


def _bar_summary(event: WeightedVotingFinalisedBarEvent) -> dict[str, Any]:
    return {
        "algorithmId": event.algorithm_id,
        "symbol": event.symbol.upper(),
        "finalisedCandleTimestamp": event.finalised_candle_timestamp.isoformat(),
        "dataManifestHash": event.data_manifest_hash,
        "publishedAt": event.published_at.isoformat(),
        "eventId": event.event_id,
    }


def _rollout_state(flags: WeightedVotingRolloutFlags | None, validation: WeightedVotingRolloutValidation | None) -> dict[str, Any]:
    return {
        "automaticSubmissionEnabled": bool(flags.auto_submit_enabled) if flags else False,
        "paperTradingOnly": True,
        "validationPassed": bool(automatic_submission_allowed(flags=flags, validation=validation)) if flags and validation else False,
        "rolloutGatePresent": flags is not None and validation is not None,
    }


def _copy_nested_counts(value: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    return {str(key): dict(inner) for key, inner in value.items()}


def _deep_get(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    cursor: Any = payload
    for part in path:
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
    return cursor


def _signals_from_result(result: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    candidates = (
        result.get("signals"),
        _deep_get(result, ("signalBundle", "signals")),
        _deep_get(result, ("observability", "signals")),
    )
    for candidate in candidates:
        if isinstance(candidate, list):
            return tuple(item for item in candidate if isinstance(item, dict))
        if isinstance(candidate, tuple):
            return tuple(item for item in candidate if isinstance(item, dict))
    return ()


def _reason_codes_from_result(result: dict[str, Any]) -> tuple[str, ...]:
    codes: list[str] = []
    for key in ("reasonCodes", "reason_codes"):
        value = result.get(key)
        if isinstance(value, (list, tuple)):
            codes.extend(str(item) for item in value)
    gate_result = result.get("gateResult")
    if isinstance(gate_result, dict):
        for key in ("reasonCodes", "reason_codes"):
            value = gate_result.get(key)
            if isinstance(value, (list, tuple)):
                codes.extend(str(item) for item in value)
    decision = result.get("decision")
    if isinstance(decision, dict):
        for key in ("reasonCodes", "reason_codes"):
            value = decision.get(key)
            if isinstance(value, (list, tuple)):
                codes.extend(str(item) for item in value)
    return tuple(codes)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _runtime_cost_estimate(
    payload: dict[str, Any],
    *,
    effective_settings: WeightedEffectiveSettings,
    weighted_config: WeightedVotingConfig,
    observed_at: datetime,
) -> WeightedVotingExecutionCostEstimate:
    return WeightedVotingExecutionCostEstimate(
        slippage_per_share=effective_settings.slippage_allowance_per_share,
        fee_per_share=weighted_config.fee_per_share,
        observed_at=observed_at,
        source_id="weighted_voting.runtime.cost_estimate_from_stable_settings",
        available=True,
        reason_codes=("weighted_voting.runtime.cost_estimate_ignores_bar_payload_settings",),
    )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _hash_payload(value: Any) -> str:
    encoded = json.dumps(_json_ready(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _now() -> datetime:
    return datetime.now(timezone.utc)
