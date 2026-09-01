"""Scheduled-event veto: turns a dated calendar into the event state the gates already read.

The blackout machinery existed and was never fed. `gates.py` blocks on
`market.event_blackout`, the context pipeline raises an entry blackout from
`snapshot.economicEventState`, and `strategies/context/economic_event_context.py` carries 32
policies across seven event families -- but the live automatic evaluation context had no
event key at all, so `_event_state` produced `{}` and no scheduled event ever blocked
anything. This module supplies what was missing.

Three sources of veto, all expressed as the same state dict the gate already understands:

* **Scheduled economic events** -- FOMC, CPI, NFP and the rest -- from a dated calendar.
* **Auction windows** at the session open and close, where the book is not continuous.
* **Contract roll** for futures, which is inert until an instrument declares a roll rule.

The calendar is data, not a vendor integration. Events are supplied by settings or by the
caller, which is what lets replay pin the exact calendar a recorded run saw instead of
resolving against whatever is current when the replay executes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo


VOTING_ENSEMBLE_EVENT_CALENDAR_VERSION = "voting_ensemble_event_calendar_v1"

_EXCHANGE_TIMEZONE = ZoneInfo("America/New_York")

EVENT_STATE_CLEAR = "clear"
EVENT_STATE_IMMINENT = "imminent"
EVENT_STATE_ACTIVE = "active"
EVENT_STATE_STABILIZING = "stabilizing"

EVENT_REASON_SCHEDULED = "voting_ensemble.event_calendar.scheduled_event_blackout"
EVENT_REASON_AUCTION = "voting_ensemble.event_calendar.auction_window_blackout"
EVENT_REASON_ROLL = "voting_ensemble.event_calendar.contract_roll_blackout"
EVENT_REASON_DISABLED = "voting_ensemble.event_calendar.disabled"
EVENT_REASON_CLEAR = "voting_ensemble.event_calendar.clear"

# Importances the gate treats as blocking. Anything below these is reported but does not
# stop an entry, so a low-importance print does not halt the day.
BLOCKING_IMPORTANCES: frozenset[str] = frozenset({"high", "critical"})


@dataclass(frozen=True)
class ScheduledEvent:
    """One dated event and the window it protects."""

    event_type: str
    event_family: str
    importance: str
    scheduled_at: datetime
    pre_event_blackout_minutes: int = 15
    post_event_stabilization_minutes: int = 30
    caution_minutes: int = 60

    @property
    def blocks(self) -> bool:
        return self.importance.strip().lower() in BLOCKING_IMPORTANCES

    def state_at(self, moment: datetime) -> str | None:
        """Which phase of this event's window `moment` falls in, if any."""
        scheduled = _utc(self.scheduled_at)
        instant = _utc(moment)
        if scheduled - timedelta(minutes=self.pre_event_blackout_minutes) <= instant < scheduled:
            return EVENT_STATE_IMMINENT
        if scheduled <= instant <= scheduled + timedelta(minutes=self.post_event_stabilization_minutes):
            return EVENT_STATE_ACTIVE if instant <= scheduled + timedelta(minutes=1) else EVENT_STATE_STABILIZING
        if scheduled - timedelta(minutes=self.caution_minutes) <= instant < scheduled - timedelta(
            minutes=self.pre_event_blackout_minutes
        ):
            return EVENT_STATE_CLEAR
        return None


@dataclass(frozen=True)
class EventCalendarSettings:
    """The veto configuration, off unless enabled."""

    enabled: bool = False
    version: str = VOTING_ENSEMBLE_EVENT_CALENDAR_VERSION
    events: tuple[ScheduledEvent, ...] = ()
    # Minutes after the session open and before the session close where the book is an
    # auction rather than continuous trading.
    opening_auction_minutes: int = 1
    closing_auction_minutes: int = 5
    block_opening_auction: bool = True
    block_closing_auction: bool = True
    session_open: str = "09:30"
    session_close: str = "16:00"
    # Days before a futures contract's roll date where new entries stop. Inert while no
    # instrument declares a roll date.
    contract_roll_blackout_days: int = 1
    contract_roll_dates: tuple[date, ...] = ()


