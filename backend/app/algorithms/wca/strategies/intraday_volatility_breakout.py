from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, average_volume, completed_candles, definition_for, eastern_minutes, invalid_result, not_applicable


class IntradayVolatilityBreakoutStrategy:
    strategy_id = "C8"
    slug = "intraday_volatility_breakout"
    name = "Intraday/Volatility Breakout"
    family = "breakout"
    version = "wca_intraday_volatility_breakout_v1"
    base_weight = 0.10
    configuration = StrategyConfig()
    minimum_data_requirements = ("31 completed intraday candles",)
    performance_history_identifier = "wca.intraday_volatility_breakout.performance.v1"
    backtest_diagnostic_identifier = "wca.intraday_volatility_breakout.backtest.v1"

    @property
    def definition(self) -> WcaStrategyDefinition:
        return definition_for(self)

    def evaluate(self, market: WcaMarketSnapshot, config: StrategyConfig = configuration) -> WcaStrategyEvaluation:
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "Intraday volatility breakout is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        minutes = eastern_minutes(market.data_timestamp)
        if minutes <= 10 * 60 + 30 or minutes >= 15 * 60 + 30:
            return not_applicable(self, "wca.session.outside_intraday_breakout_window", "Intraday volatility breakout excludes the ORB and closing windows.")
        candles = completed_candles(market)
        if len(candles) < 31:
            return not_applicable(self, "wca.data.insufficient_warmup", "Waiting for intraday volatility structure.")
        latest = candles[-1]
        structure = candles[-21:-1]
        prior_high = max(c.high for c in structure)
        prior_low = min(c.low for c in structure)
        recent_ranges = tuple(c.high - c.low for c in structure[-10:])
        earlier_ranges = tuple(c.high - c.low for c in structure[:10])
        compression = sum(recent_ranges) / len(recent_ranges) < (sum(earlier_ranges) / len(earlier_ranges)) * 0.85
        expansion = (latest.high - latest.low) > max(0.01, sum(recent_ranges) / len(recent_ranges)) * 1.35
        volume_expansion = latest.volume > average_volume(candles[:-1], 20) * 1.1
        if compression and expansion and volume_expansion and latest.close > prior_high:
            return active(self, WcaSide.BUY, 0.66, "Later-session compression expanded through structural resistance.")
        if compression and expansion and volume_expansion and latest.close < prior_low:
            return active(self, WcaSide.SELL, 0.66, "Later-session compression expanded through structural support.")
        return active(self, WcaSide.HOLD, 0.12, "No later-session volatility breakout confirmation.")
