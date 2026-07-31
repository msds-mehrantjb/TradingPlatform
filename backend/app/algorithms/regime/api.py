"""HTTP boundary for the backend-authoritative Regime runtime."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from backend.app.algorithms.regime.backtest.engine import REGIME_BACKTEST_ENGINE_VERSION
from backend.app.algorithms.regime.contracts import REGIME_ALLOWED_RUNTIME_MODE_VALUES, RegimeRuntimeMode, normalize_regime_runtime_mode
from backend.app.algorithms.regime.execution_pipeline import REGIME_EXECUTION_PIPELINE_MODULES
from backend.app.algorithms.regime.final_acceptance import build_regime_final_acceptance_report
from backend.app.algorithms.regime.rollout import (
    REGIME_OPERATIONAL_ROLLOUT_STAGES,
    RegimePaperPromotionEvidence,
    evaluate_operational_rollout_stage,
    build_regime_paper_readiness_report,
    regime_rollout_status,
)
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
from backend.app.algorithms.regime.strategy_registry import regime_strategy_inventory

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
        "position",
        "currentPosition",
        "inventory",
        "inventorySnapshot",
        "globalRiskCapacityQuantity",
        "dailyPnl",
        "availableRisk",
        "buyingPower",
        "orders",
        "fills",
        "positions",
        "trades",
    }
)
REGIME_EVALUATE_FORBIDDEN_DIRECT_DATA_KEYS = frozenset(
    {
        "marketData",
        "candles",
        "bars",
        "quotes",
        "latestBar",
        "features",
        "classificationInput",
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


@router.post("/evaluate", status_code=202, summary="Regime diagnostic, event-reference enqueue, or persisted-decision explanation")
async def evaluate_regime(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    _validate_regime_transport_payload(payload, default_mode=RegimeRuntimeMode.SHADOW, forbid_direct_market_data=True)
    request_type = str(payload.get("requestType") or payload.get("mode") or payload.get("action") or "").lower()
    decision_id = str(payload.get("decisionId") or payload.get("decision_id") or "")
    finalized_bar_event_id = str(payload.get("finalizedBarEventId") or payload.get("finalisedBarEventId") or payload.get("barEventId") or payload.get("eventId") or "")
    if decision_id or request_type in {"explain", "explain_decision", "decision_explanation"}:
        if not decision_id:
            raise _regime_bad_request("regime.api.evaluate.decision_id_required", "A persisted decision explanation requires decisionId.")
        identity = _identity_from_payload(payload, default_mode=RegimeRuntimeMode.SHADOW)
        decision = REGIME_REPOSITORY.read_decision_snapshot_by_id(identity, decision_id)
        if decision is None:
            raise HTTPException(status_code=404, detail={"algorithmId": "regime", "reasonCodes": ["regime.api.decision_not_found"], "decisionId": decision_id})
        return {
            "algorithmId": "regime",
            "apiVersion": REGIME_API_VERSION,
            "requestType": "persisted_decision_explanation",
            "apiHandlersExecuteAuthoritativeTradingLogic": False,
            "decision": decision,
        }
    if finalized_bar_event_id or request_type in {"enqueue_finalized_bar_reference", "enqueue_finalised_bar_reference", "finalized_bar_reference"}:
        if not finalized_bar_event_id:
            raise _regime_bad_request("regime.api.evaluate.finalized_bar_event_id_required", "Finalized-bar reference enqueue requires finalizedBarEventId.")
        identity = _identity_from_payload(payload, default_mode=RegimeRuntimeMode.PAPER)
        event = REGIME_REPOSITORY.read_runtime_event(identity, finalized_bar_event_id)
        if event is None:
            raise HTTPException(status_code=404, detail={"algorithmId": "regime", "reasonCodes": ["regime.api.finalized_bar_event_not_found"], "eventId": finalized_bar_event_id})
        event_payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
        receipt = await get_regime_runtime_supervisor().publish_completed_bar(event_payload)
        return {
            **receipt,
            "apiVersion": REGIME_API_VERSION,
            "requestType": "finalized_bar_reference_enqueued",
            "apiHandlersExecuteAuthoritativeTradingLogic": False,
            "sourceEventId": finalized_bar_event_id,
        }
    if request_type in {"diagnostic_shadow", "shadow_diagnostic", ""}:
        return _diagnostic_shadow_evaluation(payload)
    raise _regime_bad_request(
        "regime.api.evaluate_request_type_rejected",
        "Regime evaluate accepts only diagnostic_shadow, finalized-bar reference enqueue, or persisted-decision explanation.",
    )


@router.post("/backtests/run", status_code=202, summary="Enqueue a Regime backtest job")
def regime_backtest_run(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    _validate_regime_transport_payload(payload, default_mode=RegimeRuntimeMode.BACKTEST, allow_backtest_result=False)
    receipt = REGIME_JOB_MANAGER.enqueue("backtest", payload)
    return {
        **receipt,
        "apiVersion": REGIME_API_VERSION,
        "apiHandlersExecuteHeavyWorkInline": False,
        "statusEndpoint": f"/api/regime/backtests/jobs/{receipt.get('jobId')}",
        "resultEndpoint": f"/api/regime/backtests/jobs/{receipt.get('jobId')}/result",
    }


@router.get("/backtests/jobs", summary="List Regime backtest jobs")
def regime_backtest_jobs() -> dict[str, Any]:
    return REGIME_JOB_MANAGER.jobs(job_kind="backtest")


@router.get("/backtests/jobs/{job_id}", summary="Poll a Regime backtest job")
def regime_backtest_job_status(job_id: str) -> dict[str, Any]:
    return REGIME_JOB_MANAGER.get(job_id)


@router.get("/backtests/jobs/{job_id}/result", summary="Read a completed Regime backtest result")
def regime_backtest_job_result(job_id: str) -> dict[str, Any]:
    job = REGIME_JOB_MANAGER.get(job_id)
    if str(job.get("status")) != "completed":
        return {**job, "resultAvailable": False, "reasonCodes": ["regime.backtest.result_not_ready"]}
    return {"algorithmId": "regime", "jobId": job_id, "status": job.get("status"), "resultAvailable": True, "result": job.get("result")}


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
    _validate_explicit_settings_activation(payload)
    return REGIME_JOB_MANAGER.enqueue("settings_activation", {**payload, "commandType": "activate_version"})


@router.post("/settings/versions/rollback", status_code=202, summary="Enqueue Regime settings rollback")
def rollback_regime_settings_version(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    _validate_regime_runtime_payload(payload, default_mode=RegimeRuntimeMode.SHADOW)
    _validate_explicit_settings_activation(payload, rollback=True)
    return REGIME_JOB_MANAGER.enqueue("settings_activation", {**payload, "commandType": "rollback_version"})


@router.post("/settings/active", summary="Read the active Regime settings snapshot")
def active_regime_settings(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    _validate_regime_runtime_payload(payload, default_mode=RegimeRuntimeMode.SHADOW)
    return REGIME_SERVICE.active_settings(payload)


@router.get("/settings/active-version", summary="Read active Regime settings version")
def active_regime_settings_version(
    algorithm_instance_id: str = Query("regime-default"),
    account_id: str = Query("default"),
    runtime_mode: str = Query("paper"),
    symbol: str = Query("SPY"),
) -> dict[str, Any]:
    identity = _identity_from_query(algorithm_instance_id, account_id, runtime_mode, symbol)
    settings = REGIME_REPOSITORY.ensure_active_settings_snapshot(identity)
    return {
        "algorithmId": "regime",
        "settingsVersion": settings.get("settingsVersion"),
        "contentHash": settings.get("contentHash"),
        "activationStatus": settings.get("activationStatus"),
        "runtimeMode": identity["runtimeMode"],
        "symbol": identity["symbol"],
    }


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


@router.get("/runtime/last-processed-bar", summary="Read the latest processed Regime bar")
def regime_last_processed_bar() -> dict[str, Any]:
    status = get_regime_runtime_supervisor().status()
    return {
        "algorithmId": "regime",
        "lastProcessedBarByInstanceSymbol": status.get("lastProcessedBarByInstanceSymbol") or {},
        "lastCheckpoint": status.get("lastCheckpoint"),
        "apiHandlersExecuteAuthoritativeTradingLogic": False,
    }


@router.get("/runtime/current-regime", summary="Read current confirmed Regime state")
def current_confirmed_regime() -> dict[str, Any]:
    latest = get_regime_runtime_supervisor().latest_decision().get("decision")
    decision = latest.get("decision") if isinstance(latest, dict) and isinstance(latest.get("decision"), dict) else {}
    confirmed = decision.get("confirmed_state") if isinstance(decision.get("confirmed_state"), dict) else {}
    return {
        "algorithmId": "regime",
        "confirmedRegime": confirmed.get("confirmed_regime") or "unknown",
        "confidence": confirmed.get("regime_confidence") or confirmed.get("transition_confidence"),
        "lastTransitionTimestamp": confirmed.get("last_transition_time"),
        "decisionId": latest.get("decisionId") if isinstance(latest, dict) else None,
    }


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


@router.post("/runtime/kill-switch/activate", status_code=202, summary="Activate the persisted Regime kill switch")
async def activate_regime_kill_switch(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return await get_regime_runtime_supervisor().submit_command("kill_switch_activate", payload)


@router.post("/runtime/kill-switch/deactivate", status_code=202, summary="Deactivate the persisted Regime kill switch")
async def deactivate_regime_kill_switch(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return await get_regime_runtime_supervisor().submit_command("kill_switch_deactivate", payload)


@router.get("/runtime/kill-switch", summary="Read Regime kill-switch state")
def regime_kill_switch_status() -> dict[str, Any]:
    return get_regime_runtime_supervisor().kill_switch_status()


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


@router.get("/strategies/inventory", summary="Read Regime strategy inventory")
def regime_strategies_inventory() -> dict[str, Any]:
    return regime_strategy_inventory()


@router.get("/inventory/current", summary="Read Regime-owned current inventory")
def regime_current_inventory(
    algorithm_instance_id: str = Query("regime-default"),
    account_id: str = Query("default"),
    runtime_mode: str = Query("paper"),
    symbol: str = Query("SPY"),
) -> dict[str, Any]:
    identity = _identity_from_query(algorithm_instance_id, account_id, runtime_mode, symbol)
    return {"algorithmId": "regime", "inventory": REGIME_REPOSITORY.current_inventory_snapshot(identity), "authoritativeSource": "regime_inventory_snapshots"}


@router.get("/orders/open", summary="Read open Regime-owned orders")
def regime_open_orders(
    algorithm_instance_id: str = Query("regime-default"),
    account_id: str = Query("default"),
    runtime_mode: str = Query("paper"),
    symbol: str = Query("SPY"),
) -> dict[str, Any]:
    identity = _identity_from_query(algorithm_instance_id, account_id, runtime_mode, symbol)
    records = REGIME_REPOSITORY.read_owned_records("regime_execution_outbox", identity)
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        order_intent_id = str(record.get("orderIntentId") or record.get("order_intent_id") or "")
        if order_intent_id:
            latest[order_intent_id] = record
    open_statuses = {"created", "risk_approved", "queued", "retry_scheduled", "submitting", "acknowledged", "partially_filled", "cancel_pending", "reconciliation_required", "pending", "submitted"}
    return {
        "algorithmId": "regime",
        "orders": [record for record in latest.values() if str(record.get("processingStatus") or "") in open_statuses],
        "authoritativeSource": "regime_execution_outbox",
    }


@router.get("/reconciliation/status", summary="Read Regime reconciliation status")
def regime_reconciliation_status(
    algorithm_instance_id: str = Query("regime-default"),
    account_id: str = Query("default"),
    runtime_mode: str = Query("paper"),
    symbol: str = Query("SPY"),
) -> dict[str, Any]:
    identity = _identity_from_query(algorithm_instance_id, account_id, runtime_mode, symbol)
    runs = REGIME_REPOSITORY.read_owned_records("regime_reconciliation_runs", identity)
    latest = runs[-1] if runs else get_regime_runtime_supervisor().status().get("latestReconciliation")
    return {"algorithmId": "regime", "reconciliation": latest or {}, "authoritativeSource": "regime_reconciliation_runs"}


@router.get("/decisions/recent", summary="Read recent Regime decisions and blockers")
def recent_regime_decisions(
    algorithm_instance_id: str = Query("regime-default"),
    account_id: str = Query("default"),
    runtime_mode: str = Query("paper"),
    symbol: str = Query("SPY"),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    identity = _identity_from_query(algorithm_instance_id, account_id, runtime_mode, symbol)
    decisions = REGIME_REPOSITORY.read_owned_records("regime_decisions", identity)[-limit:]
    blockers = [_decision_blocker_summary(decision) for decision in decisions]
    return {"algorithmId": "regime", "decisions": decisions, "blockers": blockers, "authoritativeSource": "regime_decisions"}


@router.get("/blockers/recent", summary="Read recent Regime blockers")
def recent_regime_blockers(
    algorithm_instance_id: str = Query("regime-default"),
    account_id: str = Query("default"),
    runtime_mode: str = Query("paper"),
    symbol: str = Query("SPY"),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    return {"algorithmId": "regime", "blockers": recent_regime_decisions(algorithm_instance_id, account_id, runtime_mode, symbol, limit)["blockers"]}


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
    stage = get_regime_runtime_supervisor().rollout_stage()
    promotion_evidence = REGIME_REPOSITORY.read_regime_rollout_promotion_evidence(_identity_from_payload({}, default_mode=RegimeRuntimeMode.SHADOW))
    status["operationalStage"] = stage
    status["operationalStageEvaluation"] = evaluate_operational_rollout_stage(
        str(stage.get("stage") or ""),
        current_stage=str(stage.get("stage") or ""),
        evidence=RegimePaperPromotionEvidence.from_mapping(promotion_evidence),
    )
    status["finalAcceptance"] = build_regime_final_acceptance_report()
    return status


@router.get("/rollout/stage", summary="Read Regime paper rollout stage")
def regime_rollout_stage() -> dict[str, Any]:
    stage = get_regime_runtime_supervisor().rollout_stage()
    return {
        "algorithmId": "regime",
        "paperOnly": True,
        "liveTradingEnabled": False,
        "allowedStages": REGIME_OPERATIONAL_ROLLOUT_STAGES,
        "rolloutStage": stage.get("stage"),
        "status": stage,
    }


@router.post("/rollout/stage", status_code=202, summary="Enqueue an audited Regime rollout-stage command")
async def change_regime_rollout_stage(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    _reject_inline_rollout_evidence(payload)
    stage = str(payload.get("stage") or payload.get("rolloutStage") or "")
    if stage not in REGIME_OPERATIONAL_ROLLOUT_STAGES:
        raise _regime_bad_request("regime.api.rollout_stage_rejected", "Unknown Regime rollout stage.")
    if not str(payload.get("actor") or "") or not str(payload.get("reason") or ""):
        raise _regime_bad_request("regime.api.rollout_stage_requires_audit_metadata", "Rollout stage changes require actor and reason.")
    command = await get_regime_runtime_supervisor().submit_command(
        "set_rollout_stage",
        {"stage": stage, "reason": str(payload.get("reason"))},
        actor=str(payload.get("actor")),
    )
    return {
        **command,
        "jobKind": "rollout_stage_change",
        "apiHandlersExecuteAuthoritativeTradingLogic": False,
        "inlinePromotionEvidenceAccepted": False,
    }


@router.post("/rollout/automatic-paper", status_code=202, summary="Toggle Regime automatic paper trading from the global paper control")
async def set_regime_automatic_paper_trading(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    _reject_inline_rollout_evidence(payload)
    if "enabled" not in payload or not isinstance(payload.get("enabled"), bool):
        raise _regime_bad_request("regime.api.automatic_paper_requires_boolean_enabled", "Regime automatic paper control requires enabled=true or enabled=false.")
    if not str(payload.get("actor") or "") or not str(payload.get("reason") or ""):
        raise _regime_bad_request("regime.api.automatic_paper_requires_audit_metadata", "Regime automatic paper control requires actor and reason.")
    command = await get_regime_runtime_supervisor().submit_command(
        "set_automatic_paper",
        {
            "enabled": bool(payload["enabled"]),
            "reason": str(payload["reason"]),
        },
        actor=str(payload["actor"]),
    )
    control = command.get("automaticPaperControl") if isinstance(command.get("automaticPaperControl"), dict) else {}
    return {
        **command,
        "jobKind": "automatic_paper_control",
        "globalPaperControl": True,
        "manualPaperTradingUnaffected": True,
        "manualPaperTradingWhenMarketOpen": True,
        "paperOnly": True,
        "liveTradingEnabled": False,
        "apiHandlersExecuteAuthoritativeTradingLogic": False,
        "inlinePromotionEvidenceAccepted": False,
        "automaticPaperTradingEnabled": bool(control.get("automaticPaperTradingEnabled")),
        "rolloutStage": control.get("rolloutStage"),
    }


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
    forbid_direct_market_data: bool = False,
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
    direct_data = sorted(key for key in REGIME_EVALUATE_FORBIDDEN_DIRECT_DATA_KEYS if forbid_direct_market_data and key in (payload or {}))
    if direct_data:
        raise HTTPException(
            status_code=400,
            detail={
                "algorithmId": "regime",
                "reasonCodes": ["regime.api.evaluate_direct_market_data_rejected"],
                "forbiddenKeys": direct_data,
                "message": "Regime evaluate cannot execute authoritative decisions from caller-supplied market data; submit a trusted finalized-bar reference instead.",
            },
        )


def _reject_inline_rollout_evidence(payload: dict[str, Any]) -> None:
    forbidden = sorted(
        key
        for key in (
            "evidence",
            "promotionEvidence",
            "rolloutEvidence",
            "paperStabilityEvidence",
            "testResults",
            "acceptanceResults",
        )
        if key in (payload or {})
    )
    if forbidden:
        raise HTTPException(
            status_code=400,
            detail={
                "algorithmId": "regime",
                "reasonCodes": ["regime.api.rollout_inline_evidence_rejected"],
                "forbiddenKeys": forbidden,
                "message": "Regime rollout promotion uses backend-recorded evidence only; API callers cannot provide promotion evidence.",
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


def _validate_explicit_settings_activation(payload: dict[str, Any], *, rollback: bool = False) -> None:
    version = payload.get("targetSettingsVersion") if rollback else payload.get("settingsVersion") or payload.get("targetSettingsVersion")
    actor = payload.get("actor") or payload.get("createdBy") or payload.get("source")
    reason = payload.get("reason") or payload.get("activationReason") or payload.get("rollbackReason")
    missing = []
    if not version:
        missing.append("targetSettingsVersion" if rollback else "settingsVersion")
    if not actor:
        missing.append("actor")
    if not reason:
        missing.append("reason")
    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                "algorithmId": "regime",
                "reasonCodes": ["regime.api.settings_activation_requires_explicit_audit_metadata"],
                "missingKeys": missing,
                "message": "Regime settings activation or rollback must be explicit, audited, and backend-validated.",
            },
        )


def _diagnostic_shadow_evaluation(payload: dict[str, Any]) -> dict[str, Any]:
    identity = _identity_from_payload(payload, default_mode=RegimeRuntimeMode.SHADOW)
    latest_decisions = REGIME_REPOSITORY.read_owned_records("regime_decisions", identity)
    latest_decision = latest_decisions[-1] if latest_decisions else get_regime_runtime_supervisor().latest_decision().get("decision")
    settings = REGIME_REPOSITORY.ensure_active_settings_snapshot(identity)
    return {
        "algorithmId": "regime",
        "apiVersion": REGIME_API_VERSION,
        "requestType": "diagnostic_shadow",
        "runtimeMode": identity["runtimeMode"],
        "symbol": identity["symbol"],
        "settingsVersion": settings.get("settingsVersion"),
        "decision": latest_decision,
        "apiHandlersExecuteAuthoritativeTradingLogic": False,
        "reasonCodes": ["regime.api.diagnostic_shadow_repository_loaded_state"],
    }


def _identity_from_payload(payload: dict[str, Any], *, default_mode: RegimeRuntimeMode) -> dict[str, str]:
    identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else payload
    mode = normalize_regime_runtime_mode(identity.get("runtimeMode") or identity.get("runtime_mode"), default=default_mode).value
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": str(identity.get("algorithmInstanceId") or identity.get("algorithm_instance_id") or "regime-default"),
        "accountId": str(identity.get("accountId") or identity.get("account_id") or "default"),
        "runtimeMode": mode,
        "symbol": str(identity.get("symbol") or "SPY").upper(),
    }


def _identity_from_query(algorithm_instance_id: str, account_id: str, runtime_mode: str, symbol: str) -> dict[str, str]:
    mode = normalize_regime_runtime_mode(runtime_mode, default=RegimeRuntimeMode.PAPER).value
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": algorithm_instance_id,
        "accountId": account_id,
        "runtimeMode": mode,
        "symbol": symbol.upper(),
    }


def _decision_blocker_summary(decision: dict[str, Any]) -> dict[str, Any]:
    nested = decision.get("decision") if isinstance(decision.get("decision"), dict) else decision
    return {
        "decisionId": decision.get("decisionId") or nested.get("decision_id"),
        "dataTimestamp": decision.get("dataTimestamp") or nested.get("data_timestamp"),
        "signal": nested.get("signal") or nested.get("aggregate_signal"),
        "tradeAllowed": nested.get("trade_allowed"),
        "blockers": nested.get("trade_blockers") or nested.get("blockers") or decision.get("reasonCodes") or [],
    }


def _regime_bad_request(reason_code: str, message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"algorithmId": "regime", "reasonCodes": [reason_code], "message": message})
