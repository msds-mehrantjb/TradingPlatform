"""Common model interface for Meta-Strategy models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from backend.app.algorithms.meta_strategy.models.calibration import apply_meta_strategy_calibration
from backend.app.algorithms.meta_strategy.models.probability_contract import (
    CandidateConditionalProbability,
    CandidateSide,
    candidate_success_probability,
    normalize_probabilities,
    probability_label,
)


ModelRole = Literal["champion", "challenger"]


@dataclass
class MetaStrategyModelBase(ABC):
    model_id: str
    role: ModelRole
    kind: str
    calibration: dict[str, Any] | None = None
    fitted_payload: dict[str, Any] = field(default_factory=dict)

    @abstractmethod
    def fit(self, rows: list[dict[str, Any]], feature_names: list[str]) -> "MetaStrategyModelBase":
        raise NotImplementedError

    @abstractmethod
    def predict_probabilities(self, features: dict[str, float]) -> dict[str, float]:
        raise NotImplementedError

    def predict_candidate(
        self,
        features: dict[str, float],
        *,
        candidate_side: CandidateSide,
    ) -> CandidateConditionalProbability:
        raw = normalize_probabilities(self.predict_probabilities(features))
        calibrated = apply_meta_strategy_calibration(raw, self.calibration)
        return CandidateConditionalProbability(
            modelId=self.model_id,
            candidateSide=candidate_side,
            probabilities=raw,
            calibratedProbabilities=calibrated,
            predictedLabel=probability_label(calibrated),
            candidateSuccessProbability=candidate_success_probability(calibrated, candidate_side),
            calibrationMethod=str((self.calibration or {}).get("method") or "none"),
            reasonCodes=("meta_strategy.model.candidate_conditional_probability",),
        )

    @property
    def available(self) -> bool:
        return bool(self.fitted_payload.get("available", True))


__all__ = ["MetaStrategyModelBase", "ModelRole"]
