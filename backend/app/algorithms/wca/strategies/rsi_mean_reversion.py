from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, completed_candles, definition_for, invalid_result, not_applicable, outside_regular_session, rsi


class RsiMeanReversionStrategy:
    strategy_id = "C5"
    slug = "rsi_mean_reversion"
    name = "RSI Mean Reversion"
    family = "mean_reversion"
    version = "wca_rsi_mean_reversion_v1"
    base_weight = 0.08
    configuration = StrategyConfig()
    minimum_data_requirements = ("15 completed regular-session candles",)
    performance_history_identifier = "wca.rsi_mean_reversion.performance.v1"
    backtest_diagnostic_identifier = "wca.rsi_mean_reversion.backtest.v1"

    @property
    def definition(self) -> WcaStrategyDefinition:
        return definition_for(self)

    def evaluate(self, market: WcaMarketSnapshot, config: StrategyConfig = configuration) -> WcaStrategyEvaluation:
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "RSI mean reversion is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        if outside_regular_session(market):
            return not_applicable(self, "wca.session.outside_regular", "RSI mean reversion is only evaluated during regular session.")
        candles = completed_candles(market)
        if len(candles) < 15:
            return not_applicable(self, "wca.data.insufficient_warmup", "Waiting for RSI history.")
        rsi_value = rsi(tuple(c.close for c in candles), 14)
        if rsi_value <= 30:
            return active(self, WcaSide.BUY, min(0.9, 0.5 + (30 - rsi_value) / 35), f"RSI {rsi_value:.1f} is oversold.")
        if rsi_value >= 70:
            return active(self, WcaSide.SELL, min(0.9, 0.5 + (rsi_value - 70) / 35), f"RSI {rsi_value:.1f} is overbought.")
        return active(self, WcaSide.HOLD, 0.15, f"RSI {rsi_value:.1f} is neutral.")
