"""HTTP boundary for the backend-authoritative Regime runtime."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from backend.app.algorithms.regime.backtest.engine import REGIME_BACKTEST_ENGINE_VERSION
from backend.app.algorithms.regime.contracts import REGIME_ALLOWED_RUNTIME_MODE_VALUES, RegimeRuntimeMode, normalize_regime_runtime_mode
from backend.app.algorithms.regime.execution_pipeline import REGIME_EXECUTION_PIPELINE_MODULES
from backend.app.algorithms.regime.final_acceptance import build_regime_final_acceptance_report
from backend.app.algorithms.regime.rollout import build_regime_paper_readiness_report, regime_rollout_status
from backend.app.algorithms.regime.runtime import (
    REGIME_BACKTEST_JOB_STATUSES,
    REGIME_BACKGROUND_RUNTIME,
    REGIME_BACKGROUND_WORKERS,
    REGIME_JOB_MANAGER,
    REGIME_PRODUCTION_BACKTEST_CORE,
    REGIME_PRODUCTION_DECISION_CORE,
    regime_runtime_inventory,
)
from backend.app.algorithms.regime.runtime_supervisor import get_regime_runtime_supervisor
from backend.app.algorithms.regime.service import RegimeApplicationService

REGIME_API_VERSION = "regime_api_v1"
REGIME_RUNTIME_LOCATION = "backend/app/algorithms/regime"
REGIME_AUTHORITATIVE_RUNTIME = "backend.app.algorithms.regime.execution_pipeline"
REGIME_BACKTEST_ARTIFACT_ROOT = "backend/data/regime-backtests"
REGIME_BACKTEST_AUTHORITATIVE_ENGINE = "backend.app.algorithms.regime.backtest.engine"
REGIME_BACKTEST_FILE_INVENTORY = (
    "__init__.py",
    "engine.py",
    "execution.py",
    "ledger.py",
    "metrics.py",
    "walk_forward.py",
)
REGIME_BACKTEST_OWNED_CAPABILITIES = (
    "Regime replay",
    "Warm-up handling",
    "Point-in-time classification",
    "Hysteresis replay",
    "Strategy routing",
    "Dynamic-profile reconstruction",
    "Family aggregation",
    "Entry and exit simulation",
    "Costs and slippage",
    "Position ledger",
    "Trade ledger",
    "Regime-segmented performance",
    "Strategy-family attribution",
    "Walk-forward validation",
    "Untouched holdout testing",
    "Daily independent backtests",
)
REGIME_FRONTEND_ROLE = "API client, settings editor, status display, diagnostics display, and backtest-job display"
REGIME_FORBIDDEN_AUTHORITATIVE_PAYLOAD_KEYS = frozenset(
    {
        "authoritativeDecision",
        "authoritativeRuntime",
        "authoritativeEngine",
        "classification",
        "hysteresis",
        "hysteresisState",
        "strategyRouting",
        "finalSignal",
        "signal",
        "sizing",
        "orderIntent",
        "orderProposal",
        "exitDecision",
        "brokerSubmission",
        "decisionResult",
        "backtestResult",
        "settings",
        "settingsSnapshot",
        "account",
        "accountSnapshot",
        "inventory",
        "inventorySnapshot",
        "orders",
        "fills",
        "positions",
        "trades",
    }
)

router = APIRouter(prefix="/api/regime", tags=["regime"])
REGIME_SERVICE = RegimeApplicationService()
REGIME_REPOSITORY = REGIME_SERVICE.repository


@router.get("/backtests/status", summary="Poll Regime backtest status")
def regime_backtest_status() -> dict[str, Any]:
    return {
        "algorithmId": "regime",
        "apiVersion": REGIME_API_VERSION,
        "engineVersion": REGIME_BACKTEST_ENGINE_VERSION,
        "status": "backend_runtime_available",
        "artifactRoot": REGIME_BACKTEST_ARTIFACT_ROOT,
        "storageKeyPrefix": "regime-backtest:",
        "cacheKeySource": "symbol:first_timestamp:last_timestamp:candle_count",
        "productionDecisionCore": REGIME_PRODUCTION_DECISION_CORE,
        "productionBacktestCore": REGIME_PRODUCTION_BACKTEST_CORE,
        "backgroundRuntime": REGIME_BACKGROUND_RUNTIME,
        "legacyJobManager": "backend.app.algorithms.regime.runtime.RegimeBackgroundJobManager",
        "jobInfrastructure": "durable Regime-owned backtest jobs persisted in regime_backtest_jobs",
        "jobStatuses": REGIME_BACKTEST_JOB_STATUSES,
        "maxConcurrentRegimeBacktests": 1,
        "backgroundWorkers": REGIME_BACKGROUND_WORKERS,
        "authoritativeRuntime": REGIME_AUTHORITATIVE_RUNTIME,
        "authoritativeEngine": REGIME_BACKTEST_AUTHORITATIVE_ENGINE,
        "runtimeLocation": REGIME_RUNTIME_LOCATION,
        "frontendRole": REGIME_FRONTEND_ROLE,
        "apiResponsibilities": ("transport", "control", "status", "job_management"),
        "apiHandlersExecuteHeavyWorkInline": False,
        "allowedRuntimeModes": REGIME_ALLOWED_RUNTIME_MODE_VALUES,
        "fileInventory": REGIME_BACKTEST_FILE_INVENTORY,
        "ownedCapabilities": REGIME_BACKTEST_OWNED_CAPABILITIES,
        "isolatedFromWca": True,
        "pipeline": REGIME_EXECUTION_PIPELINE_MODULES,
        "message": "Regime decisions and backtests are enqueued by HTTP handlers and executed by the backend Python background runtime.",
    }


@router.post("/evaluate", status_code=202, summary="Enqueue a Regime decision job")
def evaluate_regime(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    _validate_regime_transport_payload(payload, default_mode=RegimeRuntimeMode.SHADOW)
    return REGIME_JOB_MANAGER.enqueue("decision_evaluation", payload)


@router.post("/backtests/run", status_code=202, summary="Enqueue a Regime backtest job")
def regime_backtest_run(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    _validate_regime_transport_payload(payload, default_mode=RegimeRuntimeMode.BACKTEST, allow_backtest_result=False)
    return REGIME_JOB_MANAGER.enqueue("backtest", payload)


@router.get("/backtests/jobs", summary="List Regime backtest jobs")
def regime_backtest_jobs() -> dict[str, Any]:
    return REGIME_JOB_MANAGER.jobs(job_kind="backtest")


@router.get("/backtests/jobs/{job_id}", summary="Poll a Regime backtest job")
def regime_backtest_job_status(job_id: str) -> dict[str, Any]:
    return REGIME_JOB_MANAGER.get(job_id)


@router.post("/backtests/jobs/{job_id}/cancel", status_code=202, summary="Cancel a queued or running Regime backtest job")
def cancel_regime_backtest_job(job_id: str) -> dict[str, Any]:
    return REGIME_JOB_MANAGER.cancel(job_id)


@router.post("/settings/commands", status_code=202, summary="Enqueue a Regime settings command")
def submit_regime_settings_command(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    _validate_regime_runtime_payload(payload, default_mode=RegimeRuntimeMode.SHADOW)
    return REGIME_JOB_MANAGER.enqueue("settings_activation", payload)


@router.post("/settings/versions/create", status_code=202, summary="Enqueue immutable Regime settings-version creation")
def create_regime_settings_version(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    _validate_regime_runtime_payload(payload, default_mode=RegimeRuntimeMode.SHADOW)
    return REGIME_JOB_MANAGER.enqueue("settings_activation", {**payload, "commandType": "create_version"})


@router.post("/settings/versions/validate", status_code=202, summary="Enqueue Regime settings-version validation")
def validate_regime_settings_version(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    _validate_regime_runtime_payload(payload, default_mode=RegimeRuntimeMode.SHADOW)
    return REGIME_JOB_MANAGER.enqueue("settings_activation", {**payload, "commandType": "validate_version"})


@router.post("/settings/versions/activate", status_code=202, summary="Enqueue Regime settings-version activation")
def activate_regime_settings_version(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    _validate_regime_runtime_payload(payload, default_mode=RegimeRuntimeMode.SHADOW)
    return REGIME_JOB_MANAGER.enqueue("settings_activation", {**payload, "commandType": "activate_version"})


@router.post("/settings/versions/rollback", status_code=202, summary="Enqueue Regime settings rollback")
def rollback_regime_settings_version(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    _validate_regime_runtime_payload(payload, default_mode=RegimeRuntimeMode.SHADOW)
    return REGIME_JOB_MANAGER.enqueue("settings_activation", {**payload, "commandType": "rollback_version"})


@router.post("/settings/active", summary="Read the active Regime settings snapshot")
def active_regime_settings(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    _validate_regime_runtime_payload(payload, default_mode=RegimeRuntimeMode.SHADOW)
    return REGIME_SERVICE.active_settings(payload)


@router.get("/jobs/{job_id}", summary="Poll a Regime background job")
def regime_job_status(job_id: str) -> dict[str, Any]:
    return REGIME_JOB_MANAGER.get(job_id)


@router.post("/jobs/{job_id}/cancel", status_code=202, summary="Cancel a queued or running Regime background job")
def cancel_regime_job(job_id: str) -> dict[str, Any]:
    return REGIME_JOB_MANAGER.cancel(job_id)


@router.get("/runtime/status", summary="Describe Regime background runtime status")
def regime_runtime_status() -> dict[str, Any]:
    return {**get_regime_runtime_supervisor().status(), "backgroundJobs": REGIME_JOB_MANAGER.status()}


@router.get("/runtime/supervisor/status", summary="Read Regime supervisor status")
def regime_supervisor_status() -> dict[str, Any]:
    return get_regime_runtime_supervisor().status()


@router.get("/runtime/health", summary="Read Regime supervisor health")
def regime_supervisor_health() -> dict[str, Any]:
    return get_regime_runtime_supervisor().health()


@router.get("/runtime/observability", summary="Read Regime operational observability")
def regime_runtime_observability() -> dict[str, Any]:
    return get_regime_runtime_supervisor().observability()


@router.get("/runtime/alerts", summary="Read Regime runtime alert conditions")
def regime_runtime_alerts() -> dict[str, Any]:
    return get_regime_runtime_supervisor().alerts()


@router.get("/runtime/admin-audit", summary="Read Regime administrative command audit")
def regime_runtime_admin_audit() -> dict[str, Any]:
    return get_regime_runtime_supervisor().admin_audit()


@router.post("/runtime/pause", status_code=202, summary="Enqueue a Regime runtime pause command")
async def pause_regime_runtime(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return await get_regime_runtime_supervisor().submit_command("pause", payload)


@router.post("/runtime/resume", status_code=202, summary="Enqueue a Regime runtime resume command")
async def resume_regime_runtime(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return await get_regime_runtime_supervisor().submit_command("resume", payload)


@router.post("/runtime/emergency-flatten", status_code=202, summary="Enqueue a Regime emergency flatten command")
async def emergency_flatten_regime_runtime(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return await get_regime_runtime_supervisor().submit_command("emergency_flatten", payload)


@router.post("/runtime/strategies/{strategy_id}/disable", status_code=202, summary="Enqueue a Regime strategy disable command")
async def disable_regime_strategy(strategy_id: str, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return await get_regime_runtime_supervisor().submit_command("disable_strategy", {**payload, "strategyId": strategy_id})


@router.post("/runtime/strategies/{strategy_id}/enable", status_code=202, summary="Enqueue a Regime strategy enable command")
async def enable_regime_strategy(strategy_id: str, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return await get_regime_runtime_supervisor().submit_command("enable_strategy", {**payload, "strategyId": strategy_id})


@router.post("/runtime/settings/rotate", status_code=202, summary="Enqueue a Regime settings-version rotation command")
async def rotate_regime_settings_version(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return await get_regime_runtime_supervisor().submit_command("rotate_settings_version", payload)


@router.post("/runtime/events/completed-bar", status_code=202, summary="Enqueue a completed one-minute bar event")
async def enqueue_regime_completed_bar(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return await get_regime_runtime_supervisor().publish_completed_bar(payload)


@router.get("/runtime/latest-checkpoint", summary="Read latest Regime runtime checkpoint summary")
def latest_regime_checkpoint() -> dict[str, Any]:
    return get_regime_runtime_supervisor().latest_checkpoint()


@router.get("/runtime/queue-depth", summary="Read Regime runtime queue depth")
def regime_queue_depth() -> dict[str, Any]:
    return get_regime_runtime_supervisor().queue_depth()


@router.get("/runtime/queue", summary="Read Regime runtime queue status")
def regime_queue() -> dict[str, Any]:
    return get_regime_runtime_supervisor().queue_depth()


@router.get("/runtime/latest-decision", summary="Read latest Regime runtime decision summary")
def latest_regime_decision() -> dict[str, Any]:
    return get_regime_runtime_supervisor().latest_decision()


@router.get("/runtime/recovery-status", summary="Read Regime restart recovery status")
def regime_recovery_status() -> dict[str, Any]:
    return get_regime_runtime_supervisor().recovery_status()


@router.get("/runtime/recovery", summary="Read Regime restart recovery status")
def regime_recovery() -> dict[str, Any]:
    return get_regime_runtime_supervisor().recovery_status()


@router.post("/ml/promotion/evaluate", summary="Evaluate Regime ML promotion through backend evidence only")
def evaluate_regime_ml_promotion(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return REGIME_SERVICE.evaluate_ml_promotion(payload)


@router.post("/ml/promotion/evidence", summary="Record trusted backend Regime ML promotion evidence")
def record_regime_ml_promotion_evidence(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return REGIME_SERVICE.record_ml_promotion_evidence(payload)


@router.get("/backtests/routes", summary="Describe Regime backtest API routes")
def regime_backtest_routes() -> dict[str, Any]:
    return {
        "algorithmId": "regime",
        "routes": [
            {
                "method": "GET",
                "path": "/api/regime/backtests/status",
                "purpose": "Regime backtest status and artifact metadata.",
            },
            {
                "method": "POST",
                "path": "/api/regime/evaluate",
                "purpose": "Enqueue a backend Regime decision job.",
            },
            {
                "method": "POST",
                "path": "/api/regime/backtests/run",
                "purpose": "Enqueue a backend Regime backtest job.",
            },
            {
                "method": "GET",
                "path": "/api/regime/jobs/{job_id}",
                "purpose": "Poll queued, running, completed, or failed Regime jobs.",
            },
            {
                "method": "GET",
                "path": "/api/regime/backtests/jobs/{job_id}",
                "purpose": "Poll durable queued, running, completed, failed, cancelled, or quarantined Regime backtest jobs.",
            },
            {
                "method": "GET",
                "path": "/api/regime/backtests/jobs",
                "purpose": "List Regime backtest jobs known to the background job manager.",
            },
            {
                "method": "POST",
                "path": "/api/regime/backtests/jobs/{job_id}/cancel",
                "purpose": "Request safe cancellation for a queued or running Regime backtest job.",
            },
            {
                "method": "POST",
                "path": "/api/regime/settings/commands",
                "purpose": "Compatibility route for enqueueing Regime settings commands.",
            },
            {
                "method": "POST",
                "path": "/api/regime/settings/versions/create",
                "purpose": "Enqueue creation of an immutable backend-owned Regime settings version.",
            },
            {
                "method": "POST",
                "path": "/api/regime/settings/versions/validate",
                "purpose": "Enqueue validation of a candidate Regime settings version.",
            },
            {
                "method": "POST",
                "path": "/api/regime/settings/versions/activate",
                "purpose": "Enqueue activation of a validated Regime settings version.",
            },
            {
                "method": "POST",
                "path": "/api/regime/settings/versions/rollback",
                "purpose": "Enqueue rollback to a previous immutable Regime settings version.",
            },
            {
                "method": "POST",
                "path": "/api/regime/settings/active",
                "purpose": "Read the active immutable settings snapshot.",
            },
            {
                "method": "GET",
                "path": "/api/regime/runtime/status",
                "purpose": "Report Regime background runtime and worker status.",
            },
            {
                "method": "GET",
                "path": "/api/regime/runtime/health",
                "purpose": "Read Regime supervisor health from runtime status only.",
            },
            {
                "method": "GET",
                "path": "/api/regime/runtime/recovery",
                "purpose": "Read Regime restart recovery state.",
            },
            {
                "method": "GET",
                "path": "/api/regime/runtime/latest-decision",
                "purpose": "Read the latest persisted/background Regime decision summary.",
            },
            {
                "method": "GET",
                "path": "/api/regime/runtime/latest-checkpoint",
                "purpose": "Read the latest Regime runtime checkpoint summary.",
            },
            {
                "method": "GET",
                "path": "/api/regime/runtime/queue",
                "purpose": "Read Regime runtime queue depth and lag status.",
            },
            {
                "method": "GET",
                "path": "/api/regime/runtime/observability",
                "purpose": "Read persisted/runtime Regime metrics, blockers, evidence, occupancy, execution quality and alerts.",
            },
            {
                "method": "GET",
                "path": "/api/regime/runtime/alerts",
                "purpose": "Read Regime alert conditions from runtime status only.",
            },
            {
                "method": "GET",
                "path": "/api/regime/runtime/admin-audit",
                "purpose": "Read durable Regime administrative command audit records.",
            },
            {
                "method": "POST",
                "path": "/api/regime/runtime/strategies/{strategy_id}/disable",
                "purpose": "Enqueue an audited Regime strategy-disable command.",
            },
            {
                "method": "POST",
                "path": "/api/regime/runtime/strategies/{strategy_id}/enable",
                "purpose": "Enqueue an audited Regime strategy-enable command.",
            },
            {
                "method": "POST",
                "path": "/api/regime/runtime/settings/rotate",
                "purpose": "Enqueue an audited Regime settings-version rotation command.",
            },
            {
                "method": "POST",
                "path": "/api/regime/ml/promotion/evaluate",
                "purpose": "Evaluate backend-only Regime ML promotion eligibility.",
            },
            {
                "method": "GET",
                "path": "/api/regime/backtests/routes",
                "purpose": "Regime backtest API route discovery.",
            },
        ],
    }


@router.get("/rollout/status", summary="Poll Regime staged paper rollout status")
def regime_rollout_status_route() -> dict[str, Any]:
    status = regime_rollout_status()
    status["finalAcceptance"] = build_regime_final_acceptance_report()
    return status


@router.get("/rollout/paper-readiness", summary="Poll Regime evidence-derived paper readiness")
def regime_paper_readiness_route() -> dict[str, Any]:
    return build_regime_paper_readiness_report()


@router.get("/persistence/schema", summary="Describe Regime persistence schema")
def regime_persistence_schema() -> dict[str, Any]:
    return REGIME_SERVICE.persistence_schema()


@router.get("/backend/inventory", summary="Describe Regime backend ownership boundaries")
def regime_backend_inventory_route() -> dict[str, Any]:
    return {**REGIME_SERVICE.backend_inventory(), "runtime": regime_runtime_inventory()}


@router.post("/decisions/record", summary="Record a Regime decision snapshot")
def record_regime_decision(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail={
            "algorithmId": "regime",
            "reasonCodes": ["regime.api.direct_decision_recording_disabled"],
            "message": "Regime decisions must be produced and persisted by backend workers.",
        },
    )


@router.post("/backtests/record", summary="Record a Regime backtest result")
def record_regime_backtest(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail={
            "algorithmId": "regime",
            "reasonCodes": ["regime.api.direct_backtest_recording_disabled"],
            "message": "Regime backtests must run through backend backtest jobs.",
        },
    )


def _validate_regime_transport_payload(
    payload: dict[str, Any],
    *,
    default_mode: RegimeRuntimeMode,
    allow_backtest_result: bool = True,
) -> None:
    _validate_regime_runtime_payload(payload, default_mode=default_mode)
    forbidden = set(REGIME_FORBIDDEN_AUTHORITATIVE_PAYLOAD_KEYS)
    if allow_backtest_result:
        forbidden.discard("backtestResult")
    present = sorted(key for key in forbidden if key in (payload or {}))
    if present:
        raise HTTPException(
            status_code=400,
            detail={
                "algorithmId": "regime",
                "reasonCodes": ["regime.api.frontend_authoritative_payload_rejected"],
                "forbiddenKeys": present,
                "message": "Regime API payloads may transport market data or commands only; backend workers own decisions.",
            },
        )


def _validate_regime_runtime_payload(payload: dict[str, Any], *, default_mode: RegimeRuntimeMode) -> None:
    try:
        normalize_regime_runtime_mode((payload or {}).get("runtimeMode") or (payload or {}).get("runtime_mode"), default=default_mode)
        identity = (payload or {}).get("identity") if isinstance((payload or {}).get("identity"), dict) else {}
        if identity.get("runtimeMode") is not None:
            normalize_regime_runtime_mode(identity.get("runtimeMode"), default=default_mode)
        settings = (payload or {}).get("settingsSnapshot") if isinstance((payload or {}).get("settingsSnapshot"), dict) else (payload or {}).get("settings")
        settings_identity = settings.get("identity") if isinstance(settings, dict) and isinstance(settings.get("identity"), dict) else {}
        if settings_identity.get("runtimeMode") is not None:
            normalize_regime_runtime_mode(settings_identity.get("runtimeMode"), default=default_mode)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "algorithmId": "regime",
                "reasonCodes": ["regime.api.runtime_mode_rejected"],
                "allowedRuntimeModes": REGIME_ALLOWED_RUNTIME_MODE_VALUES,
                "message": str(exc),
            },
        ) from exc
