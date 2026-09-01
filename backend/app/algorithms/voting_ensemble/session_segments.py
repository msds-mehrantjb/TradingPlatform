"""Which part of the trading day a finalized bar belongs to.

The session policy keys on this label, and until now nothing produced it. The live path
reported `phase: "regular"`, which the policy's alias table normalises to `midday`, and
replay supplied no session state at all, which falls back to the same place. So a policy
written to stand aside at the open or run smaller into the close could never see a segment
other than `midday` in any environment: the gate was enforcing against a label that had one
possible value.

One implementation, shared by the live producer and by replay, for the same reason the
event veto is shared. A segment boundary that drifted between them would make a gated live
run impossible to reproduce, and the divergence would be invisible -- both sides would
report a segment, just not the same one.

Boundaries are exchange-local (America/New_York), so they follow DST rather than tracking a
fixed UTC offset that is wrong for half the year.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo


VOTING_ENSEMBLE_SESSION_SEGMENT_VERSION = "voting_ensemble_session_segments_v1"

_EXCHANGE_TIMEZONE = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class SessionSegmentBoundaries:
    """Exchange-local segment boundaries, as "HH:MM".

    Read as half-open intervals on the bar's **end** timestamp: a one-minute bar ending at
    09:30 covers 09:29-09:30 and is therefore still premarket, while a bar ending at 16:00
    is the last bar of the close rather than the first of the overnight. Getting this edge
    wrong would misfile exactly the two bars a session policy cares most about.
    """

    open_start: str = "09:30"
    open_end: str = "10:30"
    midday_end: str = "15:00"
    close_end: str = "16:00"

    def as_minutes(self) -> tuple[int, int, int, int] | None:
        parsed = tuple(_minute_of_day(value) for value in (self.open_start, self.open_end, self.midday_end, self.close_end))
        if any(minute is None for minute in parsed):
            return None
        if not parsed[0] <= parsed[1] <= parsed[2] <= parsed[3]:
            return None
        return parsed  # type: ignore[return-value]


DEFAULT_SESSION_SEGMENT_BOUNDARIES = SessionSegmentBoundaries()


def resolve_session_segment(bar_end: datetime, *, boundaries: SessionSegmentBoundaries | None = None) -> str:
    """The segment the bar ending at `bar_end` belongs to.

    Falls back to the default boundaries when the configured ones are malformed or out of
    order, rather than half-applying them: a partially applied boundary set would put bars
    in segments no one intended and would be hard to spot in a decision record.
    """
    resolved = (boundaries or DEFAULT_SESSION_SEGMENT_BOUNDARIES).as_minutes()
    if resolved is None:
        resolved = DEFAULT_SESSION_SEGMENT_BOUNDARIES.as_minutes()
    open_start, open_end, midday_end, close_end = resolved  # type: ignore[misc]

    minute = _new_york_minute_of_day(bar_end)
    if minute <= open_start:
        return "premarket"
    if minute <= open_end:
        return "open"
    if minute <= midday_end:
        return "midday"
    if minute <= close_end:
        return "close"
    return "overnight"


def session_segment_boundaries_from_payload(payload: Mapping[str, Any] | None) -> SessionSegmentBoundaries:
    """Build boundaries from configuration, falling back to the defaults."""
    if not isinstance(payload, Mapping):
        return DEFAULT_SESSION_SEGMENT_BOUNDARIES
    try:
        boundaries = SessionSegmentBoundaries(
            open_start=str(payload.get("openStart", payload.get("open_start", DEFAULT_SESSION_SEGMENT_BOUNDARIES.open_start))),
            open_end=str(payload.get("openEnd", payload.get("open_end", DEFAULT_SESSION_SEGMENT_BOUNDARIES.open_end))),
            midday_end=str(payload.get("middayEnd", payload.get("midday_end", DEFAULT_SESSION_SEGMENT_BOUNDARIES.midday_end))),
            close_end=str(payload.get("closeEnd", payload.get("close_end", DEFAULT_SESSION_SEGMENT_BOUNDARIES.close_end))),
        )
    except Exception:
        return DEFAULT_SESSION_SEGMENT_BOUNDARIES
    return boundaries if boundaries.as_minutes() is not None else DEFAULT_SESSION_SEGMENT_BOUNDARIES


def _minute_of_day(value: Any) -> int | None:
    """Parse an "HH:MM" boundary into minutes past midnight."""
    text = str(value or "").strip()
    if not text or ":" not in text:
        return None
    hours, _, minutes = text.partition(":")
    try:
        hour, minute = int(hours), int(minutes)
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def _new_york_minute_of_day(value: datetime) -> int:
    """Exchange-local minute past midnight, DST included."""
    moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    local = moment.astimezone(_EXCHANGE_TIMEZONE)
    return local.hour * 60 + local.minute


__all__ = [
    "DEFAULT_SESSION_SEGMENT_BOUNDARIES",
    "SessionSegmentBoundaries",
    "VOTING_ENSEMBLE_SESSION_SEGMENT_VERSION",
    "resolve_session_segment",
    "session_segment_boundaries_from_payload",
]
