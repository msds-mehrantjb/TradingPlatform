from __future__ import annotations

import hashlib
import json
from datetime import time
from math import sqrt
from typing import Any, Literal

from pydantic import Field

from backend.app.domain.models import DecisionSnapshotV2, DomainModel, Signal, StrategyFamily
from backend.app.ensemble.family_aware import FAMILY_ORDER
from backend.app.ml.forecast_oos import ForecastFallbackFeature, OutOfSampleForecastFeature
from backend.app.strategies.registry import directional_strategy_input_ids


ML_FEATURE_SCHEMA_VERSION = "candidate_meta_feature_schema_v1"
MISSING_CATEGORY = "__MISSING__"
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


class ForbiddenMLFeatureFieldError(ValueError):
    pass


class MLFeatureSpec(DomainModel):
    name: str = Field(min_length=1)
    group: Literal["directional_strategy", "family", "context", "regime", "execution", "candidate", "upstream_forecast"]
    valueType: Literal["numeric", "categorical"]


class MLFeatureSet(DomainModel):
    schemaVersion: Literal["candidate_meta_feature_schema_v1"] = ML_FEATURE_SCHEMA_VERSION
    schemaHash: str = Field(min_length=1)
    snapshotId: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    decisionTimestampUtc: str = Field(min_length=1)
    featureValues: dict[str, Any]
    missingIndicators: dict[str, bool]
    forbiddenFieldsChecked: list[str]
    explanation: str = Field(min_length=1)


def build_candidate_meta_features(
    snapshot: DecisionSnapshotV2,
    *,
    forecastFeature: OutOfSampleForecastFeature | ForecastFallbackFeature | None = None,
) -> MLFeatureSet:
    reject_forbidden_training_fields(snapshot)
    _validate_forecast_feature(snapshot, forecastFeature)
    schema = candidate_meta_feature_schema()
    raw_values = _base_feature_values(snapshot, forecastFeature)
    feature_values: dict[str, Any] = {}
    missing_indicators: dict[str, bool] = {}
    for spec in schema:
        raw = raw_values.get(spec.name)
        value, missing = _normalize_value(raw, spec.valueType)
        feature_values[spec.name] = value
        feature_values[f"{spec.name}__missing"] = 1 if missing else 0
        missing_indicators[spec.name] = missing

    return MLFeatureSet(
        schemaHash=candidate_meta_feature_schema_hash(),
        snapshotId=snapshot.snapshotId,
        symbol=snapshot.symbol,
        decisionTimestampUtc=(snapshot.decisionTimestampUtc or snapshot.decisionTimestamp).isoformat(),
        featureValues=feature_values,
        missingIndicators=missing_indicators,
        forbiddenFieldsChecked=[
            "finalOutcome",
            "fillResult",
            "fills",
            "brokerSubmissionResult",
            "metaModelPrediction",
            "post_decision_timestamps",
            "raw_feature_payload_keys",
        ],
        explanation="Decision-time candidate meta-model features built without labels, fills, outcomes, or future fields.",
    )


