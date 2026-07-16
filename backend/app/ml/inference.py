from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import Field

from backend.app.domain.models import DomainModel, OperatingMode, Signal
from backend.app.ml.features import MLFeatureSet
from backend.app.meta_strategy_training import (
    apply_probability_calibration_model,
    load_meta_strategy_model_artifact_data,
    predict_softmax_logistic_probabilities,
)


class SafeMLInferenceConfig(DomainModel):
    mode: OperatingMode = OperatingMode.OFF
    fallbackBehavior: Literal["DETERMINISTIC_BASELINE", "NO_TRADE"] = "NO_TRADE"
    fallbackOnModelUnavailable: bool = True
    fallbackOnSchemaMismatch: bool = True
    minSuccessProbability: float = Field(default=0.52, ge=0.0, le=1.0)
    minCalibratedProbability: float = Field(default=0.52, ge=0.0, le=1.0)
    maxFeatureMissingness: float = Field(default=0.25, ge=0.0, le=1.0)
    maxOutOfDistributionScore: float = Field(default=0.70, ge=0.0, le=1.0)
    minModelHealthScore: float = Field(default=0.70, ge=0.0, le=1.0)
    activeMinRiskCap: float = Field(default=0.25, ge=0.0, le=1.0)
    activeMaxRiskCap: float = Field(default=1.0, ge=0.0, le=1.0)
    configurationHash: str = Field(default="safe_ml_inference_config_v1", min_length=1)


class SafeMLInferenceResult(DomainModel):
    mode: OperatingMode
    effectiveMode: OperatingMode
    deterministicSignal: Signal
    finalSignal: Signal
    candidateAccepted: bool
    mlWouldAcceptCandidate: bool
    appliedToOrder: bool
    successProbability: float | None = Field(default=None, ge=0.0, le=1.0)
    calibratedProbability: float | None = Field(default=None, ge=0.0, le=1.0)
    expectedValueAfterCosts: float | None = None
    uncertainty: float | None = Field(default=None, ge=0.0, le=1.0)
    outOfDistributionScore: float | None = Field(default=None, ge=0.0, le=1.0)
    featureMissingness: float = Field(ge=0.0, le=1.0)
    modelHealth: dict[str, Any]
    recommendedRiskCap: float = Field(ge=0.0, le=1.0)
    reasonCodes: list[str] = Field(default_factory=list)
    predictedAt: datetime
    sessionDate: date
    configurationHash: str = Field(min_length=1)


