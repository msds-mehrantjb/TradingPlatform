from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from backend.app.domain.models import DomainModel, _require_utc
from backend.app.market_forecast import OUTCOME_STOP, OUTCOME_TARGET, OUTCOME_TIMEOUT
from backend.app.train_market_forecast import (
    apply_probability_calibration,
    feature_stats,
    fit_probability_calibration,
    labels,
    score_probabilities,
    train_logistic_model,
    walk_forward_folds,
)


FORECAST_FEATURE_VERSION = "market_forecast_oos_feature_v1"


class ForecastFeatureLeakageError(ValueError):
    pass


class ForecastFallbackFeature(DomainModel):
    featureVersion: Literal["market_forecast_oos_feature_v1"] = FORECAST_FEATURE_VERSION
    status: Literal["missing_approved_forecast_model"] = "missing_approved_forecast_model"
    probabilityBuySuccess: float | None = None
    probabilitySellSuccess: float | None = None
    trainingWindowEndUtc: datetime | None = None
    artifactId: str | None = None
    reasonCodes: list[str] = Field(default_factory=lambda: ["forecast_model.missing_approved_artifact"])
    explanation: str = "No approved market-forecast artifact was available before the decision timestamp."


class OutOfSampleForecastFeature(DomainModel):
    featureVersion: Literal["market_forecast_oos_feature_v1"] = FORECAST_FEATURE_VERSION
    status: Literal["out_of_sample", "live_approved_artifact"]
    rowId: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    decisionTimestampUtc: datetime
    trainingWindowStartUtc: datetime
    trainingWindowEndUtc: datetime
    validationWindowStartUtc: datetime | None = None
    validationWindowEndUtc: datetime | None = None
    fold: int | None = Field(default=None, ge=1)
    artifactId: str = Field(min_length=1)
    modelKind: str = Field(default="logistic_oos_forecast", min_length=1)
    probabilityBuySuccess: float = Field(ge=0, le=1)
    probabilitySellSuccess: float = Field(ge=0, le=1)
    probabilityTimeout: float = Field(ge=0, le=1)
    modelDisagreement: float | None = Field(default=None, ge=0)
    reasonCodes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)

    @field_validator(
        "decisionTimestampUtc",
        "trainingWindowStartUtc",
        "trainingWindowEndUtc",
        "validationWindowStartUtc",
        "validationWindowEndUtc",
    )
    @classmethod
    def timestamps_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None


def generate_oos_forecast_features(
    rows: list[dict[str, Any]],
    *,
    symbol: str,
    requested_folds: int,
    embargo_minutes: int,
    min_train_rows: int = 20,
    min_validation_rows: int = 5,
) -> list[OutOfSampleForecastFeature]:
    """Generate market-forecast predictions only for later validation rows."""

    sorted_rows = sorted(rows, key=lambda row: _row_timestamp(row))
    folds = walk_forward_folds(sorted_rows, requested_folds=requested_folds, embargo_minutes=embargo_minutes)
    features: list[OutOfSampleForecastFeature] = []
    for fold in folds:
        train_rows = fold["trainRows"]
        validation_rows = fold["validationRows"]
        if len(train_rows) < min_train_rows or len(validation_rows) < min_validation_rows:
            continue
        validate_oos_fold(fold)
        feature_names = sorted({key for row in train_rows for key in row.get("features", {})})
        means, scales = feature_stats(train_rows, feature_names)
        model = train_logistic_model(train_rows, feature_names, means, scales, epochs=24, learning_rate=0.03)
        train_probabilities = [
            score_probabilities(row["features"], model["weightsByClass"], model["intercepts"], feature_names, means, scales)
            for row in train_rows
        ]
        calibration = fit_probability_calibration(train_probabilities, labels(train_rows))
        training_start = _row_timestamp(train_rows[0])
        training_end = max(_row_timestamp(row) for row in train_rows)
        validation_start = _row_timestamp(validation_rows[0])
        validation_end = _row_timestamp(validation_rows[-1])
        artifact_id = f"{FORECAST_FEATURE_VERSION}:{symbol.upper()}:fold-{fold['fold']}:train-end-{training_end.isoformat()}"
        for row in validation_rows:
            decision_at = _row_timestamp(row)
            if decision_at <= training_end:
                raise ForecastFeatureLeakageError("forecast model attempted to predict a row from its own fitting period")
            probabilities = apply_probability_calibration(
                score_probabilities(row["features"], model["weightsByClass"], model["intercepts"], feature_names, means, scales),
                calibration,
            )
            features.append(
                OutOfSampleForecastFeature(
                    status="out_of_sample",
                    rowId=str(row.get("rowId") or row.get("snapshotId") or row.get("timestamp")),
                    symbol=symbol.upper(),
                    decisionTimestampUtc=decision_at,
                    trainingWindowStartUtc=training_start,
                    trainingWindowEndUtc=training_end,
                    validationWindowStartUtc=validation_start,
                    validationWindowEndUtc=validation_end,
                    fold=int(fold["fold"]),
                    artifactId=artifact_id,
                    probabilityBuySuccess=round(float(probabilities[OUTCOME_TARGET]), 6),
                    probabilitySellSuccess=round(float(probabilities[OUTCOME_STOP]), 6),
                    probabilityTimeout=round(float(probabilities.get(OUTCOME_TIMEOUT, 0.0)), 6),
                    reasonCodes=["forecast_feature.out_of_sample_validation_prediction"],
                    explanation="Market-forecast feature generated by a model trained only on the outer fold training period.",
                )
            )
    return features


