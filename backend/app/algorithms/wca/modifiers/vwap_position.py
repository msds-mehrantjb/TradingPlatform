from __future__ import annotations

from backend.app.algorithms.wca.configuration import VwapPositionSettings
from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result
from backend.app.algorithms.wca.strategies.indicators import completed_candles, vwap


class VwapPositionModifier:
    modifier_id = "vwap_position"
    name = "VWAP Position"
    family = "vwap"

    def evaluate(self, snapshot: WcaMarketSnapshot, settings: VwapPositionSettings | None = None):
        settings = settings or VwapPositionSettings()
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        candles = completed_candles(snapshot)
        latest = candles[-1]
        distance = (latest.close - vwap(candles)) / max(latest.close, 0.01)
        if distance > settings.neutral_band_percent:
            return active_modifier(self, settings.supportive_multiplier, "wca.modifier.vwap_position.above", "Price is above VWAP; contextual VWAP support is elevated.", settings=settings, market_status_contributions={"vwap_distance_percent": round(distance, 6)})
        if distance < -settings.neutral_band_percent:
            return active_modifier(self, settings.defensive_multiplier, "wca.modifier.vwap_position.below", "Price is below VWAP; contextual VWAP support is reduced.", settings=settings, market_status_contributions={"vwap_distance_percent": round(distance, 6)})
        return active_modifier(self, 1.0, "wca.modifier.vwap_position.neutral", "Price is near VWAP.", settings=settings, market_status_contributions={"vwap_distance_percent": round(distance, 6)})
