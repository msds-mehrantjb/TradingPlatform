from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result, not_applicable_modifier
from backend.app.algorithms.wca.strategies.indicators import completed_candles, sma


class MultiTimeframeTrendAlignmentModifier:
    modifier_id = "multi_timeframe_trend_alignment"
    name = "Multi-Timeframe Trend Alignment"
    family = "trend"

    def evaluate(self, snapshot: WcaMarketSnapshot):
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        candles = completed_candles(snapshot)
        if len(candles) < 50:
            return not_applicable_modifier(self, "wca.modifier.multi_timeframe_trend_alignment.insufficient_history", "Trend alignment needs 50 completed candles.")
        short_up = sma(candles, 10) > sma(candles, 20)
        long_up = sma(candles, 20) > sma(candles, 50)
        if short_up == long_up:
            return active_modifier(self, 1.05, "wca.modifier.multi_timeframe_trend_alignment.aligned", "Short and long trend windows are aligned.")
        return active_modifier(self, 0.94, "wca.modifier.multi_timeframe_trend_alignment.conflicted", "Short and long trend windows conflict.")
