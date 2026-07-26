from __future__ import annotations

from backend.app.algorithms.wca.configuration import SessionPhaseSettings
from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result
from backend.app.algorithms.wca.strategies.indicators import eastern_minutes


class SessionPhaseModifier:
    modifier_id = "session_phase"
    name = "Session Phase"
    family = "session"

    def evaluate(self, snapshot: WcaMarketSnapshot, settings: SessionPhaseSettings | None = None):
        settings = settings or SessionPhaseSettings()
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        minutes = eastern_minutes(snapshot.data_timestamp)
        if minutes < settings.opening_defensive_until_minutes or minutes >= settings.closing_defensive_start_minutes:
            return active_modifier(self, 0.9, "wca.modifier.session_phase.defensive", "Opening and closing phases reduce entry permission, weight, or size.", settings=settings, risk_multiplier=0.90, position_size_multiplier=0.90, entry_requirement_multiplier=1.10, market_status_contributions={"session": "defensive"})
        if minutes < settings.midday_start_minutes or minutes >= settings.afternoon_start_minutes:
            return active_modifier(self, 1.02, "wca.modifier.session_phase.active", "Morning or afternoon phase supports normal intraday participation.", settings=settings, market_status_contributions={"session": "active"})
        return active_modifier(self, 1.0, "wca.modifier.session_phase.midday", "Midday phase is neutral.", settings=settings, market_status_contributions={"session": "midday"})
