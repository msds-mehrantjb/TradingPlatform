from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingMarketSnapshot
from backend.app.algorithms.weighted_voting.models import WeightedSide, WeightedVotingSignal, WeightedVotingStrategyFamily
from backend.app.algorithms.weighted_voting.strategies.common import (
    directional_signal,
    hold_signal,
    reject_bad_context,
    simple_moving_average,
    slope,
    vwap,
)
from backend.app.algorithms.weighted_voting.strategies.base import WeightedVotingStrategyBase


class VwapTrendContinuationStrategy(WeightedVotingStrategyBase):
    strategy_id = "S3"
    name = "VWAP Trend Continuation"
    family = WeightedVotingStrategyFamily.TREND

    def evaluate(self, snapshot: WeightedVotingMarketSnapshot) -> WeightedVotingSignal:
        context = reject_bad_context(self.strategy_id, self.name, self.family, snapshot, self.config, 50, ("10:00", "15:30"))
        if not hasattr(context, "candles"):
            return context
        closes = [candle.close for candle in context.candles]
        current_vwap = vwap(context.candles)
        fast = simple_moving_average(closes, 8)
        slow = simple_moving_average(closes, 21)
        if current_vwap is None or fast is None or slow is None:
            return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, False, ("weighted_voting.s3.missing_trend_inputs",), "VWAP trend continuation needs VWAP and moving-average warm-up.")

        latest = context.latest
        vwap_slope = _vwap_slope(context.candles)
        recent = context.candles[-6:]
        touched_vwap = any(candle.low <= current_vwap * 1.0015 <= candle.high or candle.high >= current_vwap * 0.9985 >= candle.low for candle in recent[:-1])
        continuation_up = latest.close > max(candle.close for candle in recent[:-1])
        continuation_down = latest.close < min(candle.close for candle in recent[:-1])

        if latest.close > current_vwap and fast > slow and vwap_slope > 0.00015 and (touched_vwap or continuation_up):
            confidence = 0.58 + min(0.12, slope(closes, 12) * 45) + (0.05 if touched_vwap else 0.02)
            return directional_signal(
                self.strategy_id,
                self.name,
                self.family,
                WeightedSide.BUY,
                snapshot.data_timestamp,
                confidence,
                max(0.0001, (latest.close - current_vwap) / latest.close),
                ("weighted_voting.s3.vwap_trend_continuation_buy",),
                "Buy continuation: VWAP slope, moving-average trend, and pullback/continuation confirmation align.",
                invalidation_level=current_vwap,
            )
        if latest.close < current_vwap and fast < slow and vwap_slope < -0.00015 and (touched_vwap or continuation_down):
            confidence = 0.58 + min(0.12, abs(slope(closes, 12)) * 45) + (0.05 if touched_vwap else 0.02)
            return directional_signal(
                self.strategy_id,
                self.name,
                self.family,
                WeightedSide.SELL,
                snapshot.data_timestamp,
                confidence,
                max(0.0001, (current_vwap - latest.close) / latest.close),
                ("weighted_voting.s3.vwap_trend_continuation_sell",),
                "Sell continuation: VWAP slope, moving-average trend, and pullback/continuation confirmation align.",
                invalidation_level=current_vwap,
            )
        return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, True, ("weighted_voting.s3.no_vwap_continuation",), "VWAP slope, trend alignment, or continuation setup is missing.")


def _vwap_slope(candles) -> float:
    if len(candles) < 12:
        return 0.0
    recent = vwap(tuple(candles[-12:]))
    prior = vwap(tuple(candles[-24:-12])) if len(candles) >= 24 else vwap(tuple(candles[:-12]))
    if recent is None or prior is None or prior <= 0:
        return 0.0
    return (recent - prior) / prior
