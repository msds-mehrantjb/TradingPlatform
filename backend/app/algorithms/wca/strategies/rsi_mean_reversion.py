from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from typing import Any

from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, close_location, coerce_strategy_settings, completed_candles, definition_for, invalid_result, not_applicable, outside_regular_session, rsi, sma


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

    def evaluate(self, market: WcaMarketSnapshot, config: Any = None) -> WcaStrategyEvaluation:
        from backend.app.algorithms.wca.configuration import RsiMeanReversionSettings

        config = coerce_strategy_settings(RsiMeanReversionSettings, config)
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "RSI mean reversion is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        if outside_regular_session(market):
            return not_applicable(self, "wca.session.outside_regular", "RSI mean reversion is only evaluated during regular session.")
        candles = completed_candles(market)
        warmup = max(config.rsi_period + config.confirmation_lookback + 1, 15)
        if len(candles) < warmup:
            return not_applicable(self, "wca.data.insufficient_warmup", "Waiting for RSI history.")
        latest = candles[-1]
        rsi_value = rsi(tuple(c.close for c in candles), config.rsi_period)
        trend_separation = abs(sma(candles, min(8, len(candles))) - sma(candles, min(20, len(candles)))) / latest.close
        if trend_separation > config.maximum_trend_separation_percent:
            return not_applicable(self, "wca.regime.trending", "RSI mean reversion is disabled in a directional trend.")
        previous = candles[-(config.confirmation_lookback + 1):-1]
        buy_confirmation = latest.close > max(c.close for c in previous) and latest.close > latest.open and close_location(latest) >= 0.55
        sell_confirmation = latest.close < min(c.close for c in previous) and latest.close < latest.open and close_location(latest) <= 0.45
        if rsi_value <= config.oversold_threshold and buy_confirmation:
            confidence = min(0.84, 0.50 + (config.oversold_threshold - rsi_value) / 50 + 0.08)
            return active(self, WcaSide.BUY, confidence, f"RSI {rsi_value:.1f} is oversold and price confirmed reversal in a non-trending context.", reason_codes=("wca.c5.rsi_reversion.buy",))
        if rsi_value >= config.overbought_threshold and sell_confirmation:
            confidence = min(0.84, 0.50 + (rsi_value - config.overbought_threshold) / 50 + 0.08)
            return active(self, WcaSide.SELL, confidence, f"RSI {rsi_value:.1f} is overbought and price confirmed reversal in a non-trending context.", reason_codes=("wca.c5.rsi_reversion.sell",))
        return active(self, WcaSide.HOLD, 0.10, f"RSI {rsi_value:.1f} lacks a confirmed non-trending reversal setup.", evidence_strength=0.18, reason_codes=("wca.c5.rsi_reversion.no_setup",))
