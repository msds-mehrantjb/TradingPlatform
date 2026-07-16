from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any

from pydantic import Field, field_validator, model_validator

from backend.app.domain.models import DomainModel, OperatingMode, Signal, _require_utc

from .dynamic_policy_activation import (
    DynamicPolicyActivationReport,
    DynamicPolicyRollbackControls,
    DynamicPolicyStage,
    DynamicPolicyStageComparisonReport,
    build_dynamic_policy_activation_report,
)
from .event_replay import ReplayDecisionSnapshot


ML_RISK_MODIFIER_EXPERIMENT_VERSION = "ml_bounded_risk_modifier_experiment_v1"


class MLRiskModifierConfig(DomainModel):
    experimentVersion: str = ML_RISK_MODIFIER_EXPERIMENT_VERSION
    experimentEnabled: bool = False
    validatedForActivation: bool = False
    mlFilterStable: bool = False
    deterministicDynamicPolicyStable: bool = False
    mayAffectPaperOrders: bool = False
    deterministicPolicyFallbackRequired: bool = True
    maximumRiskMultiplier: float = Field(default=1.0, ge=0.0, le=1.0)
    missingPredictionFallbackMultiplier: float = Field(default=0.75, ge=0.0, le=1.0)
    minimumSuccessProbabilityForRisk: float = Field(default=0.52, ge=0.0, le=1.0)
    fullRiskExpectedValueAfterCosts: float = Field(default=1.0, gt=0.0)
    maximumUncertaintyForTrade: float = Field(default=0.85, ge=0.0, le=1.0)
    uncertaintyRiskReductionWeight: float = Field(default=0.50, ge=0.0, le=1.0)
    maximumOutOfDistributionScoreForTrade: float = Field(default=0.70, ge=0.0, le=1.0)
    oodRiskReductionWeight: float = Field(default=0.75, ge=0.0, le=1.0)
    maximumExpectedSlippageBpsForTrade: float = Field(default=15.0, ge=0.0)
    slippageRiskReductionWeight: float = Field(default=0.50, ge=0.0, le=1.0)
    allowStopModification: bool = False
    allowDirectionCreation: bool = False
    allowHardLimitOverride: bool = False
    allowLosingPositionIncrease: bool = False
    configurationHash: str = Field(default="ml_risk_modifier_disabled_default_v1", min_length=1)

    @model_validator(mode="after")
    def enforce_safe_defaults_and_activation_prereqs(self) -> "MLRiskModifierConfig":
        if self.experimentEnabled and (not self.mlFilterStable or not self.deterministicDynamicPolicyStable):
            raise ValueError("ML risk modifier experiment requires stable ML filter and deterministic dynamic policy")
        if self.mayAffectPaperOrders and not self.validatedForActivation:
            raise ValueError("ML risk modifier cannot affect paper orders until separately validated")
        if self.mayAffectPaperOrders:
            raise ValueError("ML risk modifier remains a separate experiment and is disabled for paper order effects")
        if not self.deterministicPolicyFallbackRequired:
            raise ValueError("deterministic dynamic policy fallback is required")
        if self.maximumRiskMultiplier > 1.0:
            raise ValueError("ML risk modifier cannot exceed baseline risk")
        if self.allowStopModification or self.allowDirectionCreation or self.allowHardLimitOverride or self.allowLosingPositionIncrease:
            raise ValueError("ML risk modifier may only apply a bounded additional risk cap")
        return self


class MLRiskModifierFactor(DomainModel):
    factorName: str = Field(min_length=1)
    multiplier: float = Field(ge=0.0, le=1.0)
    sourceValue: float | None = None
    reasonCodes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)


