"""Background job runtime for Regime decision and backtest work."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import queue
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock, Thread
from typing import Any, Callable, Literal

from backend.app.algorithms.regime.contracts import REGIME_ALLOWED_RUNTIME_MODE_VALUES, RegimeRuntimeMode, normalize_regime_runtime_mode
from backend.app.algorithms.regime.service import RegimeApplicationService


REGIME_BACKGROUND_RUNTIME_VERSION = "regime_background_runtime_v1"
REGIME_BACKGROUND_RUNTIME = "backend.app.algorithms.regime.runtime_supervisor.RegimeRuntimeSupervisor"
REGIME_PRODUCTION_DECISION_CORE = "backend.app.algorithms.regime.execution_pipeline.execute_regime_pipeline"
REGIME_PRODUCTION_STATE_TRANSITION_CORE = "backend.app.algorithms.regime.stateful_core.process_regime_bar"
REGIME_PRODUCTION_BACKTEST_CORE = "backend.app.algorithms.regime.backtest.engine.run_regime_backtest"
REGIME_BACKGROUND_WORKERS = (
    "regime_finalised_bar_ingestion_worker",
    "regime_decision_worker",
    "regime_backtest_worker",
    "regime_local_risk_worker",
    "regime_execution_outbox_worker",
    "regime_broker_reconciliation_worker",
    "regime_position_management_worker",
    "regime_recovery_worker",
    "regime_backtest_job_worker",
    "regime_heartbeat_health_worker",
    "regime_daily_reset_maintenance_worker",
    "regime_runtime_command_worker",
)

RegimeJobKind = Literal["decision_evaluation", "backtest", "settings_activation"]
RegimeJobStatus = Literal["queued", "running", "completed", "failed", "cancel_requested", "cancelled", "quarantined"]
REGIME_BACKTEST_JOB_STATUSES = ("queued", "running", "completed", "failed", "cancel_requested", "cancelled", "quarantined")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegimeJobReceipt:
    algorithm_id: str
    job_id: str
    job_kind: RegimeJobKind
    status: RegimeJobStatus
    queued_at: str
    updated_at: str
    worker: str
    result: dict[str, Any] | None = None
    failure_message: str | None = None
    reason_codes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithmId": self.algorithm_id,
            "jobId": self.job_id,
            "jobKind": self.job_kind,
            "status": self.status,
            "queuedAt": self.queued_at,
            "updatedAt": self.updated_at,
            "worker": self.worker,
            "result": copy.deepcopy(self.result),
            "failureMessage": self.failure_message,
            "reasonCodes": list(self.reason_codes),
        }


@dataclass
class _RegimeJob:
    job_id: str
    job_kind: RegimeJobKind
    payload: dict[str, Any]
    queued_at: str


class RegimeBackgroundJobManager:
    """Small in-process worker queue used by API transport routes."""

    def __init__(self, service_factory: Callable[[], RegimeApplicationService] = RegimeApplicationService, *, max_concurrent_backtests: int = 1) -> None:
        self._service_factory = service_factory
        self._queue: queue.Queue[_RegimeJob] = queue.Queue()
        self._receipts: dict[str, RegimeJobReceipt] = {}
        self._lock = Lock()
        self._started = False
        self._worker_thread: Thread | None = None
        self._max_concurrent_backtests = max(1, int(max_concurrent_backtests))
        self._cancel_requested: set[str] = set()
        self._startup_recovery_failure: str | None = None
        self._component_health: dict[str, dict[str, Any]] = {
            "backtest_worker": {"status": "unknown", "lastError": None, "reasonCodes": ["regime.health.component.not_checked"]},
            "database": {"status": "unknown", "lastError": None, "reasonCodes": ["regime.health.component.not_checked"]},
        }

    def enqueue(self, job_kind: RegimeJobKind, payload: dict[str, Any]) -> dict[str, Any]:
        _validate_job_runtime_mode(job_kind, payload)
        self.start()
        if job_kind == "backtest":
            return self._enqueue_backtest(payload)
        job_id = _job_id(job_kind, payload)
        now = _utc_now()
        with self._lock:
            existing = self._receipts.get(job_id)
            if existing and existing.status in {"queued", "running", "completed"}:
                return existing.as_dict()
            receipt = RegimeJobReceipt(
                algorithm_id="regime",
                job_id=job_id,
                job_kind=job_kind,
                status="queued",
                queued_at=now,
                updated_at=now,
                worker=self._worker_for(job_kind),
                reason_codes=(f"regime.runtime.{job_kind}.queued",),
            )
            self._receipts[job_id] = receipt
            self._queue.put(_RegimeJob(job_id=job_id, job_kind=job_kind, payload=copy.deepcopy(payload), queued_at=now))
            return receipt.as_dict()

    def get(self, job_id: str) -> dict[str, Any]:
        durable = self._read_durable_backtest(job_id)
        if durable is not None:
            return durable
        with self._lock:
            receipt = self._receipts.get(job_id)
            if receipt is None:
                return {
                    "algorithmId": "regime",
                    "jobId": job_id,
                    "status": "failed",
                    "failureMessage": "Unknown Regime job.",
                    "reasonCodes": ["regime.runtime.job_not_found"],
                }
            return receipt.as_dict()

    def status(self) -> dict[str, Any]:
        with self._lock:
            counts = {status: sum(1 for receipt in self._receipts.values() if receipt.status == status) for status in REGIME_BACKTEST_JOB_STATUSES}
        return {
            "algorithmId": "regime",
            "runtimeVersion": REGIME_BACKGROUND_RUNTIME_VERSION,
            "backgroundRuntime": REGIME_BACKGROUND_RUNTIME,
            "legacyJobManager": "backend.app.algorithms.regime.runtime.RegimeBackgroundJobManager",
            "productionDecisionCore": REGIME_PRODUCTION_DECISION_CORE,
            "productionStateTransitionCore": REGIME_PRODUCTION_STATE_TRANSITION_CORE,
            "productionBacktestCore": REGIME_PRODUCTION_BACKTEST_CORE,
            "workers": REGIME_BACKGROUND_WORKERS,
            "queueDepth": self._queue.qsize(),
            "jobCounts": counts,
            "backtestStatuses": REGIME_BACKTEST_JOB_STATUSES,
            "maxConcurrentRegimeBacktests": self._max_concurrent_backtests,
            "apiHandlersExecuteHeavyWorkInline": False,
            "liveTradingEnabled": False,
            "allowedRuntimeModes": REGIME_ALLOWED_RUNTIME_MODE_VALUES,
            "startupRecoveryFailure": self._startup_recovery_failure,
            "componentHealth": copy.deepcopy(self._component_health),
            "newBacktestJobsBlocked": self._startup_recovery_failure is not None or self._component_health["database"]["status"] == "unhealthy",
        }

    def jobs(self, *, job_kind: RegimeJobKind | None = None) -> dict[str, Any]:
        with self._lock:
            receipts = [
                receipt.as_dict()
                for receipt in self._receipts.values()
                if job_kind is None or receipt.job_kind == job_kind
            ]
        return {
            "algorithmId": "regime",
            "jobs": sorted(receipts, key=lambda item: str(item.get("queuedAt") or ""), reverse=True),
            "queueDepth": self._queue.qsize(),
            "apiHandlersExecuteHeavyWorkInline": False,
            "liveTradingEnabled": False,
            "allowedRuntimeModes": REGIME_ALLOWED_RUNTIME_MODE_VALUES,
            "startupRecoveryFailure": self._startup_recovery_failure,
            "componentHealth": copy.deepcopy(self._component_health),
        }

    def cancel(self, job_id: str) -> dict[str, Any]:
        durable = self._read_durable_backtest(job_id)
        if durable is not None:
            status = str(durable.get("status") or "")
            if status in {"completed", "failed", "cancelled", "quarantined"}:
                return {**durable, "cancelAccepted": False, "reasonCodes": ["regime.backtest.job.terminal_status"]}
            self._cancel_requested.add(job_id)
            service = self._service_factory()
            service.repository.update_backtest_job_status(
                job_id,
                status="cancel_requested",
                details={"cancelRequestedAt": _utc_now(), "jobKind": "backtest"},
                progress=float(durable.get("progress") or 0.0),
                reason_codes=["regime.backtest.job.cancel_requested"],
            )
            return {**(self._read_durable_backtest(job_id) or durable), "cancelAccepted": True}
        with self._lock:
            receipt = self._receipts.get(job_id)
            if receipt is None:
                return {"algorithmId": "regime", "jobId": job_id, "status": "failed", "cancelAccepted": False, "reasonCodes": ["regime.runtime.job_not_found"]}
            if receipt.status in {"completed", "failed", "cancelled", "quarantined"}:
                return {**receipt.as_dict(), "cancelAccepted": False, "reasonCodes": ["regime.runtime.job.terminal_status"]}
            self._cancel_requested.add(job_id)
            replacement = RegimeJobReceipt(
                algorithm_id="regime",
                job_id=receipt.job_id,
                job_kind=receipt.job_kind,
                status="cancel_requested",
                queued_at=receipt.queued_at,
                updated_at=_utc_now(),
                worker=receipt.worker,
                result=receipt.result,
                failure_message=receipt.failure_message,
                reason_codes=("regime.runtime.job.cancel_requested",),
            )
            self._receipts[job_id] = replacement
            return {**replacement.as_dict(), "cancelAccepted": True}

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._worker_thread = Thread(target=self._run, name="regime-background-runtime-worker", daemon=True)
            self._worker_thread.start()
            Thread(target=self._recover_abandoned_backtest_jobs, name="regime-backtest-recovery-worker", daemon=True).start()

    def _recover_abandoned_backtest_jobs(self) -> None:
        try:
            self._service_factory().repository.recover_abandoned_backtest_jobs(owner_id="regime-background-runtime-worker", stale_after_seconds=120)
            self._mark_component("backtest_worker", "healthy", reason_codes=("regime.health.backtest_recovery.completed",))
            self._mark_component("database", "healthy", reason_codes=("regime.health.backtest_recovery.database_available",))
        except Exception as exc:
            logger.error("Regime backtest recovery failed", exc_info=(type(exc), exc, exc.__traceback__))
            with self._lock:
                self._startup_recovery_failure = str(exc)
            self._mark_component("backtest_worker", "unhealthy", reason_codes=("regime.backtest.recovery_failed",), error=str(exc))
            self._mark_component("database", "unhealthy", reason_codes=("regime.backtest.recovery_database_failed",), error=str(exc))
            self._persist_runtime_failure("backtest_worker", "regime.backtest.recovery_failed", exc)

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job.job_kind == "backtest" and self._job_cancel_requested(job.job_id):
                    self._mark_cancelled(job, {"algorithmId": "regime", "jobId": job.job_id, "__jobStatus": "cancelled"})
                    continue
                self._mark_running(job)
                result = self._execute(job)
                if result.get("__jobStatus") == "cancelled":
                    self._mark_cancelled(job, result)
                else:
                    self._mark_completed(job, result)
            except Exception as exc:  # pragma: no cover - exercised through failed-job status in integration.
                if job.job_kind == "backtest" and str((self._service_factory().repository.read_backtest_job(job.job_id) or {}).get("status")) == "quarantined":
                    continue
                self._mark_failed(job, exc)
            finally:
                self._queue.task_done()

    def _execute(self, job: _RegimeJob) -> dict[str, Any]:
        service = self._service_factory()
        if job.job_kind == "decision_evaluation":
            return service.evaluate(job.payload)
        if job.job_kind == "backtest":
            return self._execute_backtest_job(job, service)
        if job.job_kind == "settings_activation":
            return service.handle_settings_command(job.payload)
        raise ValueError(f"Unsupported Regime job kind: {job.job_kind}")

    def _mark_running(self, job: _RegimeJob) -> None:
        self._replace(job, "running", reason_codes=(f"regime.runtime.{job.job_kind}.running",))

    def _mark_completed(self, job: _RegimeJob, result: dict[str, Any]) -> None:
        self._replace(job, "completed", result=result, reason_codes=(f"regime.runtime.{job.job_kind}.completed",))

    def _mark_failed(self, job: _RegimeJob, exc: Exception) -> None:
        self._replace(job, "failed", failure_message=str(exc), reason_codes=(f"regime.runtime.{job.job_kind}.failed_closed",))

    def _mark_cancelled(self, job: _RegimeJob, result: dict[str, Any] | None = None) -> None:
        self._replace(job, "cancelled", result=result, reason_codes=(f"regime.runtime.{job.job_kind}.cancelled",))

    def _replace(
        self,
        job: _RegimeJob,
        status: RegimeJobStatus,
        *,
        result: dict[str, Any] | None = None,
        failure_message: str | None = None,
        reason_codes: tuple[str, ...],
    ) -> None:
        with self._lock:
            self._receipts[job.job_id] = RegimeJobReceipt(
                algorithm_id="regime",
                job_id=job.job_id,
                job_kind=job.job_kind,
                status=status,
                queued_at=job.queued_at,
                updated_at=_utc_now(),
                worker=self._worker_for(job.job_kind),
                result=copy.deepcopy(result),
                failure_message=failure_message,
                reason_codes=reason_codes,
            )
        if job.job_kind == "backtest":
            details = {
                "jobKind": "backtest",
                "result": copy.deepcopy(result),
                "resultMetadata": _backtest_result_metadata(result),
                "completedAt": _utc_now() if status in {"completed", "cancelled", "failed"} else None,
            }
            progress = 100.0 if status in {"completed", "cancelled", "failed"} else 10.0
            self._service_factory().repository.update_backtest_job_status(
                job.job_id,
                status=status,
                details=details,
                progress=progress,
                failure_message=failure_message,
                reason_codes=reason_codes,
            )

    def _enqueue_backtest(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._startup_recovery_failure is not None or self._component_health["database"]["status"] == "unhealthy":
            return {
                "algorithmId": "regime",
                "jobKind": "backtest",
                "status": "failed",
                "accepted": False,
                "apiHandlersExecuteHeavyWorkInline": False,
                "reasonCodes": ["regime.backtest.new_jobs_blocked_recovery_unhealthy"],
                "failureMessage": self._startup_recovery_failure or self._component_health["database"].get("lastError"),
                "componentHealth": copy.deepcopy(self._component_health),
            }
        safe_payload = _backtest_payload(payload)
        job_id = _job_id("backtest", safe_payload)
        now = _utc_now()
        service = self._service_factory()
        existing = service.repository.read_backtest_job(job_id)
        if existing is not None and str(existing.get("status")) in {"queued", "running", "completed", "cancel_requested"}:
            return _durable_job_receipt(existing)
        manifest = _backtest_manifest(safe_payload)
        job = {
            "algorithmId": "regime",
            "jobId": job_id,
            "jobKind": "backtest",
            "status": "queued",
            "queuedAt": now,
            "updatedAt": now,
            "worker": self._worker_for("backtest"),
            "runtimeMode": "backtest",
            "symbol": safe_payload.get("symbol") or "SPY",
            "algorithmInstanceId": safe_payload.get("algorithmInstanceId") or "regime-default",
            "accountId": safe_payload.get("accountId") or "default",
            "payload": safe_payload,
            "manifest": manifest,
            "settingsVersion": manifest["settingsVersion"],
            "codeVersion": manifest["codeVersion"],
            "progress": 0.0,
            "heartbeatAt": now,
            "resourceLimits": {"maxConcurrentRegimeBacktests": self._max_concurrent_backtests},
            "reasonCodes": ["regime.backtest.job.queued"],
        }
        service.repository.enqueue_backtest_job(job)
        with self._lock:
            self._receipts[job_id] = RegimeJobReceipt("regime", job_id, "backtest", "queued", now, now, self._worker_for("backtest"), reason_codes=("regime.runtime.backtest.queued",))
            self._queue.put(_RegimeJob(job_id=job_id, job_kind="backtest", payload=safe_payload, queued_at=now))
        return _durable_job_receipt(service.repository.read_backtest_job(job_id) or job)

    def _execute_backtest_job(self, job: _RegimeJob, service: RegimeApplicationService) -> dict[str, Any]:
        current = service.repository.read_backtest_job(job.job_id) or {}
        if str(current.get("status")) == "cancel_requested" or self._job_cancel_requested(job.job_id):
            return {"algorithmId": "regime", "jobId": job.job_id, "__jobStatus": "cancelled", "reasonCodes": ["regime.backtest.job.cancelled_before_execution"]}
        try:
            prepared = _load_backtest_data(job.payload)
            service.repository.update_backtest_job_status(job.job_id, status="running", details={"stage": "data_loaded", "candleCount": len(prepared.get("candles") or [])}, progress=25.0, reason_codes=["regime.backtest.job.data_loaded"])
            if self._job_cancel_requested(job.job_id):
                return {"algorithmId": "regime", "jobId": job.job_id, "__jobStatus": "cancelled", "reasonCodes": ["regime.backtest.job.cancelled_after_data_load"]}
            replay_payload = {**prepared, "runtimeMode": "backtest"}
            replay_payload.pop("inventorySnapshot", None)
            service.repository.update_backtest_job_status(job.job_id, status="running", details={"stage": "replay_running"}, progress=50.0, reason_codes=["regime.backtest.job.replay_running"])
            result = service.run_backtest(replay_payload)
            if self._job_cancel_requested(job.job_id):
                return {"algorithmId": "regime", "jobId": job.job_id, "__jobStatus": "cancelled", "resultMetadata": _backtest_result_metadata(result), "reasonCodes": ["regime.backtest.job.cancelled_after_replay"]}
            return result
        except Exception as exc:
            logger.error("Regime backtest job failed and was quarantined", exc_info=(type(exc), exc, exc.__traceback__))
            self._mark_component("backtest_worker", "unhealthy", reason_codes=("regime.backtest.job.quarantined",), error=str(exc))
            service.repository.update_backtest_job_status(job.job_id, status="quarantined", details={"stage": "failed_closed"}, progress=100.0, reason_codes=["regime.backtest.job.quarantined"])
            raise

    def _read_durable_backtest(self, job_id: str) -> dict[str, Any] | None:
        if not job_id.startswith("regime-backtest-"):
            return None
        try:
            payload = self._service_factory().repository.read_backtest_job(job_id)
        except Exception as exc:
            self._record_repository_read_failure(job_id, exc)
            return {
                "algorithmId": "regime",
                "jobId": job_id,
                "status": "failed",
                "failureMessage": str(exc),
                "reasonCodes": ["regime.backtest.job_status_read_failed"],
                "componentHealth": copy.deepcopy(self._component_health),
                "apiHandlersExecuteHeavyWorkInline": False,
            }
        return _durable_job_receipt(payload) if payload is not None else None

    def _job_cancel_requested(self, job_id: str) -> bool:
        if job_id in self._cancel_requested:
            return True
        try:
            current = self._service_factory().repository.read_backtest_job(job_id)
        except Exception as exc:
            self._record_repository_read_failure(job_id, exc)
            return True
        return str((current or {}).get("status") or "") == "cancel_requested"

    @staticmethod
    def _worker_for(job_kind: RegimeJobKind) -> str:
        if job_kind == "settings_activation":
            return "regime_runtime_control_worker"
        return "regime_backtest_worker" if job_kind == "backtest" else "regime_strategy_evaluation_worker"

    def _mark_component(self, component: str, status: str, *, reason_codes: tuple[str, ...], error: str | None = None) -> None:
        with self._lock:
            self._component_health[component] = {
                "status": status,
                "lastError": error,
                "reasonCodes": list(reason_codes),
                "lastCheckedAt": _utc_now(),
            }

    def _record_repository_read_failure(self, job_id: str, exc: BaseException) -> None:
        logger.error("Regime backtest job repository read failed", exc_info=(type(exc), exc, exc.__traceback__))
        self._mark_component("database", "unhealthy", reason_codes=("regime.backtest.job_status_read_failed",), error=str(exc))
        self._persist_runtime_failure("database", "regime.backtest.job_status_read_failed", exc, job_id=job_id)

    def _persist_runtime_failure(self, component: str, reason_code: str, exc: BaseException, *, job_id: str | None = None) -> None:
        try:
            self._service_factory().repository.record_runtime_event(
                {
                    "algorithmId": "regime",
                    "algorithmInstanceId": "regime-default",
                    "accountId": "default",
                    "runtimeMode": "backtest",
                    "symbol": "SPY",
                    "eventId": f"regime-backtest-component-failure-{component}-{_utc_now()}",
                    "eventType": "backtest_component_failure",
                    "processingStatus": "unhealthy",
                    "payload": {
                        "algorithmId": "regime",
                        "component": component,
                        "jobId": job_id,
                        "failureMessage": str(exc),
                        "reasonCodes": [reason_code],
                        "newBacktestJobsBlocked": True,
                    },
                }
            )
        except Exception:
            logger.exception("Regime failed to persist backtest component failure")


def regime_runtime_inventory() -> dict[str, Any]:
    return {
        "algorithmId": "regime",
        "runtimeVersion": REGIME_BACKGROUND_RUNTIME_VERSION,
        "backgroundRuntime": REGIME_BACKGROUND_RUNTIME,
        "legacyJobManager": "backend.app.algorithms.regime.runtime.RegimeBackgroundJobManager",
        "productionDecisionCore": REGIME_PRODUCTION_DECISION_CORE,
        "productionStateTransitionCore": REGIME_PRODUCTION_STATE_TRANSITION_CORE,
        "productionBacktestCore": REGIME_PRODUCTION_BACKTEST_CORE,
        "workers": REGIME_BACKGROUND_WORKERS,
        "apiHandlersExecuteHeavyWorkInline": False,
        "liveTradingEnabled": False,
        "allowedRuntimeModes": REGIME_ALLOWED_RUNTIME_MODE_VALUES,
        "supportedJobKinds": ("decision_evaluation", "backtest", "settings_activation"),
        "backtestJobStatuses": REGIME_BACKTEST_JOB_STATUSES,
        "maxConcurrentRegimeBacktests": 1,
    }


def _job_id(job_kind: RegimeJobKind, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(f"{job_kind}:{encoded}".encode("utf-8")).hexdigest()[:24]
    return f"regime-{job_kind.replace('_', '-')}-{digest}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _backtest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    safe = copy.deepcopy(payload or {})
    if safe.get("runtimeMode") or safe.get("runtime_mode"):
        normalize_regime_runtime_mode(safe.get("runtimeMode") or safe.get("runtime_mode"))
    safe["algorithmId"] = "regime"
    safe["runtimeMode"] = RegimeRuntimeMode.BACKTEST.value
    safe["symbol"] = str(safe.get("symbol") or "SPY").upper()
    safe.setdefault("algorithmInstanceId", "regime-default")
    safe.setdefault("accountId", "default")
    safe.pop("inventorySnapshot", None)
    return safe


def _load_backtest_data(payload: dict[str, Any]) -> dict[str, Any]:
    candles = payload.get("candles") or payload.get("primaryCandles")
    if not isinstance(candles, list) or not candles:
        raise ValueError("Regime backtest job requires point-in-time candles.")
    return {**payload, "candles": candles, "runtimeMode": RegimeRuntimeMode.BACKTEST.value}


def _validate_job_runtime_mode(job_kind: RegimeJobKind, payload: dict[str, Any]) -> None:
    raw_mode = (payload or {}).get("runtimeMode") or (payload or {}).get("runtime_mode")
    if raw_mode is not None:
        normalize_regime_runtime_mode(raw_mode)
    if job_kind == "backtest":
        return
    if job_kind == "settings_activation":
        command_settings = (payload or {}).get("settingsSnapshot") if isinstance((payload or {}).get("settingsSnapshot"), dict) else (payload or {}).get("settings")
        identity = command_settings.get("identity") if isinstance(command_settings, dict) and isinstance(command_settings.get("identity"), dict) else {}
        nested_mode = identity.get("runtimeMode") if isinstance(identity, dict) else None
        if nested_mode is not None:
            normalize_regime_runtime_mode(nested_mode)


def _backtest_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    candles = payload.get("candles") or payload.get("primaryCandles") or []
    settings = payload.get("__regime_settings_snapshot") if isinstance(payload.get("__regime_settings_snapshot"), dict) else {}
    settings_version = str(settings.get("settingsVersion") or payload.get("settingsVersion") or "active")
    code_version = REGIME_PRODUCTION_BACKTEST_CORE
    data = {
        "symbol": payload.get("symbol") or "SPY",
        "count": len(candles) if isinstance(candles, list) else 0,
        "first": candles[0].get("timestamp") if isinstance(candles, list) and candles else None,
        "last": candles[-1].get("timestamp") if isinstance(candles, list) and candles else None,
    }
    digest = hashlib.sha256(json.dumps({"data": data, "settingsVersion": settings_version, "codeVersion": code_version}, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"dataManifestHash": digest, "settingsVersion": settings_version, "codeVersion": code_version, "data": data}


def _backtest_result_metadata(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    return {
        "authoritativeEngine": result.get("authoritativeEngine"),
        "engineVersion": result.get("engineVersion"),
        "settingsVersion": result.get("settingsVersion"),
        "dataManifestHash": result.get("dataManifestHash"),
        "totalPnl": result.get("totalPnl"),
        "netProfit": metrics.get("netProfit"),
        "tradeCount": metrics.get("tradeCount"),
        "decisionCount": metrics.get("decisionCount"),
    }


def _durable_job_receipt(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"algorithmId": "regime", "status": "failed", "reasonCodes": ["regime.backtest.job_not_found"]}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else None
    return {
        "algorithmId": "regime",
        "jobId": payload.get("jobId"),
        "jobKind": "backtest",
        "status": payload.get("status") or payload.get("processingStatus"),
        "queuedAt": payload.get("queuedAt"),
        "updatedAt": payload.get("updatedAt") or payload.get("heartbeatAt"),
        "worker": payload.get("worker") or "regime_backtest_worker",
        "runtimeMode": "backtest",
        "progress": payload.get("progress"),
        "heartbeatAt": payload.get("heartbeatAt"),
        "manifest": payload.get("manifest"),
        "result": result,
        "resultMetadata": payload.get("resultMetadata") or _backtest_result_metadata(result),
        "failureMessage": payload.get("failureMessage"),
        "reasonCodes": list(payload.get("reasonCodes") or []),
        "apiHandlersExecuteHeavyWorkInline": False,
    }


REGIME_JOB_MANAGER = RegimeBackgroundJobManager()


__all__ = [
    "REGIME_BACKGROUND_RUNTIME",
    "REGIME_BACKGROUND_RUNTIME_VERSION",
    "REGIME_BACKGROUND_WORKERS",
    "REGIME_BACKTEST_JOB_STATUSES",
    "REGIME_JOB_MANAGER",
    "REGIME_PRODUCTION_BACKTEST_CORE",
    "REGIME_PRODUCTION_DECISION_CORE",
    "REGIME_PRODUCTION_STATE_TRANSITION_CORE",
    "RegimeBackgroundJobManager",
    "RegimeJobKind",
    "RegimeJobReceipt",
    "regime_runtime_inventory",
]
