from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, average_volume, completed_candles, definition_for, eastern_minutes, invalid_result, not_applicable, previous_regular_close, same_session_candles, vwap


class GapContinuationFadeStrategy:
    strategy_id = "C11"
    slug = "gap_continuation_fade"
    name = "Gap Continuation/Fade"
    family = "event"
    version = "wca_gap_continuation_fade_v1"
    base_weight = 0.10
    configuration = StrategyConfig()
    minimum_data_requirements = ("prior regular-session close", "15 opening-range candles", "one confirmation candle")
    performance_history_identifier = "wca.gap_continuation_fade.performance.v1"
    backtest_diagnostic_identifier = "wca.gap_continuation_fade.backtest.v1"

    @property
    def definition(self) -> WcaStrategyDefinition:
        return definition_for(self)

    def evaluate(self, market: WcaMarketSnapshot, config: StrategyConfig = configuration) -> WcaStrategyEvaluation:
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "Gap continuation/fade is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        minutes = eastern_minutes(market.data_timestamp)
        if minutes < 9 * 60 + 30 or minutes > 11 * 60:
            return not_applicable(self, "wca.session.outside_gap_window", "Gap continuation/fade only evaluates the first 90 minutes.")
        candles = completed_candles(market)
        prior_close = previous_regular_close(candles, market.data_timestamp)
        session = same_session_candles(candles, market.data_timestamp)
        if prior_close is None or len(session) < 16:
            return not_applicable(self, "wca.data.missing_gap_context", "Prior close or opening range is unavailable.")
        latest = session[-1]
        opening_range = session[:15]
        opening_high = max(c.high for c in opening_range)
        opening_low = min(c.low for c in opening_range)
        day_open = session[0].open
        gap = (day_open - prior_close) / prior_close
        if abs(gap) < 0.002:
            return active(self, WcaSide.HOLD, 0.12, "No meaningful opening gap.")
        current_vwap = vwap(session)
        volume_ok = latest.volume >= average_volume(session[:-1], 20) * 1.1
        if gap > 0 and latest.close > current_vwap and latest.close > opening_high and volume_ok:
            return active(self, WcaSide.BUY, 0.72, "Gap-up continuation confirmed above VWAP and opening range.")
        if gap > 0 and latest.high >= opening_high and latest.close < current_vwap and latest.close < opening_high and volume_ok:
            return active(self, WcaSide.SELL, 0.70, "Gap-up fade confirmed after failed opening-range high.")
        if gap < 0 and latest.close < current_vwap and latest.close < opening_low and volume_ok:
            return active(self, WcaSide.SELL, 0.72, "Gap-down continuation confirmed below VWAP and opening range.")
        if gap < 0 and latest.low <= opening_low and latest.close > current_vwap and latest.close > opening_low and volume_ok:
            return active(self, WcaSide.BUY, 0.70, "Gap-down fade confirmed after failed opening-range low.")
        return active(self, WcaSide.HOLD, 0.18, "Gap has not confirmed continuation or fade.")