def apply_safe_ml_inference(
    *,
    deterministic_signal: Signal | str,
    feature_set: MLFeatureSet,
    model_artifact: dict[str, Any] | None,
    config: SafeMLInferenceConfig,
    hard_gates_passed: bool = True,
    candidate_eligible: bool = True,
    session_date: date | None = None,
    predicted_at: datetime | None = None,
) -> SafeMLInferenceResult:
    predicted_at = (predicted_at or datetime.now(UTC)).astimezone(UTC)
    session_date = session_date or predicted_at.date()
    signal = normalize_signal(deterministic_signal)
    reason_codes: list[str] = []
    feature_missingness = missingness_ratio(feature_set)
    base_acceptance = signal in {Signal.BUY.value, Signal.SELL.value} and hard_gates_passed and candidate_eligible
    if signal == Signal.HOLD.value:
        reason_codes.append("ml.cannot_create_trade_from_hold")
    if not hard_gates_passed:
        reason_codes.append("ml.hard_gate_failed_no_bypass")
    if not candidate_eligible:
        reason_codes.append("ml.candidate_ineligible")

    if config.mode == OperatingMode.OFF:
        return result(
            config=config,
            effective_mode=OperatingMode.OFF,
            deterministic_signal=signal,
            final_signal=signal if base_acceptance else Signal.HOLD.value,
            candidate_accepted=base_acceptance,
            ml_would_accept=False,
            applied_to_order=False,
            feature_missingness=feature_missingness,
            model_health={"status": "OFF", "score": 1.0},
            recommended_risk_cap=1.0,
            reason_codes=reason_codes + ["ml.mode_off_ignored"],
            predicted_at=predicted_at,
            session_date=session_date,
        )

    model = select_model(model_artifact)
    health = model_health_status(model_artifact, model)
    if not model_artifact or not model:
        return model_unavailable_result(
            config=config,
            signal=signal,
            base_acceptance=base_acceptance,
            feature_missingness=feature_missingness,
            reason_codes=reason_codes + ["ml.model_unavailable"],
            predicted_at=predicted_at,
            session_date=session_date,
            health=health,
        )

    try:
        load_meta_strategy_model_artifact_data(model_artifact, expected_feature_schema_hash=feature_set.schemaHash)
    except ValueError:
        return schema_or_ood_result(
            config=config,
            signal=signal,
            base_acceptance=base_acceptance,
            feature_missingness=feature_missingness,
            reason_codes=reason_codes + ["ml.feature_schema_mismatch"],
            predicted_at=predicted_at,
            session_date=session_date,
            health={"status": "SCHEMA_MISMATCH", "score": 0.0},
        )

    probabilities = model_probabilities(model, feature_set)
    calibrated = apply_probability_calibration_model(probabilities, model.get("calibration") or {"method": "none"})
    success_probability = candidate_success_probability(signal, calibrated)
    calibrated_probability = success_probability
    uncertainty = probability_uncertainty(success_probability)
    ood_score = out_of_distribution_score(feature_set, model)
    expected_value = expected_value_after_costs(success_probability, feature_set)
    model_health = model_health_status(model_artifact, model)
    current_candidate_ok = (
        base_acceptance
        and success_probability >= config.minSuccessProbability
        and calibrated_probability >= config.minCalibratedProbability
    )
    operationally_ok = (
        float(model_health["score"]) >= config.minModelHealthScore
        and feature_missingness <= config.maxFeatureMissingness
        and ood_score <= config.maxOutOfDistributionScore
    )
    if not operationally_ok:
        codes = reason_codes + ["ml.operational_health_failed"]
        if feature_missingness > config.maxFeatureMissingness:
            codes.append("ml.feature_missingness_too_high")
        if ood_score > config.maxOutOfDistributionScore:
            codes.append("ml.out_of_distribution")
        if float(model_health["score"]) < config.minModelHealthScore:
            codes.append("ml.model_health_too_low")
        return schema_or_ood_result(
            config=config,
            signal=signal,
            base_acceptance=base_acceptance,
            feature_missingness=feature_missingness,
            reason_codes=codes,
            predicted_at=predicted_at,
            session_date=session_date,
            health=model_health,
            success_probability=success_probability,
            calibrated_probability=calibrated_probability,
            expected_value=expected_value,
            uncertainty=uncertainty,
            ood_score=ood_score,
        )

    if not current_candidate_ok:
        reason_codes.append("ml.current_candidate_probability_below_threshold")
    risk_cap = bounded_active_risk_cap(success_probability, expected_value, config)
    if config.mode == OperatingMode.SHADOW:
        return result(
            config=config,
            effective_mode=OperatingMode.SHADOW,
            deterministic_signal=signal,
            final_signal=signal if base_acceptance else Signal.HOLD.value,
            candidate_accepted=base_acceptance,
            ml_would_accept=current_candidate_ok,
            applied_to_order=False,
            success_probability=success_probability,
            calibrated_probability=calibrated_probability,
            expected_value=expected_value,
            uncertainty=uncertainty,
            ood_score=ood_score,
            feature_missingness=feature_missingness,
            model_health=model_health,
            recommended_risk_cap=1.0,
            reason_codes=reason_codes + ["ml.shadow_record_only"],
            predicted_at=predicted_at,
            session_date=session_date,
        )

    if config.mode == OperatingMode.FALLBACK:
        return fallback_result(
            config=config,
            signal=signal,
            base_acceptance=base_acceptance,
            feature_missingness=feature_missingness,
            reason_codes=reason_codes + ["ml.fallback_mode_uses_deterministic_baseline"],
            predicted_at=predicted_at,
            session_date=session_date,
            health=model_health,
            success_probability=success_probability,
            calibrated_probability=calibrated_probability,
            expected_value=expected_value,
            uncertainty=uncertainty,
            ood_score=ood_score,
        )

    accepted = bool(current_candidate_ok)
    final_signal = signal if accepted else Signal.HOLD.value
    return result(
        config=config,
        effective_mode=config.mode,
        deterministic_signal=signal,
        final_signal=final_signal,
        candidate_accepted=accepted,
        ml_would_accept=current_candidate_ok,
        applied_to_order=True,
        success_probability=success_probability,
        calibrated_probability=calibrated_probability,
        expected_value=expected_value,
        uncertainty=uncertainty,
        ood_score=ood_score,
        feature_missingness=feature_missingness,
        model_health=model_health,
        recommended_risk_cap=risk_cap if config.mode == OperatingMode.ACTIVE and accepted else 1.0,
        reason_codes=reason_codes + [f"ml.mode_{mode_value(config.mode).lower()}"],
        predicted_at=predicted_at,
        session_date=session_date,
    )


