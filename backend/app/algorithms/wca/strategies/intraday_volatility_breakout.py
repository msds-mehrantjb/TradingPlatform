from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from typing import Any

from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, average_volume, close_location, coerce_strategy_settings, completed_candles, definition_for, eastern_minutes, has_volume, invalid_result, not_applicable


class IntradayVolatilityBreakoutStrategy:
    strategy_id = "C8"
    slug = "intraday_volatility_breakout"
    name = "Intraday/Volatility Breakout"
    family = "breakout"
    version = "wca_intraday_volatility_breakout_v1"
    base_weight = 0.10
    configuration = StrategyConfig()
    minimum_data_requirements = ("31 completed intraday candles",)
    performance_history_identifier = "wca.intraday_volatility_breakout.performance.v1"
    backtest_diagnostic_identifier = "wca.intraday_volatility_breakout.backtest.v1"

    @property
    def definition(self) -> WcaStrategyDefinition:
        return definition_for(self)

    def evaluate(self, market: WcaMarketSnapshot, config: Any = None) -> WcaStrategyEvaluation:
        from backend.app.algorithms.wca.configuration import IntradayVolatilityBreakoutSettings

        config = coerce_strategy_settings(IntradayVolatilityBreakoutSettings, config)
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "Intraday volatility breakout is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        minutes = eastern_minutes(market.data_timestamp)
        if minutes <= 10 * 60 + 30 or minutes >= 15 * 60 + 30:
            return not_applicable(self, "wca.session.outside_intraday_breakout_window", "Intraday volatility breakout excludes the ORB and closing windows.")
        candles = completed_candles(market)
        warmup = config.reference_lookback + config.compression_lookback + 1
        if len(candles) < warmup:
            return not_applicable(self, "wca.data.insufficient_warmup", "Waiting for intraday volatility structure.")
        if not has_volume(candles):
            return not_applicable(self, "wca.data.missing_volume", "Intraday breakout requires volume participation.")
        latest = candles[-1]
        structure = candles[-(config.reference_lookback + 1):-1]
        prior_high = max(c.high for c in structure)
        prior_low = min(c.low for c in structure)
        recent_ranges = tuple(c.high - c.low for c in structure[-config.compression_lookback:])
        earlier_ranges = tuple(c.high - c.low for c in structure[:config.compression_lookback])
        recent_average_range = sum(recent_ranges) / len(recent_ranges)
        earlier_average_range = sum(earlier_ranges) / len(earlier_ranges)
        compression = recent_average_range <= earlier_average_range * config.compression_ratio
        expansion = (latest.high - latest.low) >= max(0.01, recent_average_range) * config.expansion_ratio
        volume_expansion = latest.volume >= average_volume(candles[:-1], 20) * config.volume_expansion_ratio
        quote_cost = ((market.quote.ask - market.quote.bid) / latest.close) if market.quote is not None else 0
        buy_edge = max(0, latest.close - prior_high) / latest.close - quote_cost
        sell_edge = max(0, prior_low - latest.close) / latest.close - quote_cost
        buy_structure = latest.close > prior_high and close_location(latest) >= 0.60
        sell_structure = latest.close < prior_low and close_location(latest) <= 0.40
        if compression and expansion and volume_expansion and buy_structure and buy_edge >= config.minimum_expected_edge_after_costs:
            return active(self, WcaSide.BUY, 0.66, "Compression expanded through structural resistance with participation and edge after costs.", reason_codes=("wca.c8.vol_breakout.buy",))
        if compression and expansion and volume_expansion and sell_structure and sell_edge >= config.minimum_expected_edge_after_costs:
            return active(self, WcaSide.SELL, 0.66, "Compression expanded through structural support with participation and edge after costs.", reason_codes=("wca.c8.vol_breakout.sell",))
        return active(self, WcaSide.HOLD, 0.10, "No intraday breakout with compression, expansion, participation, structure, and edge.", evidence_strength=0.18, reason_codes=("wca.c8.vol_breakout.no_setup",))
