from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from statistics import mean, pstdev
from typing import Any, Literal

from pydantic import Field, field_validator

from backend.app.domain.models import DomainModel, _require_utc


FORECAST_FEATURE_VERSION = "market_forecast_oos_feature_v1"
FORECAST_HORIZON_MINUTES = 5
OUTCOME_STOP = "stop_hit_first"
OUTCOME_TIMEOUT = "timeout_no_edge"
OUTCOME_TARGET = "target_hit_first"
OUTCOME_LABELS = {
    OUTCOME_STOP: -1,
    OUTCOME_TIMEOUT: 0,
    OUTCOME_TARGET: 1,
}
OUTCOME_ORDER = (OUTCOME_STOP, OUTCOME_TIMEOUT, OUTCOME_TARGET)


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


def walk_forward_folds(rows: list[dict[str, Any]], *, requested_folds: int, embargo_minutes: int) -> list[dict[str, Any]]:
    total = len(rows)
    fold_count = max(1, min(requested_folds, 8))
    validation_size = max(50, total // (fold_count + 2))
    folds: list[dict[str, Any]] = []
    for fold_index in range(fold_count):
        validation_start = total - (fold_count - fold_index) * validation_size
        validation_end = validation_start + validation_size
        if validation_start <= validation_size or validation_end > total:
            continue
        validation_rows = rows[validation_start:validation_end]
        validation_start_time = row_event_start_minutes(validation_rows[0])
        train_rows = [
            row
            for row in rows[:validation_start]
            if row_event_end_minutes(row) < validation_start_time - embargo_minutes
        ]
        folds.append(
            {
                "fold": fold_index + 1,
                "trainRows": train_rows,
                "validationRows": validation_rows,
                "validationStart": validation_rows[0]["timestamp"],
                "validationEnd": validation_rows[-1]["timestamp"],
                "purgedRows": validation_start - len(train_rows),
            }
        )
    return folds


def feature_stats(rows: list[dict[str, Any]], feature_names: list[str]) -> tuple[dict[str, float], dict[str, float]]:
    means: dict[str, float] = {}
    scales: dict[str, float] = {}
    for name in feature_names:
        values = [float(row["features"].get(name, 0)) for row in rows]
        avg = mean(values)
        scale = pstdev(values) if len(values) > 1 else 1
        means[name] = round(avg, 10)
        scales[name] = round(scale if scale > 0 else 1, 10)
    return means, scales


def labels(rows: list[dict[str, Any]]) -> list[int]:
    return [int(row["target"]) for row in rows]


def train_logistic_model(
    rows: list[dict[str, Any]],
    feature_names: list[str],
    means: dict[str, float],
    scales: dict[str, float],
    *,
    epochs: int = 35,
    learning_rate: float = 0.035,
    l2: float = 0.0005,
) -> dict[str, Any]:
    weights_by_class: dict[str, dict[str, float]] = {name: defaultdict(float) for name in OUTCOME_ORDER}
    intercepts: dict[str, float] = {name: 0.0 for name in OUTCOME_ORDER}
    class_counts = {name: max(1, sum(1 for row in rows if outcome_name(int(row["target"])) == name)) for name in OUTCOME_ORDER}
    class_weights = {name: len(rows) / (len(OUTCOME_ORDER) * count) for name, count in class_counts.items()}
    for _ in range(epochs):
        for row in rows:
            target_name = outcome_name(int(row["target"]))
            probabilities = score_probabilities(row["features"], weights_by_class, intercepts, feature_names, means, scales)
            row_weight = class_weights[target_name]
            for class_name in OUTCOME_ORDER:
                gradient = (probabilities[class_name] - (1.0 if class_name == target_name else 0.0)) * row_weight
                intercepts[class_name] -= learning_rate * gradient
                for name in feature_names:
                    value = normalized_feature(row["features"], name, means, scales)
                    weights_by_class[class_name][name] -= learning_rate * ((gradient * value) + (l2 * weights_by_class[class_name][name]))
    return {
        "intercepts": {name: round(value, 10) for name, value in intercepts.items()},
        "weightsByClass": {
            class_name: {name: round(value, 10) for name, value in weights.items() if abs(value) >= 0.000001}
            for class_name, weights in weights_by_class.items()
        },
    }


def score_probabilities(
    features: dict[str, float],
    weights_by_class: dict[str, dict[str, float]],
    intercepts: dict[str, float],
    feature_names: list[str],
    means: dict[str, float],
    scales: dict[str, float],
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for class_name in OUTCOME_ORDER:
        score = float(intercepts.get(class_name, 0.0))
        weights = weights_by_class.get(class_name) or {}
        for name in feature_names:
            score += float(weights.get(name, 0)) * normalized_feature(features, name, means, scales)
        scores[class_name] = score
    max_score = max(scores.values()) if scores else 0
    exp_scores = {name: math.exp(max(-30, min(30, score - max_score))) for name, score in scores.items()}
    total = sum(exp_scores.values()) or 1
    return {name: exp_scores.get(name, 0) / total for name in OUTCOME_ORDER}


def fit_probability_calibration(probabilities: list[dict[str, float]], targets: list[int]) -> dict[str, Any]:
    return {
        "method": "per_class_platt_sigmoid",
        "classes": {
            outcome: fit_platt_sigmoid(
                [float(row[outcome]) for row in probabilities],
                [1 if target == OUTCOME_LABELS[outcome] else 0 for target in targets],
            )
            for outcome in OUTCOME_ORDER
        },
    }


def apply_probability_calibration(probabilities: dict[str, float], calibration: dict[str, Any]) -> dict[str, float]:
    classes = calibration.get("classes") or {}
    calibrated: dict[str, float] = {}
    for outcome in OUTCOME_ORDER:
        params = classes.get(outcome) or {}
        slope = float(params.get("slope", 1.0))
        intercept = float(params.get("intercept", 0.0))
        z = (slope * logit(clamp_probability(float(probabilities[outcome])))) + intercept
        calibrated[outcome] = 1 / (1 + math.exp(-max(-30, min(30, z))))
    total = sum(calibrated.values()) or 1.0
    return {outcome: calibrated[outcome] / total for outcome in OUTCOME_ORDER}


def fit_platt_sigmoid(probabilities: list[float], labels: list[int], *, epochs: int = 400, learning_rate: float = 0.05, l2: float = 0.001) -> dict[str, float]:
    if not probabilities or len(set(labels)) < 2:
        base_rate = sum(labels) / max(1, len(labels))
        return {"slope": 1.0, "intercept": logit(clamp_probability(base_rate))}
    slope = 1.0
    intercept = 0.0
    for _ in range(epochs):
        slope_gradient = 0.0
        intercept_gradient = 0.0
        for probability, label in zip(probabilities, labels):
            x = logit(clamp_probability(probability))
            calibrated = 1 / (1 + math.exp(-max(-30, min(30, (slope * x) + intercept))))
            error = calibrated - label
            slope_gradient += error * x
            intercept_gradient += error
        count = max(1, len(probabilities))
        slope -= learning_rate * ((slope_gradient / count) + (l2 * slope))
        intercept -= learning_rate * (intercept_gradient / count)
        slope = max(-8.0, min(8.0, slope))
        intercept = max(-8.0, min(8.0, intercept))
    return {"slope": round(slope, 8), "intercept": round(intercept, 8)}


def row_event_start_minutes(row: dict[str, Any]) -> float:
    timestamp = parse_row_timestamp(str(row.get("labelStart") or row.get("timestamp") or ""))
    return timestamp.timestamp() / 60


def row_event_end_minutes(row: dict[str, Any]) -> float:
    timestamp = parse_row_timestamp(str(row.get("labelEnd") or ""))
    if timestamp != datetime.min:
        return timestamp.timestamp() / 60
    return row_event_start_minutes(row) + FORECAST_HORIZON_MINUTES


def parse_row_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return datetime.min


def outcome_name(label: int) -> str:
    for name, value in OUTCOME_LABELS.items():
        if value == label:
            return name
    return OUTCOME_TIMEOUT


def normalized_feature(features: dict[str, float], name: str, means: dict[str, float], scales: dict[str, float]) -> float:
    scale = scales.get(name, 1)
    return (float(features.get(name, 0)) - means.get(name, 0)) / (scale if scale > 0 else 1)


def clamp_probability(value: float) -> float:
    return max(0.000001, min(0.999999, value))


def logit(value: float) -> float:
    value = clamp_probability(value)
    return math.log(value / (1 - value))


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
