from __future__ import annotations

from backend.app.algorithms.wca.configuration import VolumeConfirmationSettings
from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result, not_applicable_modifier
from backend.app.algorithms.wca.strategies.indicators import average_volume, completed_candles


class VolumeConfirmationModifier:
    modifier_id = "volume_confirmation"
    name = "Volume Confirmation"
    family = "volume"

    def evaluate(self, snapshot: WcaMarketSnapshot, settings: VolumeConfirmationSettings | None = None):
        settings = settings or VolumeConfirmationSettings()
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        candles = completed_candles(snapshot)
        if len(candles) < 6:
            return not_applicable_modifier(self, "wca.modifier.volume_confirmation.insufficient_history", "Volume confirmation needs recent volume history.", settings=settings)
        latest = candles[-1]
        average = average_volume(candles[:-1], min(settings.lookback_bars, len(candles) - 1))
        if average <= 0:
            return not_applicable_modifier(self, "wca.modifier.volume_confirmation.no_average", "Average volume is unavailable.", settings=settings)
        ratio = latest.volume / average
        if ratio >= settings.expanded_volume_ratio:
            return active_modifier(self, 1.06, "wca.modifier.volume_confirmation.expanded", "Latest volume confirms participation.", settings=settings, market_status_contributions={"volume_ratio": round(ratio, 4)})
        if ratio <= settings.thin_volume_ratio:
            return active_modifier(self, 0.92, "wca.modifier.volume_confirmation.thin", "Latest volume is thin versus recent history.", settings=settings, risk_multiplier=0.95, position_size_multiplier=0.95, market_status_contributions={"volume_ratio": round(ratio, 4)})
        return active_modifier(self, 1.0, "wca.modifier.volume_confirmation.neutral", "Latest volume is near recent average.", settings=settings, market_status_contributions={"volume_ratio": round(ratio, 4)})
