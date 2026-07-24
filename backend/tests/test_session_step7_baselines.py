from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from backend.app.algorithms.session import (
    SessionConfig,
    VolatilityState,
    analyze_session_participation,
    analyze_session_volatility,
    build_session_baseline_artifact,
    classify_session,
    select_session_baseline_artifact,
)


def test_session_step7_same_minute_lookup_uses_exchange_open_minute() -> None:
    config = SessionConfig(minimum_baseline_samples=2)
    artifact = build_session_baseline_artifact(
        "SPY",
        [_session(date(2026, 7, 20), volume=100), _session(date(2026, 7, 21), volume=120)],
        cutoff_date=date(2026, 7, 23),
        config=config,
    )
    current = _session(date(2026, 7, 23), volume=110)[:6]

    volume = analyze_session_participation(current, symbol="SPY", baseline_artifact=artifact, config=config)

    assert volume["status"] == "ready"
    assert volume["baseline"]["minuteFromOpen"] == 5
    assert volume["expectedCumulativeVolume"] == 660
    assert volume["currentCumulativeVolume"] == 660
    assert volume["volumePaceRatio"] == 1


def test_session_step7_historical_cutoff_excludes_current_day() -> None:
    config = SessionConfig(minimum_baseline_samples=2)
    artifact = build_session_baseline_artifact(
        "SPY",
        [
            _session(date(2026, 7, 20), volume=100),
            _session(date(2026, 7, 21), volume=100),
            _session(date(2026, 7, 23), volume=10_000),
        ],
        cutoff_date=date(2026, 7, 23),
        config=config,
    )
    volume = analyze_session_participation(_session(date(2026, 7, 23), volume=100)[:6], symbol="SPY", baseline_artifact=artifact, config=config)

    assert artifact.source_session_dates == ("2026-07-20", "2026-07-21")
    assert volume["expectedCumulativeVolume"] == 600
    assert volume["baseline"]["baselineCutoffDate"] == "2026-07-23"


def test_session_step7_baseline_selection_is_valid_at_decision_time() -> None:
    old = build_session_baseline_artifact("SPY", [_session(date(2026, 7, 20), volume=100)], cutoff_date=date(2026, 7, 21), valid_from=datetime(2026, 7, 21, tzinfo=UTC), valid_until=datetime(2026, 7, 23, tzinfo=UTC))
    current = build_session_baseline_artifact("SPY", [_session(date(2026, 7, 21), volume=120)], cutoff_date=date(2026, 7, 22), valid_from=datetime(2026, 7, 23, tzinfo=UTC))

    selected = select_session_baseline_artifact([old, current], symbol="SPY", decision_time="2026-07-23T13:35:00Z")

    assert selected is current
    assert select_session_baseline_artifact([old, current], symbol="SPY", decision_time="2026-07-22T13:35:00Z") is old


def test_session_step7_early_close_uses_early_close_session_type() -> None:
    config = SessionConfig(minimum_baseline_samples=2)
    artifact = build_session_baseline_artifact(
        "SPY",
        [_session(date(2024, 11, 29), start=datetime(2024, 11, 29, 14, 30, tzinfo=UTC), volume=200), _session(date(2025, 11, 28), start=datetime(2025, 11, 28, 14, 30, tzinfo=UTC), volume=220)],
        cutoff_date=date(2026, 11, 27),
        config=config,
    )
    current = _session(date(2026, 11, 27), start=datetime(2026, 11, 27, 14, 30, tzinfo=UTC), volume=210)[:6]

    volume = analyze_session_participation(current, symbol="SPY", baseline_artifact=artifact, config=config)

    assert volume["status"] == "ready"
    assert volume["baseline"]["sessionType"] == "early_close"
    assert volume["baseline"]["minuteFromOpen"] == 5


def test_session_step7_missing_baseline_is_unknown() -> None:
    result = analyze_session_participation(_session(date(2026, 7, 23), volume=100)[:6], symbol="SPY")

    assert result["status"] == "unknown"
    assert result["volumePaceRatio"] is None
    assert result["baseline"]["baselineVersion"] is None


def test_session_step7_low_sample_count_is_not_ready() -> None:
    config = SessionConfig(minimum_baseline_samples=3)
    artifact = build_session_baseline_artifact("SPY", [_session(date(2026, 7, 20), volume=100), _session(date(2026, 7, 21), volume=100)], cutoff_date=date(2026, 7, 23), config=config)

    result = analyze_session_volatility(_session(date(2026, 7, 23), volume=100)[:6], symbol="SPY", baseline_artifact=artifact, config=config)

    assert result["status"] == "not_ready"
    assert result["rangePercentile"] is None
    assert result["baseline"]["sampleCount"] == 2
    assert result["baseline"]["reliability"] == "insufficient"


def test_session_step7_opening_activity_is_not_abnormal_when_same_time_expected() -> None:
    config = SessionConfig(minimum_baseline_samples=2)
    artifact = build_session_baseline_artifact("SPY", [_session(date(2026, 7, 20), volume=5_000), _session(date(2026, 7, 21), volume=5_000)], cutoff_date=date(2026, 7, 23), config=config)

    opening = analyze_session_participation(_session(date(2026, 7, 23), volume=5_000)[:1], symbol="SPY", baseline_artifact=artifact, config=config)
    midday = analyze_session_participation(_session(date(2026, 7, 23), volume=500, count=151), symbol="SPY", baseline_artifact=artifact, config=config)

    assert opening["volumePaceRatio"] == 1
    assert opening["oneMinuteRelativeVolume"] == 1
    assert midday["status"] == "unknown"


def test_session_step7_compression_to_expansion_fixture_uses_percentiles() -> None:
    config = SessionConfig(minimum_baseline_samples=3)
    artifact = build_session_baseline_artifact(
        "SPY",
        [_session(date(2026, 7, 20), width=0.10), _session(date(2026, 7, 21), width=0.12), _session(date(2026, 7, 22), width=0.11)],
        cutoff_date=date(2026, 7, 23),
        config=config,
    )
    current = _session(date(2026, 7, 23), width=0.10)[:9]
    current.append(_bar(datetime(2026, 7, 23, 13, 39, tzinfo=UTC), price=100.0, width=1.20, volume=100))

    volatility = analyze_session_volatility(current, symbol="SPY", baseline_artifact=artifact, config=config)
    classification = classify_session("SPY", current, baseline_artifact=artifact, config=config)

    assert volatility["status"] == "ready"
    assert volatility["rangePercentile"] >= 0.75
    assert classification.volatility_state in {VolatilityState.EXPANDING, VolatilityState.EXTREME}
    assert classification.evidence["volatilityEvidence"]["baseline"]["baselineVersion"] == config.baseline_version
    assert classification.evidence["volatilityEvidence"]["baseline"]["baselineCutoffDate"] == "2026-07-23"


def _session(
    session_date: date,
    *,
    start: datetime | None = None,
    price: float = 100.0,
    width: float = 0.20,
    volume: float = 100.0,
    count: int = 60,
) -> list[dict[str, object]]:
    start_at = start or datetime(session_date.year, session_date.month, session_date.day, 13, 30, tzinfo=UTC)
    return [_bar(start_at + timedelta(minutes=index), price=price + index * 0.01, width=width, volume=volume) for index in range(count)]


def _bar(timestamp: datetime, *, price: float, width: float, volume: float) -> dict[str, object]:
    return {
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "open": price,
        "high": price + (width / 2),
        "low": price - (width / 2),
        "close": price,
        "volume": volume,
    }
