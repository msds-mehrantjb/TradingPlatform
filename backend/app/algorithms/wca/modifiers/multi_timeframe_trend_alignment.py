from __future__ import annotations

from backend.app.algorithms.wca.configuration import MultiTimeframeTrendAlignmentSettings
from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result, not_applicable_modifier
from backend.app.algorithms.wca.strategies.indicators import completed_candles, sma


class MultiTimeframeTrendAlignmentModifier:
    modifier_id = "multi_timeframe_trend_alignment"
    name = "Multi-Timeframe Trend Alignment"
    family = "trend"

    def evaluate(self, snapshot: WcaMarketSnapshot, settings: MultiTimeframeTrendAlignmentSettings | None = None):
        settings = settings or MultiTimeframeTrendAlignmentSettings()
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        candles = completed_candles(snapshot)
        if len(candles) < settings.long_period:
            return not_applicable_modifier(self, "wca.modifier.multi_timeframe_trend_alignment.insufficient_history", "Trend alignment needs completed long-period history.", settings=settings)
        short_up = sma(candles, settings.short_period) > sma(candles, settings.medium_period)
        long_up = sma(candles, settings.medium_period) > sma(candles, settings.long_period)
        if short_up == long_up:
            return active_modifier(self, 1.05, "wca.modifier.multi_timeframe_trend_alignment.aligned", "Short and long trend windows are aligned.", settings=settings, market_status_contributions={"trend_alignment": "aligned"})
        return active_modifier(self, 0.94, "wca.modifier.multi_timeframe_trend_alignment.conflicted", "Short and long trend windows conflict.", settings=settings, entry_requirement_multiplier=1.08, market_status_contributions={"trend_alignment": "conflicted"})