@dataclass(frozen=True)
class EventVetoDecision:
    """What the calendar said about one bar."""

    enabled: bool
    blackout_active: bool
    state: str
    importance: str
    reason_codes: tuple[str, ...]
    active_event: str | None
    explanation: str
    resolved_at: datetime | None = None

    def as_event_state(self) -> dict[str, Any]:
        """The dict the snapshot and gates already know how to read.

        Keys match `_event_blackout_active`: it checks `eventBlackoutActive` first, then
        `importance` against high/critical combined with `state` in active/imminent/shock.
        """
        return {
            "eventBlackoutActive": self.blackout_active,
            "state": self.state,
            "eventState": self.state,
            "importance": self.importance,
            "eventImportance": self.importance,
            "activeEvent": self.active_event,
            "reasonCodes": list(self.reason_codes),
            "calendarVersion": VOTING_ENSEMBLE_EVENT_CALENDAR_VERSION,
            "explanation": self.explanation,
            # The snapshot treats an event payload with no provenance as a freshness
            # failure, and rightly so. This state is resolved from the calendar at the bar
            # being evaluated, so the bar's own moment is both its source and its receipt.
            "providerTimestamp": _iso(self.resolved_at),
            "receiptTimestamp": _iso(self.resolved_at),
        }


def default_event_calendar() -> EventCalendarSettings:
    """Disabled, with the auction windows already described.

    Shipping it on would change live behaviour the moment someone upgraded, and an empty
    calendar that silently blocks nothing is worse than one that says it is off.
    """
    return EventCalendarSettings(enabled=False)


def event_calendar_from_payload(payload: Mapping[str, Any] | None) -> EventCalendarSettings:
    """Build the calendar from settings or a caller-supplied payload.

    A malformed calendar yields the disabled default rather than a partial one: a veto that
    silently covers only some of its events is worse than an absent veto, because the gap is
    invisible.
    """
    if not isinstance(payload, Mapping):
        return default_event_calendar()
    try:
        events: list[ScheduledEvent] = []
        for raw in payload.get("events") or ():
            if not isinstance(raw, Mapping):
                continue
            scheduled = _timestamp(raw.get("scheduledAt") or raw.get("scheduled_at"))
            if scheduled is None:
                continue
            events.append(
                ScheduledEvent(
                    event_type=str(raw.get("eventType") or raw.get("event_type") or "unknown"),
                    event_family=str(raw.get("eventFamily") or raw.get("event_family") or "Unknown"),
                    importance=str(raw.get("importance") or "high"),
                    scheduled_at=scheduled,
                    pre_event_blackout_minutes=int(raw.get("preEventBlackoutMinutes", 15)),
                    post_event_stabilization_minutes=int(raw.get("postEventStabilizationMinutes", 30)),
                    caution_minutes=int(raw.get("cautionMinutes", 60)),
                )
            )
        rolls: list[date] = []
        for raw_date in payload.get("contractRollDates") or ():
            parsed = _timestamp(raw_date)
            if parsed is not None:
                rolls.append(parsed.date())
        return EventCalendarSettings(
            enabled=bool(payload.get("enabled", False)),
            events=tuple(events),
            opening_auction_minutes=int(payload.get("openingAuctionMinutes", 1)),
            closing_auction_minutes=int(payload.get("closingAuctionMinutes", 5)),
            block_opening_auction=bool(payload.get("blockOpeningAuction", True)),
            block_closing_auction=bool(payload.get("blockClosingAuction", True)),
            session_open=str(payload.get("sessionOpen", "09:30")),
            session_close=str(payload.get("sessionClose", "16:00")),
            contract_roll_blackout_days=int(payload.get("contractRollBlackoutDays", 1)),
            contract_roll_dates=tuple(rolls),
        )
    except Exception:
        return default_event_calendar()


