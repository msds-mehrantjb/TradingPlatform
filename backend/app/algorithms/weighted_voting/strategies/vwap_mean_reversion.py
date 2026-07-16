from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingMarketSnapshot
from backend.app.algorithms.weighted_voting.models import WeightedSide, WeightedVotingSignal, WeightedVotingStrategyFamily
from backend.app.algorithms.weighted_voting.strategies.common import (
    average_true_range,
    directional_signal,
    hold_signal,
    reject_bad_context,
    trend_strength,
    vwap,
)
from backend.app.algorithms.weighted_voting.strategies.base import WeightedVotingStrategyBase


class VwapMeanReversionStrategy(WeightedVotingStrategyBase):
    strategy_id = "S4"
    name = "VWAP Mean Reversion"
    family = WeightedVotingStrategyFamily.MEAN_REVERSION

    def evaluate(self, snapshot: WeightedVotingMarketSnapshot) -> WeightedVotingSignal:
        context = reject_bad_context(self.strategy_id, self.name, self.family, snapshot, self.config, 30, ("10:00", "15:15"))
        if not hasattr(context, "candles"):
            return context
        current_vwap = vwap(context.candles)
        atr = average_true_range(context.candles)
        if current_vwap is None or atr is None or atr <= 0:
            return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, False, ("weighted_voting.s4.missing_vwap_atr",), "VWAP mean reversion needs VWAP and ATR.")
        if trend_strength(context.candles) > 0.012:
            return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, True, ("weighted_voting.s4.strong_trend_rejected",), "Strong trend environment rejects VWAP mean reversion.")

        latest = context.latest
        previous = context.candles[-2]
        distance = (latest.close - current_vwap) / latest.close
        previous_distance = (previous.close - current_vwap) / previous.close
        accelerating_away = abs(distance) > abs(previous_distance) * context.config.acceleration_reject_ratio
        if accelerating_away:
            return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, True, ("weighted_voting.s4.accelerating_away_from_vwap",), "Price is accelerating away from VWAP, so mean reversion is rejected.")

        if distance <= -context.config.minimum_vwap_distance and latest.close > latest.open and latest.close > previous.close:
            confidence = 0.58 + min(0.14, abs(distance) * 28) + min(0.06, atr / latest.close * 8)
            return directional_signal(self.strategy_id, self.name, self.family, WeightedSide.BUY, snapshot.data_timestamp, confidence, abs(distance), ("weighted_voting.s4.vwap_reversion_buy",), f"Buy VWAP reversion from {distance:.4%} below VWAP with reversal confirmation.", invalidation_level=latest.low)
        if distance >= context.config.minimum_vwap_distance and latest.close < latest.open and latest.close < previous.close:
            confidence = 0.58 + min(0.14, abs(distance) * 28) + min(0.06, atr / latest.close * 8)
            return directional_signal(self.strategy_id, self.name, self.family, WeightedSide.SELL, snapshot.data_timestamp, confidence, abs(distance), ("weighted_voting.s4.vwap_reversion_sell",), f"Sell VWAP reversion from {distance:.4%} above VWAP with reversal confirmation.", invalidation_level=latest.high)
        return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, True, ("weighted_voting.s4.no_vwap_reversion",), "VWAP distance or reversal confirmation is not sufficient.")
