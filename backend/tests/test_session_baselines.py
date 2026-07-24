from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from backend.app.algorithms.session import baseline_for_decision, build_session_baseline_artifact, select_session_baseline_artifact
from session_test_fixtures import SESSION_START, golden_candles


def test_session_baselines_use_only_sessions_before_cutoff() -> None:
    historical = [_dated_session("2026-07-20"), _dated_session("2026-07-21"), _dated_session("2026-07-23")]
    artifact = build_session_baseline_artifact("SPY", historical, cutoff_date=date(2026, 7, 23), baseline_version="baseline-test")

    assert artifact.source_session_dates == ("2026-07-20", "2026-07-21")
    baseline, meta = baseline_for_decision(artifact, symbol="SPY", decision_time=SESSION_START)
    assert baseline is not None
    assert meta["baselineVersion"] == "baseline-test"


def test_session_baselines_select_artifact_valid_at_decision_time() -> None:
    early = build_session_baseline_artifact("SPY", [_dated_session("2026-07-20")], cutoff_date="2026-07-21", baseline_version="v1", valid_from=datetime(2026, 7, 21, tzinfo=UTC), valid_until=datetime(2026, 7, 23, tzinfo=UTC))
    current = build_session_baseline_artifact("SPY", [_dated_session("2026-07-21")], cutoff_date="2026-07-22", baseline_version="v2", valid_from=datetime(2026, 7, 23, tzinfo=UTC))

    selected = select_session_baseline_artifact([early, current], symbol="SPY", decision_time=SESSION_START)

    assert selected is current


def test_session_baselines_missing_same_minute_is_not_ready() -> None:
    artifact = build_session_baseline_artifact("SPY", [_dated_session("2026-07-20")], cutoff_date="2026-07-21")
    baseline, meta = baseline_for_decision(artifact, symbol="SPY", decision_time=SESSION_START + timedelta(minutes=120))

    assert baseline is None
    assert meta["reason"] == "session.baseline.minute_missing"


def _dated_session(session_date: str) -> list[dict[str, object]]:
    base = datetime.fromisoformat(f"{session_date}T13:30:00+00:00")
    return [{**bar, "timestamp": (base + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")} for index, bar in enumerate(golden_candles("balanced_range"))]
