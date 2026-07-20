"""Probability contracts for candidate-conditional Meta-Strategy model output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


CandidateSide = Literal["BUY", "SELL", "HOLD"]
LABELS: tuple[CandidateSide, ...] = ("BUY", "SELL", "HOLD")


@dataclass(frozen=True)
class CandidateConditionalProbability:
    modelId: str
    candidateSide: CandidateSide
    probabilities: dict[str, float]
    calibratedProbabilities: dict[str, float]
    predictedLabel: CandidateSide
    candidateSuccessProbability: float
    calibrationMethod: str
    reasonCodes: tuple[str, ...]


@dataclass(frozen=True)
class CandidateConditionalModelOutput:
    candidate_side: CandidateSide
    probability_of_success: float
    probability_target_first: float
    probability_stop_first: float
    probability_timeout: float
    uncertainty: float
    out_of_distribution_score: float
    source_probabilities: dict[str, float]
    reason_codes: tuple[str, ...]


def normalize_probabilities(probabilities: dict[str, float]) -> dict[str, float]:
    cleaned = {label: max(0.0, float(probabilities.get(label, 0.0))) for label in LABELS}
    total = sum(cleaned.values())
    if total <= 0.0:
        return {"BUY": 0.0, "SELL": 0.0, "HOLD": 1.0}
    return {label: cleaned[label] / total for label in LABELS}


def candidate_success_probability(probabilities: dict[str, float], candidate_side: CandidateSide) -> float:
    normalized = normalize_probabilities(probabilities)
    return normalized[candidate_side]


def probability_label(probabilities: dict[str, float]) -> CandidateSide:
    normalized = normalize_probabilities(probabilities)
    return max(LABELS, key=lambda label: normalized[label])


def candidate_conditional_output(
    *,
    candidate_side: CandidateSide,
    probabilities: dict[str, float],
    uncertainty: float,
    out_of_distribution_score: float,
) -> CandidateConditionalModelOutput:
    normalized = normalize_probabilities(probabilities)
    if candidate_side == "BUY":
        target_first = normalized["BUY"]
        stop_first = normalized["SELL"]
    elif candidate_side == "SELL":
        target_first = normalized["SELL"]
        stop_first = normalized["BUY"]
    else:
        target_first = 0.0
        stop_first = 0.0
    timeout = normalized["HOLD"]
    return CandidateConditionalModelOutput(
        candidate_side=candidate_side,
        probability_of_success=target_first,
        probability_target_first=target_first,
        probability_stop_first=stop_first,
        probability_timeout=timeout,
        uncertainty=max(0.0, min(1.0, float(uncertainty))),
        out_of_distribution_score=max(0.0, min(1.0, float(out_of_distribution_score))),
        source_probabilities=normalized,
        reason_codes=("meta_strategy.model.candidate_conditional_output",),
    )


__all__ = [
    "CandidateConditionalProbability",
    "CandidateConditionalModelOutput",
    "CandidateSide",
    "LABELS",
    "candidate_conditional_output",
    "candidate_success_probability",
    "normalize_probabilities",
    "probability_label",
]
