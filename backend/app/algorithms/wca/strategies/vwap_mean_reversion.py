from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, completed_candles, definition_for, invalid_result, not_applicable, outside_regular_session, strong_trend, vwap


class VwapMeanReversionStrategy:
    strategy_id = "C4"
    slug = "vwap_mean_reversion"
    name = "VWAP Mean Reversion"
    family = "mean_reversion"
    version = "wca_vwap_mean_reversion_v1"
    base_weight = 0.08
    configuration = StrategyConfig()
    minimum_data_requirements = ("20 completed regular-session candles", "VWAP or candle volume")
    performance_history_identifier = "wca.vwap_mean_reversion.performance.v1"
    backtest_diagnostic_identifier = "wca.vwap_mean_reversion.backtest.v1"

    @property
    def definition(self) -> WcaStrategyDefinition:
        return definition_for(self)

    def evaluate(self, market: WcaMarketSnapshot, config: StrategyConfig = configuration) -> WcaStrategyEvaluation:
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "VWAP mean reversion is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        if outside_regular_session(market):
            return not_applicable(self, "wca.session.outside_regular", "VWAP mean reversion is only evaluated during regular session.")
        candles = completed_candles(market)
        if len(candles) < 20:
            return not_applicable(self, "wca.data.insufficient_warmup", "Waiting for VWAP mean-reversion history.")
        latest = candles[-1]
        current_vwap = vwap(candles)
        if strong_trend(candles):
            return not_applicable(self, "wca.regime.strong_trend", "VWAP mean reversion is disabled in a strong trend.")
        distance = (latest.close - current_vwap) / max(current_vwap, 0.01)
        if distance < -0.003 and latest.close >= candles[-2].close:
            return active(self, WcaSide.BUY, min(0.78, 0.52 + abs(distance) * 35), "Price is stretched below VWAP and no longer accelerating lower.")
        if distance > 0.003 and latest.close <= candles[-2].close:
            return active(self, WcaSide.SELL, min(0.78, 0.52 + abs(distance) * 35), "Price is stretched above VWAP and no longer accelerating higher.")
        return active(self, WcaSide.HOLD, 0.16, "VWAP mean-reversion setup is not active.")
