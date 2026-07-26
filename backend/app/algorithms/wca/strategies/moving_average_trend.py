from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from typing import Any

from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, coerce_strategy_settings, completed_candles, definition_for, invalid_result, not_applicable, outside_regular_session, slope_percent, sma


class MovingAverageTrendStrategy:
    strategy_id = "C1"
    slug = "moving_average_trend"
    name = "Moving Average Trend"
    family = "trend"
    version = "wca_moving_average_trend_v1"
    base_weight = 0.10
    configuration = StrategyConfig()
    minimum_data_requirements = ("50 completed regular-session candles",)
    performance_history_identifier = "wca.moving_average_trend.performance.v1"
    backtest_diagnostic_identifier = "wca.moving_average_trend.backtest.v1"

    @property
    def definition(self) -> WcaStrategyDefinition:
        return definition_for(self)

    def evaluate(self, market: WcaMarketSnapshot, config: Any = None) -> WcaStrategyEvaluation:
        from backend.app.algorithms.wca.configuration import MovingAverageTrendSettings

        config = coerce_strategy_settings(MovingAverageTrendSettings, config)
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "Moving-average trend is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        if outside_regular_session(market):
            return not_applicable(self, "wca.session.outside_regular", "Moving-average trend is only evaluated during regular session.")
        candles = completed_candles(market)
        warmup = max(config.slow_period, config.fast_period + config.slope_lookback)
        if len(candles) < warmup:
            return not_applicable(self, "wca.data.insufficient_warmup", f"Waiting for {warmup} completed candles.")
        close = candles[-1].close
        fast = sma(candles, config.fast_period)
        slow = sma(candles, config.slow_period)
        fast_history = tuple(sma(candles[: index + 1], config.fast_period) for index in range(config.fast_period - 1, len(candles)))
        slow_history = tuple(sma(candles[: index + 1], config.slow_period) for index in range(config.slow_period - 1, len(candles)))
        fast_slope = slope_percent(fast_history[-config.slope_lookback:])
        slow_slope = slope_percent(slow_history[-config.slope_lookback:])
        separation = abs(fast - slow) / close
        recent = candles[-config.persistence_bars:]
        buy_persistent = all(c.close >= fast * (1 - config.price_location_tolerance_percent) for c in recent)
        sell_persistent = all(c.close <= fast * (1 + config.price_location_tolerance_percent) for c in recent)
        buy_evidence = (
            fast > slow,
            fast_slope >= config.minimum_slope_percent,
            slow_slope >= 0,
            separation >= config.minimum_ma_separation_percent,
            close > fast,
            buy_persistent,
        )
        sell_evidence = (
            fast < slow,
            fast_slope <= -config.minimum_slope_percent,
            slow_slope <= 0,
            separation >= config.minimum_ma_separation_percent,
            close < fast,
            sell_persistent,
        )
        if all(buy_evidence):
            confidence = min(0.90, 0.52 + separation * 45 + max(fast_slope, 0) * 80)
            return active(self, WcaSide.BUY, confidence, "Moving averages are ordered upward with slope, persistence, and price acceptance.", reason_codes=("wca.c1.trend.buy",))
        if all(sell_evidence):
            confidence = min(0.90, 0.52 + separation * 45 + abs(min(fast_slope, 0)) * 80)
            return active(self, WcaSide.SELL, confidence, "Moving averages are ordered downward with slope, persistence, and price acceptance.", reason_codes=("wca.c1.trend.sell",))
        return active(self, WcaSide.HOLD, 0.12, "Moving-average trend evidence is flat, mixed, or contradictory.", evidence_strength=0.2, reason_codes=("wca.c1.trend.no_direction",))
