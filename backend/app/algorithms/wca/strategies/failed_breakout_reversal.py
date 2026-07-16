from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, completed_candles, definition_for, invalid_result, not_applicable, outside_regular_session


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

    def evaluate(self, market: WcaMarketSnapshot, config: StrategyConfig = configuration) -> WcaStrategyEvaluation:
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "Failed breakout reversal is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        if outside_regular_session(market):
            return not_applicable(self, "wca.session.outside_regular", "Failed breakout reversal is only evaluated during regular session.")
        candles = completed_candles(market)
        if len(candles) < 22:
            return not_applicable(self, "wca.data.insufficient_warmup", "Waiting for tested level history.")
        latest = candles[-1]
        prior = candles[-21:-1]
        prior_high = max(c.high for c in prior)
        prior_low = min(c.low for c in prior)
        if latest.high > prior_high and latest.close < prior_high:
            return active(self, WcaSide.SELL, 0.70, "Break above prior high failed back inside the range.")
        if latest.low < prior_low and latest.close > prior_low:
            return active(self, WcaSide.BUY, 0.70, "Break below prior low failed back inside the range.")
        return active(self, WcaSide.HOLD, 0.14, "No confirmed failed breakout reversal.")
