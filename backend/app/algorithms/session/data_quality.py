"""Data-readiness checks for event-driven Session classification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from backend.app.algorithms.session.calendar import SessionClock
from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SessionConfig
from backend.app.algorithms.session.models import DataQualityState
from backend.app.algorithms.session.state import QuoteSnapshot, SessionBar


@dataclass(frozen=True)
class SessionDataQualityReport:
    state: DataQualityState
    confidence: float
    reason_codes: tuple[str, ...]
    evidence: dict[str, Any]
    block_new_entries: bool


def evaluate_session_data_quality(
    bars: tuple[SessionBar, ...],
    *,
    quote: QuoteSnapshot | None,
    clock: SessionClock | None,
    decision_time: datetime,
    config: SessionConfig = DEFAULT_SESSION_CONFIG,
) -> SessionDataQualityReport:
    sorted_bars = tuple(sorted(bars, key=lambda bar: bar.timestamp_utc))
    missing_minutes = _missing_minutes(sorted_bars)
    quote_age_ms = _quote_age_ms(quote, decision_time)
    reasons: list[str] = []
    state = DataQualityState.READY
    confidence = 0.9

    if clock is None:
        reasons.append("session.data.calendar_unavailable")
        state = DataQualityState.INCOMPLETE
        confidence = min(confidence, 0.25)
    elif not clock.regular_session:
        reasons.append(f"session.data.calendar_{clock.current_phase.value}")
        state = DataQualityState.STALE if clock.exchange_open else DataQualityState.INCOMPLETE
        confidence = min(confidence, 0.35)

    if not sorted_bars:
        reasons.append("session.data.no_finalized_bars")
        state = DataQualityState.INCOMPLETE
        confidence = min(confidence, 0.2)
    elif len(sorted_bars) < config.minimum_behavior_bars:
        reasons.append("session.data.warming_up")
        state = _worse_state(state, DataQualityState.WARMING_UP)
        confidence = min(confidence, 0.45)

    if any(bar.volume is None for bar in sorted_bars):
        reasons.append("session.data.volume_missing")
        state = DataQualityState.INVALID
        confidence = 0.0
    elif sorted_bars and all((bar.volume or 0) <= 0 for bar in sorted_bars):
        reasons.append("session.data.volume_unavailable")
        state = _worse_state(state, DataQualityState.INCOMPLETE)
        confidence = min(confidence, 0.35)

    if missing_minutes:
        reasons.append("session.data.missing_minute_gap")
        state = _worse_state(state, DataQualityState.INCOMPLETE)
        confidence = min(confidence, 0.5)

    if quote is None:
        reasons.append("session.data.quote_missing")
        state = _worse_state(state, DataQualityState.INCOMPLETE)
        confidence = min(confidence, 0.35)
    elif quote.bid is None or quote.ask is None or quote.bid <= 0 or quote.ask <= 0 or quote.ask <= quote.bid:
        reasons.append("session.data.quote_invalid")
        state = DataQualityState.INVALID
        confidence = 0.0
    elif quote_age_ms is not None and quote_age_ms > config.maximum_fresh_quote_age_ms:
        reasons.append("session.data.quote_stale")
        state = _worse_state(state, DataQualityState.STALE)
        confidence = min(confidence, 0.35)

    if not reasons:
        reasons.append("session.data.ready")

    return SessionDataQualityReport(
        state=state,
        confidence=confidence,
        reason_codes=tuple(dict.fromkeys(reasons)),
        evidence={
            "finalizedBarCount": len(sorted_bars),
            "requiredWarmupBars": config.minimum_behavior_bars,
            "missingMinuteCount": len(missing_minutes),
            "missingMinutes": missing_minutes,
            "quoteAgeMs": quote_age_ms,
            "calendarReady": clock is not None,
            "regularSession": bool(clock and clock.regular_session),
            "volumeAvailable": bool(sorted_bars) and all(bar.volume is not None and bar.volume > 0 for bar in sorted_bars),
        },
        block_new_entries=state != DataQualityState.READY,
    )


def _missing_minutes(bars: tuple[SessionBar, ...]) -> tuple[str, ...]:
    if len(bars) < 2:
        return ()
    expected = bars[0].timestamp_utc
    missing: list[str] = []
    for bar in bars:
        while expected < bar.timestamp_utc:
            missing.append(expected.isoformat())
            expected += timedelta(minutes=1)
        expected = bar.timestamp_utc + timedelta(minutes=1)
    return tuple(missing)


def _quote_age_ms(quote: QuoteSnapshot | None, decision_time: datetime) -> float | None:
    if quote is None:
        return None
    if quote.quote_age_ms is not None:
        return quote.quote_age_ms
    return max(0.0, (decision_time - quote.timestamp_utc).total_seconds() * 1000)


def _worse_state(current: DataQualityState, candidate: DataQualityState) -> DataQualityState:
    priority = {
        DataQualityState.READY: 0,
        DataQualityState.WARMING_UP: 1,
        DataQualityState.INCOMPLETE: 2,
        DataQualityState.STALE: 3,
        DataQualityState.INVALID: 4,
    }
    return candidate if priority[candidate] > priority[current] else current
