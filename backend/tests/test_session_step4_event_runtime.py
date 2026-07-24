from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.app.algorithms.session import (
    ACCEPTED_SESSION_EVENT_TYPES,
    FINALIZED_ONE_MINUTE_BAR,
    MARKET_STATUS_CALENDAR_UPDATE,
    QUOTE_NBBO_UPDATE,
    REPLAY_RESET,
    SCHEDULED_EVENT_RISK_UPDATE,
    SESSION_RESET,
    DataQualityState,
    EventDrivenSessionRuntime,
    SessionBehavior,
)


SESSION_START = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)


def test_session_step4_accepted_event_types_are_explicit() -> None:
    assert ACCEPTED_SESSION_EVENT_TYPES == {
        FINALIZED_ONE_MINUTE_BAR,
        QUOTE_NBBO_UPDATE,
        MARKET_STATUS_CALENDAR_UPDATE,
        SCHEDULED_EVENT_RISK_UPDATE,
        SESSION_RESET,
        REPLAY_RESET,
    }


def test_session_step4_no_classification_before_bar_finalization() -> None:
    runtime = EventDrivenSessionRuntime()

    assert runtime.process_event(_bar(0, finalized=False)) is None
    assert runtime.state_for(symbol="SPY", session_date="2026-07-23", runtime_mode="replay").bars == ()


def test_session_step4_duplicate_events_are_idempotent() -> None:
    runtime = EventDrivenSessionRuntime()
    first = _run_ready_stream(runtime)
    duplicate = runtime.process_event(_quote(9))

    assert first is not None
    assert duplicate is not None
    assert duplicate.deterministic_json() == first.deterministic_json()


def test_session_step4_out_of_order_bars_are_deterministic_and_flag_late_bars() -> None:
    stream = [*[_bar(index) for index in (0, 1, 3, 2, 4, 5, 6, 7, 8, 9)], _quote(9)]
    first = _run_stream(EventDrivenSessionRuntime(), stream)
    second = _run_stream(EventDrivenSessionRuntime(), stream)

    assert first is not None
    assert first.deterministic_json() == second.deterministic_json()
    assert first.evidence["latestTimestamp"] == "2026-07-23T13:39:00+00:00"
    assert first.evidence["runtime"]["lateBarEventIds"] == ("bar-2-r0",)


def test_session_step4_missing_minute_gap_is_explicit_and_fails_closed() -> None:
    runtime = EventDrivenSessionRuntime()
    stream = [_bar(index) for index in (0, 1, 2, 3, 4, 6, 7, 8, 9, 10)]
    result = _run_stream(runtime, [*stream, _quote(10)])

    assert result is not None
    assert result.data_quality_state == DataQualityState.INCOMPLETE
    assert result.block_new_entries is True
    assert result.evidence["dataQuality"]["missingMinuteCount"] == 1
    assert result.evidence["dataQuality"]["missingMinutes"] == ("2026-07-23T13:35:00+00:00",)


def test_session_step4_stale_quote_fails_closed() -> None:
    runtime = EventDrivenSessionRuntime()
    result = _run_stream(runtime, [*[_bar(index) for index in range(10)], _quote(0)])

    assert result is not None
    assert result.data_quality_state == DataQualityState.STALE
    assert result.block_new_entries is True
    assert "session.data.quote_stale" in result.reason_codes


def test_session_step4_session_reset_clears_isolated_state() -> None:
    runtime = EventDrivenSessionRuntime()
    assert _run_ready_stream(runtime) is not None

    assert runtime.process_event(
        {
            "type": SESSION_RESET,
            "event_id": "reset-2026-07-23",
            "symbol": "SPY",
            "runtime_mode": "replay",
            "timestamp": _timestamp(9),
        }
    ) is None

    assert runtime.state_for(symbol="SPY", session_date="2026-07-23", runtime_mode="replay") is None
    after_reset = runtime.process_event(_bar(10))
    assert after_reset is not None
    assert after_reset.data_quality_state == DataQualityState.INCOMPLETE
    assert "session.data.warming_up" in after_reset.reason_codes


def test_session_step4_session_rollover_is_isolated_by_date() -> None:
    runtime = EventDrivenSessionRuntime()

    runtime.process_event(_bar(389, event_id="prior-session-last"))
    runtime.process_event(_bar_at(datetime(2026, 7, 24, 13, 30, tzinfo=UTC), event_id="next-session-open"))

    assert runtime.state_for(symbol="SPY", session_date="2026-07-23", runtime_mode="replay") is not None
    assert runtime.state_for(symbol="SPY", session_date="2026-07-24", runtime_mode="replay") is not None
    assert runtime.state_for(symbol="SPY", session_date="2026-07-23", runtime_mode="paper") is None


