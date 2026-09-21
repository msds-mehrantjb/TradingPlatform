"""The clock the Voting Ensemble trades on.

Alpaca's free plan serves full-market (SIP) data fifteen minutes late. To paper trade on
it honestly, the live pipeline runs on a clock that is that far behind the wall: a bar
is "just closed", a quote is "fresh", an order is "stale" and the session "closes" all
relative to the data clock. Every freshness gate keeps its live tolerance; only the clock
it is measured against moves.

A delay of zero is the live system, which is also the default, so subscribing to a live
feed is configuration: set ``VOTING_ENSEMBLE_DATA_DELAY_SECONDS=0`` and point
``VOTING_ENSEMBLE_MARKET_DATA_FEED`` at the subscribed feed.

Operational timestamps (persistence, heartbeats, logs) stay on the wall clock.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

DATA_DELAY_ENV = "VOTING_ENSEMBLE_DATA_DELAY_SECONDS"
MARKET_DATA_FEED_ENV = "VOTING_ENSEMBLE_MARKET_DATA_FEED"
DEFAULT_MARKET_DATA_FEED = "iex"
# Alpaca refuses SIP queries newer than fifteen minutes on the free plan.
FREE_PLAN_SIP_DELAY_SECONDS = 15 * 60


def data_delay() -> timedelta:
    raw = os.environ.get(DATA_DELAY_ENV, "").strip()
    try:
        seconds = float(raw) if raw else 0.0
    except ValueError:
        seconds = 0.0
    return timedelta(seconds=max(0.0, seconds))


def data_now() -> datetime:
    """The current moment on the data clock, in UTC."""
    return datetime.now(UTC) - data_delay()


def to_data_clock(wall: datetime) -> datetime:
    """A wall-clock instant expressed on the data clock."""
    value = wall.replace(tzinfo=UTC) if wall.tzinfo is None else wall.astimezone(UTC)
    return value - data_delay()


def delayed() -> bool:
    return data_delay() > timedelta(0)


def market_data_feed() -> str:
    return (os.environ.get(MARKET_DATA_FEED_ENV, "").strip() or DEFAULT_MARKET_DATA_FEED).lower()
