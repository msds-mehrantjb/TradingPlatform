from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any

from pydantic import Field, field_validator, model_validator

from backend.app.config import ApplicationConfig, FeatureFlags
from backend.app.domain.models import (
    AccountRiskState,
    BaselineTradingSettings,
    ContextSignal,
    Direction,
    DomainModel,
    DynamicPolicyBounds,
    HardRiskLimits,
    MetaModelPrediction,
    OperatingMode,
    RegimeState,
    Signal,
    TradeCandidate,
    _require_utc,
)
from backend.app.trading_policy import (
    DynamicPolicyInputs,
    DynamicTradingPolicyConfig,
    DynamicTradingPolicyDecision,
    DynamicTradingPolicyEngine,
)

from .deterministic_activation import DETERMINISTIC_V2_BASELINE_VERSION
from .event_replay import ReplayDecisionSnapshot


DYNAMIC_POLICY_SHADOW_VERSION = "deterministic_dynamic_policy_shadow_v1"
REQUIRED_DYNAMIC_CAPS = {
    "signalQualityCap",
    "familyAgreementCap",
    "regimeCap",
    "volatilityCap",
    "liquidityCap",
    "eventCap",
    "timeOfDayCap",
    "drawdownCap",
    "MLCap",
    "dataQualityCap",
    "dynamicBoundsCap",
}


class DynamicPolicyShadowConfig(DomainModel):
    shadowVersion: str = DYNAMIC_POLICY_SHADOW_VERSION
    strategyEngineV2Enabled: bool = True
    familyEnsembleV2Enabled: bool = True
    globalGateEngineEnabled: bool = True
    dynamicTradingPolicyEnabled: bool = True
    dynamicTradingPolicyMode: str = OperatingMode.SHADOW.value
    staticPaperExecutionEnabled: bool = True
    dynamicMaySubmitOrders: bool = False
    dynamicMayExceedBaselineRisk: bool = False
    configurationHash: str

    @model_validator(mode="after")
    def enforce_shadow_posture(self) -> "DynamicPolicyShadowConfig":
        if not self.strategyEngineV2Enabled or not self.familyEnsembleV2Enabled or not self.globalGateEngineEnabled:
            raise ValueError("dynamic policy shadow requires deterministic V2 and global gates")
        if not self.dynamicTradingPolicyEnabled or self.dynamicTradingPolicyMode != OperatingMode.SHADOW.value:
            raise ValueError("dynamic policy must be enabled in SHADOW mode")
        if not self.staticPaperExecutionEnabled or self.dynamicMaySubmitOrders:
            raise ValueError("paper execution must remain on static settings during dynamic policy shadow")
        if self.dynamicMayExceedBaselineRisk:
            raise ValueError("initial dynamic policy shadow cannot exceed baseline risk")
        return self


class StaticDynamicPolicyComparison(DomainModel):
    candidateId: str | None = None
    identicalCandidate: bool
    staticRiskDollars: float = Field(ge=0.0)
    dynamicApprovedRiskDollars: float = Field(ge=0.0)
    staticQuantity: int = Field(ge=0)
    dynamicQuantity: int = Field(ge=0)
    staticEntryType: str | None = None
    dynamicEntryType: str | None = None
    staticStop: float | None = Field(default=None, gt=0)
    dynamicStop: float | None = Field(default=None, gt=0)
    staticTarget: float | None = Field(default=None, gt=0)
    dynamicTarget: float | None = Field(default=None, gt=0)
    staticHoldingMinutes: int | None = Field(default=None, ge=0)
    dynamicHoldingMinutes: int = Field(ge=0)
    riskDeltaDollars: float
    quantityDelta: int
    entryTypeChanged: bool
    targetChanged: bool
    holdingTimeChanged: bool
    explanation: str


class DynamicPolicyShadowReport(DomainModel):
    version: str = DYNAMIC_POLICY_SHADOW_VERSION
    generatedAt: datetime
    symbol: str
    sessionDate: date
    snapshotId: str
    shadowConfig: DynamicPolicyShadowConfig
    deterministicBaselineVersion: str = DETERMINISTIC_V2_BASELINE_VERSION
    deterministicDecisionSnapshot: dict[str, Any]
    staticPolicy: dict[str, Any]
    staticOrderPlan: dict[str, Any] | None
    dynamicPolicy: dict[str, Any] | None
    comparison: StaticDynamicPolicyComparison
    dynamicSubmittedPaperOrder: bool = False
    staticPaperOrderPathUnchanged: bool = True
    replayableSideBySide: bool = True
    capBreakdownsComplete: bool
    hardLimitsRespected: bool
    baselineRiskNotExceeded: bool
    reasonCodes: list[str]
    explanation: str

    @field_validator("generatedAt")
    @classmethod
    def generated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def enforce_shadow_guardrails(self) -> "DynamicPolicyShadowReport":
        if self.dynamicSubmittedPaperOrder:
            raise ValueError("dynamic policy shadow cannot submit paper orders")
        if not self.staticPaperOrderPathUnchanged:
            raise ValueError("static paper order path must remain unchanged during dynamic policy shadow")
        if not self.capBreakdownsComplete:
            raise ValueError("dynamic policy shadow requires complete cap breakdowns")
        if not self.hardLimitsRespected:
            raise ValueError("dynamic policy shadow cannot exceed hard limits")
        if not self.baselineRiskNotExceeded:
            raise ValueError("initial dynamic policy shadow cannot exceed baseline risk")
        return self


