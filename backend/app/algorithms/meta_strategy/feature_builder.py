"""Meta-Strategy-owned candidate feature builder."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from statistics import pstdev
from typing import Any

from backend.app.algorithms.meta_strategy.feature_schema import (
    META_STRATEGY_FAMILY_ORDER,
    META_STRATEGY_FEATURE_SCHEMA_VERSION,
    meta_strategy_feature_schema,
    meta_strategy_feature_schema_hash,
)
from backend.app.algorithms.meta_strategy.missingness import missingness_ratio, normalize_feature_value
from backend.app.algorithms.meta_strategy.out_of_distribution import meta_strategy_out_of_distribution_score
from backend.app.algorithms.meta_strategy.strategy_registry import DIRECTIONAL_STRATEGIES


class ForbiddenMetaStrategyFeatureFieldError(ValueError):
    pass


class ForeignAlgorithmFeatureRowError(ValueError):
    pass


@dataclass(frozen=True)
class MetaStrategyFeatureSet:
    schemaVersion: str
    schemaHash: str
    rowId: str
    symbol: str
    decisionTimestampUtc: str
    featureValues: dict[str, Any]
    missingIndicators: dict[str, bool]
    forbiddenFieldsChecked: tuple[str, ...]
    missingnessRatio: float
    outOfDistributionScore: float
    explanation: str
    recordedMissingFeatureCount: int | None = None
    recordedCompleteFeatureVectorHash: str | None = None

    def complete_feature_vector_hash(self) -> str:
        if self.recordedCompleteFeatureVectorHash:
            return self.recordedCompleteFeatureVectorHash
        return hash_feature_values(self.featureValues)

    def missing_feature_count(self) -> int:
        if self.recordedMissingFeatureCount is not None:
            return self.recordedMissingFeatureCount
        return sum(1 for value in self.missingIndicators.values() if value)


def build_meta_strategy_features(row: dict[str, Any]) -> MetaStrategyFeatureSet:
    reject_foreign_algorithm_row(row)
    reject_forbidden_future_fields(row)
    schema = meta_strategy_feature_schema()
    raw_values = _raw_feature_values(row)
    feature_values: dict[str, Any] = {}
    missing_indicators: dict[str, bool] = {}
    for spec in schema:
        value, missing = normalize_feature_value(raw_values.get(spec.name), spec.valueType)
        feature_values[spec.name] = value
        feature_values[f"{spec.name}__missing"] = 1 if missing else 0
        missing_indicators[spec.name] = missing
    return MetaStrategyFeatureSet(
        schemaVersion=META_STRATEGY_FEATURE_SCHEMA_VERSION,
        schemaHash=meta_strategy_feature_schema_hash(),
        rowId=str(row.get("id") or row.get("snapshotId") or row.get("decisionId") or "unknown"),
        symbol=str(row.get("symbol") or "SPY"),
        decisionTimestampUtc=str(row.get("decisionTimestampUtc") or row.get("timestamp") or row.get("capturedAtUtc") or ""),
        featureValues=feature_values,
        missingIndicators=missing_indicators,
        forbiddenFieldsChecked=FORBIDDEN_FIELD_NAMES,
        missingnessRatio=missingness_ratio(missing_indicators),
        outOfDistributionScore=meta_strategy_out_of_distribution_score(feature_values),
        explanation="Meta-Strategy candidate features built from decision-time deterministic state without labels, fills, outcomes, or future fields.",
        recordedMissingFeatureCount=(row.get("featureVector") or {}).get("missingFeatureCount") if isinstance(row.get("featureVector"), dict) else None,
        recordedCompleteFeatureVectorHash=(row.get("featureVector") or {}).get("completeFeatureVectorHash") if isinstance(row.get("featureVector"), dict) else None,
    )


def build_meta_strategy_features_from_characterization_fixture(fixture: dict[str, Any], *, algorithm_id: str = "meta_strategy") -> MetaStrategyFeatureSet:
    sanitized = {
        key: value
        for key, value in fixture.items()
        if key not in {"label", "mlDecision", "modelProbabilities", "riskMultiplier", "finalCandidateStatus"}
    }
    return build_meta_strategy_features({"algorithmId": algorithm_id, **sanitized})


def reject_foreign_algorithm_row(row: dict[str, Any]) -> None:
    algorithm_id = row.get("algorithmId", "meta_strategy")
    if algorithm_id != "meta_strategy":
        raise ForeignAlgorithmFeatureRowError(f"Meta-Strategy feature builder rejects row from algorithm {algorithm_id!r}")


def reject_forbidden_future_fields(row: dict[str, Any]) -> None:
    violations = tuple(sorted(_forbidden_payload_paths(row, "row")))
    if violations:
        raise ForbiddenMetaStrategyFeatureFieldError(f"Meta-Strategy feature builder rejects forbidden future fields: {', '.join(violations)}")


def hash_feature_values(feature_values: dict[str, Any]) -> str:
    serialized = json.dumps(feature_values, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _raw_feature_values(row: dict[str, Any]) -> dict[str, Any]:
    if "featureVector" in row and isinstance(row["featureVector"], dict):
        selected = row["featureVector"].get("selectedValues")
    else:
        selected = row.get("selectedValues")
    values = dict(selected or {})
    values.update(_strategy_features(row))
    values.update(_family_features(row, values))
    values.update(_context_features(row))
    values.update(_regime_features(row))
    values.update(_candidate_features(row, values))
    values.update(_execution_features(row, values))
    values.update(_forecast_features(row))
    return values


def _strategy_features(row: dict[str, Any]) -> dict[str, Any]:
    outputs = row.get("directionalStrategyOutputs") or {}
    values: dict[str, Any] = {}
    for entry in DIRECTIONAL_STRATEGIES:
        output = outputs.get(entry.strategy_id) if isinstance(outputs, dict) else None
        prefix = f"strategy_{entry.strategy_id}"
        values[f"{prefix}_direction"] = output.get("direction") if output else None
        values[f"{prefix}_confidence"] = output.get("confidence") if output else None
        values[f"{prefix}_eligible"] = _bool(output.get("eligible") if output else None)
        values[f"{prefix}_active"] = _bool(output.get("active") if output else None)
        values[f"{prefix}_data_ready"] = _bool(output.get("dataReady") if output else None)
        values[f"{prefix}_regime_fit"] = output.get("regimeFit") if output else None
        values[f"{prefix}_reliability"] = output.get("reliability") if output else None
        setup_detected = output.get("setupDetected") if output and "setupDetected" in output else output.get("signal") != "HOLD" if output else None
        values[f"{prefix}_setup_detected"] = _bool(setup_detected)
    return values


def _family_features(row: dict[str, Any], seeded: dict[str, Any]) -> dict[str, Any]:
    values = {}
    family_scores = {str(item.get("family")): item for item in row.get("familyScores", ()) if isinstance(item, dict)}
    signed_scores: dict[str, float | None] = {}
    for family in META_STRATEGY_FAMILY_ORDER:
        item = family_scores.get(family)
        signed = round(float(item.get("buyScore", 0.0)) - float(item.get("sellScore", 0.0)), 6) if item else seeded.get(f"family_{family.lower()}_score")
        signed_scores[family] = signed
        values[f"family_{family.lower()}_score"] = signed
    deterministic = row.get("deterministicCandidate") or {}
    supporting = tuple(deterministic.get("supportingFamilies") or ())
    opposing = tuple(deterministic.get("opposingFamilies") or ())
    denominator = len(supporting) + len(opposing)
    values["family_agreement"] = len(supporting) / denominator if denominator else 0.0
    values["supporting_family_count"] = len(supporting)
    values["opposing_family_count"] = len(opposing)
    present = [float(value) for value in signed_scores.values() if value is not None]
    values["directional_dispersion"] = round(pstdev(present), 8) if present else 0.0
    strongest, weakest = _strongest_weakest(signed_scores)
    values["strongest_family"] = seeded.get("strongest_family", strongest[0])
    values["strongest_family_score"] = seeded.get("strongest_family_score", strongest[1])
    values["weakest_family"] = seeded.get("weakest_family", weakest[0])
    values["weakest_family_score"] = seeded.get("weakest_family_score", weakest[1])
    return values


def _context_features(row: dict[str, Any]) -> dict[str, Any]:
    contexts = {item.get("contextId"): item for item in row.get("contextOutputs", ()) if isinstance(item, dict)}
    return {
        "spy_relative_strength_vs_qqq_iwm": _context_feature(contexts.get("relative_strength_qqq_iwm"), "primaryRelativeReturn"),
        "relative_strength_normalized_score": _context_feature(contexts.get("relative_strength_qqq_iwm"), "normalizedRelativeStrengthScore"),
        "breadth_score": _confidence(contexts.get("market_breadth_momentum")),
        "breadth_coverage": _context_feature(contexts.get("market_breadth_momentum"), "dataCoverage"),
        "economic_event_state": _context_feature(contexts.get("economic_event_context"), "eventState"),
        "economic_event_importance": _context_feature(contexts.get("economic_event_context"), "eventImportance"),
        "market_structure_state": _context_feature(contexts.get("market_structure_context"), "breakOfStructure"),
        "market_structure_quality": _context_feature(contexts.get("market_structure_context"), "structureQuality"),
        "volume_confirmation_score": _confidence(contexts.get("volume_confirmation")),
        "volume_trend": _context_feature(contexts.get("volume_confirmation"), "volumeTrend"),
        "vwap_position_state": _context_feature(contexts.get("vwap_position_context"), "reclaimRejectionState"),
        "vwap_distance_atr": _context_feature(contexts.get("vwap_position_context"), "distanceFromVwapAtr"),
    }


def _regime_features(row: dict[str, Any]) -> dict[str, Any]:
    regime = row.get("regimeOutput") or {}
    features = regime.get("features") or {}
    return {
        "regime_category": regime.get("label"),
        "adx": _first_present(features, "trendStrengthAdx", "adx"),
        "atr_percentile": _first_present(features, "atrPercentile"),
        "realized_volatility_percentile": _first_present(features, "realizedVolatilityPercentile"),
        "trend_fit": _first_present(features, "trendFit"),
        "breakout_fit": _first_present(features, "breakoutFit"),
        "reversal_fit": _first_present(features, "reversalFit"),
        "mean_reversion_fit": _first_present(features, "meanReversionFit"),
    }


def _candidate_features(row: dict[str, Any], seeded: dict[str, Any]) -> dict[str, Any]:
    deterministic = row.get("deterministicCandidate") or {}
    return {
        "candidate_side": deterministic.get("signal") or seeded.get("candidate_side"),
        "deterministic_score": deterministic.get("finalScore", seeded.get("deterministic_score")),
        "signal_margin": abs(float(deterministic.get("buyConfidence", 0.0)) - float(deterministic.get("sellConfidence", 0.0))) if deterministic else seeded.get("signal_margin"),
        "expected_transaction_cost": seeded.get("expected_transaction_cost"),
    }


def _execution_features(row: dict[str, Any], seeded: dict[str, Any]) -> dict[str, Any]:
    geometry = row.get("candidateGeometry") or {}
    entry = geometry.get("entryPrice")
    stop = geometry.get("stopPrice")
    target = geometry.get("targetPrice")
    stop_distance = abs(float(entry) - float(stop)) if entry is not None and stop is not None else seeded.get("stop_distance")
    target_distance = abs(float(target) - float(entry)) if entry is not None and target is not None else seeded.get("target_distance")
    reward_risk = target_distance / stop_distance if target_distance is not None and stop_distance not in {None, 0.0} else seeded.get("reward_risk_ratio")
    return {
        "spread_dollars": seeded.get("spread_dollars"),
        "relative_volume": seeded.get("relative_volume"),
        "estimated_slippage": seeded.get("estimated_slippage", 0.02),
        "time_of_day_minutes": seeded.get("time_of_day_minutes", 645.0),
        "minutes_since_open": seeded.get("minutes_since_open", 75.0),
        "minutes_until_close": seeded.get("minutes_until_close", 315.0),
        "entry_distance": seeded.get("entry_distance"),
        "stop_distance": stop_distance,
        "target_distance": target_distance,
        "reward_risk_ratio": reward_risk,
        "expected_transaction_cost": seeded.get("expected_transaction_cost"),
    }


def _forecast_features(row: dict[str, Any]) -> dict[str, Any]:
    forecast = row.get("oosForecast") or row.get("forecast") or {}
    return {
        "forecast_status": forecast.get("status", "missing_approved_forecast_model"),
        "forecast_probability_buy_success": forecast.get("probabilityBuySuccess"),
        "forecast_probability_sell_success": forecast.get("probabilitySellSuccess"),
        "forecast_probability_timeout": forecast.get("probabilityTimeout"),
        "forecast_training_end_age_minutes": forecast.get("trainingEndAgeMinutes"),
        "forecast_artifact_id": forecast.get("artifactId"),
    }


def _forbidden_payload_paths(value: Any, path: str) -> list[str]:
    forbidden: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            normalized = str(key).replace("-", "_").lower()
            if _is_forbidden_feature_key(normalized, child_path):
                forbidden.append(child_path)
            forbidden.extend(_forbidden_payload_paths(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            forbidden.extend(_forbidden_payload_paths(item, f"{path}[{index}]"))
    return forbidden


def _strongest_weakest(scores: dict[str, float | None]) -> tuple[tuple[str | None, float | None], tuple[str | None, float | None]]:
    present = [(family, value) for family, value in scores.items() if value is not None]
    if not present:
        return (None, None), (None, None)
    return max(present, key=lambda row: abs(row[1])), min(present, key=lambda row: abs(row[1]))


def _context_feature(context: dict[str, Any] | None, name: str) -> Any:
    return (context.get("features") or {}).get(name) if context else None


def _confidence(context: dict[str, Any] | None) -> Any:
    return context.get("confidence") if context else None


def _first_present(features: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in features:
            return features[name]
    return None


def _bool(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def _is_forbidden_feature_key(normalized_key: str, path: str) -> bool:
    if path.endswith("regimeOutput.label"):
        return False
    if normalized_key in {"label", "labels", "training_label", "meta_label"}:
        return True
    return any(token in normalized_key for token in ("future", "post_decision", "outcome", "final_pnl", "pnl", "fill"))


FORBIDDEN_FIELD_NAMES = (
    "finalOutcome",
    "fillResult",
    "fills",
    "brokerSubmissionResult",
    "metaModelPrediction",
    "post_decision_timestamps",
    "raw_feature_payload_keys",
)


__all__ = [
    "ForbiddenMetaStrategyFeatureFieldError",
    "ForeignAlgorithmFeatureRowError",
    "MetaStrategyFeatureSet",
    "build_meta_strategy_features",
    "build_meta_strategy_features_from_characterization_fixture",
    "hash_feature_values",
    "reject_forbidden_future_fields",
    "reject_foreign_algorithm_row",
]
