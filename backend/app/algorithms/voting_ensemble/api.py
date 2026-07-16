"""HTTP boundary for backend-authoritative Voting Ensemble."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.app.algorithms.voting_ensemble.models import VotingEnsembleEvaluateRequest
from backend.app.algorithms.voting_ensemble.service import VotingEnsembleService


router = APIRouter(prefix="/api/voting-ensemble", tags=["voting-ensemble"])
VOTING_ENSEMBLE_API_SERVICE = VotingEnsembleService()


@router.post("/evaluate", summary="Evaluate Voting Ensemble")
def evaluate(payload: VotingEnsembleEvaluateRequest) -> dict[str, Any]:
    try:
        return VOTING_ENSEMBLE_API_SERVICE.evaluate(payload.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/status", summary="Voting Ensemble status")
def status() -> dict[str, Any]:
    return VOTING_ENSEMBLE_API_SERVICE.status()

