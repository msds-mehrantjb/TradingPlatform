"""Primary WCA voter registry.

Each registered voter is the single dedicated strategy class implementation
for its strategy ID. Do not duplicate strategy logic in this module.
"""

from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaStrategyEvaluation
from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategy
from backend.app.algorithms.wca.strategies.bollinger_atr_reversion import BollingerAtrReversionStrategy
from backend.app.algorithms.wca.strategies.failed_breakout_reversal import FailedBreakoutReversalStrategy
from backend.app.algorithms.wca.strategies.gap_continuation_fade import GapContinuationFadeStrategy
from backend.app.algorithms.wca.strategies.intraday_volatility_breakout import IntradayVolatilityBreakoutStrategy
from backend.app.algorithms.wca.strategies.liquidity_sweep_reversal import LiquiditySweepReversalStrategy
from backend.app.algorithms.wca.strategies.moving_average_trend import MovingAverageTrendStrategy
from backend.app.algorithms.wca.strategies.opening_range_breakout import OpeningRangeBreakoutStrategy
from backend.app.algorithms.wca.strategies.rsi_mean_reversion import RsiMeanReversionStrategy
from backend.app.algorithms.wca.strategies.trend_pullback import TrendPullbackStrategy
from backend.app.algorithms.wca.strategies.vwap_mean_reversion import VwapMeanReversionStrategy
from backend.app.algorithms.wca.strategies.vwap_trend_continuation import VwapTrendContinuationStrategy


WCA_PRIMARY_VOTERS: tuple[WcaStrategy, ...] = (
    MovingAverageTrendStrategy(),
    TrendPullbackStrategy(),
    VwapTrendContinuationStrategy(),
    VwapMeanReversionStrategy(),
    RsiMeanReversionStrategy(),
    BollingerAtrReversionStrategy(),
    OpeningRangeBreakoutStrategy(),
    IntradayVolatilityBreakoutStrategy(),
    FailedBreakoutReversalStrategy(),
    LiquiditySweepReversalStrategy(),
    GapContinuationFadeStrategy(),
)


def evaluate_all_primary_voters(
    snapshot: WcaMarketSnapshot,
    config: StrategyConfig = StrategyConfig(),
) -> tuple[WcaStrategyEvaluation, ...]:
    return tuple(voter.evaluate(snapshot, config) for voter in WCA_PRIMARY_VOTERS)


__all__ = ("WCA_PRIMARY_VOTERS", "evaluate_all_primary_voters")
