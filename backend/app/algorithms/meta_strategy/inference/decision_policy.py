"""Decision policy for Meta-Strategy inference."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal

from backend.app.algorithms.meta_strategy.inference.fallback import FallbackBehavior, fallback_risk, fallback_signal


InferenceMode = Literal["OFF", "SHADOW", "FILTER", "RISK_REDUCTION", "FALLBACK", "DISABLED"]
DecisionAction = Literal["ACCEPT", "REJECT", "REDUCE_RISK", "FALLBACK"]


@dataclass(frozen=True)
class MetaStrategyInferenceConfig:
    mode: InferenceMode = "OFF"
    fallbackBehavior: FallbackBehavior = "NO_TRADE"
    fallbackOnModelUnavailable: bool = True
    fallbackOnSchemaMismatch: bool = True
    minSuccessProbability: float = 0.52
    minCalibratedProbability: float = 0.52
    maxFeatureMissingness: float = 0.25
    maxOutOfDistributionScore: float = 0.70
    minModelHealthScore: float = 0.70
    maxArtifactAgeDays: int | None = None
    maxPredictionLatencyMs: int | None = None
    requireCalibratedModel: bool = False
    riskReductionMinMultiplier: float = 0.25
    riskReductionMaxMultiplier: float = 1.0
    configurationHash: str = "meta_strategy_inference_config_v1"


@dataclass(frozen=True)
class MetaStrategyInferenceResult:
    mode: InferenceMode
    effectiveMode: InferenceMode
    deterministicSignal: str
    finalSignal: str
    candidateAccepted: bool
    mlWouldAcceptCandidate: bool
    appliedToOrder: bool
    hardGatesPassed: bool
    deterministicRiskMultiplier: float
    recommendedRiskMultiplier: float
    decisionAction: DecisionAction = "REJECT"
    candidateSide: str = "HOLD"
    probabilityOfSuccess: float | None = None
    probabilityTargetFirst: float | None = None
    probabilityStopFirst: float | None = None
    probabilityTimeout: float | None = None
    successProbability: float | None = None
    calibratedProbability: float | None = None
    expectedValueAfterCosts: float | None = None
    uncertainty: float | None = None
    outOfDistributionScore: float | None = None
    featureMissingness: float = 0.0
    modelHealth: dict[str, Any] = field(default_factory=dict)
    auditTrail: dict[str, Any] = field(default_factory=dict)
    reasonCodes: tuple[str, ...] = ()
    predictedAt: datetime = field(default_factory=lambda: datetime.now(UTC))
    sessionDate: date | None = None
    configurationHash: str = "meta_strategy_inference_config_v1"


def apply_decision_policy(
    *,
    config: MetaStrategyInferenceConfig,
    deterministic_signal: str,
    hard_gates_passed: bool,
    candidate_eligible: bool,
    deterministic_risk_multiplier: float,
    success_probability: float | None,
    calibrated_probability: float | None,
    expected_value: float | None,
    feature_missingness: float,
    ood_score: float | None,
    model_health: dict[str, Any],
    reason_codes: list[str],
    predicted_at: datetime,
    session_date: date,
    candidate_conditional_output: Any | None = None,
) -> MetaStrategyInferenceResult:
    signal = normalize_signal(deterministic_signal)
    deterministic_risk = _bounded(deterministic_risk_multiplier)
    base_acceptance = signal in {"BUY", "SELL"} and hard_gates_passed and candidate_eligible
    if signal == "HOLD":
        reason_codes.append("meta_strategy.inference.cannot_create_trade_from_hold")
    if not hard_gates_passed:
        reason_codes.append("meta_strategy.inference.safety_gate_failed_no_bypass")
    if not candidate_eligible:
        reason_codes.append("meta_strategy.inference.candidate_ineligible")

    if config.mode in {"OFF", "DISABLED"}:
        final = signal if base_acceptance else "HOLD"
        action: DecisionAction = "ACCEPT" if base_acceptance else "REJECT"
        return _result(config, config.mode, signal, final, action, base_acceptance, False, False, hard_gates_passed, deterministic_risk, deterministic_risk if final != "HOLD" else 0.0, reason_codes + [f"meta_strategy.inference.mode_{config.mode.lower()}"], predicted_at, session_date, feature_missingness, model_health, candidate_conditional_output)

    if config.mode == "FALLBACK":
        final = fallback_signal(signal, hard_gates_passed=hard_gates_passed, candidate_eligible=candidate_eligible, behavior=config.fallbackBehavior)
        return _result(config, "FALLBACK", signal, final, "FALLBACK", final != "HOLD", False, False, hard_gates_passed, deterministic_risk, fallback_risk(deterministic_risk, behavior=config.fallbackBehavior, final_signal=final), reason_codes + ["meta_strategy.inference.fallback"], predicted_at, session_date, feature_missingness, model_health, candidate_conditional_output, success_probability, calibrated_probability, expected_value, ood_score)

    operationally_ok = float(model_health.get("score", 0.0)) >= config.minModelHealthScore and feature_missingness <= config.maxFeatureMissingness and (ood_score or 0.0) <= config.maxOutOfDistributionScore
    candidate_ok = base_acceptance and (success_probability or 0.0) >= config.minSuccessProbability and (calibrated_probability or 0.0) >= config.minCalibratedProbability
    if not operationally_ok:
        if feature_missingness > config.maxFeatureMissingness:
            reason_codes.append("meta_strategy.inference.feature_missingness_too_high")
        if (ood_score or 0.0) > config.maxOutOfDistributionScore:
            reason_codes.append("meta_strategy.inference.out_of_distribution")
        if float(model_health.get("score", 0.0)) < config.minModelHealthScore:
            reason_codes.append("meta_strategy.inference.model_health_too_low")
        final = fallback_signal(signal, hard_gates_passed=hard_gates_passed, candidate_eligible=candidate_eligible, behavior=config.fallbackBehavior if config.fallbackOnSchemaMismatch else "NO_TRADE")
        return _result(config, "FALLBACK", signal, final, "FALLBACK", final != "HOLD", False, False, hard_gates_passed, deterministic_risk, fallback_risk(deterministic_risk, behavior=config.fallbackBehavior, final_signal=final), reason_codes + ["meta_strategy.inference.operational_fallback"], predicted_at, session_date, feature_missingness, model_health, candidate_conditional_output, success_probability, calibrated_probability, expected_value, ood_score)

    if not candidate_ok:
        reason_codes.append("meta_strategy.inference.current_candidate_probability_below_threshold")
    if config.mode == "SHADOW":
        final = signal if base_acceptance else "HOLD"
        action = "ACCEPT" if base_acceptance else "REJECT"
        return _result(config, "SHADOW", signal, final, action, base_acceptance, candidate_ok, False, hard_gates_passed, deterministic_risk, deterministic_risk if final != "HOLD" else 0.0, reason_codes + ["meta_strategy.inference.shadow_record_only"], predicted_at, session_date, feature_missingness, model_health, candidate_conditional_output, success_probability, calibrated_probability, expected_value, ood_score)

    final = signal if candidate_ok else "HOLD"
    accepted = final != "HOLD"
    risk = deterministic_risk
    if config.mode == "RISK_REDUCTION" and accepted:
        risk = min(deterministic_risk, bounded_risk_reduction_cap(success_probability or 0.0, expected_value or 0.0, config))
    elif config.mode == "FILTER":
        risk = deterministic_risk if accepted else 0.0
    action = "REDUCE_RISK" if config.mode == "RISK_REDUCTION" and accepted and risk < deterministic_risk else "ACCEPT" if accepted else "REJECT"
    return _result(config, config.mode, signal, final, action, accepted, candidate_ok, True, hard_gates_passed, deterministic_risk, risk if accepted else 0.0, reason_codes + [f"meta_strategy.inference.mode_{config.mode.lower()}"], predicted_at, session_date, feature_missingness, model_health, candidate_conditional_output, success_probability, calibrated_probability, expected_value, ood_score)


def bounded_risk_reduction_cap(success_probability: float, expected_value: float, config: MetaStrategyInferenceConfig) -> float:
    if expected_value <= 0:
        return _bounded(config.riskReductionMinMultiplier)
    raw = config.riskReductionMinMultiplier + ((config.riskReductionMaxMultiplier - config.riskReductionMinMultiplier) * _bounded(success_probability))
    return min(_bounded(config.riskReductionMaxMultiplier), max(_bounded(config.riskReductionMinMultiplier), raw))


def normalize_signal(value: str) -> str:
    text = str(value).upper()
    return text if text in {"BUY", "SELL", "HOLD"} else "HOLD"


def _result(
    config: MetaStrategyInferenceConfig,
    effective_mode: InferenceMode,
    deterministic_signal: str,
    final_signal: str,
    decision_action: DecisionAction,
    candidate_accepted: bool,
    ml_would_accept: bool,
    applied_to_order: bool,
    hard_gates_passed: bool,
    deterministic_risk: float,
    recommended_risk: float,
    reason_codes: list[str],
    predicted_at: datetime,
    session_date: date,
    feature_missingness: float,
    model_health: dict[str, Any],
    candidate_conditional_output: Any | None = None,
    success_probability: float | None = None,
    calibrated_probability: float | None = None,
    expected_value: float | None = None,
    ood_score: float | None = None,
) -> MetaStrategyInferenceResult:
    conditional = candidate_conditional_output
    probability_of_success = getattr(conditional, "probability_of_success", success_probability)
    probability_target_first = getattr(conditional, "probability_target_first", success_probability)
    probability_stop_first = getattr(conditional, "probability_stop_first", None)
    probability_timeout = getattr(conditional, "probability_timeout", None)
    audit_trail = {
        "candidateDirectionSource": "deterministic_candidate",
        "modelOutputType": "candidate_conditional",
        "decisionAction": decision_action,
        "finalSignalDerivedFrom": "decision_policy",
        "oppositeDirectionPredictionsRejectOnly": True,
        "candidateConditionalOutput": {
            "candidate_side": getattr(conditional, "candidate_side", deterministic_signal),
            "probability_of_success": probability_of_success,
            "probability_target_first": probability_target_first,
            "probability_stop_first": probability_stop_first,
            "probability_timeout": probability_timeout,
            "uncertainty": getattr(conditional, "uncertainty", None),
            "out_of_distribution_score": getattr(conditional, "out_of_distribution_score", ood_score),
        },
        "runtimeHealth": model_health.get("runtimeHealth"),
    }
    return MetaStrategyInferenceResult(
        mode=config.mode,
        effectiveMode=effective_mode,
        deterministicSignal=deterministic_signal,
        finalSignal=final_signal,
        candidateAccepted=bool(candidate_accepted),
        mlWouldAcceptCandidate=bool(ml_would_accept),
        appliedToOrder=bool(applied_to_order),
        hardGatesPassed=hard_gates_passed,
        deterministicRiskMultiplier=_bounded(deterministic_risk),
        recommendedRiskMultiplier=min(_bounded(deterministic_risk), _bounded(recommended_risk)),
        decisionAction=decision_action,
        candidateSide=deterministic_signal,
        probabilityOfSuccess=_bounded_optional(probability_of_success),
        probabilityTargetFirst=_bounded_optional(probability_target_first),
        probabilityStopFirst=_bounded_optional(probability_stop_first),
        probabilityTimeout=_bounded_optional(probability_timeout),
        successProbability=_bounded_optional(success_probability),
        calibratedProbability=_bounded_optional(calibrated_probability),
        expectedValueAfterCosts=expected_value,
        uncertainty=None if success_probability is None else _bounded(1.0 - abs(float(success_probability) - 0.5) * 2.0),
        outOfDistributionScore=_bounded_optional(ood_score),
        featureMissingness=_bounded(feature_missingness),
        modelHealth=model_health,
        auditTrail=audit_trail,
        reasonCodes=tuple(sorted(set(reason_codes))),
        predictedAt=predicted_at,
        sessionDate=session_date,
        configurationHash=config.configurationHash,
    )


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _bounded_optional(value: float | None) -> float | None:
    return None if value is None else _bounded(value)


__all__ = [
    "InferenceMode",
    "DecisionAction",
    "MetaStrategyInferenceConfig",
    "MetaStrategyInferenceResult",
    "apply_decision_policy",
    "bounded_risk_reduction_cap",
    "normalize_signal",
]
