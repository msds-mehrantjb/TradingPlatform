"""HTTP boundary for backend-authoritative Voting Ensemble."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException, status
from pydantic import BaseModel, ConfigDict

from backend.app.algorithms.voting_ensemble.models import VotingEnsembleEvaluateRequest
from backend.app.algorithms.voting_ensemble.runtime.events import FinalizedOneMinuteBarEvent
from backend.app.algorithms.voting_ensemble.runtime.orchestrator import VOTING_ENSEMBLE_RUNTIME
from backend.app.algorithms.voting_ensemble.runtime.status_store import VotingEnsembleJobNotFound, VotingEnsembleJobNotReady
from backend.app.algorithms.voting_ensemble.runtime_supervisor import get_voting_ensemble_runtime_supervisor
from backend.app.algorithms.voting_ensemble.service import VotingEnsembleService, voting_ensemble_service_runtime_bindings


router = APIRouter(prefix="/api/voting-ensemble", tags=["voting-ensemble"])
VOTING_ENSEMBLE_API_SERVICE = VotingEnsembleService()


class VotingEnsembleRuntimeControlUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requestedPaperTradingEnabled: bool
    localEntryBlockActive: bool | None = None
    localEntryBlockReasonCodes: list[str] | None = None


@router.post("/evaluate", status_code=status.HTTP_202_ACCEPTED, summary="Enqueue Voting Ensemble evaluation")
def evaluate(payload: VotingEnsembleEvaluateRequest) -> dict[str, Any]:
    try:
        job = _runtime_boundary().enqueue_manual_evaluation(_backend_authoritative_evaluate_payload(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _job_response(job)


@router.post("/evaluate/sync", status_code=status.HTTP_202_ACCEPTED, summary="Compatibility route that enqueues Voting Ensemble evaluation")
def evaluate_sync(payload: VotingEnsembleEvaluateRequest) -> dict[str, Any]:
    try:
        job = _runtime_boundary().enqueue_manual_evaluation(_backend_authoritative_evaluate_payload(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _job_response(
        {
            **job,
            "reasonCodes": ["voting_ensemble.api.evaluate_sync.enqueued_for_backward_compatibility"],
        }
    )


@router.post("/events/finalized-bars", status_code=status.HTTP_202_ACCEPTED, summary="Enqueue finalised one-minute-bar event")
def finalized_bar_event(event: FinalizedOneMinuteBarEvent) -> dict[str, Any]:
    try:
        job = _runtime_boundary().enqueue_finalized_bar_event(event)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _job_response(job)


@router.post("/backtests", status_code=status.HTTP_202_ACCEPTED, summary="Enqueue Voting Ensemble backtest")
def backtest(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _job_response(_runtime_boundary().enqueue_backtest(payload))


@router.post("/replay", status_code=status.HTTP_202_ACCEPTED, summary="Enqueue Voting Ensemble replay")
def replay(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _job_response(_runtime_boundary().enqueue_replay(payload))


@router.post("/settings-refresh", status_code=status.HTTP_202_ACCEPTED, summary="Enqueue Voting Ensemble settings refresh")
def settings_refresh(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _job_response(_runtime_boundary().enqueue_settings_refresh(payload))


@router.post("/recovery-reconciliation", status_code=status.HTTP_202_ACCEPTED, summary="Enqueue Voting Ensemble recovery reconciliation")
def recovery_reconciliation(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    return _job_response(_runtime_boundary().enqueue_recovery_reconciliation(payload or {}))


@router.get("/runtime/control", summary="Voting Ensemble runtime control")
def runtime_control() -> dict[str, Any]:
    return get_voting_ensemble_runtime_supervisor().control_status(refresh_readiness=False)


@router.put("/runtime/control", summary="Update Voting Ensemble requested paper control")
def update_runtime_control(payload: VotingEnsembleRuntimeControlUpdate) -> dict[str, Any]:
    return get_voting_ensemble_runtime_supervisor().update_control(
        requested_paper_trading_enabled=payload.requestedPaperTradingEnabled,
        clear_local_entry_block=payload.localEntryBlockActive is False,
        updated_by="api",
        refresh_readiness=False,
    )


@router.get("/runtime/paper-inventory", summary="Voting Ensemble paper orders, fills, and positions")
def runtime_paper_inventory() -> dict[str, Any]:
    return get_voting_ensemble_runtime_supervisor().paper_inventory()


@router.get("/evaluate/jobs/{job_id}", summary="Voting Ensemble evaluation job status")
def evaluation_job_status(job_id: str) -> dict[str, Any]:
    try:
        return _runtime_boundary().get_job(job_id)
    except VotingEnsembleJobNotFound as exc:
        raise HTTPException(status_code=404, detail="Voting Ensemble evaluation job not found") from exc


@router.get("/evaluate/jobs/{job_id}/result", summary="Voting Ensemble evaluation job result")
def evaluation_job_result(job_id: str) -> dict[str, Any]:
    try:
        return _runtime_boundary().get_result(job_id)
    except VotingEnsembleJobNotFound as exc:
        raise HTTPException(status_code=404, detail="Voting Ensemble evaluation job not found") from exc
    except VotingEnsembleJobNotReady as exc:
        raise HTTPException(status_code=409, detail="Voting Ensemble evaluation job is not complete") from exc


@router.get("/jobs/{job_id}", summary="Voting Ensemble runtime job status")
def runtime_job_status(job_id: str) -> dict[str, Any]:
    return evaluation_job_status(job_id)


@router.get("/jobs/{job_id}/result", summary="Voting Ensemble runtime job result")
def runtime_job_result(job_id: str) -> dict[str, Any]:
    return evaluation_job_result(job_id)


@router.get("/status", summary="Voting Ensemble status")
def status() -> dict[str, Any]:
    payload = VOTING_ENSEMBLE_API_SERVICE.status()
    supervisor = get_voting_ensemble_runtime_supervisor()
    payload["runtime"] = _runtime_boundary().summary()
    payload["supervisor"] = supervisor.status()
    payload["evaluationJobs"] = payload["runtime"]
    payload["apiInventory"] = {
        "endpoints": [
            {"method": "POST", "path": "/api/voting-ensemble/evaluate", "purpose": "Enqueue an evaluation job and return a job identifier."},
            {"method": "GET", "path": "/api/voting-ensemble/evaluate/jobs/{job_id}", "purpose": "Poll evaluation job status."},
            {"method": "GET", "path": "/api/voting-ensemble/evaluate/jobs/{job_id}/result", "purpose": "Fetch completed evaluation result."},
            {"method": "POST", "path": "/api/voting-ensemble/events/finalized-bars", "purpose": "Enqueue finalised one-minute-bar event evaluation."},
            {"method": "POST", "path": "/api/voting-ensemble/backtests", "purpose": "Enqueue lower-priority backtest job."},
            {"method": "POST", "path": "/api/voting-ensemble/replay", "purpose": "Enqueue lower-priority replay job."},
            {"method": "POST", "path": "/api/voting-ensemble/settings-refresh", "purpose": "Enqueue settings refresh command."},
            {"method": "POST", "path": "/api/voting-ensemble/recovery-reconciliation", "purpose": "Enqueue recovery and reconciliation command."},
            {"method": "POST", "path": "/api/voting-ensemble/evaluate/sync", "purpose": "Backward-compatible path that now enqueues instead of evaluating inline."},
            {"method": "GET", "path": "/api/voting-ensemble/runtime/control", "purpose": "Return backend-authoritative Voting Ensemble paper control."},
            {"method": "PUT", "path": "/api/voting-ensemble/runtime/control", "purpose": "Request Voting Ensemble paper trading on or off."},
            {"method": "GET", "path": "/api/voting-ensemble/runtime/status", "purpose": "Return Voting Ensemble supervisor, readiness, and worker health."},
            {"method": "GET", "path": "/api/voting-ensemble/runtime/paper-inventory", "purpose": "Return Voting Ensemble backend-owned paper orders, fills, and positions."},
            {"method": "GET", "path": "/api/voting-ensemble/inventory/status", "purpose": "Validate authoritative inventory against runtime bindings."},
            {"method": "GET", "path": "/api/voting-ensemble/status", "purpose": "Return Voting Ensemble API and worker status."},
        ],
    }
    return payload


@router.get("/inventory/status", summary="Voting Ensemble authoritative inventory runtime status")
def inventory_status() -> dict[str, Any]:
    runtime = voting_ensemble_service_runtime_bindings()
    payload = runtime["inventoryStatus"]
    payload["runtime"] = _runtime_boundary().summary()
    payload["supervisor"] = get_voting_ensemble_runtime_supervisor().status()
    return payload


@router.get("/runtime/status", summary="Voting Ensemble background runtime status")
def runtime_status() -> dict[str, Any]:
    return get_voting_ensemble_runtime_supervisor().status()


def _runtime_boundary() -> Any:
    supervisor = get_voting_ensemble_runtime_supervisor()
    if supervisor.runtime is VOTING_ENSEMBLE_RUNTIME:
        return supervisor
    return VOTING_ENSEMBLE_RUNTIME


def _backend_authoritative_evaluate_payload(payload: VotingEnsembleEvaluateRequest) -> dict[str, Any]:
    enriched = payload.model_dump(mode="json")
    market_context = dict(enriched.get("market_context") or {})
    supervisor = get_voting_ensemble_runtime_supervisor()
    control = supervisor.control_status(refresh_readiness=False)
    inventory = supervisor.paper_inventory()
    account_risk = _local_account_risk_snapshot(inventory, payload)
    if account_risk:
        market_context["accountRiskSnapshot"] = account_risk
    global_gate = _backend_global_gate(control, payload)
    operational = dict(market_context.get("operationalHealthSnapshot") or {})
    operational.update(
        {
            "globalGateDecision": global_gate,
            "upstreamGlobalGateDecision": global_gate,
            "authoritativeInventory": inventory,
            "backendRuntimeControl": control,
            "permissionContractVersion": "voting_ensemble_backend_authoritative_permission_contract_v1",
        }
    )
    market_context["operationalHealthSnapshot"] = operational
    market_context["globalGateDecision"] = global_gate
    market_context["upstreamGlobalGateDecision"] = global_gate
    market_context["authoritativeInventory"] = inventory
    enriched["market_context"] = market_context
    return enriched


def _local_account_risk_snapshot(inventory: dict[str, Any], payload: VotingEnsembleEvaluateRequest) -> dict[str, Any] | None:
    account = None
    if isinstance(inventory, dict):
        for key in ("localPaperAccount", "account"):
            candidate = inventory.get(key)
            if isinstance(candidate, dict):
                account = candidate
                break
    if not isinstance(account, dict):
        return None
    equity = _number(account.get("equity"))
    realized = _number(account.get("realizedPnlToday"))
    if equity is None or realized is None:
        return None
    open_notional = _number(account.get("openPositionNotional")) or 0.0
    total_spy_notional_percent = _percent(open_notional, equity)
    drawdown = _number(account.get("drawdownFromIntradayHighPercent"))
    if drawdown is None:
        drawdown = _number(account.get("drawdownPercent")) or 0.0
    observed_at = str(account.get("observedAt") or _payload_timestamp(payload).isoformat().replace("+00:00", "Z"))
    return {
        "algorithmId": "voting_ensemble",
        "algorithm_id": "voting_ensemble",
        "capitalPartitionId": str(account.get("capitalPartitionId") or "voting_ensemble.paper.default"),
        "accountId": str(account.get("accountId") or "voting_ensemble.paper.default.account"),
        "equity": equity,
        "buyingPower": _number(account.get("buyingPower")) or equity,
        "openPositionNotional": open_notional,
        "realizedPnlToday": realized,
        "unrealizedPnlToday": _number(account.get("unrealizedPnlToday")) or _number(account.get("unrealizedPnl")) or 0.0,
        "dailyNetPnlAfterExitCosts": _number(account.get("dailyNetPnlAfterExitCosts")) or _number(account.get("dailyNetPnl")) or 0.0,
        "intradayEquityHigh": _number(account.get("intradayEquityHigh")) or equity,
        "drawdownPercent": drawdown,
        "drawdownFromIntradayHighPercent": drawdown,
        "totalOpenRiskPercent": _number(account.get("totalOpenRiskPercent")) or 0.0,
        "totalSpyNotionalPercent": _number(account.get("totalSpyNotionalPercent")) or total_spy_notional_percent,
        "sameDirectionExposurePercent": _number(account.get("sameDirectionExposurePercent")) or total_spy_notional_percent,
        "estimatedExitCosts": _number(account.get("estimatedExitCosts")) or 0.0,
        "tradesToday": int(_number(account.get("tradesToday")) or 0),
        "observedAt": observed_at,
        "sessionDate": str(account.get("sessionDate") or _payload_timestamp(payload).date()),
        "sourceAuthority": "voting_ensemble.local_paper_account",
        "paperAccount": True,
        "localPaperAccount": True,
        "externalBrokerAccount": False,
    }


def _backend_global_gate(control: dict[str, Any], payload: VotingEnsembleEvaluateRequest) -> dict[str, Any]:
    timestamp = _payload_timestamp(payload)
    eligible = bool(control.get("newEntriesEnabled"))
    return {
        "status": "PASS" if eligible else "FAIL",
        "eligible": eligible,
        "dataReady": True,
        "gateResults": [],
        "reasonCodes": ["voting_ensemble.backend_global_gate.passed"] if eligible else ["voting_ensemble.backend_global_gate.blocked", *list(control.get("reasonCodes") or [])],
        "explanation": "Backend-authoritative Voting Ensemble runtime control was evaluated for a manual dashboard request.",
        "checkedAt": timestamp.isoformat().replace("+00:00", "Z"),
        "sessionDate": str(timestamp.date()),
        "configurationHash": f"voting_ensemble_backend_global_gate:{control.get('controlVersion') or 'runtime_control'}:{eligible}",
    }


def _payload_timestamp(payload: VotingEnsembleEvaluateRequest) -> datetime:
    value = payload.data_timestamp
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _percent(numerator: float, denominator: float) -> float:
    return 0.0 if denominator <= 0 else round((numerator / denominator) * 100.0, 6)


def _job_response(job: dict[str, Any]) -> dict[str, Any]:
    return {
        **job,
        "statusUrl": f"/api/voting-ensemble/jobs/{job['jobId']}",
        "resultUrl": f"/api/voting-ensemble/jobs/{job['jobId']}/result",
        "legacyStatusUrl": f"/api/voting-ensemble/evaluate/jobs/{job['jobId']}",
        "legacyResultUrl": f"/api/voting-ensemble/evaluate/jobs/{job['jobId']}/result",
    }
