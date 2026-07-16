from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingMarketSnapshot
from backend.app.algorithms.weighted_voting.models import WeightedDataQualityStatus, WeightedSide, WeightedVotingSignal, WeightedVotingStrategyFamily
from backend.app.algorithms.weighted_voting.strategies.common import (
    average_volume,
    directional_signal,
    hold_signal,
    lower_wick_ratio,
    reject_bad_context,
    upper_wick_ratio,
)
from backend.app.algorithms.weighted_voting.strategies.base import WeightedVotingStrategyBase


class LiquiditySweepReversalStrategy(WeightedVotingStrategyBase):
    strategy_id = "S6"
    name = "Liquidity Sweep Reversal"
    family = WeightedVotingStrategyFamily.REVERSAL

    def evaluate(self, snapshot: WeightedVotingMarketSnapshot) -> WeightedVotingSignal:
        context = reject_bad_context(self.strategy_id, self.name, self.family, snapshot, self.config, 25, ("09:45", "15:30"))
        if not hasattr(context, "candles"):
            return context
        latest = context.latest
        level_window = context.candles[-21:-1]
        if len(level_window) < 10:
            return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, False, ("weighted_voting.s6.no_swing_level",), "Liquidity sweep reversal needs a recent swing high or low.")

        swing_high = max(candle.high for candle in level_window)
        swing_low = min(candle.low for candle in level_window)
        avg_volume = average_volume(context.candles[:-1], 20)
        volume_burst = avg_volume > 0 and latest.volume >= avg_volume * context.config.minimum_volume_ratio
        quote_available = snapshot.bid is not None and snapshot.ask is not None
        proxy_multiplier = 1.0 if quote_available else 0.82
        evidence_label = "quote/volume-confirmed" if quote_available else "candle-only proxy"

        if latest.low < swing_low * (1 - context.config.minimum_breakout_distance) and latest.close > swing_low and lower_wick_ratio(latest) >= context.config.minimum_wick_ratio and volume_burst:
            sweep_depth = (swing_low - latest.low) / swing_low
            confidence = (0.58 + min(0.14, sweep_depth * 70) + min(0.06, lower_wick_ratio(latest) * 0.08)) * proxy_multiplier
            return directional_signal(
                self.strategy_id,
                self.name,
                self.family,
                WeightedSide.BUY,
                snapshot.data_timestamp,
                confidence,
                max(0.0001, sweep_depth),
                ("weighted_voting.s6.liquidity_sweep_buy", "weighted_voting.s6.proxy_mode" if not quote_available else "weighted_voting.s6.quote_available"),
                f"Buy liquidity sweep reversal ({evidence_label}): swept {swing_low:.4f}, rejected from {latest.low:.4f}, close {latest.close:.4f}.",
                data_quality_status=WeightedDataQualityStatus.FULL if quote_available else WeightedDataQualityStatus.PROXY,
                invalidation_level=latest.low,
            )
        if latest.high > swing_high * (1 + context.config.minimum_breakout_distance) and latest.close < swing_high and upper_wick_ratio(latest) >= context.config.minimum_wick_ratio and volume_burst:
            sweep_depth = (latest.high - swing_high) / swing_high
            confidence = (0.58 + min(0.14, sweep_depth * 70) + min(0.06, upper_wick_ratio(latest) * 0.08)) * proxy_multiplier
            return directional_signal(
                self.strategy_id,
                self.name,
                self.family,
                WeightedSide.SELL,
                snapshot.data_timestamp,
                confidence,
                max(0.0001, sweep_depth),
                ("weighted_voting.s6.liquidity_sweep_sell", "weighted_voting.s6.proxy_mode" if not quote_available else "weighted_voting.s6.quote_available"),
                f"Sell liquidity sweep reversal ({evidence_label}): swept {swing_high:.4f}, rejected from {latest.high:.4f}, close {latest.close:.4f}.",
                data_quality_status=WeightedDataQualityStatus.FULL if quote_available else WeightedDataQualityStatus.PROXY,
                invalidation_level=latest.high,
            )
        return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, True, ("weighted_voting.s6.no_sweep_rejection",), "S6 requires wick/level sweep, rejection, and volume evidence; it does not use S5 close-back-inside trigger.")
