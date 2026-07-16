from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingMarketSnapshot
from backend.app.algorithms.weighted_voting.models import WeightedSide, WeightedVotingSignal, WeightedVotingStrategyFamily
from backend.app.algorithms.weighted_voting.strategies.common import (
    average_true_range,
    directional_signal,
    hold_signal,
    reject_bad_context,
    regular_session_candles,
    vwap,
)
from backend.app.algorithms.weighted_voting.strategies.base import WeightedVotingStrategyBase


class FirstPullbackAfterOpenStrategy(WeightedVotingStrategyBase):
    strategy_id = "S2"
    name = "First Pullback After Open"
    family = WeightedVotingStrategyFamily.TREND

    def evaluate(self, snapshot: WeightedVotingMarketSnapshot) -> WeightedVotingSignal:
        context = reject_bad_context(self.strategy_id, self.name, self.family, snapshot, self.config, 25, ("09:45", "11:30"))
        if not hasattr(context, "candles"):
            return context
        session = regular_session_candles(context.candles)
        if len(session) < 25:
            return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, False, ("weighted_voting.s2.insufficient_session",), "First pullback needs an opening trend and completed pullback sequence.")

        opening = session[:15]
        trend_return = (opening[-1].close - opening[0].open) / opening[0].open
        atr = average_true_range(session) or 0.0
        current_vwap = vwap(session)
        if current_vwap is None or atr <= 0:
            return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, False, ("weighted_voting.s2.missing_vwap_atr",), "VWAP or ATR is unavailable for first-pullback qualification.")

        side = WeightedSide.BUY if trend_return > 0.0015 else WeightedSide.SELL if trend_return < -0.0015 else None
        if side is None:
            return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, True, ("weighted_voting.s2.no_opening_trend",), "No established opening trend exists.")

        pullback_index = _first_pullback_index(session, side, current_vwap)
        if pullback_index is None:
            return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, True, ("weighted_voting.s2.no_first_pullback",), "Opening trend exists but no first qualified pullback has completed.")
        latest_index = len(session) - 1
        if latest_index - pullback_index > 3:
            return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, True, ("weighted_voting.s2.first_pullback_expired",), "The first qualified pullback has already passed; S2 does not recycle later continuation setups.")

        latest = session[-1]
        previous = session[-2]
        if side == WeightedSide.BUY and latest.close > previous.high and latest.close > current_vwap:
            confidence = 0.59 + min(0.12, abs(trend_return) * 35) + min(0.07, (latest.close - current_vwap) / latest.close * 20)
            return directional_signal(self.strategy_id, self.name, self.family, side, snapshot.data_timestamp, confidence, abs(trend_return), ("weighted_voting.s2.first_pullback_buy",), f"Buy resumption after first pullback in opening uptrend; pullback index {pullback_index}.", invalidation_level=session[pullback_index].low)
        if side == WeightedSide.SELL and latest.close < previous.low and latest.close < current_vwap:
            confidence = 0.59 + min(0.12, abs(trend_return) * 35) + min(0.07, (current_vwap - latest.close) / latest.close * 20)
            return directional_signal(self.strategy_id, self.name, self.family, side, snapshot.data_timestamp, confidence, abs(trend_return), ("weighted_voting.s2.first_pullback_sell",), f"Sell resumption after first pullback in opening downtrend; pullback index {pullback_index}.", invalidation_level=session[pullback_index].high)
        return hold_signal(self.strategy_id, self.name, self.family, snapshot.data_timestamp, True, ("weighted_voting.s2.waiting_for_resumption",), "First pullback is present but resumption confirmation is incomplete.")


def _first_pullback_index(candles, side: WeightedSide, current_vwap: float) -> int | None:
    for index in range(15, len(candles) - 1):
        candle = candles[index]
        previous = candles[index - 1]
        if side == WeightedSide.BUY and candle.close < previous.close and candle.low <= current_vwap * 1.002 and candle.close >= candles[0].open:
            return index
        if side == WeightedSide.SELL and candle.close > previous.close and candle.high >= current_vwap * 0.998 and candle.close <= candles[0].open:
            return index
    return None
