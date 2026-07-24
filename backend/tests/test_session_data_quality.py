from __future__ import annotations

from datetime import timedelta

from backend.app.algorithms.session import DataQualityState, resolve_session_clock
from backend.app.algorithms.session.data_quality import evaluate_session_data_quality
from backend.app.algorithms.session.state import QuoteSnapshot, SessionBar
from session_test_fixtures import NOW, SESSION_START


def test_session_data_quality_unknown_critical_data_blocks_entries() -> None:
    report = evaluate_session_data_quality((), quote=None, clock=resolve_session_clock(NOW), decision_time=NOW)

    assert report.state == DataQualityState.INCOMPLETE
    assert report.block_new_entries is True
    assert "session.data.no_finalized_bars" in report.reason_codes
    assert "session.data.quote_missing" in report.reason_codes


def test_session_data_quality_duplicate_ordering_does_not_hide_missing_minutes() -> None:
    bars = (
        _bar(0),
        _bar(1),
        _bar(3),
        _bar(3, event_id="bar-3-revised"),
    )
    report = evaluate_session_data_quality(bars, quote=_quote(), clock=resolve_session_clock(SESSION_START + timedelta(minutes=3)), decision_time=SESSION_START + timedelta(minutes=3))

    assert report.state in {DataQualityState.INCOMPLETE, DataQualityState.WARMING_UP}
    assert report.block_new_entries is True
    assert report.evidence["missingMinuteCount"] == 1


def test_session_data_quality_stale_quote_fails_closed() -> None:
    report = evaluate_session_data_quality(tuple(_bar(index) for index in range(10)), quote=_quote(age_ms=10_000), clock=resolve_session_clock(NOW), decision_time=NOW)

    assert report.state == DataQualityState.STALE
    assert report.block_new_entries is True
    assert "session.data.quote_stale" in report.reason_codes


def _bar(index: int, *, event_id: str | None = None) -> SessionBar:
    price = 100 + index * 0.01
    return SessionBar(event_id=event_id or f"bar-{index}", timestamp_utc=SESSION_START + timedelta(minutes=index), open=price, high=price + 0.05, low=price - 0.05, close=price, volume=100_000)


def _quote(*, age_ms: float = 100.0) -> QuoteSnapshot:
    return QuoteSnapshot(event_id="quote", timestamp_utc=NOW - timedelta(milliseconds=age_ms), bid=100.0, ask=100.01, quote_age_ms=age_ms)