def candidate_meta_feature_schema() -> list[MLFeatureSpec]:
    specs: list[MLFeatureSpec] = []
    for strategy_id in directional_strategy_input_ids():
        prefix = f"strategy_{strategy_id}"
        for name in ("direction", "confidence", "eligible", "active", "data_ready", "regime_fit", "reliability", "setup_detected"):
            specs.append(MLFeatureSpec(name=f"{prefix}_{name}", group="directional_strategy", valueType="numeric"))

    for family in FAMILY_ORDER:
        specs.append(MLFeatureSpec(name=f"family_{family.value.lower()}_score", group="family", valueType="numeric"))
    for name in (
        "family_agreement",
        "supporting_family_count",
        "opposing_family_count",
        "directional_dispersion",
        "strongest_family_score",
        "weakest_family_score",
    ):
        specs.append(MLFeatureSpec(name=name, group="family", valueType="numeric"))
    specs.extend(
        [
            MLFeatureSpec(name="strongest_family", group="family", valueType="categorical"),
            MLFeatureSpec(name="weakest_family", group="family", valueType="categorical"),
        ]
    )

    for name, value_type in (
        ("spy_relative_strength_vs_qqq_iwm", "numeric"),
        ("relative_strength_normalized_score", "numeric"),
        ("breadth_score", "numeric"),
        ("breadth_coverage", "numeric"),
        ("economic_event_state", "categorical"),
        ("economic_event_importance", "categorical"),
        ("market_structure_state", "categorical"),
        ("market_structure_quality", "numeric"),
        ("volume_confirmation_score", "numeric"),
        ("volume_trend", "categorical"),
        ("vwap_position_state", "categorical"),
        ("vwap_distance_atr", "numeric"),
    ):
        specs.append(MLFeatureSpec(name=name, group="context", valueType=value_type))  # type: ignore[arg-type]

    for name, value_type in (
        ("regime_category", "categorical"),
        ("adx", "numeric"),
        ("atr_percentile", "numeric"),
        ("realized_volatility_percentile", "numeric"),
        ("trend_fit", "numeric"),
        ("breakout_fit", "numeric"),
        ("reversal_fit", "numeric"),
        ("mean_reversion_fit", "numeric"),
    ):
        specs.append(MLFeatureSpec(name=name, group="regime", valueType=value_type))  # type: ignore[arg-type]

    for name in (
        "spread_dollars",
        "relative_volume",
        "estimated_slippage",
        "time_of_day_minutes",
        "minutes_since_open",
        "minutes_until_close",
        "entry_distance",
        "stop_distance",
        "target_distance",
        "reward_risk_ratio",
    ):
        specs.append(MLFeatureSpec(name=name, group="execution", valueType="numeric"))

    for name, value_type in (
        ("candidate_side", "categorical"),
        ("deterministic_score", "numeric"),
        ("signal_margin", "numeric"),
        ("expected_transaction_cost", "numeric"),
    ):
        specs.append(MLFeatureSpec(name=name, group="candidate", valueType=value_type))  # type: ignore[arg-type]
    for name, value_type in (
        ("forecast_status", "categorical"),
        ("forecast_probability_buy_success", "numeric"),
        ("forecast_probability_sell_success", "numeric"),
        ("forecast_probability_timeout", "numeric"),
        ("forecast_training_end_age_minutes", "numeric"),
        ("forecast_artifact_id", "categorical"),
    ):
        specs.append(MLFeatureSpec(name=name, group="upstream_forecast", valueType=value_type))  # type: ignore[arg-type]
    return specs


def candidate_meta_feature_schema_hash() -> str:
    payload = [spec.model_dump(mode="json") for spec in candidate_meta_feature_schema()]
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def reject_forbidden_training_fields(snapshot: DecisionSnapshotV2) -> None:
    forbidden: list[str] = []
    if snapshot.finalOutcome is not None:
        forbidden.append("finalOutcome")
    if snapshot.fillResult is not None:
        forbidden.append("fillResult")
    if snapshot.fills:
        forbidden.append("fills")
    if snapshot.brokerSubmissionResult is not None:
        forbidden.append("brokerSubmissionResult")
    if snapshot.metaModelPrediction is not None:
        forbidden.append("metaModelPrediction")

    decision_at = snapshot.decisionTimestampUtc or snapshot.decisionTimestamp
    if snapshot.globalGateDecision.checkedAt > decision_at:
        forbidden.append("globalGateDecision.checkedAt")
    if any(gate.checkedAt > decision_at for gate in snapshot.globalGateResults):
        forbidden.append("globalGateResults.checkedAt")
    if snapshot.effectiveTradePolicy.effectiveAt > decision_at:
        forbidden.append("effectiveTradePolicy.effectiveAt")
    if snapshot.orderPlan and snapshot.orderPlan.generatedAt > decision_at:
        forbidden.append("orderPlan.generatedAt")
    if snapshot.tradeCandidate and snapshot.tradeCandidate.generatedAt > decision_at:
        forbidden.append("tradeCandidate.generatedAt")

    forbidden.extend(_forbidden_payload_paths(snapshot.featureSnapshot, "featureSnapshot"))
    forbidden.extend(_forbidden_payload_paths(snapshot.rawMarketReferences, "rawMarketReferences"))
    forbidden.extend(_forbidden_payload_paths(snapshot.dataQuality, "dataQuality"))
    forbidden.extend(_forbidden_payload_paths(snapshot.positionState, "positionState"))
    if forbidden:
        raise ForbiddenMLFeatureFieldError(f"candidate meta-feature builder rejects forbidden fields: {', '.join(sorted(set(forbidden)))}")


