from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from typing import Any

from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, average_volume, close_location, coerce_strategy_settings, completed_candles, definition_for, has_volume, invalid_result, not_applicable, outside_regular_session, same_session_candles


class FailedBreakoutReversalStrategy:
    strategy_id = "C9"
    slug = "failed_breakout_reversal"
    name = "Failed Breakout Reversal"
    family = "reversal"
    version = "wca_failed_breakout_reversal_v1"
    base_weight = 0.09
    configuration = StrategyConfig()
    minimum_data_requirements = ("22 completed regular-session candles",)
    performance_history_identifier = "wca.failed_breakout_reversal.performance.v1"
    backtest_diagnostic_identifier = "wca.failed_breakout_reversal.backtest.v1"

    @property
    def definition(self) -> WcaStrategyDefinition:
        return definition_for(self)

    def evaluate(self, market: WcaMarketSnapshot, config: Any = None) -> WcaStrategyEvaluation:
        from backend.app.algorithms.wca.configuration import FailedBreakoutReversalSettings

        config = coerce_strategy_settings(FailedBreakoutReversalSettings, config)
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "Failed breakout reversal is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        if outside_regular_session(market):
            return not_applicable(self, "wca.session.outside_regular", "Failed breakout reversal is only evaluated during regular session.")
        candles = completed_candles(market)
        if len(candles) < config.reference_lookback + 1:
            return not_applicable(self, "wca.data.insufficient_warmup", "Waiting for tested level history.")
        if not has_volume(candles):
            return not_applicable(self, "wca.data.missing_volume", "Failed breakout reversal requires participation context.")
        latest = candles[-1]
        session = same_session_candles(candles, market.data_timestamp)
        if len(session) >= 16:
            reference = session[:15]
        else:
            reference = candles[-(config.reference_lookback + 1):-1]
        reference_high = max(c.high for c in reference)
        reference_low = min(c.low for c in reference)
        buffer_high = reference_high * config.minimum_break_percent
        buffer_low = reference_low * config.minimum_break_percent
        close_buffer_high = reference_high * config.close_back_inside_buffer_percent
        close_buffer_low = reference_low * config.close_back_inside_buffer_percent
        volume_ok = latest.volume >= average_volume(candles[:-1], min(20, len(candles) - 1)) * config.confirmation_volume_ratio
        failed_high = latest.high > reference_high + buffer_high and latest.close < reference_high - close_buffer_high
        failed_low = latest.low < reference_low - buffer_low and latest.close > reference_low + close_buffer_low
        if failed_high and latest.close < latest.open and close_location(latest) <= 1 - config.reversal_close_fraction and volume_ok:
            return active(self, WcaSide.SELL, 0.70, "Reference high broke, failed, closed back inside, and confirmed reversal.", reason_codes=("wca.c9.failed_breakout.sell",))
        if failed_low and latest.close > latest.open and close_location(latest) >= config.reversal_close_fraction and volume_ok:
            return active(self, WcaSide.BUY, 0.70, "Reference low broke, failed, closed back inside, and confirmed reversal.", reason_codes=("wca.c9.failed_breakout.buy",))
        return active(self, WcaSide.HOLD, 0.10, "No reference break, failure, close-back-inside, and reversal confirmation.", evidence_strength=0.18, reason_codes=("wca.c9.failed_breakout.no_setup",))
