from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result, not_applicable_modifier
from backend.app.algorithms.wca.strategies.indicators import completed_candles, sma


class AdxTrendStrengthModifier:
    modifier_id = "adx_trend_strength"
    name = "ADX Trend Strength"
    family = "trend"

    def evaluate(self, snapshot: WcaMarketSnapshot):
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        candles = completed_candles(snapshot)
        if len(candles) < 20:
            return not_applicable_modifier(self, "wca.modifier.adx_trend_strength.insufficient_history", "Trend-strength proxy needs 20 completed candles.")
        close = max(candles[-1].close, 0.01)
        strength = abs(sma(candles, 10) - sma(candles, 20)) / close
        if strength >= 0.004:
            return active_modifier(self, 1.05, "wca.modifier.adx_trend_strength.strong", "Trend-strength proxy is strong.")
        if strength <= 0.001:
            return active_modifier(self, 0.96, "wca.modifier.adx_trend_strength.weak", "Trend-strength proxy is weak.")
        return active_modifier(self, 1.0, "wca.modifier.adx_trend_strength.neutral", "Trend-strength proxy is moderate.")