class MLRiskModifierDecision(DomainModel):
    experimentVersion: str = ML_RISK_MODIFIER_EXPERIMENT_VERSION
    evaluatedAt: datetime
    symbol: str
    sessionDate: date
    featureEnabled: bool
    appliedToPaperOrder: bool = False
    deterministicFallbackUsed: bool
    deterministicSignal: Signal
    finalSignal: Signal
    deterministicRiskDollars: float = Field(ge=0.0)
    baselineRiskDollars: float = Field(ge=0.0)
    hardRiskCapDollars: float = Field(ge=0.0)
    modifiedRiskDollars: float = Field(ge=0.0)
    riskReductionDollars: float = Field(ge=0.0)
    mlRiskMultiplier: float = Field(ge=0.0, le=1.0)
    limitingFactor: str | None = None
    noTradeRecommended: bool
    factors: list[MLRiskModifierFactor] = Field(default_factory=list)
    stopUnchanged: bool = True
    directionCreated: bool = False
    hardLimitsOverridden: bool = False
    losingPositionIncreased: bool = False
    reasonCodes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)
    configurationHash: str = Field(min_length=1)

    @field_validator("evaluatedAt")
    @classmethod
    def evaluated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def enforce_modifier_guardrails(self) -> "MLRiskModifierDecision":
        if self.appliedToPaperOrder:
            raise ValueError("ML risk modifier experiment cannot affect paper orders")
        if self.modifiedRiskDollars > self.deterministicRiskDollars + 1e-9:
            raise ValueError("ML risk modifier cannot exceed deterministic policy risk")
        if self.modifiedRiskDollars > self.baselineRiskDollars + 1e-9:
            raise ValueError("ML risk modifier cannot exceed baseline risk")
        if self.modifiedRiskDollars > self.hardRiskCapDollars + 1e-9:
            raise ValueError("ML risk modifier cannot override hard limits")
        if not self.stopUnchanged:
            raise ValueError("ML risk modifier cannot widen or modify stops")
        if self.directionCreated or (self.deterministicSignal == Signal.HOLD.value and self.finalSignal != Signal.HOLD.value):
            raise ValueError("ML risk modifier cannot create a direction")
        if self.hardLimitsOverridden:
            raise ValueError("ML risk modifier cannot override hard limits")
        if self.losingPositionIncreased:
            raise ValueError("ML risk modifier cannot increase a losing position")
        return self


class MLRiskModifierExperimentReport(DomainModel):
    version: str = ML_RISK_MODIFIER_EXPERIMENT_VERSION
    generatedAt: datetime
    symbol: str
    sessionDate: date
    snapshotId: str
    config: MLRiskModifierConfig
    deterministicActivationReport: dict[str, Any]
    mlRiskModifierDecision: MLRiskModifierDecision
    independentlyMeasurable: bool = True
    deterministicPolicyFallback: bool = True
    featureDisabledByDefault: bool
    submittedPaperOrder: bool = False
    reasonCodes: list[str]
    explanation: str

    @field_validator("generatedAt")
    @classmethod
    def generated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def enforce_experiment_separation(self) -> "MLRiskModifierExperimentReport":
        if self.submittedPaperOrder or self.mlRiskModifierDecision.appliedToPaperOrder:
            raise ValueError("ML risk modifier experiment cannot submit or alter paper orders")
        if not self.independentlyMeasurable:
            raise ValueError("ML risk modification must remain independently measurable")
        if not self.deterministicPolicyFallback:
            raise ValueError("deterministic dynamic policy must remain the fallback")
        return self


def ml_risk_modifier_config(
    *,
    experiment_enabled: bool = False,
    validated_for_activation: bool = False,
    ml_filter_stable: bool = False,
    deterministic_dynamic_policy_stable: bool = False,
) -> MLRiskModifierConfig:
    payload = {
        "experimentEnabled": experiment_enabled,
        "validatedForActivation": validated_for_activation,
        "mlFilterStable": ml_filter_stable,
        "deterministicDynamicPolicyStable": deterministic_dynamic_policy_stable,
    }
    return MLRiskModifierConfig(
        experimentEnabled=experiment_enabled,
        validatedForActivation=validated_for_activation,
        mlFilterStable=ml_filter_stable,
        deterministicDynamicPolicyStable=deterministic_dynamic_policy_stable,
        configurationHash=_hash_payload(payload),
    )


