from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from typing import Any

from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, average_volume, close_location, coerce_strategy_settings, completed_candles, definition_for, eastern_minutes, has_volume, invalid_result, not_applicable, same_session_candles, wick_fractions


class OpeningRangeBreakoutStrategy:
    strategy_id = "C7"
    slug = "opening_range_breakout"
    name = "Opening Range Breakout"
    family = "breakout"
    version = "wca_opening_range_breakout_v1"
    base_weight = 0.10
    configuration = StrategyConfig()
    minimum_data_requirements = ("15 opening-range candles", "one post-range confirmation candle")
    performance_history_identifier = "wca.opening_range_breakout.performance.v1"
    backtest_diagnostic_identifier = "wca.opening_range_breakout.backtest.v1"

    @property
    def definition(self) -> WcaStrategyDefinition:
        return definition_for(self)

    def evaluate(self, market: WcaMarketSnapshot, config: Any = None) -> WcaStrategyEvaluation:
        from backend.app.algorithms.wca.configuration import OpeningRangeBreakoutSettings

        config = coerce_strategy_settings(OpeningRangeBreakoutSettings, config)
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "Opening-range breakout is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        minutes = eastern_minutes(market.data_timestamp)
        range_complete = 9 * 60 + 30 + config.opening_range_minutes
        if minutes < range_complete or minutes > config.evaluation_end_minutes:
            return not_applicable(self, "wca.session.outside_opening_range_window", "Opening-range breakout only evaluates the post-opening window.")
        candles = completed_candles(market)
        session = same_session_candles(candles, market.data_timestamp)
        if len(session) < config.opening_range_minutes + 1:
            return not_applicable(self, "wca.data.insufficient_opening_range", "Waiting for the opening range to complete.")
        if not has_volume(session):
            return not_applicable(self, "wca.data.missing_volume", "Opening-range breakout requires volume confirmation.")
        latest = session[-1]
        opening = session[: config.opening_range_minutes]
        opening_high = max(c.high for c in opening)
        opening_low = min(c.low for c in opening)
        avg_volume = average_volume(session[:-1], 20)
        volume_expansion = avg_volume > 0 and latest.volume >= avg_volume * config.volume_expansion_ratio
        upper_wick, lower_wick = wick_fractions(latest)
        accepted_above = latest.close > opening_high * (1 + config.close_buffer_percent) and close_location(latest) >= config.accepted_price_fraction
        accepted_below = latest.close < opening_low * (1 - config.close_buffer_percent) and close_location(latest) <= 1 - config.accepted_price_fraction
        if accepted_above and volume_expansion and upper_wick <= config.false_breakout_wick_fraction:
            return active(self, WcaSide.BUY, 0.72, "Completed opening range broke upward with accepted close, volume, and no false-breakout wick.", reason_codes=("wca.c7.orb.buy",))
        if accepted_below and volume_expansion and lower_wick <= config.false_breakout_wick_fraction:
            return active(self, WcaSide.SELL, 0.72, "Completed opening range broke downward with accepted close, volume, and no false-breakout wick.", reason_codes=("wca.c7.orb.sell",))
        return active(self, WcaSide.HOLD, 0.12, "Opening range has not broken with accepted price, volume, and false-breakout controls.", evidence_strength=0.20, reason_codes=("wca.c7.orb.no_setup",))
