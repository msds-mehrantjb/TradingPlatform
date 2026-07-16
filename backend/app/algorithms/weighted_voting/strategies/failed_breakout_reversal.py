from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingMarketSnapshot
from backend.app.algorithms.weighted_voting.models import WeightedSide, WeightedVotingSignal, WeightedVotingStrategyFamily
from backend.app.algorithms.weighted_voting.strategies.common import average_volume, directional_signal, hold_signal, reject_bad_context
from backend.app.algorithms.weighted_voting.strategies.base import WeightedVotingStrategyBase


class FailedBreakoutReversalStrategy(WeightedVotingStrategyBase):
    strategy_id = "S5"
    name = "Failed Breakout Reversal"
    family = WeightedVotingStrategyFamily.REVERSAL

    def evaluate(self, snapshot: WeightedVotingMarketSnapshot) -> WeightedVotingSignal:
        context = reject_bad_context(self.strategy_id, self.name, self.family, snapshot, self.config, 30, ("10:00", "15:30"))
        if not hasattr(context, "candles"):
            return context
        latest = context.latest
        previous = context.candles[-2]
        level_window = context.candles[-22:-2]
        if len(level_window) < 10:
            return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, False, ("weighted_voting.s5.no_defined_level",), "Failed breakout reversal needs a defined prior range level.")
        prior_high = max(candle.high for candle in level_window)
        prior_low = min(candle.low for candle in level_window)
        avg_volume = average_volume(context.candles[:-1], 20)
        volume_ok = avg_volume > 0 and latest.volume >= avg_volume * 0.85

        if previous.high > prior_high * (1 + context.config.minimum_breakout_distance) and latest.close < prior_high and latest.close < latest.open and volume_ok:
            failure_depth = (prior_high - latest.close) / prior_high
            confidence = 0.59 + min(0.13, failure_depth * 60) + min(0.05, latest.volume / avg_volume * 0.02)
            return directional_signal(
                self.strategy_id,
                self.name,
                self.family,
                WeightedSide.SELL,
                snapshot.data_timestamp,
                confidence,
                max(0.0001, failure_depth),
                ("weighted_voting.s5.failed_upside_breakout_sell",),
                f"Sell failed breakout: tested level {prior_high:.4f}, close back inside {latest.close:.4f}, invalidation above {previous.high:.4f}.",
                invalidation_level=previous.high,
            )
        if previous.low < prior_low * (1 - context.config.minimum_breakout_distance) and latest.close > prior_low and latest.close > latest.open and volume_ok:
            failure_depth = (latest.close - prior_low) / prior_low
            confidence = 0.59 + min(0.13, failure_depth * 60) + min(0.05, latest.volume / avg_volume * 0.02)
            return directional_signal(
                self.strategy_id,
                self.name,
                self.family,
                WeightedSide.BUY,
                snapshot.data_timestamp,
                confidence,
                max(0.0001, failure_depth),
                ("weighted_voting.s5.failed_downside_breakout_buy",),
                f"Buy failed breakout: tested level {prior_low:.4f}, close back inside {latest.close:.4f}, invalidation below {previous.low:.4f}.",
                invalidation_level=previous.low,
            )
        return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, True, ("weighted_voting.s5.no_confirmed_failed_breakout",), "S5 requires a break of a defined level followed by a confirmed close back inside.")
