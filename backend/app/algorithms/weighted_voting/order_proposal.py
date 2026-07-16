"""Weighted Voting order proposal boundary."""

from backend.app.algorithms.weighted_voting.models import WeightedOrderProposal

WEIGHTED_VOTING_ORDER_PROPOSAL_VERSION = "weighted_voting_order_proposal_v1"


WeightedVotingOrderProposal = WeightedOrderProposal

__all__ = ["WEIGHTED_VOTING_ORDER_PROPOSAL_VERSION", "WeightedOrderProposal", "WeightedVotingOrderProposal"]
