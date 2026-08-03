"""Authoritative WCA market/session validation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.app.algorithms.wca.market_calendar import EXCHANGE_TIMEZONE, WcaMarketCalendar


WCA_SESSION_VALIDATION_VERSION = "wca_session_validation_v1"


@dataclass(frozen=True)
class WcaBrokerClock:
    timestamp: datetime
    is_open: bool
    next_open: datetime | None = None
    next_close: datetime | None = None
    source: str = "alpaca_paper"
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class WcaEntrySessionValidation:
    current_timestamp: datetime
    market_is_open: bool
    allowed_session_window: bool
    broker_clock_open: bool
    broker_clock_available: bool
    calendar_session_exists: bool
    before_entry_cutoff: bool
    reason_codes: tuple[str, ...]


def validate_wca_entry_session(
    *,
    timestamp: datetime,
    entry_cutoff_minutes: int,
    calendar: WcaMarketCalendar | None = None,
    broker_clock: WcaBrokerClock | None = None,
    require_broker_clock: bool = False,
) -> WcaEntrySessionValidation:
    current = timestamp.astimezone(timezone.utc)
    active_calendar = calendar or WcaMarketCalendar()
    session = active_calendar.session_for(current)
    local_current = current.astimezone(EXCHANGE_TIMEZONE)
    broker_available = broker_clock is not None
    broker_open = bool(broker_clock.is_open) if broker_clock is not None else not require_broker_clock
    calendar_open = bool(session and session.market_open <= local_current < session.market_close)
    cutoff = int(entry_cutoff_minutes)
    current_minutes = local_current.hour * 60 + local_current.minute
    before_cutoff = bool(session and current_minutes < cutoff)

    reasons: list[str] = [WCA_SESSION_VALIDATION_VERSION]
    if session is None:
        reasons.append("wca.session.calendar_session_missing")
    if not calendar_open:
        reasons.append("wca.session.calendar_market_closed")
    if require_broker_clock and broker_clock is None:
        reasons.append("wca.session.broker_clock_unavailable")
    if broker_clock is not None and not broker_clock.is_open:
        reasons.append("wca.session.broker_clock_closed")
    if not before_cutoff:
        reasons.append("wca.session.entry_cutoff_reached")

    market_is_open = calendar_open and broker_open
    allowed = market_is_open and before_cutoff
    if allowed:
        reasons.append("wca.session.entry_window_open")
    else:
        reasons.append("wca.session.entry_window_blocked")
    return WcaEntrySessionValidation(
        current_timestamp=current,
        market_is_open=market_is_open,
        allowed_session_window=allowed,
        broker_clock_open=broker_open,
        broker_clock_available=broker_available,
        calendar_session_exists=session is not None,
        before_entry_cutoff=before_cutoff,
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


__all__ = [
    "WCA_SESSION_VALIDATION_VERSION",
    "WcaBrokerClock",
    "WcaEntrySessionValidation",
    "validate_wca_entry_session",
]
