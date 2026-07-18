from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result
from backend.app.algorithms.wca.strategies.indicators import eastern_minutes


class SessionPhaseModifier:
    modifier_id = "session_phase"
    name = "Session Phase"
    family = "session"

    def evaluate(self, snapshot: WcaMarketSnapshot):
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        minutes = eastern_minutes(snapshot.data_timestamp)
        if minutes < 10 * 60 or minutes >= 15 * 60 + 30:
            return active_modifier(self, 0.9, "wca.modifier.session_phase.defensive", "Opening and closing phases reduce entry permission, weight, or size.")
        if minutes < 11 * 60 + 30 or minutes >= 13 * 60 + 30:
            return active_modifier(self, 1.02, "wca.modifier.session_phase.active", "Morning or afternoon phase supports active intraday participation.")
        return active_modifier(self, 1.0, "wca.modifier.session_phase.midday", "Midday phase is neutral.")
