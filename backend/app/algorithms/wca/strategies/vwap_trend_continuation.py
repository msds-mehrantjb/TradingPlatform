from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, completed_candles, definition_for, invalid_result, not_applicable, outside_regular_session, vwap


class VwapTrendContinuationStrategy:
    strategy_id = "C3"
    slug = "vwap_trend_continuation"
    name = "VWAP Trend Continuation"
    family = "trend"
    version = "wca_vwap_trend_continuation_v1"
    base_weight = 0.09
    configuration = StrategyConfig()
    minimum_data_requirements = ("20 completed regular-session candles", "VWAP or candle volume")
    performance_history_identifier = "wca.vwap_trend_continuation.performance.v1"
    backtest_diagnostic_identifier = "wca.vwap_trend_continuation.backtest.v1"

    @property
    def definition(self) -> WcaStrategyDefinition:
        return definition_for(self)

    def evaluate(self, market: WcaMarketSnapshot, config: StrategyConfig = configuration) -> WcaStrategyEvaluation:
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "VWAP continuation is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        if outside_regular_session(market):
            return not_applicable(self, "wca.session.outside_regular", "VWAP continuation is only evaluated during regular session.")
        candles = completed_candles(market)
        if len(candles) < 20:
            return not_applicable(self, "wca.data.insufficient_warmup", "Waiting for VWAP trend history.")
        latest = candles[-1]
        prior_vwap = vwap(candles[:-1])
        current_vwap = vwap(candles)
        slope = (current_vwap - prior_vwap) / max(current_vwap, 0.01)
        recent_high = max(c.high for c in candles[-8:-1])
        recent_low = min(c.low for c in candles[-8:-1])
        if slope > 0.00005 and latest.close > current_vwap and latest.close > recent_high:
            return active(self, WcaSide.BUY, 0.68, "VWAP slope and structure confirm upward continuation.")
        if slope < -0.00005 and latest.close < current_vwap and latest.close < recent_low:
            return active(self, WcaSide.SELL, 0.68, "VWAP slope and structure confirm downward continuation.")
        return active(self, WcaSide.HOLD, 0.18, "VWAP trend continuation is not confirmed.")