def resolve_event_veto(
    *,
    bar_end: datetime,
    settings: EventCalendarSettings | None = None,
) -> EventVetoDecision:
    """Whether this bar sits inside any protected window.

    Evaluated on the bar's own end timestamp, not the wall clock, so a replayed bar is
    judged against the calendar as it stood for that bar rather than for now.
    """
    calendar = settings or default_event_calendar()
    if not calendar.enabled:
        return EventVetoDecision(
            enabled=False,
            blackout_active=False,
            state=EVENT_STATE_CLEAR,
            importance="none",
            reason_codes=(EVENT_REASON_DISABLED,),
            active_event=None,
            explanation="Scheduled-event veto is disabled; no window blocks entries.",
            resolved_at=_utc(bar_end),
        )

    instant = _utc(bar_end)

    for event in calendar.events:
        state = event.state_at(instant)
        if state in (EVENT_STATE_IMMINENT, EVENT_STATE_ACTIVE, EVENT_STATE_STABILIZING) and event.blocks:
            return EventVetoDecision(
                enabled=True,
                blackout_active=True,
                state=EVENT_STATE_ACTIVE if state == EVENT_STATE_STABILIZING else state,
                importance=event.importance.lower(),
                reason_codes=(EVENT_REASON_SCHEDULED,),
                active_event=event.event_type,
                explanation=f"{event.event_family} / {event.event_type} is {state} at {instant.isoformat()}.",
                resolved_at=instant,
            )

    auction = _auction_window(instant, calendar)
    if auction is not None:
        return EventVetoDecision(
            enabled=True,
            blackout_active=True,
            state=EVENT_STATE_ACTIVE,
            importance="high",
            reason_codes=(EVENT_REASON_AUCTION,),
            active_event=auction,
            explanation=f"{auction} is an auction window rather than continuous trading.",
            resolved_at=instant,
        )

    if _in_roll_blackout(instant, calendar):
        return EventVetoDecision(
            enabled=True,
            blackout_active=True,
            state=EVENT_STATE_ACTIVE,
            importance="high",
            reason_codes=(EVENT_REASON_ROLL,),
            active_event="contract_roll",
            explanation="Contract roll blackout: liquidity is split across contracts.",
            resolved_at=instant,
        )

    return EventVetoDecision(
        enabled=True,
        blackout_active=False,
        state=EVENT_STATE_CLEAR,
        importance="none",
        reason_codes=(EVENT_REASON_CLEAR,),
        active_event=None,
        explanation="No scheduled event, auction window, or contract roll covers this bar.",
        resolved_at=instant,
    )


def _auction_window(instant: datetime, calendar: EventCalendarSettings) -> str | None:
    minute = _exchange_minute_of_day(instant)
    opens_at = _minute_of_day(calendar.session_open)
    closes_at = _minute_of_day(calendar.session_close)
    if opens_at is None or closes_at is None:
        return None
    if calendar.block_opening_auction and opens_at <= minute < opens_at + max(0, calendar.opening_auction_minutes):
        return "opening_auction"
    if calendar.block_closing_auction and closes_at - max(0, calendar.closing_auction_minutes) <= minute <= closes_at:
        return "closing_auction"
    return None


def _in_roll_blackout(instant: datetime, calendar: EventCalendarSettings) -> bool:
    if not calendar.contract_roll_dates:
        return False
    day = instant.astimezone(_EXCHANGE_TIMEZONE).date()
    window = max(0, calendar.contract_roll_blackout_days)
    return any(0 <= (roll - day).days <= window for roll in calendar.contract_roll_dates)


def _exchange_minute_of_day(value: datetime) -> int:
    local = _utc(value).astimezone(_EXCHANGE_TIMEZONE)
    return local.hour * 60 + local.minute


def _minute_of_day(value: Any) -> int | None:
    text = str(value or "").strip()
    if ":" not in text:
        return None
    hours, _, minutes = text.partition(":")
    try:
        return int(hours) * 60 + int(minutes)
    except ValueError:
        return None


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _iso(value: datetime | None) -> str | None:
    return None if value is None else _utc(value).isoformat()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


__all__ = [
    "BLOCKING_IMPORTANCES",
    "EVENT_REASON_AUCTION",
    "EVENT_REASON_CLEAR",
    "EVENT_REASON_DISABLED",
    "EVENT_REASON_ROLL",
    "EVENT_REASON_SCHEDULED",
    "EVENT_STATE_ACTIVE",
    "EVENT_STATE_CLEAR",
    "EVENT_STATE_IMMINENT",
    "EVENT_STATE_STABILIZING",
    "EventCalendarSettings",
    "EventVetoDecision",
    "ScheduledEvent",
    "VOTING_ENSEMBLE_EVENT_CALENDAR_VERSION",
    "default_event_calendar",
    "event_calendar_from_payload",
    "resolve_event_veto",
]
