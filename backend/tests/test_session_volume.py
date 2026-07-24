from __future__ import annotations

from datetime import date

from backend.app.algorithms.session import SessionConfig, analyze_session_participation, build_session_baseline_artifact
from session_test_fixtures import SESSION_START, golden_candles
from test_session_baselines import _dated_session


def test_session_volume_uses_same_time_expected_cumulative_volume() -> None:
    artifact = build_session_baseline_artifact("SPY", [_dated_session("2026-07-20") for _ in range(5)], cutoff_date=date(2026, 7, 23))
    result = analyze_session_participation(golden_candles("strong_morning_trend"), symbol="SPY", baseline_artifact=artifact, decision_time=SESSION_START)

    assert result["status"] == "ready"
    assert result["expectedCumulativeVolume"] is not None
    assert result["volumePaceRatio"] is not None
    assert result["baseline"]["minuteFromOpen"] == 0


def test_session_volume_low_sample_count_is_not_optimistic() -> None:
    config = SessionConfig(minimum_baseline_samples=4)
    artifact = build_session_baseline_artifact("SPY", [_dated_session("2026-07-20")], cutoff_date=date(2026, 7, 23), config=config)

    result = analyze_session_participation(golden_candles("balanced_range"), symbol="SPY", baseline_artifact=artifact, decision_time=SESSION_START, config=config)

    assert result["status"] == "not_ready"
    assert result["volumePaceRatio"] is None