def dynamic_policy_shadow_application_config() -> ApplicationConfig:
    return ApplicationConfig(
        version="application-config-v1-dynamic-policy-shadow",
        featureFlags=FeatureFlags(
            strategyEngineV2Enabled=True,
            familyEnsembleV2Enabled=True,
            metaModelV2Enabled=False,
            dynamicTradingPolicyEnabled=True,
            globalGateEngineEnabled=True,
        ),
    )


def dynamic_policy_shadow_config() -> DynamicPolicyShadowConfig:
    payload = dynamic_policy_shadow_application_config().as_dict()
    return DynamicPolicyShadowConfig(configurationHash=_hash_payload(payload))


def build_dynamic_policy_shadow_report(
    *,
    snapshot: ReplayDecisionSnapshot | dict[str, Any],
    generatedAt: datetime | None = None,
) -> DynamicPolicyShadowReport:
    static_snapshot = snapshot if isinstance(snapshot, ReplayDecisionSnapshot) else ReplayDecisionSnapshot(**snapshot)
    generated = generatedAt or datetime.now(UTC)
    config = dynamic_policy_shadow_config()
    dynamic_decision = _dynamic_policy_decision(static_snapshot)
    comparison = _comparison(static_snapshot, dynamic_decision)
    dynamic_payload = dynamic_decision.model_dump(mode="json") if dynamic_decision else None
    caps_complete = _caps_complete(dynamic_decision)
    hard_limits_respected = _hard_limits_respected(dynamic_decision)
    baseline_not_exceeded = comparison.dynamicApprovedRiskDollars <= comparison.staticRiskDollars + 1e-9
    reason_codes = [
        "dynamic_policy.shadow_calculated_not_submitted",
        "dynamic_policy.static_paper_execution_preserved",
        "dynamic_policy.side_by_side_replay_available",
    ]
    if dynamic_decision:
        reason_codes.extend(dynamic_decision.reasonCodes)
        reason_codes.append("dynamic_policy.cap_breakdown_complete" if caps_complete else "dynamic_policy.cap_breakdown_incomplete")
        reason_codes.append("dynamic_policy.hard_limits_respected" if hard_limits_respected else "dynamic_policy.hard_limits_exceeded")
        reason_codes.append("dynamic_policy.baseline_risk_not_exceeded" if baseline_not_exceeded else "dynamic_policy.baseline_risk_exceeded")
    else:
        reason_codes.append("dynamic_policy.no_deterministic_candidate")
    return DynamicPolicyShadowReport(
        generatedAt=generated,
        symbol=static_snapshot.symbol,
        sessionDate=static_snapshot.sessionDate,
        snapshotId=static_snapshot.snapshotId,
        shadowConfig=config,
        deterministicDecisionSnapshot=_snapshot_payload(static_snapshot),
        staticPolicy=static_snapshot.effectivePolicy,
        staticOrderPlan=static_snapshot.orderPlan,
        dynamicPolicy=dynamic_payload,
        comparison=comparison,
        capBreakdownsComplete=caps_complete,
        hardLimitsRespected=hard_limits_respected,
        baselineRiskNotExceeded=baseline_not_exceeded,
        reasonCodes=sorted(set(reason_codes)),
        explanation=(
            "Deterministic dynamic policy was calculated in shadow mode from the same candidate used by the static paper path. "
            "Static settings remain the execution source while dynamic risk cap, stop, quantity, entry type, target, and holding time are replayable side by side."
        ),
    )


def _dynamic_policy_decision(snapshot: ReplayDecisionSnapshot) -> DynamicTradingPolicyDecision | None:
    if not snapshot.deterministicCandidate:
        return None
    inputs = dynamic_policy_inputs_from_snapshot(snapshot)
    return DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE)).evaluate(inputs)


