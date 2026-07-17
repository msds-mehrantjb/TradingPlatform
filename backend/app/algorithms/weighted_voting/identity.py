"""Dedicated identity and service-boundary contract for Weighted Voting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


WEIGHTED_VOTING_ALGORITHM_ID: Final[str] = "weighted_voting"
WEIGHTED_VOTING_SERVICE_VERSION: Final[str] = "weighted_voting_service_v2"
WEIGHTED_VOTING_API_NAMESPACE: Final[str] = "/api/weighted-voting"
WEIGHTED_VOTING_API_TAG: Final[str] = "weighted-voting"
WEIGHTED_VOTING_API_VERSION: Final[str] = "weighted_voting_api_v2"
WEIGHTED_VOTING_CONFIGURATION_VERSION: Final[str] = "weighted_voting_config_v1"
WEIGHTED_VOTING_STRATEGY_VERSION: Final[str] = "weighted_voting_catalog_v2"
WEIGHTED_VOTING_ACTIVE_WEIGHT_VERSION: Final[str] = "weighted_weights_v1"
WEIGHTED_VOTING_REASON_CODE_PREFIX: Final[str] = "weighted_voting."
WEIGHTED_VOTING_ERROR_CODE_PREFIX: Final[str] = "weighted_voting."

WEIGHTED_VOTING_INPUT_MODELS: Final[tuple[str, ...]] = (
    "WeightedVotingEvaluateRequest",
    "WeightedVotingConfigUpdateRequest",
    "WeightedVotingBacktestRequest",
    "WeightedVotingDailyUpdateRequest",
)
WEIGHTED_VOTING_OUTPUT_MODELS: Final[tuple[str, ...]] = (
    "WeightedVotingDecision",
    "WeightedVotingSignal",
    "WeightedWeightState",
    "WeightedOrderProposal",
    "WeightedBacktestRun",
    "WeightedArtifactManifest",
)


@dataclass(frozen=True)
class WeightedVotingServiceBoundary:
    algorithm_id: str = WEIGHTED_VOTING_ALGORITHM_ID
    service_version: str = WEIGHTED_VOTING_SERVICE_VERSION
    api_namespace: str = WEIGHTED_VOTING_API_NAMESPACE
    api_tag: str = WEIGHTED_VOTING_API_TAG
    api_version: str = WEIGHTED_VOTING_API_VERSION
    reason_code_namespace: str = WEIGHTED_VOTING_REASON_CODE_PREFIX
    error_code_namespace: str = WEIGHTED_VOTING_ERROR_CODE_PREFIX
    configuration_version: str = WEIGHTED_VOTING_CONFIGURATION_VERSION
    strategy_version: str = WEIGHTED_VOTING_STRATEGY_VERSION
    active_weight_version: str = WEIGHTED_VOTING_ACTIVE_WEIGHT_VERSION
    input_models: tuple[str, ...] = WEIGHTED_VOTING_INPUT_MODELS
    output_models: tuple[str, ...] = WEIGHTED_VOTING_OUTPUT_MODELS


def weighted_voting_service_boundary() -> WeightedVotingServiceBoundary:
    return WeightedVotingServiceBoundary()


def weighted_voting_reason_code(code: str) -> str:
    normalized = str(code).strip()
    if not normalized:
        raise ValueError("Weighted Voting reason code cannot be empty")
    if normalized.startswith(WEIGHTED_VOTING_REASON_CODE_PREFIX):
        return normalized
    return f"{WEIGHTED_VOTING_REASON_CODE_PREFIX}{normalized}"


def is_weighted_voting_reason_code(code: str) -> bool:
    return str(code).startswith(WEIGHTED_VOTING_REASON_CODE_PREFIX)
