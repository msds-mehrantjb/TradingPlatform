"""HTTP boundary for backend-authoritative Voting Ensemble."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, status

from backend.app.algorithms.voting_ensemble.models import VotingEnsembleEvaluateRequest
from backend.app.algorithms.voting_ensemble.runtime.events import FinalizedOneMinuteBarEvent
from backend.app.algorithms.voting_ensemble.runtime.orchestrator import VOTING_ENSEMBLE_RUNTIME
from backend.app.algorithms.voting_ensemble.runtime.status_store import VotingEnsembleJobNotFound, VotingEnsembleJobNotReady
from backend.app.algorithms.voting_ensemble.service import VotingEnsembleService, voting_ensemble_service_runtime_bindings


router = APIRouter(prefix="/api/voting-ensemble", tags=["voting-ensemble"])
VOTING_ENSEMBLE_API_SERVICE = VotingEnsembleService()


@router.post("/evaluate", status_code=status.HTTP_202_ACCEPTED, summary="Enqueue Voting Ensemble evaluation")
def evaluate(payload: VotingEnsembleEvaluateRequest) -> dict[str, Any]:
    try:
        job = VOTING_ENSEMBLE_RUNTIME.enqueue_manual_evaluation(payload.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _job_response(job)


@router.post("/evaluate/sync", status_code=status.HTTP_202_ACCEPTED, summary="Compatibility route that enqueues Voting Ensemble evaluation")
def evaluate_sync(payload: VotingEnsembleEvaluateRequest) -> dict[str, Any]:
    try:
        job = VOTING_ENSEMBLE_RUNTIME.enqueue_manual_evaluation(payload.model_dump(mode="json"))
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
        job = VOTING_ENSEMBLE_RUNTIME.enqueue_finalized_bar_event(event)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _job_response(job)


@router.post("/backtests", status_code=status.HTTP_202_ACCEPTED, summary="Enqueue Voting Ensemble backtest")
def backtest(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _job_response(VOTING_ENSEMBLE_RUNTIME.enqueue_backtest(payload))


@router.post("/replay", status_code=status.HTTP_202_ACCEPTED, summary="Enqueue Voting Ensemble replay")
def replay(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _job_response(VOTING_ENSEMBLE_RUNTIME.enqueue_replay(payload))


@router.post("/settings-refresh", status_code=status.HTTP_202_ACCEPTED, summary="Enqueue Voting Ensemble settings refresh")
def settings_refresh(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _job_response(VOTING_ENSEMBLE_RUNTIME.enqueue_settings_refresh(payload))


@router.post("/recovery-reconciliation", status_code=status.HTTP_202_ACCEPTED, summary="Enqueue Voting Ensemble recovery reconciliation")
def recovery_reconciliation(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    return _job_response(VOTING_ENSEMBLE_RUNTIME.enqueue_recovery_reconciliation(payload or {}))


@router.get("/evaluate/jobs/{job_id}", summary="Voting Ensemble evaluation job status")
def evaluation_job_status(job_id: str) -> dict[str, Any]:
    try:
        return VOTING_ENSEMBLE_RUNTIME.get_job(job_id)
    except VotingEnsembleJobNotFound as exc:
        raise HTTPException(status_code=404, detail="Voting Ensemble evaluation job not found") from exc


@router.get("/evaluate/jobs/{job_id}/result", summary="Voting Ensemble evaluation job result")
def evaluation_job_result(job_id: str) -> dict[str, Any]:
    try:
        return VOTING_ENSEMBLE_RUNTIME.get_result(job_id)
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
    payload["runtime"] = VOTING_ENSEMBLE_RUNTIME.summary()
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
            {"method": "GET", "path": "/api/voting-ensemble/inventory/status", "purpose": "Validate authoritative inventory against runtime bindings."},
            {"method": "GET", "path": "/api/voting-ensemble/status", "purpose": "Return Voting Ensemble API and worker status."},
        ],
    }
    return payload


@router.get("/inventory/status", summary="Voting Ensemble authoritative inventory runtime status")
def inventory_status() -> dict[str, Any]:
    runtime = voting_ensemble_service_runtime_bindings()
    payload = runtime["inventoryStatus"]
    payload["runtime"] = VOTING_ENSEMBLE_RUNTIME.summary()
    return payload


@router.get("/runtime/status", summary="Voting Ensemble background runtime status")
def runtime_status() -> dict[str, Any]:
    return VOTING_ENSEMBLE_RUNTIME.summary()


def _job_response(job: dict[str, Any]) -> dict[str, Any]:
    return {
        **job,
        "statusUrl": f"/api/voting-ensemble/jobs/{job['jobId']}",
        "resultUrl": f"/api/voting-ensemble/jobs/{job['jobId']}/result",
        "legacyStatusUrl": f"/api/voting-ensemble/evaluate/jobs/{job['jobId']}",
        "legacyResultUrl": f"/api/voting-ensemble/evaluate/jobs/{job['jobId']}/result",
    }
