"""Weighted Voting V2 isolated backend package."""

from backend.app.algorithms.weighted_voting.config import WEIGHTED_VOTING_CONFIG_VERSION, WeightedVotingConfig
from backend.app.algorithms.weighted_voting.service import WEIGHTED_VOTING_SERVICE_VERSION, WeightedVotingService

__all__ = [
    "WEIGHTED_VOTING_CONFIG_VERSION",
    "WEIGHTED_VOTING_SERVICE_VERSION",
    "WeightedVotingConfig",
    "WeightedVotingService",
]
