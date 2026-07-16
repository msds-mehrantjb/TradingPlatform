from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingMarketSnapshot
from backend.app.algorithms.weighted_voting.models import WeightedSide, WeightedVotingSignal, WeightedVotingStrategyFamily
from backend.app.algorithms.weighted_voting.strategies.common import (
    average_true_range,
    bollinger,
    directional_signal,
    hold_signal,
    reject_bad_context,
    slope,
)
from backend.app.algorithms.weighted_voting.strategies.base import WeightedVotingStrategyBase


class BollingerAtrReversionStrategy(WeightedVotingStrategyBase):
    strategy_id = "S7"
    name = "Bollinger/ATR Reversion"
    family = WeightedVotingStrategyFamily.MEAN_REVERSION

    def evaluate(self, snapshot: WeightedVotingMarketSnapshot) -> WeightedVotingSignal:
        context = reject_bad_context(self.strategy_id, self.name, self.family, snapshot, self.config, 50, ("10:00", "15:15"))
        if not hasattr(context, "candles"):
            return context
        closes = [candle.close for candle in context.candles]
        bands = bollinger(closes, 20, 2)
        atr = average_true_range(context.candles)
        if bands is None or atr is None or atr <= 0:
            return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, False, ("weighted_voting.s7.missing_bands_atr",), "Bollinger/ATR reversion needs band and ATR warm-up.")
        middle, upper, lower = bands
        latest = context.latest
        previous = context.candles[-2]
        expansion = abs(slope(closes, 8)) + (atr / latest.close)
        if expansion > 0.014:
            return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, True, ("weighted_voting.s7.directional_expansion_rejected",), "Strong directional expansion rejects Bollinger/ATR reversion.")

        lower_extension = (lower - latest.close) / atr
        upper_extension = (latest.close - upper) / atr
        if lower_extension >= context.config.minimum_band_extension_atr and latest.close > latest.open and latest.close >= previous.close:
            confidence = 0.58 + min(0.15, lower_extension * 0.16) + min(0.05, (middle - latest.close) / latest.close * 12)
            return directional_signal(self.strategy_id, self.name, self.family, WeightedSide.BUY, snapshot.data_timestamp, confidence, max(0.0001, (middle - latest.close) / latest.close), ("weighted_voting.s7.bollinger_atr_reversion_buy",), f"Buy Bollinger/ATR reversion: close is {lower_extension:.2f} ATR below lower band with reversal confirmation.", invalidation_level=latest.low)
        if upper_extension >= context.config.minimum_band_extension_atr and latest.close < latest.open and latest.close <= previous.close:
            confidence = 0.58 + min(0.15, upper_extension * 0.16) + min(0.05, (latest.close - middle) / latest.close * 12)
            return directional_signal(self.strategy_id, self.name, self.family, WeightedSide.SELL, snapshot.data_timestamp, confidence, max(0.0001, (latest.close - middle) / latest.close), ("weighted_voting.s7.bollinger_atr_reversion_sell",), f"Sell Bollinger/ATR reversion: close is {upper_extension:.2f} ATR above upper band with reversal confirmation.", invalidation_level=latest.high)
        return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, True, ("weighted_voting.s7.no_statistical_extension",), "S7 requires simultaneous Bollinger extension, ATR-normalized distance, and reversal confirmation.")