def model_unavailable_result(**kwargs: Any) -> SafeMLInferenceResult:
    config: SafeMLInferenceConfig = kwargs["config"]
    if config.fallbackOnModelUnavailable:
        return fallback_result(**kwargs)
    return no_trade_result(**kwargs)


def schema_or_ood_result(**kwargs: Any) -> SafeMLInferenceResult:
    config: SafeMLInferenceConfig = kwargs["config"]
    if config.fallbackOnSchemaMismatch and config.fallbackBehavior == "DETERMINISTIC_BASELINE":
        return fallback_result(**kwargs)
    return no_trade_result(**kwargs)


def fallback_result(**kwargs: Any) -> SafeMLInferenceResult:
    signal = kwargs["signal"]
    base_acceptance = kwargs["base_acceptance"]
    return result(
        config=kwargs["config"],
        effective_mode=OperatingMode.FALLBACK,
        deterministic_signal=signal,
        final_signal=signal if base_acceptance else Signal.HOLD.value,
        candidate_accepted=base_acceptance,
        ml_would_accept=False,
        applied_to_order=False,
        success_probability=kwargs.get("success_probability"),
        calibrated_probability=kwargs.get("calibrated_probability"),
        expected_value=kwargs.get("expected_value"),
        uncertainty=kwargs.get("uncertainty"),
        ood_score=kwargs.get("ood_score"),
        feature_missingness=kwargs["feature_missingness"],
        model_health=kwargs["health"],
        recommended_risk_cap=1.0,
        reason_codes=kwargs["reason_codes"] + ["ml.fallback_deterministic_baseline"],
        predicted_at=kwargs["predicted_at"],
        session_date=kwargs["session_date"],
    )


def no_trade_result(**kwargs: Any) -> SafeMLInferenceResult:
    return result(
        config=kwargs["config"],
        effective_mode=OperatingMode.FALLBACK,
        deterministic_signal=kwargs["signal"],
        final_signal=Signal.HOLD.value,
        candidate_accepted=False,
        ml_would_accept=False,
        applied_to_order=True,
        success_probability=kwargs.get("success_probability"),
        calibrated_probability=kwargs.get("calibrated_probability"),
        expected_value=kwargs.get("expected_value"),
        uncertainty=kwargs.get("uncertainty"),
        ood_score=kwargs.get("ood_score"),
        feature_missingness=kwargs["feature_missingness"],
        model_health=kwargs["health"],
        recommended_risk_cap=0.0,
        reason_codes=kwargs["reason_codes"] + ["ml.fallback_no_trade"],
        predicted_at=kwargs["predicted_at"],
        session_date=kwargs["session_date"],
    )


def result(
    *,
    config: SafeMLInferenceConfig,
    effective_mode: OperatingMode,
    deterministic_signal: str,
    final_signal: str,
    candidate_accepted: bool,
    ml_would_accept: bool,
    applied_to_order: bool,
    feature_missingness: float,
    model_health: dict[str, Any],
    recommended_risk_cap: float,
    reason_codes: list[str],
    predicted_at: datetime,
    session_date: date,
    success_probability: float | None = None,
    calibrated_probability: float | None = None,
    expected_value: float | None = None,
    uncertainty: float | None = None,
    ood_score: float | None = None,
) -> SafeMLInferenceResult:
    return SafeMLInferenceResult(
        mode=config.mode,
        effectiveMode=effective_mode,
        deterministicSignal=Signal(deterministic_signal),
        finalSignal=Signal(final_signal),
        candidateAccepted=bool(candidate_accepted),
        mlWouldAcceptCandidate=bool(ml_would_accept),
        appliedToOrder=bool(applied_to_order),
        successProbability=bounded_optional(success_probability),
        calibratedProbability=bounded_optional(calibrated_probability),
        expectedValueAfterCosts=expected_value,
        uncertainty=bounded_optional(uncertainty),
        outOfDistributionScore=bounded_optional(ood_score),
        featureMissingness=bounded(feature_missingness),
        modelHealth=model_health,
        recommendedRiskCap=bounded(recommended_risk_cap),
        reasonCodes=sorted(set(reason_codes)),
        predictedAt=predicted_at,
        sessionDate=session_date,
        configurationHash=config.configurationHash,
    )


def select_model(artifact: dict[str, Any] | None) -> dict[str, Any] | None:
    if not artifact:
        return None
    models = artifact.get("models") or {}
    champion_name = str(artifact.get("championModel") or "logistic_regression_champion")
    model = models.get(champion_name) or models.get("logistic_regression_champion")
    if not model or model.get("available") is False:
        return None
    return model


