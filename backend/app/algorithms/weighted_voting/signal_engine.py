"""Signal engine boundary for Weighted Voting strategies."""

from __future__ import annotations

from backend.app.algorithms.weighted_voting.catalog import WEIGHTED_VOTING_STRATEGY_CATALOG
from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingMarketSnapshot
from backend.app.algorithms.weighted_voting.models import WeightedVotingSignal
from backend.app.algorithms.weighted_voting.strategies.bollinger_atr_reversion import BollingerAtrReversionStrategy
from backend.app.algorithms.weighted_voting.strategies.failed_breakout_reversal import FailedBreakoutReversalStrategy
from backend.app.algorithms.weighted_voting.strategies.first_pullback_after_open import FirstPullbackAfterOpenStrategy
from backend.app.algorithms.weighted_voting.strategies.liquidity_sweep_reversal import LiquiditySweepReversalStrategy
from backend.app.algorithms.weighted_voting.strategies.opening_range_breakout import OpeningRangeBreakoutStrategy
from backend.app.algorithms.weighted_voting.strategies.volatility_breakout import VolatilityBreakoutStrategy
from backend.app.algorithms.weighted_voting.strategies.vwap_mean_reversion import VwapMeanReversionStrategy
from backend.app.algorithms.weighted_voting.strategies.vwap_trend_continuation import VwapTrendContinuationStrategy


WEIGHTED_VOTING_SIGNAL_ENGINE_VERSION = "weighted_voting_signal_engine_v1"


WEIGHTED_VOTING_STRATEGY_CLASSES = (
    OpeningRangeBreakoutStrategy,
    FirstPullbackAfterOpenStrategy,
    VwapTrendContinuationStrategy,
    VwapMeanReversionStrategy,
    FailedBreakoutReversalStrategy,
    LiquiditySweepReversalStrategy,
    BollingerAtrReversionStrategy,
    VolatilityBreakoutStrategy,
)


def evaluate_signals(snapshot: WeightedVotingMarketSnapshot, config: WeightedVotingConfig | None = None) -> list[WeightedVotingSignal]:
    active_config = config or WeightedVotingConfig()
    return [strategy_class(active_config).evaluate(snapshot) for strategy_class in WEIGHTED_VOTING_STRATEGY_CLASSES]


def waiting_signals(snapshot: WeightedVotingMarketSnapshot) -> list[WeightedVotingSignal]:
    return [
        WeightedVotingSignal(
            strategy_id=entry.strategy_id,
            strategy_name=entry.name,
            strategy_version="weighted_strategy_skeleton_v1",
            family=entry.family,
            signal="Hold",
            p_buy=0.0,
            p_sell=0.0,
            p_hold=1.0,
            expected_return=0.0,
            expected_return_after_costs=0.0,
            strength=0.0,
            final_weight=1.0 / len(WEIGHTED_VOTING_STRATEGY_CATALOG),
            eligible=False,
            data_ready=False,
            data_timestamp=snapshot.data_timestamp,
            reason_codes=("weighted_voting.strategy_not_implemented",),
            explanation=f"{entry.name} is waiting for backend implementation at {snapshot.data_timestamp.isoformat()}.",
        )
        for entry in WEIGHTED_VOTING_STRATEGY_CATALOG
    ]