def dynamic_policy_inputs_from_snapshot(snapshot: ReplayDecisionSnapshot) -> DynamicPolicyInputs:
    if not snapshot.deterministicCandidate:
        raise ValueError("dynamic policy shadow requires a deterministic candidate")
    policy = snapshot.effectivePolicy
    return DynamicPolicyInputs(
        candidate=TradeCandidate.model_validate(snapshot.deterministicCandidate),
        regimeState=_regime_state(snapshot),
        contextSignals=[ContextSignal.model_validate(context) for context in snapshot.contextOutputs],
        metaModelPrediction=_meta_prediction(snapshot),
        accountRiskState=AccountRiskState.model_validate(policy["accountRiskState"]),
        baselineSettings=BaselineTradingSettings.model_validate(policy["baselineSettings"]),
        hardRiskLimits=HardRiskLimits.model_validate(policy["hardRiskLimits"]),
        dynamicBounds=DynamicPolicyBounds.model_validate(policy["dynamicBounds"]),
        evaluatedAt=snapshot.decisionTimestampUtc,
    )


def _regime_state(snapshot: ReplayDecisionSnapshot) -> RegimeState:
    if snapshot.regimeState:
        return RegimeState.model_validate(snapshot.regimeState)
    return RegimeState(
        regimeId="dynamic-policy-shadow-unknown-regime",
        label="unknown",
        direction=Direction.FLAT,
        volatility="NORMAL",
        confidence=0.0,
        evaluatedAt=snapshot.decisionTimestampUtc,
        sessionDate=snapshot.sessionDate,
        configurationHash="dynamic_policy_shadow_unknown_regime",
    )


def _meta_prediction(snapshot: ReplayDecisionSnapshot) -> MetaModelPrediction:
    ml = snapshot.mlInference
    deterministic_signal = Signal(str(ml.get("deterministicSignal") or snapshot.ensembleDecision.get("signal") or Signal.HOLD.value))
    probability = ml.get("calibratedProbability", ml.get("successProbability"))
    probability_value = float(probability) if probability is not None else None
    return MetaModelPrediction(
        modelId="safe_ml_inference_shadow",
        modelVersion=str(ml.get("configurationHash") or "safe_ml_inference_config_v1"),
        candidateSide=deterministic_signal if deterministic_signal != Signal.HOLD.value else None,
        probabilityCandidateSuccess=probability_value,
        probabilityTargetBeforeStop=probability_value,
        probabilityProfitableAfterCosts=probability_value,
        signal=deterministic_signal,
        probabilityBuy=probability_value if deterministic_signal == Signal.BUY.value and probability_value is not None else 0.0,
        probabilitySell=probability_value if deterministic_signal == Signal.SELL.value and probability_value is not None else 0.0,
        probabilityHold=1.0 - probability_value if probability_value is not None else 1.0,
        confidence=0.0 if probability_value is None else min(1.0, max(0.0, probability_value)),
        reliability=0.0 if str(ml.get("effectiveMode")) == OperatingMode.FALLBACK.value else 0.5,
        features={"source": "safe_ml_inference_result", "effectiveMode": ml.get("effectiveMode")},
        predictedAt=snapshot.decisionTimestampUtc,
        sessionDate=snapshot.sessionDate,
        configurationHash=str(ml.get("configurationHash") or "safe_ml_inference_config_v1"),
    )