def mode_value(mode: OperatingMode | str) -> str:
    return mode.value if isinstance(mode, OperatingMode) else str(mode)


def model_probabilities(model: dict[str, Any], feature_set: MLFeatureSet) -> dict[str, float]:
    if "fixedProbabilities" in model:
        return normalize_probability_map(model["fixedProbabilities"])
    if "fixedProbability" in model:
        value = bounded(float(model["fixedProbability"]))
        return {"BUY": value, "SELL": 1.0 - value, "HOLD": min(0.2, 1.0 - value)}
    if model.get("kind") == "softmax_regularized_logistic_regression":
        return predict_softmax_logistic_probabilities(model, numeric_features(feature_set.featureValues))
    return {"BUY": 0.0, "SELL": 0.0, "HOLD": 1.0}


def candidate_success_probability(signal: str, probabilities: dict[str, float]) -> float:
    if signal == Signal.BUY.value:
        return bounded(float(probabilities.get("BUY") or 0.0))
    if signal == Signal.SELL.value:
        return bounded(float(probabilities.get("SELL") or 0.0))
    return 0.0


def expected_value_after_costs(success_probability: float, feature_set: MLFeatureSet) -> float:
    values = feature_set.featureValues
    target = positive_float(values.get("target_distance"), default=1.0)
    stop = positive_float(values.get("stop_distance"), default=1.0)
    cost = max(0.0, positive_float(values.get("expected_transaction_cost"), default=0.0))
    return round((success_probability * target) - ((1.0 - success_probability) * stop) - cost, 6)


def bounded_active_risk_cap(success_probability: float, expected_value: float, config: SafeMLInferenceConfig) -> float:
    if expected_value <= 0:
        return config.activeMinRiskCap
    raw = config.activeMinRiskCap + ((config.activeMaxRiskCap - config.activeMinRiskCap) * success_probability)
    return min(config.activeMaxRiskCap, max(config.activeMinRiskCap, raw))


def out_of_distribution_score(feature_set: MLFeatureSet, model: dict[str, Any]) -> float:
    explicit = model.get("outOfDistributionScore")
    if explicit is not None:
        return bounded(float(explicit))
    values = [float(value) for value in numeric_features(feature_set.featureValues).values()]
    if not values:
        return 1.0
    large = sum(1 for value in values if abs(value) > 5.0)
    return bounded((large / len(values)) + (missingness_ratio(feature_set) * 0.5))


def missingness_ratio(feature_set: MLFeatureSet) -> float:
    if not feature_set.missingIndicators:
        return 0.0
    return bounded(sum(1 for value in feature_set.missingIndicators.values() if value) / len(feature_set.missingIndicators))


def model_health_status(artifact: dict[str, Any] | None, model: dict[str, Any] | None) -> dict[str, Any]:
    if not artifact:
        return {"status": "UNAVAILABLE", "score": 0.0, "reasonCodes": ["ml.artifact_missing"]}
    if not model:
        return {"status": "UNAVAILABLE", "score": 0.0, "reasonCodes": ["ml.champion_model_unavailable"]}
    if model.get("available") is False:
        return {"status": "UNAVAILABLE", "score": 0.0, "reasonCodes": ["ml.model_marked_unavailable"]}
    explicit_score = model.get("modelHealthScore", artifact.get("modelHealthScore"))
    if explicit_score is not None:
        score = bounded(float(explicit_score))
    else:
        score = 1.0 if model.get("modelHash") and model.get("featureSchemaHash") else 0.75
    return {"status": "OK" if score >= 0.7 else "DEGRADED", "score": score, "reasonCodes": []}


def probability_uncertainty(success_probability: float) -> float:
    return bounded(1.0 - abs(success_probability - 0.5) * 2.0)


def numeric_features(values: dict[str, Any]) -> dict[str, float]:
    numeric: dict[str, float] = {}
    for key, value in values.items():
        try:
            numeric[key] = float(value)
        except (TypeError, ValueError):
            continue
    return numeric


def normalize_probability_map(values: dict[str, Any]) -> dict[str, float]:
    raw = {label.value: max(0.0, float(values.get(label.value) or values.get(label.name) or 0.0)) for label in Signal}
    total = sum(raw.values()) or 1.0
    return {key: value / total for key, value in raw.items()}


def positive_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not parsed or parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return default
    return abs(parsed)


def normalize_signal(value: Signal | str) -> str:
    text = value.value if isinstance(value, Signal) else str(value).upper()
    if text in {"BUY", "SELL", "HOLD"}:
        return text
    return Signal.HOLD.value


def bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def bounded_optional(value: float | None) -> float | None:
    return None if value is None else bounded(value)
