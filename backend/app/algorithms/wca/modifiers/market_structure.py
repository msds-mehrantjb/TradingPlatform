from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result, not_applicable_modifier
from backend.app.algorithms.wca.strategies.indicators import completed_candles


class MarketStructureModifier:
    modifier_id = "market_structure"
    name = "Market Structure"
    family = "structure"

    def evaluate(self, snapshot: WcaMarketSnapshot):
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        candles = completed_candles(snapshot)
        if len(candles) < 21:
            return not_applicable_modifier(self, "wca.modifier.market_structure.insufficient_history", "Market structure needs 21 completed candles.")
        latest = candles[-1]
        prior = candles[-21:-1]
        if latest.close > max(candle.high for candle in prior):
            return active_modifier(self, 1.05, "wca.modifier.market_structure.breakout", "Close is above recent structure.")
        if latest.close < min(candle.low for candle in prior):
            return active_modifier(self, 0.95, "wca.modifier.market_structure.breakdown", "Close is below recent structure.")
        return active_modifier(self, 1.0, "wca.modifier.market_structure.range", "Close remains inside recent structure.")
