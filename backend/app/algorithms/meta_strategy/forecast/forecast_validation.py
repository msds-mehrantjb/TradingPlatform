"""Validation helpers for Meta-Strategy OOS forecast features."""

from __future__ import annotations

from datetime import datetime
from typing import Any


class ForecastFeatureLeakageError(ValueError):
    pass


def validate_training_ends_before_prediction(*, training_window_end_utc: datetime, prediction_time_utc: datetime) -> None:
    if training_window_end_utc >= prediction_time_utc:
        raise ForecastFeatureLeakageError("forecast training window must end before prediction time")


def validate_oos_fold(fold: dict[str, Any]) -> None:
    train_rows = fold.get("trainRows") or []
    validation_rows = fold.get("validationRows") or []
    if not train_rows or not validation_rows:
        return
    training_end = max(row_timestamp(row) for row in train_rows)
    validation_start = min(row_timestamp(row) for row in validation_rows)
    if training_end >= validation_start:
        raise ForecastFeatureLeakageError("forecast fold training window overlaps validation period")


def reject_in_sample_forecast_feature(forecast_feature: Any) -> None:
    if forecast_feature is None or getattr(forecast_feature, "status", None) == "missing_approved_forecast_model":
        return
    training_end = getattr(forecast_feature, "trainingWindowEndUtc", None)
    decision_at = getattr(forecast_feature, "decisionTimestampUtc", None)
    if training_end is None or decision_at is None:
        raise ForecastFeatureLeakageError("forecast feature must persist training and prediction timestamps")
    validate_training_ends_before_prediction(training_window_end_utc=training_end, prediction_time_utc=decision_at)


def reject_full_history_forecast_artifact_for_historical_features(artifact: dict[str, Any]) -> None:
    policy = artifact.get("forecastFeaturePolicy") or {}
    if artifact.get("trainedOnFullHistory") or policy.get("historicalMetaFeatures") == "full_history_artifact":
        raise ForecastFeatureLeakageError("final full-history forecast artifacts cannot manufacture historical meta-training features")


def artifact_training_start(artifact: dict[str, Any]) -> datetime | None:
    for key in ("trainingWindowStartUtc", "trainingStartUtc", "trainingStart"):
        if artifact.get(key):
            return parse_utc(str(artifact[key]))
    return None


def artifact_training_end(artifact: dict[str, Any]) -> datetime | None:
    for key in ("trainingWindowEndUtc", "trainingEndUtc", "trainingEnd", "trainedThroughUtc"):
        if artifact.get(key):
            return parse_utc(str(artifact[key]))
    return None


def row_timestamp(row: dict[str, Any]) -> datetime:
    value = str(row.get("timestamp") or row.get("decisionTimestampUtc") or row.get("decisionTimestamp") or "")
    return parse_utc(value)


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ForecastFeatureLeakageError("forecast timestamp must be timezone-aware")
    return parsed


__all__ = [
    "ForecastFeatureLeakageError",
    "artifact_training_end",
    "artifact_training_start",
    "parse_utc",
    "reject_full_history_forecast_artifact_for_historical_features",
    "reject_in_sample_forecast_feature",
    "row_timestamp",
    "validate_oos_fold",
    "validate_training_ends_before_prediction",
]
