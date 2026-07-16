from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, completed_candles, definition_for, invalid_result, not_applicable, outside_regular_session, sma, vwap


class TrendPullbackStrategy:
    strategy_id = "C2"
    slug = "trend_pullback"
    name = "Trend Pullback"
    family = "trend"
    version = "wca_trend_pullback_v1"
    base_weight = 0.09
    configuration = StrategyConfig()
    minimum_data_requirements = ("30 completed regular-session candles",)
    performance_history_identifier = "wca.trend_pullback.performance.v1"
    backtest_diagnostic_identifier = "wca.trend_pullback.backtest.v1"

    @property
    def definition(self) -> WcaStrategyDefinition:
        return definition_for(self)

    def evaluate(self, market: WcaMarketSnapshot, config: StrategyConfig = configuration) -> WcaStrategyEvaluation:
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "Trend pullback is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        if outside_regular_session(market):
            return not_applicable(self, "wca.session.outside_regular", "Trend pullback is only evaluated during regular session.")
        candles = completed_candles(market)
        if len(candles) < 30:
            return not_applicable(self, "wca.data.insufficient_warmup", "Waiting for 30 completed candles.")
        latest = candles[-1]
        sma10 = sma(candles, 10)
        sma30 = sma(candles, 30)
        vwap_value = vwap(candles)
        near_sma10 = abs(latest.close - sma10) / latest.close < 0.004
        if sma10 > sma30 and latest.close > vwap_value and near_sma10 and latest.close > latest.open:
            return active(self, WcaSide.BUY, 0.68, "Uptrend pullback held near short moving average and resumed upward.")
        if sma10 < sma30 and latest.close < vwap_value and near_sma10 and latest.close < latest.open:
            return active(self, WcaSide.SELL, 0.68, "Downtrend pullback rejected near short moving average and resumed downward.")
        return active(self, WcaSide.HOLD, 0.2, "No qualified trend pullback.")
