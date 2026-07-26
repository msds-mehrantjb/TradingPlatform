from __future__ import annotations

from backend.app.algorithms.wca.configuration import MarketStructureSettings
from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result, not_applicable_modifier
from backend.app.algorithms.wca.strategies.indicators import completed_candles


class MarketStructureModifier:
    modifier_id = "market_structure"
    name = "Market Structure"
    family = "structure"

    def evaluate(self, snapshot: WcaMarketSnapshot, settings: MarketStructureSettings | None = None):
        settings = settings or MarketStructureSettings()
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        candles = completed_candles(snapshot)
        if len(candles) < settings.lookback_bars + 1:
            return not_applicable_modifier(self, "wca.modifier.market_structure.insufficient_history", "Market structure needs completed lookback history.", settings=settings)
        latest = candles[-1]
        prior = candles[-settings.lookback_bars - 1 : -1]
        if latest.close > max(candle.high for candle in prior):
            return active_modifier(self, settings.breakout_multiplier, "wca.modifier.market_structure.breakout", "Close is outside the recent upper structure reference.", settings=settings, market_status_contributions={"structure_state": "upper_break"})
        if latest.close < min(candle.low for candle in prior):
            return active_modifier(self, settings.breakdown_multiplier, "wca.modifier.market_structure.breakdown", "Close is outside the recent lower structure reference.", settings=settings, market_status_contributions={"structure_state": "lower_break"})
        return active_modifier(self, 1.0, "wca.modifier.market_structure.range", "Close remains inside recent structure.", settings=settings, market_status_contributions={"structure_state": "range"})
