"""Compatibility facade for Voting Ensemble runtime jobs.

The dedicated runtime package owns queueing, status, worker execution, and
recovery. This module remains only for older imports that referenced the first
background job wrapper.
"""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.voting_ensemble.runtime.orchestrator import (
    VOTING_ENSEMBLE_RUNTIME,
    VOTING_ENSEMBLE_RUNTIME_VERSION,
    VotingEnsembleRuntimeOrchestrator,
)
from backend.app.algorithms.voting_ensemble.runtime.status_store import VotingEnsembleJobNotFound, VotingEnsembleJobNotReady
from backend.app.algorithms.voting_ensemble.runtime.worker import VotingEnsembleEvaluator


VOTING_ENSEMBLE_JOB_QUEUE_VERSION = VOTING_ENSEMBLE_RUNTIME_VERSION


class VotingEnsembleEvaluationJobQueue(VotingEnsembleRuntimeOrchestrator):
    """Backward-compatible test helper backed by the dedicated runtime."""

    def __init__(self, *, service: VotingEnsembleEvaluator | None = None, max_workers: int = 1) -> None:
        del max_workers
        super().__init__(service=service, auto_start=False)

    def enqueue_evaluation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.enqueue_manual_evaluation(payload)


VOTING_ENSEMBLE_JOB_QUEUE = VOTING_ENSEMBLE_RUNTIME
