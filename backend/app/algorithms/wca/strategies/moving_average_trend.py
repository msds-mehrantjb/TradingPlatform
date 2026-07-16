from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, completed_candles, definition_for, invalid_result, not_applicable, outside_regular_session, sma


class MovingAverageTrendStrategy:
    strategy_id = "C1"
    slug = "moving_average_trend"
    name = "Moving Average Trend"
    family = "trend"
    version = "wca_moving_average_trend_v1"
    base_weight = 0.10
    configuration = StrategyConfig()
    minimum_data_requirements = ("50 completed regular-session candles",)
    performance_history_identifier = "wca.moving_average_trend.performance.v1"
    backtest_diagnostic_identifier = "wca.moving_average_trend.backtest.v1"

    @property
    def definition(self) -> WcaStrategyDefinition:
        return definition_for(self)

    def evaluate(self, market: WcaMarketSnapshot, config: StrategyConfig = configuration) -> WcaStrategyEvaluation:
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "Moving-average trend is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        if outside_regular_session(market):
            return not_applicable(self, "wca.session.outside_regular", "Moving-average trend is only evaluated during regular session.")
        candles = completed_candles(market)
        if len(candles) < 50:
            return not_applicable(self, "wca.data.insufficient_warmup", "Waiting for 50 completed candles.")
        close = candles[-1].close
        sma20 = sma(candles, 20)
        sma50 = sma(candles, 50)
        if sma20 > sma50 and close > sma20:
            return active(self, WcaSide.BUY, min(0.95, 0.45 + abs(sma20 - sma50) / close * 80), "20 SMA is above 50 SMA and price is above 20 SMA.")
        if sma20 < sma50 and close < sma20:
            return active(self, WcaSide.SELL, min(0.95, 0.45 + abs(sma20 - sma50) / close * 80), "20 SMA is below 50 SMA and price is below 20 SMA.")
        return active(self, WcaSide.HOLD, 0.2, "Moving averages are mixed.")
