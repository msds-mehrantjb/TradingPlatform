from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from typing import Any

from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, average_volume, close_location, coerce_strategy_settings, completed_candles, definition_for, eastern_minutes, has_volume, invalid_result, not_applicable, previous_regular_close, same_session_candles, vwap


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

    def evaluate(self, market: WcaMarketSnapshot, config: Any = None) -> WcaStrategyEvaluation:
        from backend.app.algorithms.wca.configuration import GapContinuationFadeSettings

        config = coerce_strategy_settings(GapContinuationFadeSettings, config)
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "Gap continuation/fade is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        if any(code in tuple(market.reason_codes) for code in config.maximum_event_risk_reason_codes):
            return not_applicable(self, "wca.event_risk.blocked", "Gap strategy is disabled by WCA event-risk context.")
        minutes = eastern_minutes(market.data_timestamp)
        if minutes < 9 * 60 + 30 or minutes > 11 * 60:
            return not_applicable(self, "wca.session.outside_gap_window", "Gap continuation/fade only evaluates the first 90 minutes.")
        candles = completed_candles(market)
        prior_close = previous_regular_close(candles, market.data_timestamp)
        session = same_session_candles(candles, market.data_timestamp)
        if prior_close is None or len(session) < config.opening_range_minutes + 1:
            return not_applicable(self, "wca.data.missing_gap_context", "Prior close or opening range is unavailable.")
        if not has_volume(session):
            return not_applicable(self, "wca.data.missing_volume", "Gap continuation/fade requires opening volume context.")
        latest = session[-1]
        opening_range = session[: config.opening_range_minutes]
        opening_high = max(c.high for c in opening_range)
        opening_low = min(c.low for c in opening_range)
        day_open = session[0].open
        gap = (day_open - prior_close) / prior_close
        if abs(gap) < config.minimum_gap_percent:
            return active(self, WcaSide.HOLD, 0.10, "No meaningful opening gap.", evidence_strength=0.12, reason_codes=("wca.c11.gap.too_small",))
        current_vwap = vwap(session)
        volume_ok = latest.volume >= average_volume(session[:-1], 20) * config.volume_expansion_ratio
        location = close_location(latest)
        gap_up_continuation = gap > 0 and latest.close > current_vwap and latest.close > opening_high * (1 + config.continuation_buffer_percent) and location >= 0.55 and volume_ok
        gap_up_fade = gap > 0 and latest.high >= opening_high and latest.close < current_vwap and latest.close < opening_high * (1 - config.fade_reclaim_buffer_percent) and location <= 0.45 and volume_ok
        gap_down_continuation = gap < 0 and latest.close < current_vwap and latest.close < opening_low * (1 - config.continuation_buffer_percent) and location <= 0.45 and volume_ok
        gap_down_fade = gap < 0 and latest.low <= opening_low and latest.close > current_vwap and latest.close > opening_low * (1 + config.fade_reclaim_buffer_percent) and location >= 0.55 and volume_ok
        continuation_active = gap_up_continuation or gap_down_continuation
        fade_active = gap_up_fade or gap_down_fade
        if continuation_active and fade_active:
            return active(self, WcaSide.HOLD, 0.0, "Gap continuation and fade evidence are contradictory; no directional vote.", evidence_strength=0, reason_codes=("wca.c11.gap.contradictory",))
        if gap_up_continuation:
            return active(self, WcaSide.BUY, 0.72, "Gap-up continuation confirmed above VWAP and opening range with volume.", reason_codes=("wca.c11.gap.continuation.buy",))
        if gap_up_fade:
            return active(self, WcaSide.SELL, 0.70, "Gap-up fade confirmed after opening acceptance failed and price rejected VWAP.", reason_codes=("wca.c11.gap.fade.sell",))
        if gap_down_continuation:
            return active(self, WcaSide.SELL, 0.72, "Gap-down continuation confirmed below VWAP and opening range with volume.", reason_codes=("wca.c11.gap.continuation.sell",))
        if gap_down_fade:
            return active(self, WcaSide.BUY, 0.70, "Gap-down fade confirmed after opening rejection reclaimed VWAP.", reason_codes=("wca.c11.gap.fade.buy",))
        return active(self, WcaSide.HOLD, 0.12, "Gap has not confirmed continuation or fade.", evidence_strength=0.20, reason_codes=("wca.c11.gap.no_setup",))
