from __future__ import annotations

from backend.app.algorithms.wca.configuration import AdxTrendStrengthSettings
from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result, not_applicable_modifier
from backend.app.algorithms.wca.strategies.indicators import completed_candles, sma


class AdxTrendStrengthModifier:
    modifier_id = "adx_trend_strength"
    name = "ADX Trend Strength"
    family = "trend"

    def evaluate(self, snapshot: WcaMarketSnapshot, settings: AdxTrendStrengthSettings | None = None):
        settings = settings or AdxTrendStrengthSettings()
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        candles = completed_candles(snapshot)
        if len(candles) < settings.long_period:
            return not_applicable_modifier(self, "wca.modifier.adx_trend_strength.insufficient_history", "Trend-strength proxy needs completed long-period history.", settings=settings)
        close = max(candles[-1].close, 0.01)
        strength = abs(sma(candles, settings.short_period) - sma(candles, settings.long_period)) / close
        if strength >= settings.strong_threshold_percent:
            return active_modifier(self, 1.05, "wca.modifier.adx_trend_strength.strong", "Trend-strength proxy is strong.", settings=settings, market_status_contributions={"trend_strength": round(strength, 6)})
        if strength <= settings.weak_threshold_percent:
            return active_modifier(self, 0.96, "wca.modifier.adx_trend_strength.weak", "Trend-strength proxy is weak.", settings=settings, entry_requirement_multiplier=1.05, market_status_contributions={"trend_strength": round(strength, 6)})
        return active_modifier(self, 1.0, "wca.modifier.adx_trend_strength.neutral", "Trend-strength proxy is moderate.", settings=settings, market_status_contributions={"trend_strength": round(strength, 6)})
