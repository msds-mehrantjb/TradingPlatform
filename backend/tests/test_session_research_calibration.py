from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import shutil
import uuid

import pytest

from backend.app.algorithms.session import (
    SESSION_FEATURE_SCHEMA_VERSION,
    SESSION_RESEARCH_REPORT_VERSION,
    SessionCalibrationRunnerConfig,
    run_session_characterization_calibration,
    save_immutable_session_report,
)
from backend.app.algorithms.session.state import FINALIZED_ONE_MINUTE_BAR, QUOTE_NBBO_UPDATE


CREATED_AT = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


def test_session_research_runner_builds_chronological_partitions_without_holdout_selection() -> None:
    report = run_session_characterization_calibration(_multi_session_events(9), config=SessionCalibrationRunnerConfig(run_id="session-research-test"), created_at=CREATED_AT)

    assert report.report_version == SESSION_RESEARCH_REPORT_VERSION
    assert report.feature_schema_version == SESSION_FEATURE_SCHEMA_VERSION
    assert report.partitions["development_end"] < report.partitions["calibration_start"]
    assert report.partitions["calibration_end"] < report.partitions["final_holdout_start"]
    assert report.walk_forward["enabled"] is True
    assert report.walk_forward["finalHoldoutUsedForSelection"] is False
    assert report.untouched_holdout_policy["thresholdSelectionUsesFinalHoldout"] is False
    assert report.selected_candidate["source_period"] == "calibration"


def test_session_research_report_contains_required_characterization_metrics() -> None:
    report = run_session_characterization_calibration(_multi_session_events(9), config=SessionCalibrationRunnerConfig(run_id="session-research-metrics"), created_at=CREATED_AT)
    metrics = report.final_holdout_report

    for key in (
        "occupancyByPhase",
        "occupancyByBehavior",
        "averageDwellTimeBars",
        "transitionFrequency",
        "oneBarReversalRate",
        "threeBarReversalRate",
        "unknownStaleRate",
        "strategyFamilyPerformanceBySessionState",
        "grossExpectancy",
        "netExpectancy",
        "spreadDistribution",
        "slippageDistribution",
        "opportunityCount",
        "fillRate",
        "drawdown",
        "turnover",
        "holdingTime",
        "performanceByTimeOfDay",
    ):
        assert key in metrics
    assert metrics["decisionCount"] > 0
    assert isinstance(metrics["occupancyByBehavior"], dict)


def test_session_research_calibrates_all_requested_threshold_groups() -> None:
    report = run_session_characterization_calibration(_multi_session_events(9), config=SessionCalibrationRunnerConfig(run_id="session-research-thresholds"), created_at=CREATED_AT)
    thresholds = report.selected_candidate["thresholds"]

    assert "behaviorThresholds" in thresholds
    assert "confidenceThresholds" in thresholds
    assert "transitionConfirmationCount" in thresholds
    assert "minimumDwellSeconds" in thresholds
    assert "volatilityPercentiles" in thresholds
    assert "volumePaceThresholds" in thresholds
    assert "spreadLimitsBasisPoints" in thresholds
    assert "minimumNetEdge" in thresholds
    assert "signalValiditySeconds" in thresholds
    assert report.threshold_justification["empiricallyJustified"] is True


def test_session_research_stress_tests_and_baseline_comparison_are_present() -> None:
    report = run_session_characterization_calibration(_multi_session_events(9), config=SessionCalibrationRunnerConfig(run_id="session-research-stress"), created_at=CREATED_AT)

    scenarios = {item["scenario"] for item in report.stress_tests}
    assert {"costs_1_0x", "costs_1_5x", "costs_2_0x", "added_latency", "lower_fill_probability", "missing_data_period"} <= scenarios
    assert report.baseline_no_session_routing["mode"] == "no_session_routing"
    assert report.incremental_value["demonstratedValue"] is True
    assert any(item["incremental_value"]["reasonCodes"] for item in report.stress_tests)


def test_session_research_saves_immutable_report_with_versions() -> None:
    report = run_session_characterization_calibration(_multi_session_events(9), config=SessionCalibrationRunnerConfig(run_id="session-research-save"), created_at=CREATED_AT)
    scratch = _scratch_path()

    path = save_immutable_session_report(report, scratch)

    assert path.exists()
    assert report.report_id in path.name
    text = path.read_text(encoding="utf-8")
    assert report.dataset_cutoff in text
    assert report.config_version in text
    with pytest.raises(FileExistsError):
        save_immutable_session_report(report, scratch)
    shutil.rmtree(scratch, ignore_errors=True)


def test_session_research_requires_three_chronological_sessions() -> None:
    with pytest.raises(ValueError, match="at least three chronological sessions"):
        run_session_characterization_calibration(_multi_session_events(2), config=SessionCalibrationRunnerConfig(run_id="session-research-too-short"), created_at=CREATED_AT)


def _multi_session_events(session_count: int) -> tuple[dict[str, object], ...]:
    events: list[dict[str, object]] = []
    start = datetime(2026, 7, 13, 13, 30, tzinfo=UTC)
    made = 0
    day_offset = 0
    while made < session_count:
        day = start + timedelta(days=day_offset)
        day_offset += 1
        if day.weekday() >= 5:
            continue
        events.extend(_session_events(day, made))
        made += 1
    return tuple(events)


def _session_events(start: datetime, session_index: int) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for index in range(18):
        timestamp = start + timedelta(minutes=index)
        close = 100 + session_index * 0.2 + index * (0.08 if session_index % 2 == 0 else -0.03)
        events.append(
            {
                "type": QUOTE_NBBO_UPDATE,
                "event_id": f"quote-{session_index}-{index}",
                "symbol": "SPY",
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "bid": round(close - 0.005, 4),
                "ask": round(close + 0.005, 4),
            }
        )
        events.append(
            {
                "type": FINALIZED_ONE_MINUTE_BAR,
                "event_id": f"bar-{session_index}-{index}",
                "symbol": "SPY",
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "open": round(close - 0.03, 4),
                "high": round(close + 0.08, 4),
                "low": round(close - 0.07, 4),
                "close": round(close, 4),
                "volume": 100_000 + index * 1_000,
                "finalized": True,
            }
        )
    return events


def _scratch_path() -> Path:
    path = Path("backend") / ".test_artifacts" / f"session_research_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path
