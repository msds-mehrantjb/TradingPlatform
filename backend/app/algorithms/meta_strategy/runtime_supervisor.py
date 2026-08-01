"""Meta-Strategy background runtime supervisor."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from backend.app.algorithms.meta_strategy.alpaca_paper_broker import MetaStrategyAlpacaPaperBroker
from backend.app.algorithms.meta_strategy.finalized_candle_producer import (
    MetaStrategyFinalizedCandleProducer,
    MetaStrategyFinalizedCandleProducerConfig,
    MetaStrategyMarketDataClient,
)
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.jobs import META_STRATEGY_JOB_QUEUES, MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.runtime import (
    MetaStrategyRuntimeDependencies,
    MetaStrategyRuntimeMode,
    MetaStrategyRuntimeStartupError,
    MetaStrategyRuntimeStartupReport,
    reconstruct_meta_strategy_runtime_state,
    validate_meta_strategy_runtime_startup,
)
from backend.app.algorithms.meta_strategy.state_provider import MetaStrategyCandleStoreStateProvider
from backend.app.algorithms.meta_strategy.worker_main import build_meta_strategy_worker
from backend.app.execution import PaperOrderGateway


META_STRATEGY_RUNTIME_SUPERVISOR_VERSION = "meta_strategy_runtime_supervisor_v1"
MARKET_TIME_QUEUES = (
    "finalized_candle_producer",
    "finalised_bar_decisions",
    "order_submission",
    "order_reconciliation",
    "stale_order_handling",
    "inventory_reconciliation",
    "position_management",
)


@dataclass(frozen=True)
class MetaStrategyRuntimeSupervisorConfig:
    enabled: bool = False
    mode: MetaStrategyRuntimeMode = MetaStrategyRuntimeMode.SHADOW
    database_url: str | None = None
    worker_poll_seconds: float = 1.0
    reconciliation_poll_seconds: float = 15.0
    stale_order_poll_seconds: float = 30.0
    inventory_poll_seconds: float = 60.0
    position_poll_seconds: float = 15.0
    heartbeat_interval_seconds: float = 5.0
    maintenance_interval_seconds: float = 15.0
    candle_poll_seconds: float = 5.0
    worker_lease_seconds: int = 60
    max_queue_lag_seconds: int = 75
    max_dead_letter_count: int = 0
    symbols: tuple[str, ...] = ("SPY",)
    market_data_feed: str = "iex"


@dataclass
class MetaStrategyRuntimeSupervisorMetrics:
    supervisor_started: bool = False
    startup_attempted: bool = False
    startup_failed: bool = False
    paper_orders_blocked: bool = True
    unavailable_reason_codes: tuple[str, ...] = ("meta_strategy.runtime.disabled",)
    worker_status: dict[str, str] = field(default_factory=lambda: {queue: "stopped" for queue in MARKET_TIME_QUEUES})
    worker_iterations: dict[str, int] = field(default_factory=dict)
    worker_failures: dict[str, int] = field(default_factory=dict)
    scheduled_jobs: dict[str, str] = field(default_factory=dict)
    last_worker_result: dict[str, Any] = field(default_factory=dict)
    queue_lag_seconds: dict[str, int] = field(default_factory=dict)
    dead_letter_count: int = 0
    lease_recovery_count: int = 0
    last_reconstruction: dict[str, Any] | None = None
    last_health_check_at: str | None = None
    last_error: str | None = None


class MetaStrategyRuntimeSnapshotSource:
    def __init__(self, loader: Callable[[], Mapping[str, Any]]) -> None:
        self._loader = loader

    def load_snapshot(self) -> Mapping[str, Any]:
        return self._loader()


class MetaStrategyRuntimeSupervisor:
    def __init__(
        self,
        *,
        config: MetaStrategyRuntimeSupervisorConfig | None = None,
        settings: Any | None = None,
        dependencies: MetaStrategyRuntimeDependencies | None = None,
        paper_gateway: PaperOrderGateway | None = None,
        global_risk_source: Any | None = None,
        market_data_client: MetaStrategyMarketDataClient | None = None,
        candle_store: Any | None = None,
    ) -> None:
        self.settings = settings
        self.config = config or _config_from_settings(self.settings)
        self.dependencies = dependencies
        self.paper_gateway = paper_gateway
        self.global_risk_source = global_risk_source
        self.market_data_client = market_data_client
        self.candle_store = candle_store
        self.candle_producer: MetaStrategyFinalizedCandleProducer | None = None
        self.stop_event = asyncio.Event()
        self.metrics = MetaStrategyRuntimeSupervisorMetrics()
        self.startup_report: MetaStrategyRuntimeStartupReport | None = None
        self._tasks: list[asyncio.Task] = []
        self._workers: dict[str, Any] = {}
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self.metrics.startup_attempted = True
        if not self.config.enabled:
            self.mark_disabled()
            return
        self.stop_event.clear()
        try:
            self._construct_dependencies()
            self.startup_report = validate_meta_strategy_runtime_startup(self.dependencies)
            self._construct_candle_producer()
            self._validate_broker_reachable()
            reconstruction = reconstruct_meta_strategy_runtime_state(self.dependencies)
            self.metrics.last_reconstruction = reconstruction
            if reconstruction.get("status") != "OK":
                raise MetaStrategyRuntimeStartupError(tuple(reconstruction.get("reasonCodes") or ("meta_strategy.runtime.restart_reconstruction_failed",)))
            self._construct_workers()
        except Exception as exc:
            self.mark_startup_failed(exc)
            return
        self._started = True
        self.metrics.supervisor_started = True
        self.metrics.startup_failed = False
        self.metrics.paper_orders_blocked = self.config.mode != MetaStrategyRuntimeMode.PAPER
        self.metrics.unavailable_reason_codes = (
            ("meta_strategy.runtime.shadow_mode_paper_orders_blocked",)
            if self.metrics.paper_orders_blocked
            else ("meta_strategy.runtime.ready",)
        )
        self._start_loop("finalised_bar_decisions", self._poll_worker_loop("finalised_bar_decisions", self.config.worker_poll_seconds))
        self._start_loop("finalized_candle_producer", self._candle_producer_loop())
        self._start_loop("order_submission", self._poll_worker_loop("order_submission", self.config.worker_poll_seconds))
        self._start_loop("order_reconciliation", self._poll_worker_loop("order_reconciliation", self.config.reconciliation_poll_seconds))
        self._start_loop("stale_order_handling", self._poll_worker_loop("stale_order_handling", self.config.stale_order_poll_seconds))
        self._start_loop("inventory_reconciliation", self._scheduled_job_loop("inventory_reconciliation", "inventory_reconciliation", self.config.inventory_poll_seconds))
        self._start_loop("position_management", self._scheduled_job_loop("position_management", "position_management", self.config.position_poll_seconds))
        self._start_loop("maintenance", self._maintenance_loop())

    async def shutdown(self) -> None:
        if not self._started and not self._tasks:
            return
        self.stop_event.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for worker in self._workers.values():
            gateway = getattr(worker, "paper_gateway", None)
            broker = getattr(gateway, "broker", None)
            close = getattr(broker, "close", None)
            if callable(close):
                close()
        self._workers.clear()
        for queue_name in MARKET_TIME_QUEUES:
            self.metrics.worker_status[queue_name] = "stopped"
        self.metrics.supervisor_started = False
        self._started = False

    def mark_disabled(self) -> None:
        self.metrics.supervisor_started = False
        self.metrics.startup_failed = False
        self.metrics.paper_orders_blocked = True
        self.metrics.unavailable_reason_codes = ("meta_strategy.runtime.disabled",)
        for queue_name in MARKET_TIME_QUEUES:
            self.metrics.worker_status[queue_name] = "disabled"

    def mark_startup_failed(self, exc: Exception) -> None:
        reason_codes = tuple(getattr(exc, "reason_codes", None) or (str(exc) or "meta_strategy.runtime.startup_failed",))
        self.metrics.supervisor_started = False
        self.metrics.startup_failed = True
        self.metrics.paper_orders_blocked = True
        self.metrics.unavailable_reason_codes = reason_codes
        self.metrics.last_error = str(exc)
        for queue_name in MARKET_TIME_QUEUES:
            self.metrics.worker_status[queue_name] = "unavailable"

    def readiness_status(self) -> dict[str, Any]:
        queue_status = None
        if self.dependencies is not None and self.dependencies.job_repository is not None:
            queue_status = self.dependencies.job_repository.queue_status()
            self._observe_queue_status(queue_status)
        enabled = self.config.enabled
        market_workers_healthy = all(
            self.metrics.worker_status.get(queue_name) == "healthy"
            for queue_name in MARKET_TIME_QUEUES
        )
        lag_clear = all(lag <= self.config.max_queue_lag_seconds for lag in self.metrics.queue_lag_seconds.values())
        dead_letters_clear = self.metrics.dead_letter_count <= self.config.max_dead_letter_count
        ready = bool(
            enabled
            and self.config.mode == MetaStrategyRuntimeMode.PAPER
            and self.metrics.supervisor_started
            and not self.metrics.startup_failed
            and not self.metrics.paper_orders_blocked
            and market_workers_healthy
            and lag_clear
            and dead_letters_clear
        )
        status = "disabled" if not enabled else "ready" if ready else "unavailable" if self.metrics.startup_failed else "shadow" if self.metrics.supervisor_started else "stopped"
        return {
            "algorithmId": ALGORITHM_ID,
            "supervisorVersion": META_STRATEGY_RUNTIME_SUPERVISOR_VERSION,
            "enabled": enabled,
            "mode": self.config.mode.value,
            "ready": ready,
            "status": status,
            "paperOrdersBlocked": bool(self.metrics.paper_orders_blocked or not ready),
            "reasonCodes": tuple(self.metrics.unavailable_reason_codes),
            "marketWorkersHealthy": market_workers_healthy,
            "paperReadinessPrerequisites": {
                "paperMode": self.config.mode == MetaStrategyRuntimeMode.PAPER,
                "marketWorkersHealthy": market_workers_healthy,
                "queueLagClear": lag_clear,
                "deadLettersClear": dead_letters_clear,
            },
            "startupReport": self.startup_report.to_dict() if self.startup_report else None,
            "restartState": self.metrics.last_reconstruction,
            "workers": dict(self.metrics.worker_status),
            "queueLagSeconds": dict(self.metrics.queue_lag_seconds),
            "deadLetterCount": self.metrics.dead_letter_count,
            "leaseRecoveryCount": self.metrics.lease_recovery_count,
            "scheduledJobs": dict(self.metrics.scheduled_jobs),
            "lastWorkerResult": dict(self.metrics.last_worker_result),
            "lastHealthCheckAt": self.metrics.last_health_check_at,
            "queues": queue_status["queues"] if queue_status else {},
        }

    def _construct_dependencies(self) -> None:
        if self.dependencies is not None:
            return
        database_url = self.config.database_url or getattr(self.settings, "database_url", None)
        if not database_url:
            raise MetaStrategyRuntimeStartupError(("meta_strategy.runtime.durable_database_required",))
        job_repository = MetaStrategyJobRepository(database_url)
        inventory_repository = MetaStrategySqliteRepository(database_url)
        broker = MetaStrategyAlpacaPaperBroker(self.settings)
        if self.global_risk_source is None:
            raise MetaStrategyRuntimeStartupError(("meta_strategy.runtime.global_risk_source_required",))
        self.paper_gateway = PaperOrderGateway(broker, job_repository.gateway_store())
        self.dependencies = MetaStrategyRuntimeDependencies(
            mode=self.config.mode,
            persistence_adapter=None,
            broker_adapter=broker,
            inventory_repository=inventory_repository,
            job_repository=job_repository,
            settings_store=None,
            account_data_source=MetaStrategyRuntimeSnapshotSource(lambda: {"paperBrokerConfigured": broker.configured}),
            global_risk_source=MetaStrategyRuntimeSnapshotSource(lambda: _load_global_risk_snapshot(self.global_risk_source)),
            operational_health_source=MetaStrategyRuntimeSnapshotSource(lambda: {"clockUtc": datetime.now(UTC).isoformat(), "status": "OK"}),
        )
        from backend.app.algorithms.meta_strategy.repository import MetaStrategyRepositoryPersistenceAdapter
        from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore
        from pathlib import Path

        self.dependencies.persistence_adapter = MetaStrategyRepositoryPersistenceAdapter(inventory_repository)
        self.dependencies.settings_store = MetaStrategySettingsStore(Path("./data/meta_strategy_settings.db"))
        self.dependencies.settings_store.get_active_settings()

    def _construct_candle_producer(self) -> None:
        assert self.dependencies is not None
        if self.market_data_client is None:
            raise RuntimeError("meta_strategy.runtime.market_data_client_required")
        if self.candle_store is None:
            raise RuntimeError("meta_strategy.runtime.candle_store_required")
        self.candle_producer = MetaStrategyFinalizedCandleProducer(
            market_data_client=self.market_data_client,
            candle_store=self.candle_store,
            job_repository=_require(self.dependencies.job_repository, "meta_strategy.runtime.job_repository_required"),
            settings_store=_require(self.dependencies.settings_store, "meta_strategy.runtime.settings_store_required"),
            config=MetaStrategyFinalizedCandleProducerConfig(
                symbols=tuple(symbol.upper() for symbol in self.config.symbols),
                feed=self.config.market_data_feed,
                mode=self.config.mode.value,
            ),
        )

    def _construct_workers(self) -> None:
        assert self.dependencies is not None
        repository = _require(self.dependencies.job_repository, "meta_strategy.runtime.job_repository_required")
        inventory = _require(self.dependencies.inventory_repository, "meta_strategy.runtime.inventory_repository_required")
        gateway = _require(self.paper_gateway, "meta_strategy.runtime.paper_gateway_required")
        risk = _require(self.global_risk_source, "meta_strategy.runtime.global_risk_source_required")
        state_provider = MetaStrategyCandleStoreStateProvider(
            inventory_repository=inventory,
            job_repository=repository,
            quote_source=self.market_data_client,
            global_risk_source=self.global_risk_source,
        )
        common = {"repository": repository, "inventory_repository": inventory, "paper_gateway": gateway, "global_risk_source": risk}
        self._workers = {
            "finalised_bar_decisions": build_meta_strategy_worker(
                repository=repository,
                queue_name="finalised_bar_decisions",
                worker_id="meta_strategy.supervisor.finalised_bar_decisions",
                state_provider=state_provider,
            ),
            "order_submission": build_meta_strategy_worker(
                queue_name="order_submission",
                worker_id="meta_strategy.supervisor.order_submission",
                **common,
            ),
            "order_reconciliation": build_meta_strategy_worker(
                repository=repository,
                queue_name="order_reconciliation",
                worker_id="meta_strategy.supervisor.order_reconciliation",
                inventory_repository=inventory,
                paper_gateway=gateway,
            ),
            "stale_order_handling": build_meta_strategy_worker(
                repository=repository,
                queue_name="stale_order_handling",
                worker_id="meta_strategy.supervisor.stale_order_handling",
                inventory_repository=inventory,
                paper_gateway=gateway,
            ),
            "inventory_reconciliation": build_meta_strategy_worker(
                repository=repository,
                queue_name="inventory_reconciliation",
                worker_id="meta_strategy.supervisor.inventory_reconciliation",
                inventory_repository=inventory,
            ),
            "position_management": build_meta_strategy_worker(
                repository=repository,
                queue_name="position_management",
                worker_id="meta_strategy.supervisor.position_management",
                inventory_repository=inventory,
            ),
        }

    def _validate_broker_reachable(self) -> None:
        if self.config.mode not in {MetaStrategyRuntimeMode.PAPER, MetaStrategyRuntimeMode.SHADOW}:
            return
        broker = getattr(self.paper_gateway, "broker", None) if self.paper_gateway is not None else getattr(self.dependencies, "broker_adapter", None)
        if broker is None or not callable(getattr(broker, "verify_paper_account", None)) or broker.verify_paper_account() is not True:
            raise MetaStrategyRuntimeStartupError(("meta_strategy.runtime.paper_broker_unavailable",))

    def _start_loop(self, worker_id: str, coro) -> None:
        self._tasks.append(asyncio.create_task(coro, name=f"meta_strategy.runtime.{worker_id}"))

    async def _poll_worker_loop(self, queue_name: str, interval_seconds: float) -> None:
        worker = self._workers[queue_name]
        while not self.stop_event.is_set():
            await self._run_worker_once(queue_name, worker)
            await self._sleep(interval_seconds)

    async def _scheduled_job_loop(self, queue_name: str, job_type: str, interval_seconds: float) -> None:
        worker = self._workers[queue_name]
        while not self.stop_event.is_set():
            now = datetime.now(UTC)
            self._schedule_job(job_type, now=now)
            await self._run_worker_once(queue_name, worker, now=now)
            await self._sleep(interval_seconds)

    async def _maintenance_loop(self) -> None:
        while not self.stop_event.is_set():
            self._monitor_queues()
            await self._sleep(self.config.maintenance_interval_seconds)

    async def _candle_producer_loop(self) -> None:
        while not self.stop_event.is_set():
            current = datetime.now(UTC)
            repository = _require(self.dependencies.job_repository if self.dependencies else None, "meta_strategy.runtime.job_repository_required")
            try:
                repository.record_worker_heartbeat(
                    worker_id="meta_strategy.supervisor.finalized_candle_producer",
                    queue_name="finalised_bar_decisions",
                    now=current,
                )
                producer = _require(self.candle_producer, "meta_strategy.runtime.candle_producer_required")
                result = await producer.poll_once(now=current)
                self.metrics.worker_status["finalized_candle_producer"] = "healthy"
                self.metrics.worker_iterations["finalized_candle_producer"] = self.metrics.worker_iterations.get("finalized_candle_producer", 0) + 1
                self.metrics.last_worker_result["finalized_candle_producer"] = _plain(result)
            except Exception as exc:
                self.metrics.worker_status["finalized_candle_producer"] = "failed"
                self.metrics.worker_failures["finalized_candle_producer"] = self.metrics.worker_failures.get("finalized_candle_producer", 0) + 1
                self.metrics.last_error = str(exc)
                self.metrics.unavailable_reason_codes = ("meta_strategy.runtime.candle_producer_failed",)
            await self._sleep(self.config.candle_poll_seconds)

    async def _run_worker_once(self, queue_name: str, worker: Any, *, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        repository = _require(self.dependencies.job_repository if self.dependencies else None, "meta_strategy.runtime.job_repository_required")
        try:
            repository.record_worker_heartbeat(worker_id=getattr(worker, "worker_id", f"meta_strategy.supervisor.{queue_name}"), queue_name=queue_name, now=current)
            result = worker.run_once(now=current)
            self.metrics.worker_status[queue_name] = "healthy"
            self.metrics.worker_iterations[queue_name] = self.metrics.worker_iterations.get(queue_name, 0) + 1
            if result is not None:
                self.metrics.last_worker_result[queue_name] = _plain(result)
        except Exception as exc:
            self.metrics.worker_status[queue_name] = "failed"
            self.metrics.worker_failures[queue_name] = self.metrics.worker_failures.get(queue_name, 0) + 1
            self.metrics.last_error = str(exc)
            if queue_name in {"order_submission", "order_reconciliation", "stale_order_handling"}:
                self.metrics.paper_orders_blocked = True
                self.metrics.unavailable_reason_codes = (f"meta_strategy.runtime.worker_failed.{queue_name}",)

    def _schedule_job(self, job_type: str, *, now: datetime) -> None:
        repository = _require(self.dependencies.job_repository if self.dependencies else None, "meta_strategy.runtime.job_repository_required")
        bucket = now.strftime("%Y%m%dT%H%M")
        job = repository.enqueue_job(
            job_type=job_type,
            idempotency_key=f"meta_strategy.runtime.{job_type}.{bucket}",
            payload={"scheduledBy": "meta_strategy_runtime_supervisor", "scheduledAt": now.isoformat()},
            max_attempts=2,
            now=now,
        )
        self.metrics.scheduled_jobs[job_type] = job.job_id

    def _monitor_queues(self) -> None:
        if self.dependencies is None or self.dependencies.job_repository is None:
            return
        status = self.dependencies.job_repository.queue_status()
        self._observe_queue_status(status)
        metrics = self.dependencies.job_repository.operational_metrics()
        self.metrics.dead_letter_count = int(metrics.get("jobDeadLetterCount") or 0)
        self.metrics.lease_recovery_count = int(metrics.get("leaseRecoveryCount") or 0)
        self.metrics.last_health_check_at = str(metrics.get("asOf") or datetime.now(UTC).isoformat())
        excessive_lag = any(lag > self.config.max_queue_lag_seconds for lag in self.metrics.queue_lag_seconds.values())
        dead_letters = self.metrics.dead_letter_count > self.config.max_dead_letter_count
        if excessive_lag or dead_letters:
            self.metrics.paper_orders_blocked = True
            reasons: list[str] = []
            if excessive_lag:
                reasons.append("meta_strategy.runtime.queue_lag_exceeded")
            if dead_letters:
                reasons.append("meta_strategy.runtime.dead_letters_present")
            self.metrics.unavailable_reason_codes = tuple(reasons)

    def _observe_queue_status(self, status: Mapping[str, Any]) -> None:
        queues = status.get("queues") if isinstance(status, Mapping) else {}
        if isinstance(queues, Mapping):
            self.metrics.queue_lag_seconds = {str(queue): int(data.get("lagSeconds") or 0) for queue, data in queues.items() if isinstance(data, Mapping)}

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=max(0.05, float(seconds)))
        except TimeoutError:
            return


def _config_from_settings(settings: Any | None) -> MetaStrategyRuntimeSupervisorConfig:
    flags = getattr(getattr(settings, "application_config", None), "featureFlags", None)
    mode = str(getattr(flags, "metaStrategyRuntimeMode", os.getenv("META_STRATEGY_RUNTIME_MODE", "shadow"))).upper()
    return MetaStrategyRuntimeSupervisorConfig(
        enabled=bool(getattr(flags, "metaStrategyRuntimeEnabled", os.getenv("META_STRATEGY_RUNTIME_ENABLED", "").lower() in {"1", "true", "yes", "on"})),
        mode=MetaStrategyRuntimeMode.PAPER if mode == "PAPER" else MetaStrategyRuntimeMode.SHADOW,
        database_url=str(getattr(settings, "database_url", os.getenv("DATABASE_URL", "sqlite:///./data/trading.db"))),
    )


def _require(value: Any | None, reason_code: str) -> Any:
    if value is None:
        raise RuntimeError(reason_code)
    return value


def _load_global_risk_snapshot(source: Any | None) -> Mapping[str, Any]:
    if source is None:
        raise RuntimeError("meta_strategy.runtime.global_risk_source_required")
    if hasattr(source, "load_snapshot"):
        return source.load_snapshot()
    if hasattr(source, "read_global_risk_snapshot"):
        return source.read_global_risk_snapshot(at=datetime.now(UTC), capital_partition_id="meta_strategy.paper.default")
    if hasattr(source, "approve_order"):
        return {"source": type(source).__name__, "approveOrderAvailable": True}
    raise RuntimeError("meta_strategy.runtime.global_risk_source_required")


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


_META_STRATEGY_RUNTIME_SUPERVISOR: MetaStrategyRuntimeSupervisor | None = None


def get_meta_strategy_runtime_supervisor(
    *,
    settings: Any | None = None,
    market_data_client: MetaStrategyMarketDataClient | None = None,
    candle_store: Any | None = None,
) -> MetaStrategyRuntimeSupervisor:
    global _META_STRATEGY_RUNTIME_SUPERVISOR
    if _META_STRATEGY_RUNTIME_SUPERVISOR is None:
        _META_STRATEGY_RUNTIME_SUPERVISOR = MetaStrategyRuntimeSupervisor(
            settings=settings,
            market_data_client=market_data_client,
            candle_store=candle_store,
        )
    return _META_STRATEGY_RUNTIME_SUPERVISOR


__all__ = [
    "MARKET_TIME_QUEUES",
    "META_STRATEGY_RUNTIME_SUPERVISOR_VERSION",
    "MetaStrategyRuntimeSnapshotSource",
    "MetaStrategyRuntimeSupervisor",
    "MetaStrategyRuntimeSupervisorConfig",
    "MetaStrategyRuntimeSupervisorMetrics",
    "get_meta_strategy_runtime_supervisor",
]
