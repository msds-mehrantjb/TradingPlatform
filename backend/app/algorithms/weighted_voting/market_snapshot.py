"""Neutral raw-market snapshot contracts for Weighted Voting."""

from backend.app.algorithms.weighted_voting.models import WeightedCandle, WeightedMarketSnapshot


WeightedVotingCandle = WeightedCandle
WeightedVotingMarketSnapshot = WeightedMarketSnapshot

__all__ = ["WeightedCandle", "WeightedMarketSnapshot", "WeightedVotingCandle", "WeightedVotingMarketSnapshot"]
