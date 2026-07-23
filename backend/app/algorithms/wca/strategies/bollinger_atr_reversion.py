from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, atr, completed_candles, definition_for, directional_expansion, invalid_result, invalid_strategy, not_applicable, outside_regular_session, sma, standard_deviation


class BollingerAtrReversionStrategy:
    strategy_id = "C6"
    slug = "bollinger_atr_reversion"
    name = "Bollinger/ATR Reversion"
    family = "mean_reversion"
    version = "wca_bollinger_atr_reversion_v1"
    base_weight = 0.25
    configuration = StrategyConfig()
    minimum_data_requirements = ("21 completed regular-session candles",)
    performance_history_identifier = "wca.bollinger_atr_reversion.performance.v1"
    backtest_diagnostic_identifier = "wca.bollinger_atr_reversion.backtest.v1"

    @property
    def definition(self) -> WcaStrategyDefinition:
        return definition_for(self)

    def evaluate(self, market: WcaMarketSnapshot, config: StrategyConfig = configuration) -> WcaStrategyEvaluation:
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "Bollinger/ATR reversion is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        if outside_regular_session(market):
            return not_applicable(self, "wca.session.outside_regular", "Bollinger/ATR reversion is only evaluated during regular session.")
        candles = completed_candles(market)
        if len(candles) < 21:
            return not_applicable(self, "wca.data.insufficient_warmup", "Waiting for Bollinger and ATR history.")
        latest = candles[-1]
        atr_value = atr(candles, 14)
        if atr_value <= 0:
            return invalid_strategy(self, "wca.data.invalid_atr", "ATR is unavailable.")
        if directional_expansion(candles, atr_value):
            return not_applicable(self, "wca.regime.directional_expansion", "Strong directional expansion disables Bollinger/ATR reversion.")
        middle = sma(candles, 20)
        std = standard_deviation(tuple(c.close for c in candles[-20:]))
        upper = middle + 2 * std
        lower = middle - 2 * std
        if latest.close < lower and (lower - latest.close) >= atr_value * 0.35 and latest.close >= candles[-2].close:
            return active(self, WcaSide.BUY, 0.68, "Price is below lower Bollinger band by an ATR-confirmed distance and reversing.")
        if latest.close > upper and (latest.close - upper) >= atr_value * 0.35 and latest.close <= candles[-2].close:
            return active(self, WcaSide.SELL, 0.68, "Price is above upper Bollinger band by an ATR-confirmed distance and reversing.")
        return active(self, WcaSide.HOLD, 0.12, "Price is not statistically extended enough for Bollinger/ATR reversion.")
