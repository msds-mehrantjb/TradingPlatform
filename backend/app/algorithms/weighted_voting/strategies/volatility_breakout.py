from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingMarketSnapshot
from backend.app.algorithms.weighted_voting.models import WeightedSide, WeightedVotingSignal, WeightedVotingStrategyFamily
from backend.app.algorithms.weighted_voting.strategies.common import average_true_range, average_volume, directional_signal, hold_signal, reject_bad_context
from backend.app.algorithms.weighted_voting.strategies.base import WeightedVotingStrategyBase


class VolatilityBreakoutStrategy(WeightedVotingStrategyBase):
    strategy_id = "S8"
    name = "Volatility Breakout"
    family = WeightedVotingStrategyFamily.BREAKOUT

    def evaluate(self, snapshot: WeightedVotingMarketSnapshot) -> WeightedVotingSignal:
        context = reject_bad_context(self.strategy_id, self.name, self.family, snapshot, self.config, 50, ("11:00", "15:30"))
        if not hasattr(context, "candles"):
            return context
        latest = context.latest
        atr = average_true_range(context.candles)
        if atr is None or atr <= 0:
            return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, False, ("weighted_voting.s8.missing_atr",), "Volatility breakout needs ATR warm-up.")
        compression = context.candles[-31:-11]
        expansion = context.candles[-11:-1]
        if len(compression) < 15 or len(expansion) < 8:
            return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, False, ("weighted_voting.s8.insufficient_compression_window",), "Volatility breakout needs completed compression and expansion windows.")

        compression_high = max(candle.high for candle in compression)
        compression_low = min(candle.low for candle in compression)
        compression_mid = (compression_high + compression_low) / 2
        compression_range = (compression_high - compression_low) / compression_mid
        expansion_range = (max(candle.high for candle in expansion) - min(candle.low for candle in expansion)) / latest.close
        avg_volume = average_volume(context.candles[:-1], 20)
        volume_confirmed = avg_volume > 0 and latest.volume >= avg_volume * context.config.minimum_volume_ratio
        compressed = compression_range <= context.config.compression_range_percent
        expanded = expansion_range >= context.config.expansion_range_percent or (latest.high - latest.low) / latest.close >= context.config.expansion_range_percent

        if compressed and expanded and volume_confirmed and latest.close > compression_high * (1 + context.config.minimum_breakout_distance) and latest.close > latest.open:
            breakout_distance = (latest.close - compression_high) / compression_high
            confidence = 0.59 + min(0.14, breakout_distance * 55) + min(0.08, expansion_range * 8)
            return directional_signal(
                self.strategy_id,
                self.name,
                self.family,
                WeightedSide.BUY,
                snapshot.data_timestamp,
                confidence,
                max(0.0001, breakout_distance),
                ("weighted_voting.s8.volatility_breakout_buy",),
                f"Buy volatility breakout from compression range {compression_range:.4%} into expansion {expansion_range:.4%}.",
                invalidation_level=compression_high,
            )
        if compressed and expanded and volume_confirmed and latest.close < compression_low * (1 - context.config.minimum_breakout_distance) and latest.close < latest.open:
            breakout_distance = (compression_low - latest.close) / compression_low
            confidence = 0.59 + min(0.14, breakout_distance * 55) + min(0.08, expansion_range * 8)
            return directional_signal(
                self.strategy_id,
                self.name,
                self.family,
                WeightedSide.SELL,
                snapshot.data_timestamp,
                confidence,
                max(0.0001, breakout_distance),
                ("weighted_voting.s8.volatility_breakout_sell",),
                f"Sell volatility breakout from compression range {compression_range:.4%} into expansion {expansion_range:.4%}.",
                invalidation_level=compression_low,
            )
        return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, True, ("weighted_voting.s8.no_volatility_expansion_breakout",), "S8 needs compression-to-expansion, volume confirmation, and breakout-quality confirmation outside the S1 opening range.")