def validate_oos_fold(fold: dict[str, Any]) -> None:
    train_rows = fold.get("trainRows") or []
    validation_rows = fold.get("validationRows") or []
    if not train_rows or not validation_rows:
        return
    training_end = max(_row_timestamp(row) for row in train_rows)
    validation_start = min(_row_timestamp(row) for row in validation_rows)
    if training_end >= validation_start:
        raise ForecastFeatureLeakageError("forecast fold training window overlaps validation period")


def reject_full_history_forecast_artifact_for_historical_features(artifact: dict[str, Any]) -> None:
    policy = artifact.get("forecastFeaturePolicy") or {}
    if artifact.get("trainedOnFullHistory") or policy.get("historicalMetaFeatures") == "full_history_artifact":
        raise ForecastFeatureLeakageError("final full-history forecast artifacts cannot manufacture historical meta-training features")


def select_live_forecast_feature(
    *,
    decision_timestamp_utc: datetime,
    approved_artifacts: list[dict[str, Any]],
    symbol: str,
) -> OutOfSampleForecastFeature | ForecastFallbackFeature:
    decision_at = _require_utc(decision_timestamp_utc)
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for artifact in approved_artifacts:
        if str(artifact.get("symbol") or "").upper() != symbol.upper():
            continue
        if artifact.get("approved") is not True:
            continue
        training_end = _artifact_training_end(artifact)
        if training_end and training_end < decision_at:
            candidates.append((training_end, artifact))
    if not candidates:
        return ForecastFallbackFeature()
    training_end, artifact = max(candidates, key=lambda item: item[0])
    return OutOfSampleForecastFeature(
        status="live_approved_artifact",
        rowId=f"live:{symbol.upper()}:{decision_at.isoformat()}",
        symbol=symbol.upper(),
        decisionTimestampUtc=decision_at,
        trainingWindowStartUtc=_artifact_training_start(artifact) or training_end,
        trainingWindowEndUtc=training_end,
        artifactId=str(artifact.get("artifactId") or artifact.get("modelVersion") or artifact.get("version") or "approved_forecast_artifact"),
        modelKind=str(artifact.get("modelKind") or "approved_forecast_artifact"),
        probabilityBuySuccess=0.0,
        probabilitySellSuccess=0.0,
        probabilityTimeout=0.0,
        reasonCodes=["forecast_feature.live_approved_artifact_available"],
        explanation="Live inference may use this approved forecast artifact because its training end precedes the decision timestamp.",
    )


def _artifact_training_start(artifact: dict[str, Any]) -> datetime | None:
    for key in ("trainingWindowStartUtc", "trainingStartUtc", "trainingStart"):
        if artifact.get(key):
            return _parse_utc(str(artifact[key]))
    return None


def _artifact_training_end(artifact: dict[str, Any]) -> datetime | None:
    for key in ("trainingWindowEndUtc", "trainingEndUtc", "trainingEnd", "trainedThroughUtc"):
        if artifact.get(key):
            return _parse_utc(str(artifact[key]))
    return None


def _row_timestamp(row: dict[str, Any]) -> datetime:
    value = str(row.get("timestamp") or row.get("decisionTimestampUtc") or row.get("decisionTimestamp") or "")
    return _parse_utc(value)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _require_utc(parsed)
