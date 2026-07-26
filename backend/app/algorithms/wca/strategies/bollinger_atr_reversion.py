from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from typing import Any

from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, atr, close_location, coerce_strategy_settings, completed_candles, definition_for, directional_expansion, invalid_result, invalid_strategy, not_applicable, outside_regular_session, sma, standard_deviation


class BollingerAtrReversionStrategy:
    strategy_id = "C6"
    slug = "bollinger_atr_reversion"
    name = "Bollinger/ATR Reversion"
    family = "mean_reversion"
    version = "wca_bollinger_atr_reversion_v1"
    base_weight = 0.08
    configuration = StrategyConfig()
    minimum_data_requirements = ("21 completed regular-session candles",)
    performance_history_identifier = "wca.bollinger_atr_reversion.performance.v1"
    backtest_diagnostic_identifier = "wca.bollinger_atr_reversion.backtest.v1"

    @property
    def definition(self) -> WcaStrategyDefinition:
        return definition_for(self)

    def evaluate(self, market: WcaMarketSnapshot, config: Any = None) -> WcaStrategyEvaluation:
        from backend.app.algorithms.wca.configuration import BollingerAtrReversionSettings

        config = coerce_strategy_settings(BollingerAtrReversionSettings, config)
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "Bollinger/ATR reversion is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        if outside_regular_session(market):
            return not_applicable(self, "wca.session.outside_regular", "Bollinger/ATR reversion is only evaluated during regular session.")
        candles = completed_candles(market)
        warmup = max(config.bollinger_period + 1, config.atr_period + 1)
        if len(candles) < warmup:
            return not_applicable(self, "wca.data.insufficient_warmup", "Waiting for Bollinger and ATR history.")
        latest = candles[-1]
        atr_value = atr(candles, config.atr_period)
        if atr_value <= 0:
            return invalid_strategy(self, "wca.data.invalid_atr", "ATR is unavailable.")
        if directional_expansion(candles, atr_value, config.directional_expansion_atr_multiple):
            return not_applicable(self, "wca.regime.directional_expansion", "Strong directional expansion disables Bollinger/ATR reversion.")
        middle = sma(candles, config.bollinger_period)
        std = standard_deviation(tuple(c.close for c in candles[-config.bollinger_period:]))
        upper = middle + config.bollinger_stddev * std
        lower = middle - config.bollinger_stddev * std
        location = close_location(latest)
        buy_reversal = latest.close > candles[-2].close and latest.close > latest.open and location >= config.reversal_close_fraction
        sell_reversal = latest.close < candles[-2].close and latest.close < latest.open and location <= 1 - config.reversal_close_fraction
        if latest.low < lower and (lower - latest.low) >= atr_value * config.minimum_atr_extension and buy_reversal:
            return active(self, WcaSide.BUY, 0.68, "Price is below lower Bollinger band by an ATR-confirmed distance and reversing.", reason_codes=("wca.c6.bollinger_atr.buy",))
        if latest.high > upper and (latest.high - upper) >= atr_value * config.minimum_atr_extension and sell_reversal:
            return active(self, WcaSide.SELL, 0.68, "Price is above upper Bollinger band by an ATR-confirmed distance and reversing.", reason_codes=("wca.c6.bollinger_atr.sell",))
        return active(self, WcaSide.HOLD, 0.10, "Bollinger/ATR reversion lacks statistical extension, non-continuation, or reversal trigger.", evidence_strength=0.18, reason_codes=("wca.c6.bollinger_atr.no_setup",))
