from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from typing import Any

from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, atr, close_location, coerce_strategy_settings, completed_candles, definition_for, has_volume, invalid_result, not_applicable, outside_regular_session, sma, vwap


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

    def evaluate(self, market: WcaMarketSnapshot, config: Any = None) -> WcaStrategyEvaluation:
        from backend.app.algorithms.wca.configuration import VwapMeanReversionSettings

        config = coerce_strategy_settings(VwapMeanReversionSettings, config)
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "VWAP mean reversion is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        if outside_regular_session(market):
            return not_applicable(self, "wca.session.outside_regular", "VWAP mean reversion is only evaluated during regular session.")
        candles = completed_candles(market)
        if len(candles) < config.lookback:
            return not_applicable(self, "wca.data.insufficient_warmup", "Waiting for VWAP mean-reversion history.")
        if not any(c.vwap is not None for c in candles) and not has_volume(candles):
            return not_applicable(self, "wca.data.missing_vwap", "VWAP mean reversion requires VWAP or positive volume inputs.")
        latest = candles[-1]
        history = candles[-config.lookback:]
        current_vwap = vwap(history)
        trend_separation = abs(sma(history, min(10, len(history))) - sma(history, min(20, len(history)))) / latest.close
        if trend_separation > config.maximum_trend_separation_percent:
            return not_applicable(self, "wca.regime.strong_trend", "VWAP mean reversion is disabled in a strong trend.")
        distance = (latest.close - current_vwap) / max(current_vwap, 0.01)
        atr_value = max(atr(history, min(14, len(history) - 1)), 0.01)
        room_to_vwap = abs(latest.close - current_vwap) / atr_value
        location = close_location(latest)
        prior = candles[-2]
        buy_reversal = latest.close > prior.close and latest.close > latest.open and location >= config.reversal_close_fraction
        sell_reversal = latest.close < prior.close and latest.close < latest.open and location <= 1 - config.reversal_close_fraction
        if distance <= -config.minimum_overextension_percent and room_to_vwap >= config.minimum_room_to_vwap_atr and buy_reversal:
            return active(self, WcaSide.BUY, min(0.80, 0.52 + abs(distance) * 35 + min(room_to_vwap, 2) * 0.05), "VWAP downside overextension shows exhaustion with room to revert.", reason_codes=("wca.c4.reversion.buy",))
        if distance >= config.minimum_overextension_percent and room_to_vwap >= config.minimum_room_to_vwap_atr and sell_reversal:
            return active(self, WcaSide.SELL, min(0.80, 0.52 + abs(distance) * 35 + min(room_to_vwap, 2) * 0.05), "VWAP upside overextension shows exhaustion with room to revert.", reason_codes=("wca.c4.reversion.sell",))
        return active(self, WcaSide.HOLD, 0.12, "VWAP mean-reversion lacks overextension, exhaustion, or room back to VWAP.", evidence_strength=0.2, reason_codes=("wca.c4.reversion.no_setup",))
