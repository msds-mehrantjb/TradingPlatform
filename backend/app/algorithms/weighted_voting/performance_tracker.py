"""Weighted Voting performance tracking boundary."""

from __future__ import annotations

from dataclasses import dataclass


WEIGHTED_VOTING_PERFORMANCE_TRACKER_VERSION = "weighted_voting_performance_tracker_v1"


@dataclass(frozen=True)
class WeightedVotingPerformanceSnapshot:
    strategy_id: str
    trade_count: int
    recent_expectancy: float | None
    explanation: str
