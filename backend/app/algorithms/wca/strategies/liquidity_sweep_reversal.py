from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, average_volume, completed_candles, definition_for, invalid_result, not_applicable, outside_regular_session


class LiquiditySweepReversalStrategy:
    strategy_id = "C10"
    slug = "liquidity_sweep_reversal"
    name = "Liquidity Sweep Reversal"
    family = "reversal"
    version = "wca_liquidity_sweep_reversal_v1"
    base_weight = 0.25
    configuration = StrategyConfig()
    minimum_data_requirements = ("22 completed regular-session candles", "volume expansion")
    performance_history_identifier = "wca.liquidity_sweep_reversal.performance.v1"
    backtest_diagnostic_identifier = "wca.liquidity_sweep_reversal.backtest.v1"

    @property
    def definition(self) -> WcaStrategyDefinition:
        return definition_for(self)

    def evaluate(self, market: WcaMarketSnapshot, config: StrategyConfig = configuration) -> WcaStrategyEvaluation:
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "Liquidity sweep reversal is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        if outside_regular_session(market):
            return not_applicable(self, "wca.session.outside_regular", "Liquidity sweep reversal is only evaluated during regular session.")
        candles = completed_candles(market)
        if len(candles) < 22:
            return not_applicable(self, "wca.data.insufficient_warmup", "Waiting for sweep level history.")
        latest = candles[-1]
        prior = candles[-21:-1]
        prior_high = max(c.high for c in prior)
        prior_low = min(c.low for c in prior)
        avg_vol = average_volume(candles[:-1], 20)
        volume_expansion = avg_vol > 0 and latest.volume > avg_vol * 1.2
        candle_range = max(latest.high - latest.low, 0.01)
        upper_wick = latest.high - max(latest.open, latest.close)
        lower_wick = min(latest.open, latest.close) - latest.low
        if volume_expansion and latest.high > prior_high and latest.close < prior_high and upper_wick / candle_range >= 0.35:
            return active(self, WcaSide.SELL, 0.72, "High-side liquidity sweep rejected with expanded volume and upper wick.")
        if volume_expansion and latest.low < prior_low and latest.close > prior_low and lower_wick / candle_range >= 0.35:
            return active(self, WcaSide.BUY, 0.72, "Low-side liquidity sweep rejected with expanded volume and lower wick.")
        return active(self, WcaSide.HOLD, 0.14, "No wick-and-volume liquidity sweep reversal.")
