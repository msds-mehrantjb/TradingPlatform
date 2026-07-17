"""Weighted Voting V2 isolated backend package."""

from backend.app.algorithms.weighted_voting.catalog import (
    WEIGHTED_VOTING_BASELINE_STRATEGY_WEIGHT,
    WEIGHTED_VOTING_MAXIMUM_STRATEGY_WEIGHT,
    WEIGHTED_VOTING_MINIMUM_STRATEGY_WEIGHT,
    WeightedVotingDedicatedStrategyInventoryItem,
    weighted_voting_dedicated_strategy_inventory,
    weighted_voting_enabled_strategy_catalog,
)
from backend.app.algorithms.weighted_voting.config import WEIGHTED_VOTING_CONFIG_VERSION, WeightedVotingConfig
from backend.app.algorithms.weighted_voting.identity import (
    WEIGHTED_VOTING_ACTIVE_WEIGHT_VERSION,
    WEIGHTED_VOTING_ALGORITHM_ID,
    WEIGHTED_VOTING_API_NAMESPACE,
    WEIGHTED_VOTING_API_TAG,
    WEIGHTED_VOTING_API_VERSION,
    WEIGHTED_VOTING_CONFIGURATION_VERSION,
    WEIGHTED_VOTING_ERROR_CODE_PREFIX,
    WEIGHTED_VOTING_REASON_CODE_PREFIX,
    WEIGHTED_VOTING_STRATEGY_VERSION,
    WeightedVotingServiceBoundary,
    weighted_voting_reason_code,
    weighted_voting_service_boundary,
)
from backend.app.algorithms.weighted_voting.service import WEIGHTED_VOTING_SERVICE_VERSION, WeightedVotingService

__all__ = [
    "WEIGHTED_VOTING_ACTIVE_WEIGHT_VERSION",
    "WEIGHTED_VOTING_ALGORITHM_ID",
    "WEIGHTED_VOTING_API_NAMESPACE",
    "WEIGHTED_VOTING_API_TAG",
    "WEIGHTED_VOTING_API_VERSION",
    "WEIGHTED_VOTING_BASELINE_STRATEGY_WEIGHT",
    "WEIGHTED_VOTING_CONFIG_VERSION",
    "WEIGHTED_VOTING_CONFIGURATION_VERSION",
    "WEIGHTED_VOTING_ERROR_CODE_PREFIX",
    "WEIGHTED_VOTING_MAXIMUM_STRATEGY_WEIGHT",
    "WEIGHTED_VOTING_MINIMUM_STRATEGY_WEIGHT",
    "WEIGHTED_VOTING_REASON_CODE_PREFIX",
    "WEIGHTED_VOTING_SERVICE_VERSION",
    "WEIGHTED_VOTING_STRATEGY_VERSION",
    "WeightedVotingConfig",
    "WeightedVotingDedicatedStrategyInventoryItem",
    "WeightedVotingService",
    "WeightedVotingServiceBoundary",
    "weighted_voting_dedicated_strategy_inventory",
    "weighted_voting_enabled_strategy_catalog",
    "weighted_voting_reason_code",
    "weighted_voting_service_boundary",
]
