from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, average_volume, completed_candles, definition_for, eastern_minutes, invalid_result, not_applicable, same_session_candles


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

    def evaluate(self, market: WcaMarketSnapshot, config: StrategyConfig = configuration) -> WcaStrategyEvaluation:
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "Opening-range breakout is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        minutes = eastern_minutes(market.data_timestamp)
        if minutes < 9 * 60 + 45 or minutes > 10 * 60 + 30:
            return not_applicable(self, "wca.session.outside_opening_range_window", "Opening-range breakout only evaluates the post-opening window.")
        candles = completed_candles(market)
        session = same_session_candles(candles, market.data_timestamp)
        if len(session) < 16:
            return not_applicable(self, "wca.data.insufficient_opening_range", "Waiting for the opening range to complete.")
        latest = session[-1]
        opening = session[:15]
        opening_high = max(c.high for c in opening)
        opening_low = min(c.low for c in opening)
        avg_volume = average_volume(session[:-1], 20)
        volume_expansion = avg_volume > 0 and latest.volume > avg_volume * 1.15
        if latest.close > opening_high and volume_expansion:
            return active(self, WcaSide.BUY, 0.72, "Close broke the opening-range high with volume.")
        if latest.close < opening_low and volume_expansion:
            return active(self, WcaSide.SELL, 0.72, "Close broke the opening-range low with volume.")
        return active(self, WcaSide.HOLD, 0.18, "Opening range has not broken with volume.")
