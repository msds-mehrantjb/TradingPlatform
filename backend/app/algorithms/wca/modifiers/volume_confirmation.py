from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result, not_applicable_modifier
from backend.app.algorithms.wca.strategies.indicators import average_volume, completed_candles


class VolumeConfirmationModifier:
    modifier_id = "volume_confirmation"
    name = "Volume Confirmation"
    family = "volume"

    def evaluate(self, snapshot: WcaMarketSnapshot):
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        candles = completed_candles(snapshot)
        if len(candles) < 6:
            return not_applicable_modifier(self, "wca.modifier.volume_confirmation.insufficient_history", "Volume confirmation needs recent volume history.")
        latest = candles[-1]
        average = average_volume(candles[:-1], min(20, len(candles) - 1))
        if average <= 0:
            return not_applicable_modifier(self, "wca.modifier.volume_confirmation.no_average", "Average volume is unavailable.")
        ratio = latest.volume / average
        if ratio >= 1.2:
            return active_modifier(self, 1.06, "wca.modifier.volume_confirmation.expanded", "Latest volume confirms participation.")
        if ratio <= 0.7:
            return active_modifier(self, 0.92, "wca.modifier.volume_confirmation.thin", "Latest volume is thin versus recent history.")
        return active_modifier(self, 1.0, "wca.modifier.volume_confirmation.neutral", "Latest volume is near recent average.")
