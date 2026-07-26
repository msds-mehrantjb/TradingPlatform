from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from typing import Any

from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, atr, coerce_strategy_settings, completed_candles, definition_for, has_volume, invalid_result, not_applicable, outside_regular_session, slope_percent, sma, vwap, vwap_series


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

    def evaluate(self, market: WcaMarketSnapshot, config: Any = None) -> WcaStrategyEvaluation:
        from backend.app.algorithms.wca.configuration import VwapTrendContinuationSettings

        config = coerce_strategy_settings(VwapTrendContinuationSettings, config)
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "VWAP continuation is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        if outside_regular_session(market):
            return not_applicable(self, "wca.session.outside_regular", "VWAP continuation is only evaluated during regular session.")
        candles = completed_candles(market)
        if len(candles) < config.trend_lookback:
            return not_applicable(self, "wca.data.insufficient_warmup", "Waiting for VWAP trend history.")
        if not any(c.vwap is not None for c in candles) and not has_volume(candles):
            return not_applicable(self, "wca.data.missing_vwap", "VWAP continuation requires VWAP or positive volume inputs.")
        latest = candles[-1]
        history = candles[-config.trend_lookback:]
        vwaps = vwap_series(history)
        current_vwap = vwaps[-1]
        vwap_slope = slope_percent(vwaps[-config.vwap_slope_lookback:])
        fast = sma(history, min(8, len(history)))
        slow = sma(history, min(20, len(history)))
        atr_value = max(atr(history, min(14, len(history) - 1)), 0.01)
        acceptance = candles[-config.acceptance_bars:]
        pullback = candles[-(config.acceptance_bars + 4):-1]
        recent_high = max(c.high for c in history[:-1])
        recent_low = min(c.low for c in history[:-1])
        buy_acceptance = all(c.close >= current_vwap for c in acceptance)
        sell_acceptance = all(c.close <= current_vwap for c in acceptance)
        buy_pullback = pullback and min(c.low for c in pullback) >= current_vwap - atr_value * config.controlled_pullback_max_atr
        sell_pullback = pullback and max(c.high for c in pullback) <= current_vwap + atr_value * config.controlled_pullback_max_atr
        if fast > slow and vwap_slope >= config.minimum_vwap_slope_percent and buy_acceptance and buy_pullback and latest.close > recent_high * (1 + config.confirmation_buffer_percent):
            confidence = min(0.86, 0.56 + vwap_slope * 250 + (latest.close - current_vwap) / latest.close * 25)
            return active(self, WcaSide.BUY, confidence, "Trend, VWAP slope, acceptance, controlled pullback, and continuation confirmation align.", reason_codes=("wca.c3.continuation.buy",))
        if fast < slow and vwap_slope <= -config.minimum_vwap_slope_percent and sell_acceptance and sell_pullback and latest.close < recent_low * (1 - config.confirmation_buffer_percent):
            confidence = min(0.86, 0.56 + abs(vwap_slope) * 250 + (current_vwap - latest.close) / latest.close * 25)
            return active(self, WcaSide.SELL, confidence, "Downtrend, VWAP slope, acceptance, controlled pullback, and continuation confirmation align.", reason_codes=("wca.c3.continuation.sell",))
        return active(self, WcaSide.HOLD, 0.14, "VWAP continuation lacks trend, acceptance, pullback, or confirmation.", evidence_strength=0.2, reason_codes=("wca.c3.continuation.no_setup",))
