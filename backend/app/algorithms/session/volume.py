"""Same-time normalized Session participation evidence."""

from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any

from backend.app.algorithms.session.baselines import MinuteBaseline, SessionBaselineArtifact, baseline_for_decision, percentile_rank
from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SessionConfig


def analyze_session_participation(
    candles: list[dict[str, Any]],
    *,
    symbol: str,
    baseline_artifact: SessionBaselineArtifact | None = None,
    decision_time: datetime | str | None = None,
    config: SessionConfig = DEFAULT_SESSION_CONFIG,
) -> dict[str, Any]:
    if not candles:
        return _unknown("session.volume.no_bars", baseline_artifact)
    latest = candles[-1]
    timestamp = decision_time or latest.get("timestamp")
    baseline, baseline_meta = baseline_for_decision(baseline_artifact, symbol=symbol, decision_time=timestamp, config=config)
    volumes = [0.0 if candle.get("volume") is None else float(candle["volume"]) for candle in candles]
    current_volume = volumes[-1]
    cumulative_volume = sum(volumes)
    if baseline is None:
        return {
            "status": "unknown",
            "currentCumulativeVolume": cumulative_volume,
            "expectedCumulativeVolume": None,
            "volumePaceRatio": None,
            "oneMinuteRelativeVolume": None,
            "rollingRelativeVolume": None,
            "oneMinuteVolumePercentile": None,
            "cumulativeVolumePercentile": None,
            "baseline": baseline_meta,
            "reasonCodes": (baseline_meta["reason"],),
        }
    if baseline.sample_count < config.minimum_baseline_samples:
        return _not_ready(cumulative_volume, baseline, baseline_meta, "session.volume.baseline_sample_count_insufficient", config)
    expected_cumulative = median(baseline.cumulative_volume_samples)
    expected_one_minute = median(baseline.one_minute_volume_samples)
    rolling_current = sum(volumes[-config.rolling_relative_volume_window_bars :]) / min(len(volumes), config.rolling_relative_volume_window_bars)
    rolling_expected = expected_one_minute
    return {
        "status": "ready",
        "currentCumulativeVolume": cumulative_volume,
        "expectedCumulativeVolume": expected_cumulative,
        "volumePaceRatio": None if expected_cumulative <= 0 else cumulative_volume / expected_cumulative,
        "oneMinuteRelativeVolume": None if expected_one_minute <= 0 else current_volume / expected_one_minute,
        "rollingRelativeVolume": None if rolling_expected <= 0 else rolling_current / rolling_expected,
        "oneMinuteVolumePercentile": percentile_rank(current_volume, baseline.one_minute_volume_samples),
        "cumulativeVolumePercentile": percentile_rank(cumulative_volume, baseline.cumulative_volume_samples),
        "baseline": baseline_meta,
        "reasonCodes": ("session.volume.same_time_ready",),
    }


def _not_ready(cumulative_volume: float, baseline: MinuteBaseline, baseline_meta: dict[str, Any], reason: str, config: SessionConfig) -> dict[str, Any]:
    return {
        "status": "not_ready",
        "currentCumulativeVolume": cumulative_volume,
        "expectedCumulativeVolume": None,
        "volumePaceRatio": None,
        "oneMinuteRelativeVolume": None,
        "rollingRelativeVolume": None,
        "oneMinuteVolumePercentile": None,
        "cumulativeVolumePercentile": None,
        "baseline": {**baseline_meta, "sampleCount": baseline.sample_count, "reliability": baseline.reliability(config=config)},
        "reasonCodes": (reason,),
    }


def _unknown(reason: str, baseline_artifact: SessionBaselineArtifact | None) -> dict[str, Any]:
    return {
        "status": "unknown",
        "currentCumulativeVolume": None,
        "expectedCumulativeVolume": None,
        "volumePaceRatio": None,
        "oneMinuteRelativeVolume": None,
        "rollingRelativeVolume": None,
        "oneMinuteVolumePercentile": None,
        "cumulativeVolumePercentile": None,
        "baseline": {
            "baselineVersion": baseline_artifact.baseline_version if baseline_artifact else None,
            "baselineCutoffDate": baseline_artifact.cutoff_date if baseline_artifact else None,
        },
        "reasonCodes": (reason,),
    }
