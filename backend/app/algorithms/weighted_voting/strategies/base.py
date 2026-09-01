"""Base contract for isolated Weighted Voting strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.app.algorithms.weighted_voting.catalog import weighted_voting_catalog_entry
from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingMarketSnapshot
from backend.app.algorithms.weighted_voting.models import WeightedVotingSignal, WeightedVotingStrategyFamily


class WeightedVotingStrategyBase(ABC):
    strategy_id: str
    name: str
    family: WeightedVotingStrategyFamily

    def __init__(self, config: WeightedVotingConfig | None = None) -> None:
        self.config = config or WeightedVotingConfig()

    @property
    def minimum_warmup(self) -> int:
        """Completed candles this strategy needs, as the catalogue declares it.

        Reading it here rather than hardcoding it in each module keeps the published
        inventory honest: what the API advertises is what the strategy enforces.
        """
        return weighted_voting_catalog_entry(self.strategy_id).minimum_warmup

    @property
    def session_window(self) -> tuple[str, str]:
        """Session window this strategy runs in, as the catalogue declares it."""
        return weighted_voting_catalog_entry(self.strategy_id).session_window_bounds

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
