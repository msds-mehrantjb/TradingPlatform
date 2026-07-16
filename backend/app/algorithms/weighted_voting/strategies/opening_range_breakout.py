from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingMarketSnapshot
from backend.app.algorithms.weighted_voting.models import WeightedSide, WeightedVotingSignal, WeightedVotingStrategyFamily
from backend.app.algorithms.weighted_voting.strategies.common import (
    average_true_range,
    average_volume,
    directional_signal,
    hold_signal,
    opening_range,
    reject_bad_context,
)
from backend.app.algorithms.weighted_voting.strategies.base import WeightedVotingStrategyBase


class OpeningRangeBreakoutStrategy(WeightedVotingStrategyBase):
    strategy_id = "S1"
    name = "Opening Range Breakout"
    family = WeightedVotingStrategyFamily.BREAKOUT

    def evaluate(self, snapshot: WeightedVotingMarketSnapshot) -> WeightedVotingSignal:
        context = reject_bad_context(self.strategy_id, self.name, self.family, snapshot, self.config, 15, ("09:45", "11:00"))
        if not hasattr(context, "candles"):
            return context
        range_ = opening_range(context.candles, self.config.opening_range_minutes)
        atr = average_true_range(context.candles)
        if range_ is None or atr is None:
            return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, False, ("weighted_voting.s1.missing_opening_range",), "Opening range or ATR is unavailable.")

        latest = context.latest
        prior_volume = average_volume(context.candles[:-1], 20)
        volume_confirmed = prior_volume > 0 and latest.volume >= prior_volume * self.config.minimum_volume_ratio
        buy_distance = (latest.close - range_.high) / range_.high
        sell_distance = (range_.low - latest.close) / range_.low
        buy_extension = (latest.close - range_.high) / atr
        sell_extension = (range_.low - latest.close) / atr

        if buy_distance >= self.config.minimum_breakout_distance and volume_confirmed and 0 <= buy_extension <= self.config.maximum_opening_extension_atr:
            confidence = 0.58 + min(0.16, buy_distance * 45) + min(0.08, (latest.volume / prior_volume - 1) * 0.08)
            return directional_signal(
                self.strategy_id,
                self.name,
                self.family,
                WeightedSide.BUY,
                snapshot.data_timestamp,
                confidence,
                buy_distance,
                ("weighted_voting.s1.opening_range_breakout_buy",),
                f"Buy breakout above opening range high {range_.high:.4f}; distance {buy_distance:.4%}, extension {buy_extension:.2f} ATR.",
                invalidation_level=range_.high,
            )
        if sell_distance >= self.config.minimum_breakout_distance and volume_confirmed and 0 <= sell_extension <= self.config.maximum_opening_extension_atr:
            confidence = 0.58 + min(0.16, sell_distance * 45) + min(0.08, (latest.volume / prior_volume - 1) * 0.08)
            return directional_signal(
                self.strategy_id,
                self.name,
                self.family,
                WeightedSide.SELL,
                snapshot.data_timestamp,
                confidence,
                sell_distance,
                ("weighted_voting.s1.opening_range_breakout_sell",),
                f"Sell breakout below opening range low {range_.low:.4f}; distance {sell_distance:.4%}, extension {sell_extension:.2f} ATR.",
                invalidation_level=range_.low,
            )
        return hold_signal(
            self.strategy_id,
            self.name,
            self.family,
            snapshot.data_timestamp,
            True,
            ("weighted_voting.s1.no_confirmed_opening_breakout",),
            "Opening-range breakout needs distance, volume confirmation, and acceptable ATR extension.",
        )