def _base_feature_values(
    snapshot: DecisionSnapshotV2,
    forecast_feature: OutOfSampleForecastFeature | ForecastFallbackFeature | None = None,
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    strategies = {signal.strategyId: signal for signal in snapshot.directionalStrategyOutputs or snapshot.strategySignals}
    for strategy_id in directional_strategy_input_ids():
        signal = strategies.get(strategy_id)
        prefix = f"strategy_{strategy_id}"
        values[f"{prefix}_direction"] = _direction(signal.direction if signal else None)
        values[f"{prefix}_confidence"] = signal.confidence if signal else None
        values[f"{prefix}_eligible"] = _bool(signal.eligible if signal else None)
        values[f"{prefix}_active"] = _bool(signal.active if signal else None)
        values[f"{prefix}_data_ready"] = _bool(signal.dataReady if signal else None)
        values[f"{prefix}_regime_fit"] = signal.regimeFit if signal else None
        values[f"{prefix}_reliability"] = signal.reliability if signal else None
        values[f"{prefix}_setup_detected"] = _bool(signal.setupDetected if signal else None)

    family_scores = _family_signed_scores(snapshot)
    for family in FAMILY_ORDER:
        values[f"family_{family.value.lower()}_score"] = family_scores.get(family.value)
    supporting = len(snapshot.ensembleDecision.supportingFamilies)
    opposing = len(snapshot.ensembleDecision.opposingFamilies)
    denominator = supporting + opposing
    values["family_agreement"] = supporting / denominator if denominator else 0.0
    values["supporting_family_count"] = supporting
    values["opposing_family_count"] = opposing
    present_family_values = [value for value in family_scores.values() if value is not None]
    values["directional_dispersion"] = _population_std(present_family_values)
    strongest, weakest = _strongest_weakest(family_scores)
    values["strongest_family"] = strongest[0]
    values["strongest_family_score"] = strongest[1]
    values["weakest_family"] = weakest[0]
    values["weakest_family_score"] = weakest[1]

    contexts = {context.contextId: context for context in snapshot.contextOutputs or snapshot.contextSignals}
    relative_strength = contexts.get("relative_strength_qqq_iwm")
    breadth = contexts.get("market_breadth_momentum")
    economic = contexts.get("economic_event_context")
    structure = contexts.get("market_structure_context")
    volume = contexts.get("volume_confirmation")
    vwap = contexts.get("vwap_position_context")
    values["spy_relative_strength_vs_qqq_iwm"] = _context_feature(relative_strength, "primaryRelativeReturn")
    values["relative_strength_normalized_score"] = _context_feature(relative_strength, "normalizedRelativeStrengthScore")
    values["breadth_score"] = breadth.confidence if breadth else None
    values["breadth_coverage"] = _context_feature(breadth, "dataCoverage")
    values["economic_event_state"] = _context_feature(economic, "eventState")
    values["economic_event_importance"] = _context_feature(economic, "eventImportance")
    values["market_structure_state"] = _context_feature(structure, "breakOfStructure")
    values["market_structure_quality"] = _context_feature(structure, "structureQuality")
    values["volume_confirmation_score"] = volume.confidence if volume else None
    values["volume_trend"] = _context_feature(volume, "volumeTrend")
    values["vwap_position_state"] = _context_feature(vwap, "reclaimRejectionState")
    values["vwap_distance_atr"] = _context_feature(vwap, "distanceFromVwapAtr")

    regime_features = snapshot.regimeState.features
    values["regime_category"] = snapshot.regimeState.label
    values["adx"] = _first_present(regime_features, "trendStrengthAdx", "adx")
    values["atr_percentile"] = _first_present(regime_features, "atrPercentile")
    values["realized_volatility_percentile"] = _first_present(regime_features, "realizedVolatilityPercentile")
    values["trend_fit"] = _first_present(regime_features, "trendFit")
    values["breakout_fit"] = _first_present(regime_features, "breakoutFit")
    values["reversal_fit"] = _first_present(regime_features, "reversalFit")
    values["mean_reversion_fit"] = _first_present(regime_features, "meanReversionFit")

    execution = _execution_geometry(snapshot)
    spread = _feature_snapshot_value(snapshot, "spreadDollars")
    slippage = snapshot.effectiveTradePolicy.baselineSettings.slippagePerShare
    values["spread_dollars"] = spread
    values["relative_volume"] = _feature_snapshot_value(snapshot, "spy1mRelativeVolume")
    values["estimated_slippage"] = slippage
    values.update(_time_features(snapshot))
    values["entry_distance"] = execution["entry_distance"]
    values["stop_distance"] = execution["stop_distance"]
    values["target_distance"] = execution["target_distance"]
    values["reward_risk_ratio"] = execution["reward_risk_ratio"]
    values["candidate_side"] = _signal_value(snapshot.ensembleDecision.signal)
    values["deterministic_score"] = snapshot.ensembleDecision.finalScore
    values["signal_margin"] = abs(snapshot.ensembleDecision.buyConfidence - snapshot.ensembleDecision.sellConfidence)
    values["expected_transaction_cost"] = (float(spread or 0.0) + (2.0 * float(slippage or 0.0)))
    values.update(_forecast_feature_values(snapshot, forecast_feature))
    return values


def _forecast_feature_values(
    snapshot: DecisionSnapshotV2,
    forecast_feature: OutOfSampleForecastFeature | ForecastFallbackFeature | None,
) -> dict[str, Any]:
    if forecast_feature is None:
        return {
            "forecast_status": "missing_approved_forecast_model",
            "forecast_probability_buy_success": None,
            "forecast_probability_sell_success": None,
            "forecast_probability_timeout": None,
            "forecast_training_end_age_minutes": None,
            "forecast_artifact_id": None,
        }
    if isinstance(forecast_feature, ForecastFallbackFeature):
        return {
            "forecast_status": forecast_feature.status,
            "forecast_probability_buy_success": None,
            "forecast_probability_sell_success": None,
            "forecast_probability_timeout": None,
            "forecast_training_end_age_minutes": None,
            "forecast_artifact_id": None,
        }
    decision_at = snapshot.decisionTimestampUtc or snapshot.decisionTimestamp
    training_age = (decision_at - forecast_feature.trainingWindowEndUtc).total_seconds() / 60.0
    return {
        "forecast_status": forecast_feature.status,
        "forecast_probability_buy_success": forecast_feature.probabilityBuySuccess,
        "forecast_probability_sell_success": forecast_feature.probabilitySellSuccess,
        "forecast_probability_timeout": forecast_feature.probabilityTimeout,
        "forecast_training_end_age_minutes": training_age,
        "forecast_artifact_id": forecast_feature.artifactId,
    }


def _validate_forecast_feature(
    snapshot: DecisionSnapshotV2,
    forecast_feature: OutOfSampleForecastFeature | ForecastFallbackFeature | None,
) -> None:
    if forecast_feature is None or isinstance(forecast_feature, ForecastFallbackFeature):
        return
    decision_at = snapshot.decisionTimestampUtc or snapshot.decisionTimestamp
    if forecast_feature.decisionTimestampUtc != decision_at:
        raise ForbiddenMLFeatureFieldError("forecast feature decision timestamp must match the snapshot decision timestamp")
    if forecast_feature.trainingWindowEndUtc >= decision_at:
        raise ForbiddenMLFeatureFieldError("forecast feature training window must end before the decision timestamp")


def _family_signed_scores(snapshot: DecisionSnapshotV2) -> dict[str, float | None]:
    scores: dict[str, float | None] = {family.value: None for family in FAMILY_ORDER}
    for score in snapshot.ensembleDecision.familyScores:
        scores[str(score.family)] = round(float(score.buyScore) - float(score.sellScore), 6)
    return scores


def _strongest_weakest(scores: dict[str, float | None]) -> tuple[tuple[str | None, float | None], tuple[str | None, float | None]]:
    present = [(family, value) for family, value in scores.items() if value is not None]
    if not present:
        return (None, None), (None, None)
    strongest = max(present, key=lambda row: abs(row[1]))
    weakest = min(present, key=lambda row: abs(row[1]))
    return strongest, weakest


def _execution_geometry(snapshot: DecisionSnapshotV2) -> dict[str, float | None]:
    plan = snapshot.orderPlan
    candidate = snapshot.tradeCandidate
    entry = plan.entryPrice if plan else candidate.entryPrice if candidate else None
    stop = plan.stopPrice if plan else candidate.stopPrice if candidate else None
    target = plan.targetPrice if plan else candidate.targetPrice if candidate else None
    current = _feature_snapshot_value(snapshot, "spy1mClose")
    if current is None:
        current = _feature_snapshot_value(snapshot, "latestClose")
    entry_distance = abs(float(entry) - float(current)) if entry is not None and current is not None else None
    stop_distance = abs(float(entry) - float(stop)) if entry is not None and stop is not None else None
    target_distance = abs(float(target) - float(entry)) if target is not None and entry is not None else None
    reward_risk = target_distance / stop_distance if target_distance is not None and stop_distance not in {None, 0.0} else None
    return {
        "entry_distance": entry_distance,
        "stop_distance": stop_distance,
        "target_distance": target_distance,
        "reward_risk_ratio": reward_risk,
    }


def _time_features(snapshot: DecisionSnapshotV2) -> dict[str, float]:
    decision_utc = snapshot.decisionTimestampUtc or snapshot.decisionTimestamp
    minutes = _new_york_clock_minutes(decision_utc)
    open_minutes = (MARKET_OPEN.hour * 60) + MARKET_OPEN.minute
    close_minutes = (MARKET_CLOSE.hour * 60) + MARKET_CLOSE.minute
    return {
        "time_of_day_minutes": round(minutes, 4),
        "minutes_since_open": round(minutes - open_minutes, 4),
        "minutes_until_close": round(close_minutes - minutes, 4),
    }


def _new_york_clock_minutes(decision_utc) -> float:
    offset_hours = -4 if _is_us_eastern_dst(decision_utc.date()) else -5
    shifted_hour = (decision_utc.hour + offset_hours) % 24
    return (shifted_hour * 60) + decision_utc.minute + (decision_utc.second / 60.0)


def _is_us_eastern_dst(day) -> bool:
    year = day.year
    march_second_sunday = _nth_weekday(year, 3, weekday=6, occurrence=2)
    november_first_sunday = _nth_weekday(year, 11, weekday=6, occurrence=1)
    return march_second_sunday <= day < november_first_sunday


def _nth_weekday(year: int, month: int, *, weekday: int, occurrence: int):
    from datetime import date, timedelta

    current = date(year, month, 1)
    while current.weekday() != weekday:
        current += timedelta(days=1)
    return current + timedelta(days=7 * (occurrence - 1))


def _feature_snapshot_value(snapshot: DecisionSnapshotV2, name: str) -> Any:
    features = snapshot.featureSnapshot.get("features") if isinstance(snapshot.featureSnapshot, dict) else None
    if isinstance(features, dict) and name in features:
        value = features[name]
        if isinstance(value, dict) and "value" in value:
            return value["value"]
        return value
    value = snapshot.featureSnapshot.get(name) if isinstance(snapshot.featureSnapshot, dict) else None
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def _context_feature(context, name: str) -> Any:
    if not context:
        return None
    return context.features.get(name)


def _first_present(features: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in features:
            return features[name]
    return None


def _normalize_value(value: Any, value_type: str) -> tuple[Any, bool]:
    if value is None:
        return (0.0 if value_type == "numeric" else MISSING_CATEGORY), True
    if value_type == "categorical":
        text = str(value)
        return (text if text else MISSING_CATEGORY), not bool(text)
    if isinstance(value, bool):
        return int(value), False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0, True
    if number != number:
        return 0.0, True
    return round(number, 8), False


def _forbidden_payload_paths(value: Any, path: str) -> list[str]:
    forbidden: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            normalized = str(key).replace("-", "_").lower()
            if any(token in normalized for token in ("future", "post_decision", "label", "outcome", "final_pnl", "pnl", "fill")):
                forbidden.append(child_path)
            forbidden.extend(_forbidden_payload_paths(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            forbidden.extend(_forbidden_payload_paths(item, f"{path}[{index}]"))
    return forbidden


def _population_std(values: list[float]) -> float:
    if not values:
        return 0.0
    average = sum(values) / len(values)
    return round(sqrt(sum((value - average) ** 2 for value in values) / len(values)), 8)


def _direction(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def _signal_value(signal: Signal | str) -> str:
    return signal.value if isinstance(signal, Signal) else str(signal)