def test_session_step4_replay_reset_clears_symbol_and_mode_only() -> None:
    runtime = EventDrivenSessionRuntime()
    runtime.process_event(_bar(0))
    runtime.process_event({**_bar(0), "runtime_mode": "paper", "event_id": "paper-bar-0"})

    assert runtime.process_event(
        {
            "type": REPLAY_RESET,
            "event_id": "replay-reset",
            "symbol": "SPY",
            "runtime_mode": "replay",
            "timestamp": _timestamp(0),
        }
    ) is None

    assert runtime.state_for(symbol="SPY", session_date="2026-07-23", runtime_mode="replay") is None
    assert runtime.state_for(symbol="SPY", session_date="2026-07-23", runtime_mode="paper") is not None


def test_session_step4_revised_bar_recomputes_state_and_records_revision() -> None:
    runtime = EventDrivenSessionRuntime()
    _run_ready_stream(runtime)

    revised = runtime.process_event(_bar(5, close_offset=0.50, event_id="bar-5-r1", revision=1))

    assert revised is not None
    assert revised.evidence["runtime"]["revisedBarTimestamps"] == ("2026-07-23T13:35:00+00:00",)
    assert revised.evidence["featureSnapshotId"].startswith("session-feature-")
    assert revised.evidence["classificationId"].startswith("session-classification-")


def test_session_step4_market_calendar_and_event_risk_updates_are_accepted() -> None:
    runtime = EventDrivenSessionRuntime()
    _run_ready_stream(runtime)
    runtime.process_event(
        {
            "type": MARKET_STATUS_CALENDAR_UPDATE,
            "event_id": "calendar-open",
            "symbol": "SPY",
            "runtime_mode": "replay",
            "timestamp": _timestamp(9),
            "status": "open",
            "isOpen": True,
        }
    )
    result = runtime.process_event(
        {
            "type": SCHEDULED_EVENT_RISK_UPDATE,
            "event_id": "event-risk",
            "symbol": "SPY",
            "runtime_mode": "replay",
            "timestamp": _timestamp(9),
            "riskState": "blackout",
            "blockNewEntries": True,
            "reasonCodes": ("session.event_risk.blackout",),
        }
    )

    assert result is not None
    assert result.behavior == SessionBehavior.EVENT_DRIVEN
    assert result.direction_bias == "cash"
    assert result.block_new_entries is True
    assert "session.event_risk.blackout" in result.reason_codes


def test_session_step4_restart_snapshot_restores_state_boundary() -> None:
    runtime = EventDrivenSessionRuntime()
    original = _run_ready_stream(runtime)
    restored = EventDrivenSessionRuntime.restore(runtime.snapshot())

    after_restore = restored.process_event(_quote(9))

    assert original is not None
    assert after_restore is not None
    assert after_restore.deterministic_json() == original.deterministic_json()


def test_session_step4_ordered_event_stream_replay_is_byte_equivalent() -> None:
    stream = [*[_bar(index) for index in range(10)], _quote(9)]
    first = _run_stream(EventDrivenSessionRuntime(), stream)
    second = _run_stream(EventDrivenSessionRuntime(), stream)

    assert first is not None
    assert second is not None
    assert first.deterministic_json() == second.deterministic_json()


def _run_ready_stream(runtime: EventDrivenSessionRuntime):
    return _run_stream(runtime, [*[_bar(index) for index in range(10)], _quote(9)])


def _run_stream(runtime: EventDrivenSessionRuntime, stream: list[dict[str, object]]):
    result = None
    for event in stream:
        result = runtime.process_event(event)
    return result


def _bar(index: int, *, finalized: bool = True, close_offset: float = 0.0, event_id: str | None = None, revision: int = 0) -> dict[str, object]:
    return _bar_at(SESSION_START + timedelta(minutes=index), finalized=finalized, close_offset=close_offset, event_id=event_id or f"bar-{index}-r{revision}", revision=revision)


def _bar_at(
    timestamp: datetime,
    *,
    finalized: bool = True,
    close_offset: float = 0.0,
    event_id: str,
    revision: int = 0,
) -> dict[str, object]:
    minute = int((timestamp - SESSION_START).total_seconds() // 60)
    close = 100 + minute * 0.02 + close_offset
    return {
        "type": FINALIZED_ONE_MINUTE_BAR,
        "event_id": event_id,
        "symbol": "SPY",
        "runtime_mode": "replay",
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "open": close - 0.03,
        "high": close + 0.08,
        "low": close - 0.07,
        "close": close,
        "volume": 100_000,
        "finalized": finalized,
        "revision": revision,
    }


def _quote(index: int) -> dict[str, object]:
    timestamp = SESSION_START + timedelta(minutes=index)
    return {
        "type": QUOTE_NBBO_UPDATE,
        "event_id": f"quote-{index}",
        "symbol": "SPY",
        "runtime_mode": "replay",
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "bid": 100.10,
        "ask": 100.11,
    }


def _timestamp(index: int) -> str:
    return (SESSION_START + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
