from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from typing import Any

from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, average_volume, close_location, coerce_strategy_settings, completed_candles, definition_for, has_volume, invalid_result, not_applicable, outside_regular_session, wick_fractions


class LiquiditySweepReversalStrategy:
    strategy_id = "C10"
    slug = "liquidity_sweep_reversal"
    name = "Liquidity Sweep Reversal"
    family = "reversal"
    version = "wca_liquidity_sweep_reversal_v1"
    base_weight = 0.09
    configuration = StrategyConfig()
    minimum_data_requirements = ("22 completed regular-session candles", "volume expansion")
    performance_history_identifier = "wca.liquidity_sweep_reversal.performance.v1"
    backtest_diagnostic_identifier = "wca.liquidity_sweep_reversal.backtest.v1"

    @property
    def definition(self) -> WcaStrategyDefinition:
        return definition_for(self)

    def evaluate(self, market: WcaMarketSnapshot, config: Any = None) -> WcaStrategyEvaluation:
        from backend.app.algorithms.wca.configuration import LiquiditySweepReversalSettings

        config = coerce_strategy_settings(LiquiditySweepReversalSettings, config)
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "Liquidity sweep reversal is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        if outside_regular_session(market):
            return not_applicable(self, "wca.session.outside_regular", "Liquidity sweep reversal is only evaluated during regular session.")
        candles = completed_candles(market)
        if len(candles) < config.reference_lookback + 1:
            return not_applicable(self, "wca.data.insufficient_warmup", "Waiting for sweep level history.")
        if not has_volume(candles):
            return not_applicable(self, "wca.data.missing_microstructure", "Liquidity sweep requires volume/trade evidence.")
        latest = candles[-1]
        prior = candles[-(config.reference_lookback + 1):-1]
        prior_high = max(c.high for c in prior)
        prior_low = min(c.low for c in prior)
        avg_vol = average_volume(candles[:-1], 20)
        volume_expansion = avg_vol > 0 and latest.volume >= avg_vol * config.volume_expansion_ratio
        upper_wick, lower_wick = wick_fractions(latest)
        high_sweep = latest.high > prior_high * (1 + config.minimum_sweep_percent) and latest.close < prior_high
        low_sweep = latest.low < prior_low * (1 - config.minimum_sweep_percent) and latest.close > prior_low
        if volume_expansion and high_sweep and upper_wick >= config.rejection_wick_fraction and latest.close < latest.open and close_location(latest) <= 1 - config.follow_through_close_fraction:
            return active(self, WcaSide.SELL, 0.72, "High-side liquidity sweep rejected through confirmed swing liquidity with volume, wick, reclaim, and follow-through.", reason_codes=("wca.c10.sweep.sell",))
        if volume_expansion and low_sweep and lower_wick >= config.rejection_wick_fraction and latest.close > latest.open and close_location(latest) >= config.follow_through_close_fraction:
            return active(self, WcaSide.BUY, 0.72, "Low-side liquidity sweep rejected through confirmed swing liquidity with volume, wick, reclaim, and follow-through.", reason_codes=("wca.c10.sweep.buy",))
        return active(self, WcaSide.HOLD, 0.10, "No genuine sweep, rejection/reclaim, wick evidence, and follow-through confirmation.", evidence_strength=0.18, reason_codes=("wca.c10.sweep.no_setup",))
