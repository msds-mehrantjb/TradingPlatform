"""Risk budget calculations owned by Weighted Voting."""

from __future__ import annotations

from dataclasses import dataclass


WEIGHTED_VOTING_RISK_BUDGET_VERSION = "weighted_voting_risk_budget_v1"


@dataclass(frozen=True)
class WeightedVotingRiskBudget:
    account_equity: float
    risk_percent: float

    @property
    def risk_dollars(self) -> float:
        return max(0.0, self.account_equity * (self.risk_percent / 100.0))
