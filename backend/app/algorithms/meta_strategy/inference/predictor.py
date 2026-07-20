"""Prediction engine for Meta-Strategy inference."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from backend.app.algorithms.meta_strategy.inference.artifact_health import (
    artifact_schema_compatible,
    model_health_status,
    select_champion_model,
)
from backend.app.algorithms.meta_strategy.inference.decision_policy import (
    MetaStrategyInferenceConfig,
    MetaStrategyInferenceResult,
    apply_decision_policy,
    normalize_signal,
)
from backend.app.algorithms.meta_strategy.inference.feature_health import (
    feature_missingness_ratio,
    feature_schema_hash,
    out_of_distribution_score,
)
from backend.app.algorithms.meta_strategy.inference.result_validation import validate_inference_result
from backend.app.algorithms.meta_strategy.inference.runtime_health import (
    RuntimeHealthGateResult,
    evaluate_runtime_health_gates,
)
from backend.app.algorithms.meta_strategy.inference.uncertainty import probability_uncertainty
from backend.app.algorithms.meta_strategy.models.calibration import apply_meta_strategy_calibration
from backend.app.algorithms.meta_strategy.models.probability_contract import (
    candidate_conditional_output,
    candidate_success_probability,
    normalize_probabilities,
)


def apply_meta_strategy_inference(
    *,
    deterministic_signal: str,
    feature_set: Any,
    model_artifact: dict[str, Any] | None,
    config: MetaStrategyInferenceConfig,
    hard_gates_passed: bool = True,
    candidate_eligible: bool = True,
    deterministic_risk_multiplier: float = 1.0,
    session_date: date | None = None,
    predicted_at: datetime | None = None,
) -> MetaStrategyInferenceResult:
    predicted_at = (predicted_at or datetime.now(UTC)).astimezone(UTC)
    session_date = session_date or predicted_at.date()
    signal = normalize_signal(deterministic_signal)
    reason_codes: list[str] = []
    missingness = feature_missingness_ratio(feature_set)
    schema_hash = feature_schema_hash(feature_set)
    model = select_champion_model(model_artifact)
    health = model_health_status(model_artifact, model)
    if not model_artifact or not model:
        reason_codes.append("meta_strategy.inference.model_unavailable")
        runtime_health = evaluate_runtime_health_gates(
            config=config,
            model_artifact=model_artifact,
            model=model,
            expected_schema_hash=schema_hash,
            feature_missingness=missingness,
            out_of_distribution_score=0.0,
            model_health=health,
            deterministic_signal=signal,
            prediction_started_at=predicted_at,
            prediction_finished_at=predicted_at,
        )
        result = apply_decision_policy(
            config=config if config.fallbackOnModelUnavailable else MetaStrategyInferenceConfig(mode="FALLBACK", fallbackBehavior="NO_TRADE"),
            deterministic_signal=signal,
            hard_gates_passed=hard_gates_passed,
            candidate_eligible=candidate_eligible,
            deterministic_risk_multiplier=deterministic_risk_multiplier,
            success_probability=None,
            calibrated_probability=None,
            expected_value=None,
            feature_missingness=missingness,
            ood_score=None,
            model_health=_runtime_blocked_health(health, runtime_health),
            candidate_conditional_output=None,
            reason_codes=reason_codes + list(runtime_health.reasonCodes),
            predicted_at=predicted_at,
            session_date=session_date,
        )
        return validate_inference_result(result)

    if not artifact_schema_compatible(model_artifact, schema_hash):
        reason_codes.append("meta_strategy.inference.feature_schema_mismatch")
        runtime_health = evaluate_runtime_health_gates(
            config=config,
            model_artifact=model_artifact,
            model=model,
            expected_schema_hash=schema_hash,
            feature_missingness=missingness,
            out_of_distribution_score=1.0,
            model_health=health,
            deterministic_signal=signal,
            prediction_started_at=predicted_at,
            prediction_finished_at=predicted_at,
        )
        result = apply_decision_policy(
            config=config,
            deterministic_signal=signal,
            hard_gates_passed=hard_gates_passed,
            candidate_eligible=candidate_eligible,
            deterministic_risk_multiplier=deterministic_risk_multiplier,
            success_probability=None,
            calibrated_probability=None,
            expected_value=None,
            feature_missingness=missingness,
            ood_score=1.0,
            model_health=_runtime_blocked_health(
                {"status": "SCHEMA_MISMATCH", "score": 0.0, "reasonCodes": ("meta_strategy.inference.schema_mismatch",)},
                runtime_health,
            ),
            candidate_conditional_output=None,
            reason_codes=reason_codes + list(runtime_health.reasonCodes),
            predicted_at=predicted_at,
            session_date=session_date,
        )
        return validate_inference_result(result)

    ood = out_of_distribution_score(feature_set, model)
    pre_prediction_health = evaluate_runtime_health_gates(
        config=config,
        model_artifact=model_artifact,
        model=model,
        expected_schema_hash=schema_hash,
        feature_missingness=missingness,
        out_of_distribution_score=ood,
        model_health=health,
        deterministic_signal=signal,
        prediction_started_at=predicted_at,
        prediction_finished_at=predicted_at,
    )
    if not pre_prediction_health.passed:
        result = apply_decision_policy(
            config=config,
            deterministic_signal=signal,
            hard_gates_passed=hard_gates_passed,
            candidate_eligible=candidate_eligible,
            deterministic_risk_multiplier=deterministic_risk_multiplier,
            success_probability=None,
            calibrated_probability=None,
            expected_value=None,
            feature_missingness=missingness,
            ood_score=ood,
            model_health=_runtime_blocked_health(health, pre_prediction_health),
            candidate_conditional_output=None,
            reason_codes=reason_codes + list(pre_prediction_health.reasonCodes),
            predicted_at=predicted_at,
            session_date=session_date,
        )
        return validate_inference_result(result)

    prediction_started_at = datetime.now(UTC)
    raw_probabilities = model_probabilities(model, feature_set)
    calibrated = apply_meta_strategy_calibration(raw_probabilities, model.get("calibration") or {"method": "none"})
    success = candidate_success_probability(calibrated, signal if signal in {"BUY", "SELL"} else "HOLD") if signal != "HOLD" else 0.0
    expected_value = expected_value_after_costs(success, feature_set)
    conditional = candidate_conditional_output(
        candidate_side=signal if signal in {"BUY", "SELL"} else "HOLD",
        probabilities=calibrated,
        uncertainty=probability_uncertainty(success),
        out_of_distribution_score=ood,
    )
    prediction_finished_at = _prediction_finished_at(prediction_started_at, model)
    runtime_health = evaluate_runtime_health_gates(
        config=config,
        model_artifact=model_artifact,
        model=model,
        expected_schema_hash=schema_hash,
        feature_missingness=missingness,
        out_of_distribution_score=ood,
        model_health=health,
        deterministic_signal=signal,
        candidate_conditional_output=conditional,
        prediction_started_at=prediction_started_at,
        prediction_finished_at=prediction_finished_at,
    )
    if not runtime_health.passed:
        result = apply_decision_policy(
            config=config,
            deterministic_signal=signal,
            hard_gates_passed=hard_gates_passed,
            candidate_eligible=candidate_eligible,
            deterministic_risk_multiplier=deterministic_risk_multiplier,
            success_probability=None,
            calibrated_probability=None,
            expected_value=None,
            feature_missingness=missingness,
            ood_score=ood,
            model_health=_runtime_blocked_health(health, runtime_health),
            candidate_conditional_output=conditional,
            reason_codes=reason_codes + list(runtime_health.reasonCodes),
            predicted_at=predicted_at,
            session_date=session_date,
        )
        return validate_inference_result(result)

    result = apply_decision_policy(
        config=config,
        deterministic_signal=signal,
        hard_gates_passed=hard_gates_passed,
        candidate_eligible=candidate_eligible,
        deterministic_risk_multiplier=deterministic_risk_multiplier,
        success_probability=success,
        calibrated_probability=success,
        expected_value=expected_value,
        feature_missingness=missingness,
        ood_score=ood,
        model_health=_runtime_passed_health(health, runtime_health),
        candidate_conditional_output=conditional,
        reason_codes=reason_codes + ["meta_strategy.inference.runtime_health_passed"],
        predicted_at=predicted_at,
        session_date=session_date,
    )
    return validate_inference_result(result)


def model_probabilities(model: dict[str, Any], feature_set: Any) -> dict[str, float]:
    if "fixedProbabilities" in model:
        return normalize_probabilities(dict(model["fixedProbabilities"]))
    if "fixedProbability" in model:
        value = max(0.0, min(1.0, float(model["fixedProbability"])))
        return normalize_probabilities({"BUY": value, "SELL": 1.0 - value, "HOLD": 0.0})
    return normalize_probabilities({"BUY": 0.0, "SELL": 0.0, "HOLD": 1.0})


def expected_value_after_costs(success_probability: float, feature_set: Any) -> float:
    values = getattr(feature_set, "featureValues", None) or getattr(feature_set, "feature_values", None) or {}
    target = positive_float(values.get("target_distance"), default=1.0)
    stop = positive_float(values.get("stop_distance"), default=1.0)
    cost = max(0.0, positive_float(values.get("expected_transaction_cost"), default=0.0))
    return round((success_probability * target) - ((1.0 - success_probability) * stop) - cost, 6)


def positive_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not parsed or parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return default
    return abs(parsed)


def _prediction_finished_at(started_at: datetime, model: dict[str, Any]) -> datetime:
    explicit_latency = model.get("predictionLatencyMs")
    if explicit_latency is None:
        return datetime.now(UTC)
    try:
        latency_ms = max(0.0, float(explicit_latency))
    except (TypeError, ValueError):
        return datetime.now(UTC)
    return started_at + timedelta(milliseconds=latency_ms)


def _runtime_passed_health(model_health: dict[str, Any], runtime_health: RuntimeHealthGateResult) -> dict[str, Any]:
    return {**model_health, "runtimeHealth": runtime_health.as_dict()}


def _runtime_blocked_health(model_health: dict[str, Any], runtime_health: RuntimeHealthGateResult) -> dict[str, Any]:
    return {
        **model_health,
        "status": "RUNTIME_HEALTH_FAILED",
        "score": 0.0,
        "runtimeHealth": runtime_health.as_dict(),
        "reasonCodes": tuple(model_health.get("reasonCodes") or ()) + runtime_health.reasonCodes,
    }


__all__ = [
    "apply_meta_strategy_inference",
    "expected_value_after_costs",
    "model_probabilities",
    "positive_float",
]
