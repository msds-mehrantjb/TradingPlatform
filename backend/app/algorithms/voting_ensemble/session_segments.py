"""Which part of the trading day a finalized bar belongs to.

The session policy keys on this label, and until recently nothing produced one. The live
path reported `phase: "regular"`, which the policy's alias table normalises to `midday`, and
replay supplied no session state at all, which falls back to the same place. So a policy
written to stand aside at the open or run smaller into the close could only ever see
`midday`, in every environment.

One implementation, shared by the live producer and by replay, for the same reason the
event veto is shared. A segment boundary that drifted between them would make a gated live
run impossible to reproduce, and the divergence would be invisible -- both sides would
report a segment, just not the same one.

Boundaries are exchange-local (America/New_York), so they follow DST rather than tracking a
fixed UTC offset that is wrong for half the year.

Two session shapes exist, because an index future does not keep equity hours:

* **Equity RTH** -- premarket, then 09:30-16:00 continuous, then overnight. Weekends closed.
* **Futures Globex** -- Sunday 18:00 through Friday 17:00, broken every day by a 17:00-18:00
  maintenance halt. The RTH window inside it still matters (that is where the volume is), so
  the same open/midday/close labels apply there and the rest of the near-23-hour day is
  overnight. A profile that ignored the maintenance break would label an hour of no trading
  as tradable overnight.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo


VOTING_ENSEMBLE_SESSION_SEGMENT_VERSION = "voting_ensemble_session_segments_v2"

_EXCHANGE_TIMEZONE = ZoneInfo("America/New_York")

SEGMENT_PREMARKET = "premarket"
SEGMENT_OPEN = "open"
SEGMENT_MIDDAY = "midday"
SEGMENT_CLOSE = "close"
SEGMENT_OVERNIGHT = "overnight"
SEGMENT_MAINTENANCE = "maintenance_break"
SEGMENT_WEEKEND = "weekend"


@dataclass(frozen=True)
class SessionSegmentBoundaries:
    """Exchange-local equity segment boundaries, as "HH:MM".

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


@dataclass(frozen=True)
class SessionProfile:
    """An ordered set of segment upper bounds, plus how the week closes.

    `segments` is scanned in order and the first bound the bar end falls at or below wins.
    A `None` bound is the catch-all for the rest of the day.
    """

    name: str
    segments: tuple[tuple[str | None, str], ...]
    weekend_rule: str = "equity"

    def segment_at(self, minute: int) -> str:
        for bound, segment in self.segments:
            if bound is None:
                return segment
            parsed = _minute_of_day(bound)
            if parsed is not None and minute <= parsed:
                return segment
        return self.segments[-1][1]


def equity_profile(boundaries: SessionSegmentBoundaries | None = None) -> SessionProfile:
    """The equity RTH shape, honouring configured boundaries."""
    resolved = (boundaries or DEFAULT_SESSION_SEGMENT_BOUNDARIES)
    if resolved.as_minutes() is None:
        resolved = DEFAULT_SESSION_SEGMENT_BOUNDARIES
    return SessionProfile(
        name="equity_rth",
        segments=(
            (resolved.open_start, SEGMENT_PREMARKET),
            (resolved.open_end, SEGMENT_OPEN),
            (resolved.midday_end, SEGMENT_MIDDAY),
            (resolved.close_end, SEGMENT_CLOSE),
            (None, SEGMENT_OVERNIGHT),
        ),
        weekend_rule="equity",
    )


# Globex runs Sunday 18:00 to Friday 17:00 ET with a daily 17:00-18:00 halt. The RTH labels
# still apply inside it because that is where the volume is; everything outside is overnight.
FUTURES_GLOBEX_PROFILE = SessionProfile(
    name="futures_globex",
    segments=(
        ("09:30", SEGMENT_OVERNIGHT),
        ("10:30", SEGMENT_OPEN),
        ("15:00", SEGMENT_MIDDAY),
        ("17:00", SEGMENT_CLOSE),
        ("18:00", SEGMENT_MAINTENANCE),
        (None, SEGMENT_OVERNIGHT),
    ),
    weekend_rule="futures",
)


def resolve_session_segment(
    bar_end: datetime,
    *,
    boundaries: SessionSegmentBoundaries | None = None,
    profile: SessionProfile | None = None,
) -> str:
    """The segment the bar ending at `bar_end` belongs to.

    Falls back to the default boundaries when the configured ones are malformed or out of
    order, rather than half-applying them: a partially applied boundary set would put bars
    in segments no one intended and would be hard to spot in a decision record.
    """
    resolved = profile or equity_profile(boundaries)
    local = _exchange_local(bar_end)
    if _is_weekend(local, resolved.weekend_rule):
        return SEGMENT_WEEKEND
    return resolved.segment_at(local.hour * 60 + local.minute)


def session_profile_for_instrument(selected: Any | None) -> SessionProfile:
    """Globex for anything needing an extended session, equity RTH otherwise.

    Keyed on the declared capability rather than on a symbol list, so a future instrument
    that needs the same treatment gets it by declaring it rather than by being remembered.
    """
    required = tuple(getattr(selected, "required_capabilities", ()) or ()) if selected else ()
    if "extended_session" in required:
        return FUTURES_GLOBEX_PROFILE
    return EQUITY_RTH_PROFILE


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


def _is_weekend(local: datetime, rule: str) -> bool:
    weekday = local.weekday()  # Monday is 0, Sunday is 6
    minute = local.hour * 60 + local.minute
    if rule == "futures":
        # Friday 17:00 through Sunday 18:00 is closed, and the Sunday reopen is an evening.
        if weekday == 5:
            return True
        if weekday == 4 and minute > 17 * 60:
            return True
        if weekday == 6 and minute <= 18 * 60:
            return True
        return False
    return weekday >= 5


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


def _exchange_local(value: datetime) -> datetime:
    moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return moment.astimezone(_EXCHANGE_TIMEZONE)


def _new_york_minute_of_day(value: datetime) -> int:
    """Exchange-local minute past midnight, DST included."""
    local = _exchange_local(value)
    return local.hour * 60 + local.minute


# Built after the helpers it calls, since module bodies execute top to bottom.
EQUITY_RTH_PROFILE = equity_profile()


__all__ = [
    "DEFAULT_SESSION_SEGMENT_BOUNDARIES",
    "EQUITY_RTH_PROFILE",
    "FUTURES_GLOBEX_PROFILE",
    "SEGMENT_CLOSE",
    "SEGMENT_MAINTENANCE",
    "SEGMENT_MIDDAY",
    "SEGMENT_OPEN",
    "SEGMENT_OVERNIGHT",
    "SEGMENT_PREMARKET",
    "SEGMENT_WEEKEND",
    "SessionProfile",
    "SessionSegmentBoundaries",
    "VOTING_ENSEMBLE_SESSION_SEGMENT_VERSION",
    "equity_profile",
    "resolve_session_segment",
    "session_profile_for_instrument",
    "session_segment_boundaries_from_payload",
]
