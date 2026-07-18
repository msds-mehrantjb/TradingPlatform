from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result
from backend.app.algorithms.wca.strategies.indicators import completed_candles, vwap


class VwapPositionModifier:
    modifier_id = "vwap_position"
    name = "VWAP Position"
    family = "vwap"

    def evaluate(self, snapshot: WcaMarketSnapshot):
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        candles = completed_candles(snapshot)
        latest = candles[-1]
        distance = (latest.close - vwap(candles)) / max(latest.close, 0.01)
        if distance > 0.002:
            return active_modifier(self, 1.05, "wca.modifier.vwap_position.above", "Price is above VWAP, supporting long-biased strategy confidence or weight.")
        if distance < -0.002:
            return active_modifier(self, 0.95, "wca.modifier.vwap_position.below", "Price is below VWAP, reducing long-biased strategy confidence or weight.")
        return active_modifier(self, 1.0, "wca.modifier.vwap_position.neutral", "Price is near VWAP.")