def _comparison(
    snapshot: ReplayDecisionSnapshot,
    dynamic_decision: DynamicTradingPolicyDecision | None,
) -> StaticDynamicPolicyComparison:
    static_order = snapshot.orderPlan or {}
    static_risk = float(snapshot.effectivePolicy.get("riskDollars") or 0.0)
    static_quantity = int(static_order.get("quantity") or 0)
    if dynamic_decision is None:
        return StaticDynamicPolicyComparison(
            identicalCandidate=False,
            staticRiskDollars=static_risk,
            dynamicApprovedRiskDollars=0.0,
            staticQuantity=static_quantity,
            dynamicQuantity=0,
            staticEntryType=static_order.get("orderType"),
            dynamicEntryType=None,
            staticStop=static_order.get("stopPrice"),
            dynamicStop=None,
            staticTarget=static_order.get("targetPrice"),
            dynamicTarget=None,
            staticHoldingMinutes=static_order.get("maximumHoldingMinutes"),
            dynamicHoldingMinutes=0,
            riskDeltaDollars=round(-static_risk, 6),
            quantityDelta=-static_quantity,
            entryTypeChanged=bool(static_order.get("orderType")),
            targetChanged=bool(static_order.get("targetPrice")),
            holdingTimeChanged=bool(static_order.get("maximumHoldingMinutes")),
            explanation="No deterministic trade candidate was available, so dynamic policy shadow produced no trade plan.",
        )
    dynamic_entry_type = dynamic_decision.entryPlan.orderType if dynamic_decision.entryPlan else None
    static_target = static_order.get("targetPrice")
    dynamic_target = dynamic_decision.target
    static_holding = static_order.get("maximumHoldingMinutes")
    dynamic_holding = dynamic_decision.holdingPeriodMinutes
    risk_delta = dynamic_decision.approvedRiskDollars - static_risk
    quantity_delta = dynamic_decision.quantity - static_quantity
    candidate_id = snapshot.deterministicCandidate.get("candidateId") if snapshot.deterministicCandidate else None
    return StaticDynamicPolicyComparison(
        candidateId=candidate_id,
        identicalCandidate=bool(candidate_id and candidate_id == dynamic_decision.entryPlan.intent.split(":")[-1])
        if dynamic_decision.entryPlan and ":" in dynamic_decision.entryPlan.intent
        else bool(candidate_id),
        staticRiskDollars=round(static_risk, 6),
        dynamicApprovedRiskDollars=round(dynamic_decision.approvedRiskDollars, 6),
        staticQuantity=static_quantity,
        dynamicQuantity=dynamic_decision.quantity,
        staticEntryType=static_order.get("orderType"),
        dynamicEntryType=dynamic_entry_type,
        staticStop=static_order.get("stopPrice"),
        dynamicStop=dynamic_decision.stop,
        staticTarget=static_target,
        dynamicTarget=dynamic_target,
        staticHoldingMinutes=static_holding,
        dynamicHoldingMinutes=dynamic_holding,
        riskDeltaDollars=round(risk_delta, 6),
        quantityDelta=quantity_delta,
        entryTypeChanged=static_order.get("orderType") != dynamic_entry_type,
        targetChanged=_changed(static_target, dynamic_target),
        holdingTimeChanged=static_holding != dynamic_holding,
        explanation="Static and dynamic policy outcomes were calculated from the same deterministic candidate.",
    )


def _caps_complete(dynamic_decision: DynamicTradingPolicyDecision | None) -> bool:
    if dynamic_decision is None:
        return True
    names = {cap.capName for cap in dynamic_decision.capBreakdown.dynamicRiskCaps}
    share_names = {cap.capName for cap in dynamic_decision.capBreakdown.shareCaps}
    stop_names = {component.componentName for component in (dynamic_decision.capBreakdown.stopPlan.components if dynamic_decision.capBreakdown.stopPlan else [])}
    return (
        REQUIRED_DYNAMIC_CAPS.issubset(names)
        and bool(dynamic_decision.capBreakdown.limitingRiskCap)
        and {"riskBasedShares", "orderNotionalShares", "globalExposureShares"}.issubset(share_names)
        and {"atrVolatilityStop", "minimumPercentageStop", "spreadMicrostructureStop", "strategyStructuralInvalidationStop"}.issubset(stop_names)
    )


def _hard_limits_respected(dynamic_decision: DynamicTradingPolicyDecision | None) -> bool:
    if dynamic_decision is None:
        return True
    breakdown = dynamic_decision.capBreakdown
    risk_caps = [
        breakdown.dynamicRiskDollars,
        breakdown.hardRiskCapDollars,
        breakdown.dailyLossRemainingDollars,
        breakdown.openRiskCapDollars,
    ]
    notional_caps = [
        breakdown.orderNotionalCapDollars,
        breakdown.positionNotionalCapDollars,
        breakdown.dailyNotionalCapDollars,
        breakdown.buyingPowerCapDollars,
    ]
    share_caps = [breakdown.shareCap, *[cap.shares for cap in breakdown.shareCaps]]
    return (
        dynamic_decision.approvedRiskDollars <= min(risk_caps) + 1e-9
        and dynamic_decision.maximumNotional <= min(notional_caps) + 1e-9
        and dynamic_decision.quantity <= min(share_caps)
        and breakdown.plannedRiskDollars <= dynamic_decision.approvedRiskDollars + 1e-9
    )


def _snapshot_payload(snapshot: ReplayDecisionSnapshot) -> dict[str, Any]:
    payload = snapshot.model_dump(mode="json")
    payload["dynamicPolicyShadowVersion"] = DYNAMIC_POLICY_SHADOW_VERSION
    payload["dynamicPolicySubmittedPaperOrder"] = False
    payload["reasonCodes"] = sorted(set([*snapshot.reasonCodes, "dynamic_policy_shadow.static_execution_source"]))
    return payload


def _changed(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left != right
    try:
        return abs(float(left) - float(right)) > 1e-9
    except (TypeError, ValueError):
        return left != right


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