def build_ml_risk_modifier_experiment_report(
    *,
    snapshot: ReplayDecisionSnapshot | dict[str, Any],
    stageComparisons: list[DynamicPolicyStageComparisonReport | dict[str, Any]],
    requestedStages: list[DynamicPolicyStage] | None = None,
    dynamicPolicyRollback: DynamicPolicyRollbackControls | dict[str, Any] | None = None,
    config: MLRiskModifierConfig | dict[str, Any] | None = None,
    generatedAt: datetime | None = None,
) -> MLRiskModifierExperimentReport:
    v2_snapshot = snapshot if isinstance(snapshot, ReplayDecisionSnapshot) else ReplayDecisionSnapshot(**snapshot)
    resolved_config = config if isinstance(config, MLRiskModifierConfig) else MLRiskModifierConfig(**(config or {}))
    generated = generatedAt or datetime.now(UTC)
    activation = build_dynamic_policy_activation_report(
        snapshot=v2_snapshot,
        stageComparisons=stageComparisons,
        requestedStages=requestedStages,
        rollback=dynamicPolicyRollback,
        generatedAt=generated,
    )
    decision = build_ml_risk_modifier_decision(
        snapshot=v2_snapshot,
        activationReport=activation,
        config=resolved_config,
        evaluatedAt=generated,
    )
    reason_codes = [
        "ml_risk_modifier.independent_experiment",
        "ml_risk_modifier.deterministic_policy_fallback",
        "ml_risk_modifier.disabled_by_default" if not resolved_config.experimentEnabled else "ml_risk_modifier.experiment_enabled",
        *decision.reasonCodes,
    ]
    return MLRiskModifierExperimentReport(
        generatedAt=generated,
        symbol=v2_snapshot.symbol,
        sessionDate=v2_snapshot.sessionDate,
        snapshotId=v2_snapshot.snapshotId,
        config=resolved_config,
        deterministicActivationReport=activation.model_dump(mode="json"),
        mlRiskModifierDecision=decision,
        featureDisabledByDefault=not MLRiskModifierConfig().experimentEnabled,
        reasonCodes=sorted(set(reason_codes)),
        explanation=(
            "ML risk modification was evaluated as a separate bounded-cap experiment after the deterministic dynamic policy. "
            "It can only reduce risk or recommend no trade; deterministic dynamic policy remains the fallback."
        ),
    )


def build_ml_risk_modifier_decision(
    *,
    snapshot: ReplayDecisionSnapshot,
    activationReport: DynamicPolicyActivationReport,
    config: MLRiskModifierConfig,
    evaluatedAt: datetime,
) -> MLRiskModifierDecision:
    dynamic_policy = activationReport.dynamicPolicy or {}
    cap_breakdown = dynamic_policy.get("capBreakdown") or {}
    deterministic_risk = float(dynamic_policy.get("approvedRiskDollars") or 0.0)
    baseline_risk = float(cap_breakdown.get("baselineRiskDollars") or activationReport.staticPolicy.get("riskDollars") or deterministic_risk)
    hard_cap = float(cap_breakdown.get("hardRiskCapDollars") or baseline_risk)
    deterministic_signal = Signal(str(snapshot.mlInference.get("deterministicSignal") or snapshot.ensembleDecision.get("signal") or Signal.HOLD.value))
    final_signal = Signal(str(snapshot.mlInference.get("finalSignal") or deterministic_signal.value))
    if not config.experimentEnabled:
        return _fallback_decision(
            snapshot=snapshot,
            config=config,
            evaluated_at=evaluatedAt,
            deterministic_signal=deterministic_signal,
            final_signal=final_signal,
            deterministic_risk=deterministic_risk,
            baseline_risk=baseline_risk,
            hard_cap=hard_cap,
            reason="ml_risk_modifier.feature_disabled",
        )
    factors = _risk_modifier_factors(snapshot=snapshot, config=config)
    limiting = min(factors, key=lambda factor: (factor.multiplier, factor.factorName))
    bounded_multiplier = min(config.maximumRiskMultiplier, limiting.multiplier)
    modified_risk = max(0.0, min(deterministic_risk, baseline_risk, hard_cap, deterministic_risk * bounded_multiplier))
    no_trade = modified_risk <= 0.0 or bounded_multiplier <= 0.0
    return MLRiskModifierDecision(
        evaluatedAt=evaluatedAt,
        symbol=snapshot.symbol,
        sessionDate=snapshot.sessionDate,
        featureEnabled=True,
        deterministicFallbackUsed=False,
        deterministicSignal=deterministic_signal,
        finalSignal=Signal.HOLD if no_trade else final_signal,
        deterministicRiskDollars=round(deterministic_risk, 6),
        baselineRiskDollars=round(baseline_risk, 6),
        hardRiskCapDollars=round(hard_cap, 6),
        modifiedRiskDollars=round(modified_risk, 6),
        riskReductionDollars=round(max(0.0, deterministic_risk - modified_risk), 6),
        mlRiskMultiplier=round(bounded_multiplier, 6),
        limitingFactor=limiting.factorName,
        noTradeRecommended=no_trade,
        factors=factors,
        reasonCodes=sorted(set(["ml_risk_modifier.bounded_cap_evaluated", *[code for factor in factors for code in factor.reasonCodes]])),
        explanation="ML provided a bounded additional risk cap using probability, EV, uncertainty, OOD, and slippage; it did not alter direction, stop, or hard limits.",
        configurationHash=config.configurationHash,
    )


