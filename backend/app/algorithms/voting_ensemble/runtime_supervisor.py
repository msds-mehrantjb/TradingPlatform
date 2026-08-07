"""Voting Ensemble-owned backend runtime supervisor."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from backend.app.config import Settings, get_settings
from backend.app.algorithms.voting_ensemble.finalized_bar_producer import (
    VotingEnsembleAutomaticEvaluationPayloadBuilder,
    VotingEnsembleCandleStore,
    VotingEnsembleFinalizedBarEventStore,
    VotingEnsembleFinalizedBarMarketEvent,
    VotingEnsembleFinalizedBarProducer,
    VotingEnsembleFinalizedBarProducerConfig,
    VotingEnsembleMarketDataClient,
)
from backend.app.algorithms.voting_ensemble.paper_execution import (
    VOTING_ENSEMBLE_ALGORITHM_ID,
    VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
    VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
    VOTING_ENSEMBLE_PAPER_EXECUTION_RUNTIME,
    VotingEnsemblePaperExecutionRuntime,
)
from backend.app.algorithms.voting_ensemble.runtime.events import FinalizedOneMinuteBarEvent
from backend.app.algorithms.voting_ensemble.runtime.orchestrator import VOTING_ENSEMBLE_RUNTIME, VotingEnsembleRuntimeOrchestrator


VOTING_ENSEMBLE_RUNTIME_SUPERVISOR_VERSION = "voting_ensemble_runtime_supervisor_v1"
VOTING_ENSEMBLE_CONTROL_NAMESPACE = "voting_ensemble.runtime.controls"
VOTING_ENSEMBLE_CONTROL_VERSION = "voting_ensemble_runtime_control_v1"
VOTING_ENSEMBLE_SUPERVISOR_WORKERS = (
    "finalized_bar_producer",
    "finalized_bar_event_consumer",
    "evaluation_worker",
    "execution_worker",
    "position_order_manager",
    "reconciliation_loop",
    "health_monitor",
)
VOTING_ENSEMBLE_RECOVERABLE_WORKER_ENTRY_BLOCKS = {
    f"voting_ensemble.runtime.{worker_id}.failed": worker_id for worker_id in VOTING_ENSEMBLE_SUPERVISOR_WORKERS
}


@dataclass
class VotingEnsembleRuntimeControl:
    algorithmId: str = VOTING_ENSEMBLE_ALGORITHM_ID
    requestedPaperTradingEnabled: bool = False
    effectivePaperTradingEnabled: bool = False
    liveTradingEnabled: bool = False
    newEntriesEnabled: bool = False
    killSwitchActive: bool = False
    controlVersion: str = VOTING_ENSEMBLE_CONTROL_VERSION
    updatedAt: str | None = None
    updatedBy: str = "system"
    reasonCodes: list[str] = field(default_factory=lambda: ["voting_ensemble.control.default_paper_off"])
    localEntryBlockActive: bool = False
    localEntryBlockReasonCodes: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "requestedPaperTradingEnabled": self.requestedPaperTradingEnabled,
            "effectivePaperTradingEnabled": self.effectivePaperTradingEnabled,
            "liveTradingEnabled": False,
            "newEntriesEnabled": self.newEntriesEnabled,
            "killSwitchActive": self.killSwitchActive,
            "controlVersion": self.controlVersion,
            "updatedAt": self.updatedAt,
            "updatedBy": self.updatedBy,
            "reasonCodes": list(self.reasonCodes),
            "localEntryBlockActive": self.localEntryBlockActive,
            "localEntryBlockReasonCodes": list(self.localEntryBlockReasonCodes),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "VotingEnsembleRuntimeControl":
        if payload.get("algorithmId", payload.get("algorithm_id", VOTING_ENSEMBLE_ALGORITHM_ID)) != VOTING_ENSEMBLE_ALGORITHM_ID:
            raise ValueError("Voting Ensemble control payload must use algorithmId=voting_ensemble")
        return cls(
            requestedPaperTradingEnabled=bool(payload.get("requestedPaperTradingEnabled", False)),
            effectivePaperTradingEnabled=bool(payload.get("effectivePaperTradingEnabled", False)),
            liveTradingEnabled=False,
            newEntriesEnabled=bool(payload.get("newEntriesEnabled", False)),
            killSwitchActive=bool(payload.get("killSwitchActive", False)),
            controlVersion=str(payload.get("controlVersion") or VOTING_ENSEMBLE_CONTROL_VERSION),
            updatedAt=str(payload["updatedAt"]) if payload.get("updatedAt") else None,
            updatedBy=str(payload.get("updatedBy") or "system"),
            reasonCodes=list(payload.get("reasonCodes") or ["voting_ensemble.control.loaded"]),
            localEntryBlockActive=bool(payload.get("localEntryBlockActive", False)),
            localEntryBlockReasonCodes=list(payload.get("localEntryBlockReasonCodes") or []),
        )


class VotingEnsembleRuntimeControlRepository:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).resolve() if path is not None else default_control_store_path()
        self._lock = Lock()
        self._last_signature: tuple[int, int] | None = None

    def load(self) -> VotingEnsembleRuntimeControl:
        with self._lock:
            return self._load_unlocked()

    def load_if_changed(self, current: VotingEnsembleRuntimeControl) -> VotingEnsembleRuntimeControl:
        with self._lock:
            signature = self._signature_unlocked()
            if signature is not None and signature == self._last_signature:
                return current
            if signature is None and self._last_signature is None:
                return current
            return self._load_unlocked()

    def save(self, control: VotingEnsembleRuntimeControl) -> VotingEnsembleRuntimeControl:
        with self._lock:
            return self._save_unlocked(control)

    def _load_unlocked(self) -> VotingEnsembleRuntimeControl:
        if not self.path.exists():
            control = VotingEnsembleRuntimeControl(updatedAt=_now())
            self._save_unlocked(control)
            return control
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            control = VotingEnsembleRuntimeControl.from_payload(payload if isinstance(payload, dict) else {})
            self._last_signature = self._signature_unlocked()
        except Exception:
            control = VotingEnsembleRuntimeControl(
                updatedAt=_now(),
                reasonCodes=["voting_ensemble.control.unrecoverable_startup_state_fail_closed"],
                localEntryBlockActive=True,
                localEntryBlockReasonCodes=["voting_ensemble.control.unrecoverable_startup_state_fail_closed"],
            )
            self._save_unlocked(control)
        return control

    def _save_unlocked(self, control: VotingEnsembleRuntimeControl) -> VotingEnsembleRuntimeControl:
        control.liveTradingEnabled = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        encoded = json.dumps(control.snapshot(), sort_keys=True, indent=2)
        try:
            temporary.write_text(encoded, encoding="utf-8")
            temporary.replace(self.path)
        except PermissionError:
            self.path.write_text(encoded, encoding="utf-8")
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        self._last_signature = self._signature_unlocked()
        return control

    def _signature_unlocked(self) -> tuple[int, int] | None:
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)


class VotingEnsembleRuntimeControlStore:
    namespace: str = VOTING_ENSEMBLE_CONTROL_NAMESPACE

    def __init__(self, repository: VotingEnsembleRuntimeControlRepository | None = None) -> None:
        self.repository = repository or VotingEnsembleRuntimeControlRepository()
        self.control = self.repository.load()

    @property
    def automaticPaperTradingEnabled(self) -> bool:
        return self.control.effectivePaperTradingEnabled

    @property
    def entryCreationBlocked(self) -> bool:
        return self.control.localEntryBlockActive

    @property
    def blockReasonCodes(self) -> list[str]:
        return self.control.localEntryBlockReasonCodes

    def reload_if_changed(self) -> VotingEnsembleRuntimeControl:
        self.control = self.repository.load_if_changed(self.control)
        return self.control

    def update_requested_paper(self, requested: bool, *, updated_by: str, reason_codes: list[str]) -> VotingEnsembleRuntimeControl:
        self.reload_if_changed()
        self.control.requestedPaperTradingEnabled = bool(requested)
        if not requested:
            self.control.effectivePaperTradingEnabled = False
            self.control.newEntriesEnabled = False
        self.control.liveTradingEnabled = False
        self.control.updatedAt = _now()
        self.control.updatedBy = updated_by
        self.control.reasonCodes = reason_codes
        return self.repository.save(self.control)

    def save_effective(self, *, effective: bool, new_entries: bool, reason_codes: list[str]) -> VotingEnsembleRuntimeControl:
        self.reload_if_changed()
        self.control.effectivePaperTradingEnabled = bool(effective)
        self.control.newEntriesEnabled = bool(new_entries)
        self.control.liveTradingEnabled = False
        self.control.updatedAt = _now()
        self.control.reasonCodes = reason_codes
        return self.repository.save(self.control)

    def block_new_entries(self, reason_code: str) -> None:
        self.reload_if_changed()
        self.control.localEntryBlockActive = True
        if reason_code not in self.control.localEntryBlockReasonCodes:
            self.control.localEntryBlockReasonCodes.append(reason_code)
        self.control.newEntriesEnabled = False
        self.control.effectivePaperTradingEnabled = False
        self.control.updatedAt = _now()
        self.repository.save(self.control)

    def clear_entry_block(self, reason_code: str) -> None:
        self.reload_if_changed()
        self.control.localEntryBlockActive = False
        self.control.localEntryBlockReasonCodes = []
        self.control.reasonCodes = [reason_code]
        self.control.updatedAt = _now()
        self.repository.save(self.control)

    def snapshot(self, *, reason_codes: list[str] | None = None) -> dict[str, Any]:
        self.reload_if_changed()
        payload = self.control.snapshot()
        payload["namespace"] = self.namespace
        if reason_codes is not None:
            payload["reasonCodes"] = reason_codes
        return payload


@dataclass
class VotingEnsembleRuntimeSupervisorMetrics:
    supervisorStarted: bool = False
    readiness: str = "blocked"
    finalizedBarsReceived: int = 0
    finalizedBarsQueued: int = 0
    finalizedBarsProduced: int = 0
    duplicateFinalizedBarEvents: int = 0
    staleFinalizedBarEvents: int = 0
    rejectedEvents: int = 0
    evaluationWorkerFailures: int = 0
    executionWorkerFailures: int = 0
    reconciliationFailures: int = 0
    workerStatus: dict[str, str] = field(default_factory=lambda: {worker: "stopped" for worker in VOTING_ENSEMBLE_SUPERVISOR_WORKERS})
    lastFinalizedBarEvent: dict[str, Any] | None = None
    lastFinalizedBarProducerResult: dict[str, Any] | None = None
    lastEvaluationJob: dict[str, Any] | None = None
    lastExecutionResult: dict[str, Any] | None = None
    lastReconciliation: dict[str, Any] | None = None
    lastError: str | None = None
    lastErrorAt: str | None = None


@dataclass(frozen=True)
class VotingEnsembleRuntimeSupervisorConfig:
    event_queue_maxsize: int = 256
    event_consumer_poll_seconds: float = 0.25
    execution_worker_poll_seconds: float = 0.25
    reconciliation_poll_seconds: float = 15.0
    health_poll_seconds: float = 5.0
    global_master_paper_enabled: bool = True
    market_data_healthy_default: bool = True
    finalized_bar_producer_enabled: bool = True


class VotingEnsembleRuntimeSupervisor:
    def __init__(
        self,
        *,
        runtime: VotingEnsembleRuntimeOrchestrator | None = None,
        paper_execution_runtime: VotingEnsemblePaperExecutionRuntime | None = None,
        control_store: VotingEnsembleRuntimeControlStore | None = None,
        config: VotingEnsembleRuntimeSupervisorConfig | None = None,
        settings: Settings | None = None,
        market_clock_provider: Callable[[], dict[str, Any]] | None = None,
        market_data_client: VotingEnsembleMarketDataClient | None = None,
        candle_store: VotingEnsembleCandleStore | None = None,
        finalized_bar_event_store: VotingEnsembleFinalizedBarEventStore | None = None,
        finalized_bar_producer_config: VotingEnsembleFinalizedBarProducerConfig | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.market_clock_provider = market_clock_provider or self._default_market_clock
        self.runtime = runtime or VOTING_ENSEMBLE_RUNTIME
        self.paper_execution_runtime = paper_execution_runtime or self.runtime.paper_execution_runtime or VOTING_ENSEMBLE_PAPER_EXECUTION_RUNTIME
        self.runtime.autoManageWorker = True
        self.control_store = control_store or VotingEnsembleRuntimeControlStore()
        self.config = config or VotingEnsembleRuntimeSupervisorConfig()
        self.paper_execution_runtime.entry_permission_provider = self.entry_permission_snapshot
        self.finalized_bar_event_store = finalized_bar_event_store or VotingEnsembleFinalizedBarEventStore()
        self.automatic_payload_builder = (
            VotingEnsembleAutomaticEvaluationPayloadBuilder(
                candle_store=candle_store,
                control_snapshot_provider=self.entry_permission_snapshot,
                paper_inventory_provider=self.paper_inventory,
                market_status_provider=self.market_clock_provider,
                account_snapshot_provider=self._default_account_snapshot,
                quote_provider=self._latest_quote,
                last_trade_provider=self._latest_trade,
                feed=(finalized_bar_producer_config.feed if finalized_bar_producer_config else "iex"),
                history_limit=(finalized_bar_producer_config.history_limit if finalized_bar_producer_config else 390),
            )
            if candle_store is not None
            else None
        )
        self.runtime.set_automatic_payload_builder(self.automatic_payload_builder)
        self.finalized_bar_producer = (
            VotingEnsembleFinalizedBarProducer(
                market_data_client=market_data_client,
                candle_store=candle_store,
                publish_event=self.publish_finalized_market_event,
                event_store=self.finalized_bar_event_store,
                config=finalized_bar_producer_config,
            )
            if market_data_client is not None and candle_store is not None and self.config.finalized_bar_producer_enabled
            else None
        )
        self.event_queue: asyncio.Queue[FinalizedOneMinuteBarEvent] = asyncio.Queue(maxsize=self.config.event_queue_maxsize)
        self.stop_event = asyncio.Event()
        self.metrics = VotingEnsembleRuntimeSupervisorMetrics()
        self._tasks: list[asyncio.Task] = []
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.stop_event.clear()
        self.metrics.supervisorStarted = True
        self.runtime.autoManageWorker = True
        self.runtime.recover_incomplete_jobs()
        self.runtime.start()
        self.paper_execution_runtime.autoManageWorker = True
        self.paper_execution_runtime.start()
        if self.finalized_bar_producer is not None:
            self._start_loop("finalized_bar_producer", self._finalized_bar_producer_loop)
        else:
            self.metrics.workerStatus["finalized_bar_producer"] = "not_configured"
        self._start_loop("finalized_bar_event_consumer", self._finalized_bar_event_consumer_loop)
        self._start_loop("execution_worker", self._execution_worker_loop)
        self._start_loop("position_order_manager", self._position_order_manager_loop)
        self._start_loop("reconciliation_loop", self._reconciliation_loop)
        self._start_loop("health_monitor", self._health_monitor_loop)
        self.metrics.workerStatus["evaluation_worker"] = "running" if self.runtime.summary().get("workerAlive") else "blocked"
        self._run_reconciliation_once()
        self._refresh_readiness()

    async def shutdown(self) -> None:
        if not self._started and not self._tasks:
            return
        self.stop_event.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self.runtime.autoManageWorker = False
        self.paper_execution_runtime.autoManageWorker = False
        self.runtime.stop()
        self.paper_execution_runtime.stop()
        broker = getattr(getattr(self.paper_execution_runtime, "paper_gateway", None), "broker", None)
        close = getattr(broker, "close", None)
        if callable(close):
            close()
        for worker in VOTING_ENSEMBLE_SUPERVISOR_WORKERS:
            self.metrics.workerStatus[worker] = "stopped"
        self.metrics.supervisorStarted = False
        self.metrics.readiness = "blocked"
        self._started = False

    def enqueue_manual_evaluation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.enqueue_manual_evaluation(payload)

    def enqueue_finalized_bar_event(self, event: FinalizedOneMinuteBarEvent) -> dict[str, Any]:
        command = event.to_command()
        permission = self.entry_permission_snapshot()
        if not permission["newEntriesAllowed"]:
            return {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "commandKind": "finalized_bar_evaluation",
                "status": "blocked",
                "accepted": False,
                "deduplicated": False,
                "jobId": f"ve-blocked-{command.commandId}",
                "commandId": command.commandId,
                "error": "Voting Ensemble runtime readiness is blocked; new entries are disabled.",
                "reasonCodes": ["voting_ensemble.runtime.supervisor.new_entries_blocked", *permission["blockers"]],
            }
        self.metrics.finalizedBarsReceived += 1
        self.metrics.lastFinalizedBarEvent = {
            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
            "symbol": event.symbol.upper(),
            "barEndTimestamp": event.barEndTimestamp.isoformat(),
            "finalized": event.finalized,
        }
        job = self.runtime.enqueue_command(command)
        self.metrics.finalizedBarsQueued += 1 if job.get("accepted") else 0
        self.metrics.lastEvaluationJob = job
        return job

    def publish_finalized_market_event(
        self,
        event: VotingEnsembleFinalizedBarMarketEvent,
        settings_hash: str,
        deadline_seconds: int,
    ) -> dict[str, Any]:
        self.metrics.finalizedBarsProduced += 1
        runtime_event = FinalizedOneMinuteBarEvent.from_market_event(
            event,
            settings_hash=settings_hash,
            deadline_seconds=deadline_seconds,
        )
        job = self.enqueue_finalized_bar_event(runtime_event)
        self.metrics.lastFinalizedBarProducerResult = {
            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
            "eventId": event.eventId,
            "barEndTimestamp": event.barEndTimestamp.isoformat(),
            "job": job,
            "reasonCodes": ["voting_ensemble.runtime.supervisor.finalized_market_event_published"],
        }
        return job

    def enqueue_backtest(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.enqueue_backtest(payload)

    def enqueue_replay(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.enqueue_replay(payload)

    def enqueue_settings_refresh(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.enqueue_settings_refresh(payload)

    def enqueue_recovery_reconciliation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.enqueue_recovery_reconciliation(payload)

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self.runtime.get_job(job_id)

    def get_result(self, job_id: str) -> dict[str, Any]:
        return self.runtime.get_result(job_id)

    def readiness_status(self) -> dict[str, Any]:
        return self.status()["readiness"]

    def control_status(self, *, refresh_readiness: bool = True) -> dict[str, Any]:
        if refresh_readiness:
            self._refresh_readiness()
        return self.control_store.snapshot()

    def paper_inventory(self) -> dict[str, Any]:
        return self.paper_execution_runtime.inventory_snapshot()

    def update_control(
        self,
        *,
        requested_paper_trading_enabled: bool,
        clear_local_entry_block: bool = False,
        updated_by: str = "api",
        refresh_readiness: bool = True,
    ) -> dict[str, Any]:
        reason_codes = [
            "voting_ensemble.control.paper_requested_on"
            if requested_paper_trading_enabled
            else "voting_ensemble.control.paper_requested_off",
        ]
        self.control_store.update_requested_paper(
            requested_paper_trading_enabled,
            updated_by=updated_by,
            reason_codes=reason_codes,
        )
        if clear_local_entry_block:
            self.control_store.clear_entry_block("voting_ensemble.control.local_entry_block_cleared_by_operator")
        if refresh_readiness:
            self._refresh_readiness()
        return self.control_store.snapshot()

    def entry_permission_snapshot(self) -> dict[str, Any]:
        self._refresh_readiness()
        control = self.control_store.control
        local_paper_mode = _is_local_paper_mode(self.paper_execution_runtime)
        return {
            **control.snapshot(),
            "newEntriesAllowed": control.newEntriesEnabled,
            "blockers": _effective_blockers_from_reason_codes(control.reasonCodes),
            "protectiveExitsEnabled": True,
            "stopLossOrdersEnabled": True,
            "profitTargetOrdersEnabled": True,
            "positionReducingExitsEnabled": True,
            "endOfDayLiquidationEnabled": True,
            "fillProcessingEnabled": True,
            "cancelReplaceProcessingEnabled": True,
            "brokerReconciliationEnabled": not local_paper_mode,
            "localInventoryRecoveryEnabled": local_paper_mode,
            "localInventoryAuthority": "voting_ensemble.local_paper_account" if local_paper_mode else "broker_paper",
        }

    def status(self) -> dict[str, Any]:
        self.control_store.reload_if_changed()
        runtime_summary = self.runtime.summary()
        paper_summary = self.paper_execution_runtime.summary()
        if self.metrics.workerStatus.get("evaluation_worker") != "failed":
            self.metrics.workerStatus["evaluation_worker"] = "running" if runtime_summary.get("workerAlive") else self.metrics.workerStatus.get("evaluation_worker", "blocked")
        if self.metrics.workerStatus.get("execution_worker") != "failed":
            self.metrics.workerStatus["execution_worker"] = "running" if paper_summary.get("workerAlive") else self.metrics.workerStatus.get("execution_worker", "blocked")
        self._clear_recovered_worker_entry_block(runtime_summary, paper_summary)
        readiness = self._readiness_from_summaries(runtime_summary, paper_summary)
        effective = self._effective_control(runtime_summary, paper_summary)
        inventory = _paper_inventory_snapshot(self.paper_execution_runtime)
        jobs = _runtime_jobs(self.runtime)
        last_evaluation = _latest_record(jobs, "updatedAt", "completedAt", "startedAt", "createdAt")
        last_decision = _last_decision_from_evaluation(last_evaluation)
        last_execution_intent = _latest_record(inventory.get("outbox") or [], "updatedAt", "submittedAt", "createdAt", "evaluatedAt")
        local_paper_mode = _is_local_paper_mode(self.paper_execution_runtime)
        last_local_order = _latest_record(
            inventory.get("localOrders") or inventory.get("orders") or [],
            "updatedAt",
            "submittedAt",
            "acceptedAt",
            "createdAt",
            "observedAt",
            "persistedAt",
        )
        last_broker_order = _latest_record(
            inventory.get("brokerOrders") or [],
            "updatedAt",
            "submittedAt",
            "acceptedAt",
            "createdAt",
            "observedAt",
            "persistedAt",
        )
        last_reconciliation = _latest_record(
            [self.metrics.lastReconciliation, *(inventory.get("reconciliations") or [])],
            "evaluatedAt",
            "completedAt",
            "updatedAt",
            "createdAt",
            "persistedAt",
        )
        active_entry_blocks = _active_entry_blocks(readiness, effective, inventory, paper_summary)
        evaluation_worker_healthy = bool(runtime_summary.get("workerAlive")) and self.metrics.workerStatus.get("evaluation_worker") != "failed"
        execution_worker_healthy = bool(paper_summary.get("workerAlive")) and self.metrics.workerStatus.get("execution_worker") != "failed"
        checks = effective.get("checks", {})
        reconciliation_healthy = (
            self.metrics.workerStatus.get("reconciliation_loop") != "failed"
            and bool(checks.get("inventoryHealthy") if local_paper_mode else checks.get("inventoryReconciled"))
            and not bool(inventory.get("reconciliationBlocks"))
        )
        paper_ready_blocking_reason_codes = _paper_ready_blocking_reason_codes(
            local_paper_mode=local_paper_mode,
            supervisor_running=self.metrics.supervisorStarted,
            finalized_bar_producer_configured=self.finalized_bar_producer is not None,
            finalized_bar_event_consumer_healthy=self.metrics.workerStatus.get("finalized_bar_event_consumer") == "running",
            evaluation_worker_healthy=evaluation_worker_healthy,
            execution_worker_healthy=execution_worker_healthy,
            reconciliation_healthy=reconciliation_healthy,
            local_paper_account_loaded=bool(checks.get("localPaperAccountLoaded")),
            local_paper_account_verified=bool(checks.get("localPaperAccountLoaded")) and bool(checks.get("localPaperInventoryIsolated")),
            inventory_healthy=bool(checks.get("inventoryHealthy")),
            persistence_healthy=bool(checks.get("persistenceHealthy")),
            market_data_healthy=bool(checks.get("marketDataHealthy")),
            market_data_fresh=bool(checks.get("marketDataFresh")),
            market_clock_healthy=bool(checks.get("marketClockHealthy")),
            automatic_execution_enabled=bool(checks.get("automaticExecutionEnabled")),
            kill_switch_off=bool(checks.get("killSwitchOff")),
            external_broker_client_configured=getattr(self.paper_execution_runtime, "broker_client", None) is not None,
            durable_execution_state_active=bool(paper_summary.get("persistencePath")) or bool(paper_summary.get("durableExecutionStateActive")),
            new_entries_allowed=bool(effective["newEntriesEnabled"]) and bool(readiness["ready"]),
            active_entry_blocks=active_entry_blocks,
        )
        local_observability = _local_paper_observability(
            inventory=inventory,
            checks=checks,
            automatic_trading_ready=not paper_ready_blocking_reason_codes,
        )
        self.metrics.readiness = readiness["status"]
        return {
            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "supervisorVersion": VOTING_ENSEMBLE_RUNTIME_SUPERVISOR_VERSION,
            "executionMode": str(inventory.get("executionMode") or paper_summary.get("executionMode") or "LOCAL_PAPER"),
            "sourceAuthority": str(inventory.get("sourceAuthority") or local_observability.get("sourceAuthority") or "voting_ensemble_local_paper_account"),
            "paperReady": not paper_ready_blocking_reason_codes,
            "automaticTradingReady": not paper_ready_blocking_reason_codes,
            "paperReadyBlockingReasonCodes": paper_ready_blocking_reason_codes,
            "supervisorRunning": self.metrics.supervisorStarted,
            "supervisorStarted": self.metrics.supervisorStarted,
            "evaluationWorkerHealthy": evaluation_worker_healthy,
            "executionWorkerHealthy": execution_worker_healthy,
            "reconciliationHealthy": reconciliation_healthy,
            "requestedPaperTradingEnabled": self.control_store.control.requestedPaperTradingEnabled,
            "effectivePaperTradingEnabled": effective["effectivePaperTradingEnabled"],
            "liveTradingEnabled": False,
            "localPaperAccountVerified": bool(checks.get("localPaperAccountLoaded")) and bool(checks.get("localPaperInventoryIsolated")),
            "localInventoryVerified": bool(checks.get("localPaperInventoryIsolated")) and bool(checks.get("inventoryHealthy")),
            "brokerPaperAccountVerified": None if local_paper_mode else bool(checks.get("inventoryReconciled")),
            "localPaperObservability": local_observability,
            "localPaperAccount": local_observability["localPaperAccount"],
            "localPositions": local_observability["localPositions"],
            "localOrders": local_observability["localOrders"],
            "openOrders": local_observability["openOrders"],
            "recentFills": local_observability["recentFills"],
            "closedTrades": local_observability["closedTrades"],
            "localPaperAccountLoaded": bool(checks.get("localPaperAccountLoaded")),
            "inventoryHealthy": bool(checks.get("inventoryHealthy")),
            "persistenceHealthy": bool(checks.get("persistenceHealthy")),
            "marketDataHealthy": bool(checks.get("marketDataHealthy")),
            "marketDataFresh": bool(checks.get("marketDataFresh")),
            "marketClockHealthy": bool(checks.get("marketClockHealthy")),
            "killSwitchOff": bool(checks.get("killSwitchOff")),
            "automaticExecutionEnabled": bool(checks.get("automaticExecutionEnabled")),
            "marketOpen": bool(checks.get("marketClockHealthy")),
            "marketDataReady": bool(checks.get("marketDataHealthy")) and bool(checks.get("marketDataFresh")),
            "inventoryReconciled": bool(checks.get("inventoryHealthy")) and not bool(inventory.get("reconciliationBlocks")),
            "newEntriesAllowed": bool(effective["newEntriesEnabled"]) and bool(readiness["ready"]),
            "activeEntryBlocks": active_entry_blocks,
            "lastFinalizedBar": self.metrics.lastFinalizedBarEvent or self.metrics.lastFinalizedBarProducerResult,
            "lastEvaluation": last_evaluation,
            "lastDecision": last_decision,
            "lastExecutionIntent": last_execution_intent,
            "lastLocalOrder": last_local_order if local_paper_mode else None,
            "lastBrokerOrder": last_broker_order,
            "openVotingEnsembleOrders": _open_order_records(inventory),
            "openVotingEnsemblePositions": _open_position_records(inventory),
            "lastReconciliation": last_reconciliation,
            "lastError": self.metrics.lastError,
            "settingsHash": _settings_hash(
                last_evaluation=last_evaluation,
                last_decision=last_decision,
                last_execution_intent=last_execution_intent,
                last_broker_order=last_broker_order if not local_paper_mode else last_local_order,
            ),
            "controlStore": self.control_store.snapshot(),
            "readiness": readiness,
            "workerHealth": {
                "workerStatus": dict(self.metrics.workerStatus),
                "evaluationWorker": runtime_summary.get("workerThread", {}),
                "executionWorker": paper_summary.get("workerThread", {}),
                "failureCounts": {
                    "evaluationWorkerFailures": self.metrics.evaluationWorkerFailures,
                    "executionWorkerFailures": self.metrics.executionWorkerFailures,
                    "reconciliationFailures": self.metrics.reconciliationFailures,
                },
            },
            "eventConsumer": {
                "queueDepth": self.event_queue.qsize(),
                "finalizedBarsReceived": self.metrics.finalizedBarsReceived,
                "finalizedBarsQueued": self.metrics.finalizedBarsQueued,
                "finalizedBarsProduced": self.metrics.finalizedBarsProduced,
                "duplicateFinalizedBarEvents": self.metrics.duplicateFinalizedBarEvents,
                "staleFinalizedBarEvents": self.metrics.staleFinalizedBarEvents,
                "rejectedEvents": self.metrics.rejectedEvents,
                "lastFinalizedBarEvent": self.metrics.lastFinalizedBarEvent,
                "lastFinalizedBarProducerResult": self.metrics.lastFinalizedBarProducerResult,
                "eventStore": self.finalized_bar_event_store.summary(),
            },
            "runtime": runtime_summary,
            "paperExecution": paper_summary,
            "lastExecutionResult": self.metrics.lastExecutionResult,
            "lastErrorAt": self.metrics.lastErrorAt,
            "reasonCodes": ["voting_ensemble.runtime.supervisor.status_reported", *active_entry_blocks],
        }

    def summary(self) -> dict[str, Any]:
        return self.runtime.summary()

    def record_worker_failure(self, worker_id: str, exc: Exception | str) -> None:
        reason_code = f"voting_ensemble.runtime.{worker_id}.failed"
        if worker_id == "evaluation_worker":
            self.metrics.evaluationWorkerFailures += 1
        elif worker_id == "execution_worker":
            self.metrics.executionWorkerFailures += 1
        elif worker_id == "reconciliation_loop":
            self.metrics.reconciliationFailures += 1
        self.metrics.workerStatus[worker_id] = "failed"
        self.metrics.lastError = str(exc)
        self.metrics.lastErrorAt = _now()
        self.control_store.block_new_entries(reason_code)
        self._refresh_readiness()

    def _start_loop(self, worker_id: str, coroutine_factory: Callable[[], Awaitable[None]]) -> None:
        self.metrics.workerStatus[worker_id] = "starting"
        self._tasks.append(asyncio.create_task(self._run_loop(worker_id, coroutine_factory), name=f"voting-ensemble-{worker_id}"))

    async def _run_loop(self, worker_id: str, coroutine_factory: Callable[[], Awaitable[None]]) -> None:
        self.metrics.workerStatus[worker_id] = "running"
        try:
            await coroutine_factory()
        except asyncio.CancelledError:
            self.metrics.workerStatus[worker_id] = "stopped"
            raise
        except Exception as exc:
            self.record_worker_failure(worker_id, exc)

    async def _finalized_bar_event_consumer_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                event = await asyncio.wait_for(self.event_queue.get(), timeout=self.config.event_consumer_poll_seconds)
            except asyncio.TimeoutError:
                continue
            self.enqueue_finalized_bar_event(event)

    async def _finalized_bar_producer_loop(self) -> None:
        assert self.finalized_bar_producer is not None
        while not self.stop_event.is_set():
            results = await self.finalized_bar_producer.poll_once()
            for result in results:
                if result.get("duplicate"):
                    self.metrics.duplicateFinalizedBarEvents += 1
                if result.get("stale"):
                    self.metrics.staleFinalizedBarEvents += 1
                self.metrics.lastFinalizedBarProducerResult = result
            await asyncio.sleep(self.finalized_bar_producer.config.poll_seconds)

    async def _execution_worker_loop(self) -> None:
        while not self.stop_event.is_set():
            if not self.paper_execution_runtime.summary().get("workerAlive"):
                self.record_worker_failure("execution_worker", "Voting Ensemble paper execution worker is not alive")
                return
            await asyncio.sleep(self.config.execution_worker_poll_seconds)

    async def _position_order_manager_loop(self) -> None:
        while not self.stop_event.is_set():
            if _is_local_paper_mode(self.paper_execution_runtime):
                local_maintenance = getattr(self.paper_execution_runtime, "run_local_position_order_maintenance", None)
                if callable(local_maintenance):
                    await asyncio.to_thread(local_maintenance, evaluated_at=datetime.now(UTC))
            else:
                gateway = getattr(self.paper_execution_runtime, "paper_gateway", None)
                if gateway is not None:
                    await asyncio.to_thread(gateway.cancel_stale_orders, evaluated_at=datetime.now(UTC))
            await asyncio.sleep(self.config.reconciliation_poll_seconds)

    async def _reconciliation_loop(self) -> None:
        while not self.stop_event.is_set():
            if _is_local_paper_mode(self.paper_execution_runtime):
                local_validate = getattr(self.paper_execution_runtime, "validate_local_consistency", None)
                if callable(local_validate):
                    self.metrics.lastReconciliation = await asyncio.to_thread(local_validate, evaluated_at=datetime.now(UTC))
            else:
                broker_reconcile = getattr(self.paper_execution_runtime, "reconcile_broker_state", None)
                if callable(broker_reconcile):
                    self.metrics.lastReconciliation = await asyncio.to_thread(broker_reconcile, evaluated_at=datetime.now(UTC))
                else:
                    gateway = getattr(self.paper_execution_runtime, "paper_gateway", None)
                    if gateway is not None:
                        self.metrics.lastReconciliation = await asyncio.to_thread(gateway.recover_from_restart, evaluated_at=datetime.now(UTC))
            await asyncio.sleep(self.config.reconciliation_poll_seconds)

    async def _legacy_reconciliation_loop(self) -> None:
        while not self.stop_event.is_set():
            gateway = getattr(self.paper_execution_runtime, "paper_gateway", None)
            if gateway is not None:
                self.metrics.lastReconciliation = await asyncio.to_thread(gateway.recover_from_restart, evaluated_at=datetime.now(UTC))
            await asyncio.sleep(self.config.reconciliation_poll_seconds)

    async def _health_monitor_loop(self) -> None:
        while not self.stop_event.is_set():
            self._refresh_readiness()
            await asyncio.sleep(self.config.health_poll_seconds)

    def _refresh_readiness(self) -> None:
        self.control_store.reload_if_changed()
        requested_at_start = self.control_store.control.requestedPaperTradingEnabled
        runtime_summary = self.runtime.summary()
        paper_summary = self.paper_execution_runtime.summary()
        if self.metrics.workerStatus.get("evaluation_worker") != "failed":
            self.metrics.workerStatus["evaluation_worker"] = "running" if runtime_summary.get("workerAlive") else "blocked"
        if self.metrics.workerStatus.get("execution_worker") != "failed":
            self.metrics.workerStatus["execution_worker"] = "running" if paper_summary.get("workerAlive") else self.metrics.workerStatus.get("execution_worker", "blocked")
        self._clear_recovered_worker_entry_block(runtime_summary, paper_summary)
        readiness = self._readiness_from_summaries(runtime_summary, paper_summary)
        self.metrics.readiness = readiness["status"]
        effective = self._effective_control(runtime_summary, paper_summary)
        self.control_store.reload_if_changed()
        if self.control_store.control.requestedPaperTradingEnabled != requested_at_start:
            effective = self._effective_control(runtime_summary, paper_summary)
        self.control_store.save_effective(
            effective=effective["effectivePaperTradingEnabled"],
            new_entries=effective["newEntriesEnabled"],
            reason_codes=effective["reasonCodes"],
        )

    def _clear_recovered_worker_entry_block(self, runtime_summary: dict[str, Any], paper_summary: dict[str, Any]) -> None:
        control = self.control_store.control
        if not control.localEntryBlockActive or not control.localEntryBlockReasonCodes:
            return
        reasons = set(control.localEntryBlockReasonCodes)
        recoverable_reasons = set(VOTING_ENSEMBLE_RECOVERABLE_WORKER_ENTRY_BLOCKS)
        if not reasons <= recoverable_reasons:
            return
        if not all(self._worker_recovered_for_entry_block(reason, runtime_summary, paper_summary) for reason in reasons):
            return
        self.metrics.lastError = None
        self.metrics.lastErrorAt = None
        self.control_store.clear_entry_block("voting_ensemble.control.transient_worker_entry_block_recovered")

    def _worker_recovered_for_entry_block(
        self,
        reason_code: str,
        runtime_summary: dict[str, Any],
        paper_summary: dict[str, Any],
    ) -> bool:
        worker_id = VOTING_ENSEMBLE_RECOVERABLE_WORKER_ENTRY_BLOCKS.get(reason_code)
        if worker_id == "evaluation_worker":
            return self.metrics.workerStatus.get("evaluation_worker") == "running" and bool(runtime_summary.get("workerAlive"))
        if worker_id == "execution_worker":
            return self.metrics.workerStatus.get("execution_worker") == "running" and bool(paper_summary.get("workerAlive"))
        return self.metrics.workerStatus.get(str(worker_id)) == "running"

    def _readiness_from_summaries(self, runtime_summary: dict[str, Any], paper_summary: dict[str, Any]) -> dict[str, Any]:
        blockers: list[str] = []
        if not self.metrics.supervisorStarted:
            blockers.append("voting_ensemble.runtime.supervisor.not_started")
        if not runtime_summary.get("workerAlive"):
            blockers.append("voting_ensemble.runtime.evaluation_worker_not_alive")
        if not paper_summary.get("workerAlive"):
            blockers.append("voting_ensemble.runtime.execution_worker_not_alive")
        if paper_summary.get("persistenceHealthy") is False:
            blockers.append("voting_ensemble.paper_execution.persistence_failure_blocks_new_entries")
        if self.metrics.lastError:
            blockers.append("voting_ensemble.runtime.worker_failure_recorded")
        if self.control_store.entryCreationBlocked and self.control_store.blockReasonCodes:
            blockers.extend(self.control_store.blockReasonCodes)
        status = "blocked" if blockers else "ready"
        return {
            "status": status,
            "ready": status == "ready",
            "newEntriesAllowed": status == "ready" and self.control_store.control.newEntriesEnabled,
            "paperOnly": True,
            "liveTradingEnabled": False,
            "blockers": sorted(set(blockers)),
            "reasonCodes": ["voting_ensemble.runtime.supervisor.ready" if status == "ready" else "voting_ensemble.runtime.supervisor.blocked"],
        }

    def _effective_control(self, runtime_summary: dict[str, Any], paper_summary: dict[str, Any]) -> dict[str, Any]:
        control = self.control_store.control
        clock = self.market_clock_provider()
        local_paper_mode = _is_local_paper_mode(self.paper_execution_runtime)
        inventory = self.paper_inventory()
        if local_paper_mode:
            self._refresh_local_market_data_mark(symbol="SPY", feed="iex")
            inventory = self.paper_inventory()
        account = inventory.get("account") if isinstance(inventory, dict) else None
        local_account_loaded = _local_paper_account_loaded(account)
        inventory_healthy = _local_inventory_healthy(inventory)
        persistence_healthy = paper_summary.get("persistenceHealthy") is not False and inventory.get("persistenceHealthy") is not False
        market_data_healthy = bool(self.config.market_data_healthy_default)
        market_data_fresh = _local_market_data_fresh(inventory)
        market_clock_healthy = bool(clock.get("isOpen"))
        kill_switch_off = not control.killSwitchActive
        automatic_execution_enabled = (
            bool(self.config.global_master_paper_enabled)
            and bool(control.requestedPaperTradingEnabled)
            and not bool(control.liveTradingEnabled)
        )
        checks = {
            "globalMasterPaperEnabled": self.config.global_master_paper_enabled,
            "requestedPaperTradingEnabled": control.requestedPaperTradingEnabled,
            "liveTradingDisabled": not control.liveTradingEnabled,
            "localPaperAccountLoaded": local_account_loaded,
            "localPaperAccountConfigured": local_account_loaded,
            "localPaperInventoryIsolated": inventory.get("capitalPartitionId") == VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
            "inventoryHealthy": inventory_healthy,
            "persistenceHealthy": persistence_healthy,
            "marketDataHealthy": market_data_healthy,
            "marketDataFresh": market_data_fresh,
            "marketClockHealthy": market_clock_healthy,
            "killSwitchOff": kill_switch_off,
            "automaticExecutionEnabled": automatic_execution_enabled,
            "backendBrokerClockOpen": market_clock_healthy,
            "workerHealthy": bool(runtime_summary.get("workerAlive")) and bool(paper_summary.get("workerAlive")) and not bool(self.metrics.lastError),
            "inventoryReconciled": inventory_healthy if local_paper_mode else self.metrics.lastReconciliation is not None and persistence_healthy,
            "globalKillSwitchInactive": kill_switch_off,
            "localEntryBlockInactive": not control.localEntryBlockActive,
            "executionPersistenceHealthy": persistence_healthy,
        }
        blocking_keys = [
            "automaticExecutionEnabled",
            "localPaperAccountLoaded",
            "localPaperInventoryIsolated",
            "inventoryHealthy",
            "persistenceHealthy",
            "marketDataHealthy",
            "marketDataFresh",
            "marketClockHealthy",
            "killSwitchOff",
            "workerHealthy",
            "localEntryBlockInactive",
        ]
        if not local_paper_mode:
            blocking_keys.append("inventoryReconciled")
        blockers = [f"voting_ensemble.control.{key}" for key in blocking_keys if not checks.get(key)]
        effective = not blockers
        if not control.requestedPaperTradingEnabled:
            blockers.append("voting_ensemble.control.paper_requested_off")
        blockers.extend(control.localEntryBlockReasonCodes)
        blockers = sorted(set(blockers))
        reason_codes = ["voting_ensemble.control.effective_paper_on"] if effective else ["voting_ensemble.control.effective_paper_off", *blockers]
        return {
            "effectivePaperTradingEnabled": effective,
            "newEntriesEnabled": effective,
            "reasonCodes": reason_codes,
            "checks": checks,
            "brokerClock": clock,
        }

    def _run_reconciliation_once(self) -> None:
        try:
            if _is_local_paper_mode(self.paper_execution_runtime):
                local_validate = getattr(self.paper_execution_runtime, "validate_local_consistency", None)
                if callable(local_validate):
                    self.metrics.lastReconciliation = local_validate(evaluated_at=datetime.now(UTC))
                return
            broker_reconcile = getattr(self.paper_execution_runtime, "reconcile_broker_state", None)
            if callable(broker_reconcile):
                self.metrics.lastReconciliation = broker_reconcile(evaluated_at=datetime.now(UTC))
                return
            gateway = getattr(self.paper_execution_runtime, "paper_gateway", None)
            if gateway is not None:
                self.metrics.lastReconciliation = gateway.recover_from_restart(evaluated_at=datetime.now(UTC))
        except Exception as exc:
            self.record_worker_failure("reconciliation_loop", exc)

    def _default_market_clock(self) -> dict[str, Any]:
        if not self.settings.has_alpaca_credentials:
            if _is_local_paper_mode(self.paper_execution_runtime):
                return {
                    "isOpen": False,
                    "status": "unconfigured",
                    "sourceAuthority": "voting_ensemble.local_market_clock",
                    "reasonCodes": ["voting_ensemble.control.marketClockHealthy"],
                }
            return {"isOpen": False, "status": "unconfigured", "reasonCodes": ["voting_ensemble.control.paper_credentials_missing"]}
        try:
            import httpx

            with httpx.Client(timeout=httpx.Timeout(4.0, connect=2.0), trust_env=False) as client:
                response = client.get(
                    f"{self.settings.alpaca_trading_base_url}/clock",
                    headers={
                        "APCA-API-KEY-ID": self.settings.alpaca_key_id,
                        "APCA-API-SECRET-KEY": self.settings.alpaca_secret_key,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            return {"isOpen": False, "status": "unavailable", "warning": str(exc), "reasonCodes": ["voting_ensemble.control.broker_clock_unavailable"]}
        return {
            "isOpen": bool(payload.get("is_open")),
            "status": "open" if payload.get("is_open") else "closed",
            "timestamp": payload.get("timestamp"),
            "nextOpen": payload.get("next_open"),
            "nextClose": payload.get("next_close"),
            "reasonCodes": ["voting_ensemble.control.broker_clock_reported"],
        }

    def _default_account_snapshot(self) -> dict[str, Any] | None:
        inventory = self.paper_inventory()
        account = inventory.get("account") if isinstance(inventory, dict) else None
        if isinstance(account, dict):
            return {
                **account,
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "capitalPartitionId": VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
                "accountId": str(account.get("accountId") or VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID),
                "sourceAuthority": "voting_ensemble.local_paper_account",
                "paperAccount": True,
                "localPaperAccount": True,
                "externalBrokerAccount": False,
                "liveTradingEnabled": False,
            }
        return None

    def _latest_quote(self, *, symbol: str, feed: str) -> dict[str, Any] | None:
        client = getattr(self.finalized_bar_producer, "market_data_client", None) if self.finalized_bar_producer is not None else None
        reader = getattr(client, "get_latest_quote_sync", None)
        if not callable(reader):
            return None
        return reader(symbol=symbol, feed=feed)

    def _latest_trade(self, *, symbol: str, feed: str) -> dict[str, Any] | None:
        client = getattr(self.finalized_bar_producer, "market_data_client", None) if self.finalized_bar_producer is not None else None
        reader = getattr(client, "get_latest_trade_sync", None)
        if not callable(reader):
            return None
        return reader(symbol=symbol, feed=feed)

    def _refresh_local_market_data_mark(self, *, symbol: str, feed: str) -> dict[str, Any] | None:
        if not _is_local_paper_mode(self.paper_execution_runtime):
            return None
        quote = self._latest_quote(symbol=symbol, feed=feed)
        if not quote:
            return None
        marker = getattr(self.paper_execution_runtime, "mark_to_market_from_payload", None)
        if not callable(marker):
            return None
        try:
            observed_at = datetime.now(UTC)
            for key in ("quoteTimestamp", "marketDataReceiptTimestamp", "lastTradeTimestamp"):
                timestamp = _parse_supervisor_time(quote.get(key))
                if timestamp is not None and timestamp > observed_at:
                    observed_at = timestamp
            return marker({"symbol": symbol, "feed": feed, "nbbo": quote}, observed_at=observed_at)
        except Exception:
            return None


_VOTING_ENSEMBLE_RUNTIME_SUPERVISOR: VotingEnsembleRuntimeSupervisor | None = None


def get_voting_ensemble_runtime_supervisor(
    *,
    settings: Settings | None = None,
    market_data_client: VotingEnsembleMarketDataClient | None = None,
    candle_store: VotingEnsembleCandleStore | None = None,
) -> VotingEnsembleRuntimeSupervisor:
    global _VOTING_ENSEMBLE_RUNTIME_SUPERVISOR
    if _VOTING_ENSEMBLE_RUNTIME_SUPERVISOR is None:
        _VOTING_ENSEMBLE_RUNTIME_SUPERVISOR = VotingEnsembleRuntimeSupervisor(
            settings=settings,
            market_data_client=market_data_client,
            candle_store=candle_store,
        )
    return _VOTING_ENSEMBLE_RUNTIME_SUPERVISOR


def default_control_store_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "algorithms" / "voting_ensemble" / "runtime" / "control.json"


def _effective_blockers_from_reason_codes(reason_codes: list[str]) -> list[str]:
    return [
        code
        for code in reason_codes
        if code not in {"voting_ensemble.control.effective_paper_on", "voting_ensemble.control.effective_paper_off"}
    ]


def _paper_inventory_snapshot(paper_execution_runtime: Any) -> dict[str, Any]:
    try:
        snapshot = paper_execution_runtime.inventory_snapshot()
    except Exception as exc:
        return {
            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "executionMode": "LOCAL_PAPER",
            "sourceAuthority": "voting_ensemble.local_paper_account.unavailable",
            "orders": [],
            "fills": [],
            "positions": [],
            "localOrders": [],
            "localFills": [],
            "localPositions": [],
            "outbox": [],
            "reconciliationBlocks": [
                {
                    "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                    "reconciliationStatus": "RECONCILIATION_REQUIRED",
                    "error": str(exc),
                    "reasonCodes": ["voting_ensemble.runtime.status.inventory_snapshot_unavailable"],
                    "createdAt": _now(),
                }
            ],
            "reasonCodes": ["voting_ensemble.runtime.status.inventory_snapshot_unavailable"],
        }
    return snapshot if isinstance(snapshot, dict) else {}


def _local_paper_observability(
    *,
    inventory: dict[str, Any],
    checks: dict[str, Any],
    automatic_trading_ready: bool,
) -> dict[str, Any]:
    account = inventory.get("localPaperAccount") or inventory.get("account") or {}
    account_payload = dict(account) if isinstance(account, dict) else {}
    local_positions = list(inventory.get("localPositions") or inventory.get("positions") or [])
    local_orders = list(inventory.get("localOrders") or inventory.get("orders") or [])
    open_orders = list(inventory.get("openOrders") or _open_order_records(inventory))
    recent_fills = list(inventory.get("recentFills") or inventory.get("fills") or [])[:25]
    closed_trades = list(inventory.get("closedTrades") or [])
    source_authority = str(account_payload.get("sourceAuthority") or inventory.get("sourceAuthority") or "voting_ensemble_local_paper_account")
    return {
        "executionMode": str(inventory.get("executionMode") or account_payload.get("executionMode") or "LOCAL_PAPER"),
        "sourceAuthority": source_authority,
        "localPaperAccount": account_payload,
        "initialCash": _number(account_payload.get("initialCash")),
        "cash": _number(account_payload.get("cash")),
        "equity": _number(account_payload.get("equity")),
        "buyingPower": _number(account_payload.get("buyingPower")),
        "realizedPnl": _signed_number(account_payload.get("realizedPnl")),
        "realizedPnlToday": _signed_number(account_payload.get("realizedPnlToday")),
        "unrealizedPnl": _signed_number(account_payload.get("unrealizedPnl")),
        "dailyNetPnl": _signed_number(account_payload.get("dailyNetPnl")),
        "positions": local_positions,
        "localPositions": local_positions,
        "openOrders": open_orders,
        "localOrders": local_orders,
        "recentFills": recent_fills,
        "closedTrades": closed_trades,
        "grossExposure": _number(account_payload.get("grossExposure")),
        "netExposure": _signed_number(account_payload.get("netExposure")),
        "openRisk": _number(_first_present(account_payload, "totalOpenRiskDollars", "openRisk")),
        "drawdown": _number(_first_present(account_payload, "drawdownDollars", "drawdown")),
        "drawdownPercent": _number(_first_present(account_payload, "drawdownPercent", "drawdownFromIntradayHighPercent")),
        "inventoryHealthy": bool(checks.get("inventoryHealthy")),
        "persistenceHealthy": bool(checks.get("persistenceHealthy")),
        "automaticTradingReady": bool(automatic_trading_ready),
    }


def _first_present(payload: dict[str, Any], *keys: str, default: Any = 0.0) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None and value != "":
            return value
    return default


def _number(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 else 0.0


def _signed_number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _runtime_jobs(runtime: Any) -> list[dict[str, Any]]:
    status_store = getattr(runtime, "status_store", None)
    list_jobs = getattr(status_store, "list_jobs", None)
    if not callable(list_jobs):
        return []
    try:
        return [dict(job) for job in list_jobs() if isinstance(job, dict)]
    except Exception:
        return []


def _latest_record(records: Any, *time_fields: str) -> dict[str, Any] | None:
    candidates = [dict(record) for record in records if isinstance(record, dict)]
    if not candidates:
        return None
    return max(candidates, key=lambda record: _record_sort_key(record, time_fields))


def _record_sort_key(record: dict[str, Any], time_fields: tuple[str, ...]) -> tuple[datetime, str]:
    for field_name in time_fields:
        parsed = _parse_status_time(record.get(field_name))
        if parsed is not None:
            return parsed, str(record.get("id") or record.get("jobId") or record.get("orderIntentId") or record.get("clientOrderId") or "")
    nested_timestamps = record.get("timestamps")
    if isinstance(nested_timestamps, dict):
        for field_name in time_fields:
            parsed = _parse_status_time(nested_timestamps.get(field_name))
            if parsed is not None:
                return parsed, str(record.get("id") or record.get("jobId") or record.get("orderIntentId") or record.get("clientOrderId") or "")
    return datetime.min.replace(tzinfo=UTC), str(record.get("id") or record.get("jobId") or record.get("orderIntentId") or record.get("clientOrderId") or "")


def _parse_status_time(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _last_decision_from_evaluation(last_evaluation: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(last_evaluation, dict):
        return None
    result = last_evaluation.get("result")
    if not isinstance(result, dict):
        return None
    decision = result.get("decision")
    if isinstance(decision, dict):
        return dict(decision)
    if any(key in result for key in ("signal", "side", "quantity", "decisionId", "orderPlan")):
        return {
            key: result[key]
            for key in ("decisionId", "signal", "side", "quantity", "confidence", "orderPlan", "settingsHash", "reasonCodes")
            if key in result
        }
    return None


def _active_entry_blocks(
    readiness: dict[str, Any],
    effective: dict[str, Any],
    inventory: dict[str, Any],
    paper_summary: dict[str, Any],
) -> list[str]:
    blocks: list[str] = []
    blocks.extend(str(code) for code in readiness.get("blockers") or [])
    blocks.extend(_effective_blockers_from_reason_codes([str(code) for code in effective.get("reasonCodes") or []]))
    for block in inventory.get("reconciliationBlocks") or []:
        if isinstance(block, dict):
            blocks.extend(str(code) for code in block.get("reasonCodes") or ["voting_ensemble.paper_execution.reconciliation_required"])
    if paper_summary.get("persistenceHealthy") is False:
        blocks.append("voting_ensemble.paper_execution.persistence_failure_blocks_new_entries")
    for warning in paper_summary.get("highSeverityRuntimeWarnings") or []:
        if isinstance(warning, dict):
            blocks.extend(str(code) for code in warning.get("reasonCodes") or [])
            if warning.get("code"):
                blocks.append(str(warning["code"]))
    return sorted(set(code for code in blocks if code))


def _open_order_records(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    execution_mode = str(inventory.get("executionMode") or "LOCAL_PAPER").upper()
    if execution_mode == "LOCAL_PAPER":
        records = inventory.get("localOrders") or inventory.get("orders") or []
    else:
        records = [*(inventory.get("brokerOrders") or []), *(inventory.get("orders") or [])]
    return [dict(record) for record in records if isinstance(record, dict) and _order_is_open(record)]


def _order_is_open(record: dict[str, Any]) -> bool:
    status = str(
        record.get("status")
        or record.get("entryOrderStatus")
        or record.get("brokerStatus")
        or record.get("state")
        or ""
    ).upper()
    if not status:
        return True
    return status in {
        "PENDING",
        "CLAIMED",
        "SUBMITTING",
        "SUBMITTED",
        "ACCEPTED",
        "NEW",
        "OPEN",
        "PARTIALLY_FILLED",
        "PENDING_NEW",
        "PENDING_REPLACE",
        "PENDING_CANCEL",
        "HELD",
    }


def _open_position_records(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    records = inventory.get("positions") or []
    open_positions: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        quantity = _position_quantity(record)
        if quantity is None or quantity != 0:
            open_positions.append(dict(record))
    return open_positions


def _position_quantity(record: dict[str, Any]) -> float | None:
    for key in ("quantity", "qty", "netQuantity", "netQty", "shares", "positionQuantity"):
        if key not in record:
            continue
        try:
            return float(record[key])
        except (TypeError, ValueError):
            return None
    return None


def _settings_hash(
    *,
    last_evaluation: dict[str, Any] | None,
    last_decision: dict[str, Any] | None,
    last_execution_intent: dict[str, Any] | None,
    last_broker_order: dict[str, Any] | None,
) -> str | None:
    for record in (last_execution_intent, last_decision, last_evaluation, last_broker_order):
        if not isinstance(record, dict):
            continue
        for key in ("settingsHash", "settings_hash", "approvedDecisionSettingsHash", "configurationHash"):
            value = record.get(key)
            if value:
                return str(value)
        command = record.get("command")
        if isinstance(command, dict):
            value = command.get("settingsHash")
            if value:
                return str(value)
        order_plan = record.get("orderPlan")
        if isinstance(order_plan, dict):
            value = order_plan.get("configurationHash")
            if value:
                return str(value)
    return None


def _is_local_paper_mode(paper_execution_runtime: Any) -> bool:
    return str(getattr(paper_execution_runtime, "execution_mode", "LOCAL_PAPER") or "LOCAL_PAPER").upper() == "LOCAL_PAPER"


def _local_paper_account_loaded(account: Any) -> bool:
    if not isinstance(account, dict):
        return False
    return (
        account.get("algorithmId", account.get("algorithm_id")) == VOTING_ENSEMBLE_ALGORITHM_ID
        and account.get("capitalPartitionId") == VOTING_ENSEMBLE_CAPITAL_PARTITION_ID
        and account.get("accountId") == VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID
        and str(account.get("executionMode") or "LOCAL_PAPER").upper() == "LOCAL_PAPER"
    )


def _parse_supervisor_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _local_inventory_healthy(inventory: dict[str, Any]) -> bool:
    if not isinstance(inventory, dict):
        return False
    if inventory.get("capitalPartitionId") != VOTING_ENSEMBLE_CAPITAL_PARTITION_ID:
        return False
    if inventory.get("persistenceHealthy") is False:
        return False
    if inventory.get("reconciliationBlocks"):
        return False
    recovery = inventory.get("localRecovery")
    if isinstance(recovery, dict):
        status = str(recovery.get("status") or recovery.get("recoveryStatus") or "").upper()
        if status in {"FAILED", "RECOVERY_FAILED", "CORRUPTED", "CORRUPT", "BLOCKED"}:
            return False
    consistency = inventory.get("localConsistency")
    if isinstance(consistency, dict):
        status = str(consistency.get("status") or consistency.get("consistencyStatus") or "").upper()
        if status in {"FAILED", "LOCAL_CONSISTENCY_REQUIRED", "CORRUPTED", "CORRUPT", "BLOCKED"}:
            return False
    return True


def _local_market_data_fresh(inventory: dict[str, Any]) -> bool:
    statuses = inventory.get("marketData") if isinstance(inventory, dict) else None
    if not statuses:
        return True
    for status in statuses:
        if not isinstance(status, dict):
            continue
        if status.get("marketDataFresh") is False or status.get("fresh") is False:
            return False
    return True


def _paper_ready_blocking_reason_codes(
    *,
    local_paper_mode: bool,
    supervisor_running: bool,
    finalized_bar_producer_configured: bool,
    finalized_bar_event_consumer_healthy: bool,
    evaluation_worker_healthy: bool,
    execution_worker_healthy: bool,
    reconciliation_healthy: bool,
    local_paper_account_loaded: bool,
    local_paper_account_verified: bool,
    inventory_healthy: bool,
    persistence_healthy: bool,
    market_data_healthy: bool,
    market_data_fresh: bool,
    market_clock_healthy: bool,
    automatic_execution_enabled: bool,
    kill_switch_off: bool,
    external_broker_client_configured: bool,
    durable_execution_state_active: bool,
    new_entries_allowed: bool,
    active_entry_blocks: list[str],
) -> list[str]:
    blockers: list[str] = []
    if not supervisor_running:
        blockers.append("voting_ensemble.paper_ready.runtime_supervisor_not_running")
    if not finalized_bar_producer_configured:
        blockers.append("voting_ensemble.paper_ready.backend_finalized_bar_producer_not_configured")
    if not finalized_bar_event_consumer_healthy:
        blockers.append("voting_ensemble.paper_ready.finalized_bar_event_consumer_not_healthy")
    if not evaluation_worker_healthy:
        blockers.append("voting_ensemble.paper_ready.evaluation_worker_not_healthy")
    if not execution_worker_healthy:
        blockers.append("voting_ensemble.paper_ready.execution_worker_not_healthy")
    if local_paper_mode:
        if not local_paper_account_loaded:
            blockers.append("voting_ensemble.paper_ready.local_paper_account_not_loaded")
        if not local_paper_account_verified:
            blockers.append("voting_ensemble.paper_ready.local_paper_account_not_verified")
        if not inventory_healthy:
            blockers.append("voting_ensemble.paper_ready.inventory_not_healthy")
        if not market_data_healthy:
            blockers.append("voting_ensemble.paper_ready.market_data_not_healthy")
        if not market_data_fresh:
            blockers.append("voting_ensemble.paper_ready.market_data_not_fresh")
        if not market_clock_healthy:
            blockers.append("voting_ensemble.paper_ready.market_clock_not_healthy")
        if not kill_switch_off:
            blockers.append("voting_ensemble.paper_ready.kill_switch_active")
        if not automatic_execution_enabled:
            blockers.append("voting_ensemble.paper_ready.automatic_execution_not_enabled")
    else:
        if not reconciliation_healthy:
            blockers.append("voting_ensemble.paper_ready.reconciliation_not_healthy")
        if not external_broker_client_configured:
            blockers.append("voting_ensemble.paper_ready.alpaca_paper_client_not_configured")
        if not durable_execution_state_active:
            blockers.append("voting_ensemble.paper_ready.execution_state_not_durable")
    if not persistence_healthy:
        blockers.append("voting_ensemble.paper_ready.persistence_unhealthy")
    if not new_entries_allowed:
        blockers.append("voting_ensemble.paper_ready.new_entries_not_allowed")
    if active_entry_blocks:
        blockers.extend(active_entry_blocks)
    return sorted(set(blockers))


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
