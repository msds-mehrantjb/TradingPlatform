"""Base contract for isolated Weighted Voting strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingMarketSnapshot
from backend.app.algorithms.weighted_voting.models import WeightedVotingSignal, WeightedVotingStrategyFamily


class WeightedVotingStrategyBase(ABC):
    strategy_id: str
    name: str
    family: WeightedVotingStrategyFamily

    def __init__(self, config: WeightedVotingConfig | None = None) -> None:
        self.config = config or WeightedVotingConfig()

    @abstractmethod
    def evaluate(self, snapshot: WeightedVotingMarketSnapshot) -> WeightedVotingSignal:
        ...

    def waiting_signal(self, snapshot: WeightedVotingMarketSnapshot) -> WeightedVotingSignal:
        return WeightedVotingSignal(
            strategy_id=self.strategy_id,
            strategy_name=self.name,
            strategy_version="weighted_strategy_skeleton_v1",
            family=self.family,
            signal="Hold",
            p_buy=0.0,
            p_sell=0.0,
            p_hold=1.0,
            expected_return=0.0,
            expected_return_after_costs=0.0,
            strength=0.0,
            final_weight=0.0,
            eligible=False,
            data_ready=False,
            data_timestamp=snapshot.data_timestamp,
            reason_codes=("weighted_voting.strategy_not_implemented",),
            explanation=f"{self.name} is not implemented for backend evaluation at {snapshot.data_timestamp.isoformat()}.",
        )