def _fallback_decision(
    *,
    snapshot: ReplayDecisionSnapshot,
    config: MLRiskModifierConfig,
    evaluated_at: datetime,
    deterministic_signal: Signal,
    final_signal: Signal,
    deterministic_risk: float,
    baseline_risk: float,
    hard_cap: float,
    reason: str,
) -> MLRiskModifierDecision:
    fallback_risk = min(deterministic_risk, baseline_risk, hard_cap)
    return MLRiskModifierDecision(
        evaluatedAt=evaluated_at,
        symbol=snapshot.symbol,
        sessionDate=snapshot.sessionDate,
        featureEnabled=False,
        deterministicFallbackUsed=True,
        deterministicSignal=deterministic_signal,
        finalSignal=final_signal if deterministic_signal != Signal.HOLD else Signal.HOLD,
        deterministicRiskDollars=round(deterministic_risk, 6),
        baselineRiskDollars=round(baseline_risk, 6),
        hardRiskCapDollars=round(hard_cap, 6),
        modifiedRiskDollars=round(fallback_risk, 6),
        riskReductionDollars=0.0,
        mlRiskMultiplier=1.0,
        limitingFactor="deterministicPolicyFallback",
        noTradeRecommended=deterministic_signal == Signal.HOLD,
        factors=[],
        reasonCodes=[reason, "ml_risk_modifier.deterministic_fallback_used"],
        explanation="ML risk modifier is disabled or unavailable; deterministic dynamic policy remains the fallback.",
        configurationHash=config.configurationHash,
    )


def _risk_modifier_factors(*, snapshot: ReplayDecisionSnapshot, config: MLRiskModifierConfig) -> list[MLRiskModifierFactor]:
    ml = snapshot.mlInference
    success_probability = _optional_float(ml.get("calibratedProbability", ml.get("successProbability")))
    expected_value = _optional_float(ml.get("expectedValueAfterCosts"))
    uncertainty = _optional_float(ml.get("uncertainty"))
    ood_score = _optional_float(ml.get("outOfDistributionScore"))
    slippage_bps = _expected_slippage_bps(snapshot)
    return [
        _success_probability_factor(success_probability, config),
        _expected_value_factor(expected_value, config),
        _uncertainty_factor(uncertainty, config),
        _ood_factor(ood_score, config),
        _slippage_factor(slippage_bps, config),
    ]


def _success_probability_factor(value: float | None, config: MLRiskModifierConfig) -> MLRiskModifierFactor:
    if value is None:
        return _factor("successProbabilityCap", config.missingPredictionFallbackMultiplier, None, "ml_risk_modifier.success_probability_missing", "Missing success probability uses fallback cap.")
    if value < config.minimumSuccessProbabilityForRisk:
        return _factor("successProbabilityCap", 0.0, value, "ml_risk_modifier.success_probability_below_minimum", "Low success probability recommends no trade.")
    return _factor("successProbabilityCap", min(1.0, value), value, "ml_risk_modifier.success_probability_cap", "Success probability can only cap risk at or below baseline.")


def _expected_value_factor(value: float | None, config: MLRiskModifierConfig) -> MLRiskModifierFactor:
    if value is None:
        return _factor("expectedValueCap", config.missingPredictionFallbackMultiplier, None, "ml_risk_modifier.expected_value_missing", "Missing expected value uses fallback cap.")
    if value <= 0:
        return _factor("expectedValueCap", 0.0, value, "ml_risk_modifier.expected_value_non_positive", "Non-positive expected value recommends no trade.")
    return _factor("expectedValueCap", min(1.0, value / config.fullRiskExpectedValueAfterCosts), value, "ml_risk_modifier.expected_value_cap", "Expected value scales only as a downward risk cap.")


def _uncertainty_factor(value: float | None, config: MLRiskModifierConfig) -> MLRiskModifierFactor:
    if value is None:
        return _factor("uncertaintyCap", config.missingPredictionFallbackMultiplier, None, "ml_risk_modifier.uncertainty_missing", "Missing uncertainty uses fallback cap.")
    if value > config.maximumUncertaintyForTrade:
        return _factor("uncertaintyCap", 0.0, value, "ml_risk_modifier.uncertainty_too_high", "Excess uncertainty recommends no trade.")
    multiplier = 1.0 - (value * config.uncertaintyRiskReductionWeight)
    return _factor("uncertaintyCap", multiplier, value, "ml_risk_modifier.uncertainty_reduces_risk", "Uncertainty can only reduce risk.")


def _ood_factor(value: float | None, config: MLRiskModifierConfig) -> MLRiskModifierFactor:
    if value is None:
        return _factor("outOfDistributionCap", config.missingPredictionFallbackMultiplier, None, "ml_risk_modifier.ood_missing", "Missing OOD score uses fallback cap.")
    if value > config.maximumOutOfDistributionScoreForTrade:
        return _factor("outOfDistributionCap", 0.0, value, "ml_risk_modifier.ood_too_high", "OOD prediction recommends no trade.")
    multiplier = 1.0 - (value * config.oodRiskReductionWeight)
    return _factor("outOfDistributionCap", multiplier, value, "ml_risk_modifier.ood_reduces_risk", "OOD score can only reduce risk.")


def _slippage_factor(value: float | None, config: MLRiskModifierConfig) -> MLRiskModifierFactor:
    if value is None:
        return _factor("expectedSlippageCap", 1.0, None, "ml_risk_modifier.slippage_not_supplied", "Missing slippage does not increase risk.")
    if value > config.maximumExpectedSlippageBpsForTrade:
        return _factor("expectedSlippageCap", 0.0, value, "ml_risk_modifier.slippage_too_high", "Excess expected slippage recommends no trade.")
    ratio = value / config.maximumExpectedSlippageBpsForTrade if config.maximumExpectedSlippageBpsForTrade > 0 else 1.0
    return _factor("expectedSlippageCap", 1.0 - (ratio * config.slippageRiskReductionWeight), value, "ml_risk_modifier.slippage_reduces_risk", "Expected slippage can only reduce risk.")


def _factor(name: str, multiplier: float, value: float | None, reason: str, explanation: str) -> MLRiskModifierFactor:
    bounded = max(0.0, min(1.0, multiplier))
    return MLRiskModifierFactor(
        factorName=name,
        multiplier=round(bounded, 6),
        sourceValue=value,
        reasonCodes=[reason],
        explanation=explanation,
    )


def _expected_slippage_bps(snapshot: ReplayDecisionSnapshot) -> float | None:
    candidate = snapshot.deterministicCandidate or {}
    features = candidate.get("features") if isinstance(candidate, dict) else {}
    if not isinstance(features, dict):
        return None
    for key in ("expectedSlippageBps", "estimatedSlippageBps", "slippageBps"):
        value = _optional_float(features.get(key))
        if value is not None:
            return value
    return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric == numeric else None


def _hash_payload(payload: Any) -> str:
    serialized = json.dumps(_jsonable(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value
