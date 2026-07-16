from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from backend.app.config import ApplicationConfig, FeatureFlags
from backend.app.domain.models import DomainModel, OperatingMode, Signal, _require_utc

from .deterministic_activation import DETERMINISTIC_V2_BASELINE_VERSION
from .event_replay import ReplayDecisionSnapshot


ML_FILTER_ROLLOUT_VERSION = "ml_filter_paper_rollout_v1"
MLFilterStage = Literal["SHADOW", "FILTER_ACTIVE"]


class MLFilterRolloutConfig(DomainModel):
    rolloutVersion: str = ML_FILTER_ROLLOUT_VERSION
    stage: MLFilterStage = "SHADOW"
    strategyEngineV2Enabled: bool = True
    familyEnsembleV2Enabled: bool = True
    globalGateEngineEnabled: bool = True
    metaModelV2Enabled: bool = True
    metaModelV2Mode: Literal["SHADOW", "FILTER"]
    staticRiskSizingEnabled: bool = True
    dynamicRiskSizingEnabled: bool = False
    mlMayAlterDirection: bool = False
    mlMayCreateTrade: bool = False
    mlMayAffectSizing: bool = False
    shadowComparisonPassed: bool = False
    fallbackBehavior: Literal["DETERMINISTIC_BASELINE", "NO_TRADE"] = "DETERMINISTIC_BASELINE"
    configurationHash: str

    @model_validator(mode="after")
    def enforce_rollout_posture(self) -> "MLFilterRolloutConfig":
        if not self.strategyEngineV2Enabled or not self.familyEnsembleV2Enabled or not self.globalGateEngineEnabled:
            raise ValueError("ML filter rollout requires deterministic V2 strategies, family ensemble, and global gates")
        if not self.metaModelV2Enabled:
            raise ValueError("ML filter rollout requires Meta-Model V2 to be enabled")
        if self.stage == "SHADOW" and self.metaModelV2Mode != "SHADOW":
            raise ValueError("ML shadow rollout must use SHADOW mode")
        if self.stage == "FILTER_ACTIVE" and self.metaModelV2Mode != "FILTER":
            raise ValueError("paper-active ML filter rollout must use FILTER mode")
        if not self.staticRiskSizingEnabled or self.dynamicRiskSizingEnabled:
            raise ValueError("first active ML filter stage must keep static risk sizing")
        if self.mlMayAlterDirection or self.mlMayCreateTrade or self.mlMayAffectSizing:
            raise ValueError("ML filter may only accept or reject deterministic candidates")
        return self


class MLFilterOutcomeSample(DomainModel):
    snapshotId: str = Field(min_length=1)
    decisionTimestampUtc: datetime
    sessionDate: date
    deterministicSignal: Signal
    deterministicWouldTrade: bool
    mlWouldAcceptCandidate: bool
    realizedSuccess: bool | None = None
    realizedNetPnlAfterCosts: float | None = None
    deterministicMaxDrawdown: float = Field(default=0.0, ge=0.0)
    mlFilteredMaxDrawdown: float = Field(default=0.0, ge=0.0)
    deterministicExpectancy: float = 0.0
    mlFilteredExpectancy: float = 0.0
    calibratedProbability: float | None = Field(default=None, ge=0.0, le=1.0)
    regime: str = "unknown"

    @field_validator("decisionTimestampUtc")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class MLFilterShadowComparisonReport(DomainModel):
    version: str = ML_FILTER_ROLLOUT_VERSION
    generatedAt: datetime
    sampleCount: int = Field(ge=0)
    deterministicCandidateCount: int = Field(ge=0)
    acceptedCount: int = Field(ge=0)
    rejectedCount: int = Field(ge=0)
    falseRejectionCost: float = Field(ge=0.0)
    drawdownEffect: float
    expectancyEffect: float
    coverage: float = Field(ge=0.0, le=1.0)
    calibrationError: float | None = Field(default=None, ge=0.0, le=1.0)
    regimeStability: float = Field(ge=0.0, le=1.0)
    passed: bool
    reasonCodes: list[str]
    explanation: str

    @field_validator("generatedAt")
    @classmethod
    def generated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class StaticRiskSizingCheck(DomainModel):
    baselineRiskDollars: float = Field(ge=0.0)
    effectiveRiskDollars: float = Field(ge=0.0)
    baselineMaxQuantity: int = Field(ge=0)
    effectiveMaxQuantity: int = Field(ge=0)
    baselineMaxNotional: float = Field(ge=0.0)
    effectiveMaxNotional: float = Field(ge=0.0)
    unchanged: bool
    recommendedRiskCapIgnored: bool
    explanation: str

    @model_validator(mode="after")
    def static_risk_must_be_unchanged(self) -> "StaticRiskSizingCheck":
        if not self.unchanged:
            raise ValueError("ML filter rollout must keep static risk sizing unchanged")
        return self


class MLFilterFallbackCheck(DomainModel):
    exercised: bool
    fallbackBehavior: Literal["DETERMINISTIC_BASELINE", "NO_TRADE"]
    effectiveMode: str
    modelHealthStatus: str
    reasonCodes: list[str]
    explanation: str


class MLFilterRolloutReport(DomainModel):
    version: str = ML_FILTER_ROLLOUT_VERSION
    generatedAt: datetime
    symbol: str
    sessionDate: date
    stage: MLFilterStage
    rolloutConfig: MLFilterRolloutConfig
    snapshotId: str
    deterministicBaselineVersion: str = DETERMINISTIC_V2_BASELINE_VERSION
    deterministicDecisionSnapshot: dict[str, Any]
    mlFilteredDecisionSnapshot: dict[str, Any]
    deterministicSignal: Signal
    finalSignal: Signal
    deterministicCandidateWouldTrade: bool
    mlCandidateAccepted: bool
    mlAppliedToOrder: bool
    globalGateDecision: dict[str, Any]
    orderPlan: dict[str, Any] | None
    staticRiskSizing: StaticRiskSizingCheck
    fallback: MLFilterFallbackCheck
    shadowComparison: MLFilterShadowComparisonReport | None = None
    automaticPaperEntryAllowed: bool
    submittedPaperOrder: bool = False
    submissionSeparated: bool = True
    reasonCodes: list[str]
    explanation: str

    @field_validator("generatedAt")
    @classmethod
    def generated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def enforce_ml_filter_guardrails(self) -> "MLFilterRolloutReport":
        deterministic = signal_value(self.deterministicSignal)
        final = signal_value(self.finalSignal)
        if deterministic == Signal.HOLD.value and final != Signal.HOLD.value:
            raise ValueError("ML filter cannot create a trade from Hold")
        if deterministic in {Signal.BUY.value, Signal.SELL.value} and final not in {deterministic, Signal.HOLD.value}:
            raise ValueError("ML filter cannot alter candidate direction")
        if self.stage == "SHADOW":
            if self.mlAppliedToOrder:
                raise ValueError("shadow ML filter mode cannot apply to paper orders")
            if self.automaticPaperEntryAllowed:
                raise ValueError("shadow ML filter mode cannot allow automatic paper entries")
        if self.stage == "FILTER_ACTIVE" and self.automaticPaperEntryAllowed and not self.rolloutConfig.shadowComparisonPassed:
            raise ValueError("paper-active ML filter requires a passing shadow comparison")
        if self.submittedPaperOrder:
            if not self.submissionSeparated:
                raise ValueError("ML filter evaluation must keep submission as a separate action")
            if not self.globalGateDecision.get("eligible"):
                raise ValueError("submitted paper orders must pass global gates")
            if not (self.orderPlan and self.orderPlan.get("eligible")):
                raise ValueError("submitted paper orders require an eligible order plan")
        return self


def ml_filter_rollout_application_config(*, stage: MLFilterStage = "SHADOW") -> ApplicationConfig:
    return ApplicationConfig(
        version=f"application-config-v1-ml-filter-{stage.lower()}",
        featureFlags=FeatureFlags(
            strategyEngineV2Enabled=True,
            familyEnsembleV2Enabled=True,
            metaModelV2Enabled=True,
            dynamicTradingPolicyEnabled=False,
            globalGateEngineEnabled=True,
        ),
    )


def ml_filter_rollout_config(
    *,
    stage: MLFilterStage = "SHADOW",
    shadow_comparison_passed: bool = False,
    fallback_behavior: Literal["DETERMINISTIC_BASELINE", "NO_TRADE"] = "DETERMINISTIC_BASELINE",
) -> MLFilterRolloutConfig:
    payload = {
        "applicationConfig": ml_filter_rollout_application_config(stage=stage).as_dict(),
        "stage": stage,
        "shadowComparisonPassed": shadow_comparison_passed,
        "fallbackBehavior": fallback_behavior,
    }
    return MLFilterRolloutConfig(
        stage=stage,
        metaModelV2Mode="SHADOW" if stage == "SHADOW" else "FILTER",
        shadowComparisonPassed=shadow_comparison_passed,
        fallbackBehavior=fallback_behavior,
        configurationHash=_hash_payload(payload),
    )


def build_ml_filter_shadow_comparison_report(
    *,
    samples: list[MLFilterOutcomeSample | dict[str, Any]],
    minimum_samples: int = 30,
    maximum_false_rejection_cost: float = 0.0,
    minimum_coverage: float = 0.50,
    maximum_calibration_error: float = 0.15,
    maximum_regime_instability: float = 0.35,
    generatedAt: datetime | None = None,
) -> MLFilterShadowComparisonReport:
    rows = [sample if isinstance(sample, MLFilterOutcomeSample) else MLFilterOutcomeSample(**sample) for sample in samples]
    candidates = [row for row in rows if row.deterministicWouldTrade and signal_value(row.deterministicSignal) != Signal.HOLD.value]
    accepted = [row for row in candidates if row.mlWouldAcceptCandidate]
    rejected = [row for row in candidates if not row.mlWouldAcceptCandidate]
    false_rejection_cost = sum(max(0.0, float(row.realizedNetPnlAfterCosts or 0.0)) for row in rejected if row.realizedSuccess is True)
    drawdown_effect = _mean([row.mlFilteredMaxDrawdown - row.deterministicMaxDrawdown for row in candidates])
    expectancy_effect = _mean([row.mlFilteredExpectancy - row.deterministicExpectancy for row in candidates])
    coverage = len(accepted) / len(candidates) if candidates else 0.0
    calibration_rows = [row for row in candidates if row.calibratedProbability is not None and row.realizedSuccess is not None]
    calibration_error = (
        _mean([abs(float(row.calibratedProbability or 0.0) - (1.0 if row.realizedSuccess else 0.0)) for row in calibration_rows])
        if calibration_rows
        else None
    )
    regime_stability = _regime_acceptance_instability(candidates)
    reason_codes: list[str] = []
    if len(candidates) < minimum_samples:
        reason_codes.append("ml_filter.shadow_insufficient_samples")
    if false_rejection_cost > maximum_false_rejection_cost:
        reason_codes.append("ml_filter.shadow_false_rejection_cost_too_high")
    if coverage < minimum_coverage:
        reason_codes.append("ml_filter.shadow_coverage_too_low")
    if calibration_error is not None and calibration_error > maximum_calibration_error:
        reason_codes.append("ml_filter.shadow_calibration_error_too_high")
    if regime_stability > maximum_regime_instability:
        reason_codes.append("ml_filter.shadow_regime_instability_too_high")
    if not reason_codes:
        reason_codes.append("ml_filter.shadow_comparison_passed")
    return MLFilterShadowComparisonReport(
        generatedAt=generatedAt or datetime.now(UTC),
        sampleCount=len(rows),
        deterministicCandidateCount=len(candidates),
        acceptedCount=len(accepted),
        rejectedCount=len(rejected),
        falseRejectionCost=round(false_rejection_cost, 6),
        drawdownEffect=round(drawdown_effect, 6),
        expectancyEffect=round(expectancy_effect, 6),
        coverage=round(coverage, 6),
        calibrationError=round(calibration_error, 6) if calibration_error is not None else None,
        regimeStability=round(regime_stability, 6),
        passed=reason_codes == ["ml_filter.shadow_comparison_passed"],
        reasonCodes=reason_codes,
        explanation=(
            "ML filter shadow comparison measured accepted/rejected candidates, false rejection cost, "
            "drawdown and expectancy effects, coverage, calibration, and regime stability."
        ),
    )


def build_ml_filter_rollout_report(
    *,
    snapshot: ReplayDecisionSnapshot | dict[str, Any],
    deterministicBaselineSnapshot: ReplayDecisionSnapshot | dict[str, Any] | None = None,
    stage: MLFilterStage = "SHADOW",
    shadowComparisonPassed: bool = False,
    fallbackBehavior: Literal["DETERMINISTIC_BASELINE", "NO_TRADE"] = "DETERMINISTIC_BASELINE",
    shadowComparison: MLFilterShadowComparisonReport | dict[str, Any] | None = None,
    generatedAt: datetime | None = None,
) -> MLFilterRolloutReport:
    filtered = snapshot if isinstance(snapshot, ReplayDecisionSnapshot) else ReplayDecisionSnapshot(**snapshot)
    baseline_source = deterministicBaselineSnapshot or filtered
    baseline = baseline_source if isinstance(baseline_source, ReplayDecisionSnapshot) else ReplayDecisionSnapshot(**baseline_source)
    generated = generatedAt or datetime.now(UTC)
    config = ml_filter_rollout_config(
        stage=stage,
        shadow_comparison_passed=shadowComparisonPassed,
        fallback_behavior=fallbackBehavior,
    )
    ml = filtered.mlInference
    deterministic_signal = Signal(str(ml.get("deterministicSignal") or filtered.ensembleDecision.get("signal") or Signal.HOLD.value))
    final_signal = Signal(str(ml.get("finalSignal") or Signal.HOLD.value))
    order_eligible = bool(filtered.orderPlan and filtered.orderPlan.get("eligible") and filtered.orderPlan.get("orderType") != "NO_ORDER")
    gate_eligible = bool(filtered.gateDecision.get("eligible"))
    automatic_allowed = bool(stage == "FILTER_ACTIVE" and shadowComparisonPassed and gate_eligible and order_eligible and ml.get("candidateAccepted"))
    reason_codes = _rollout_reason_codes(stage, ml, automatic_allowed, shadowComparisonPassed)
    comparison = (
        shadowComparison
        if shadowComparison is None or isinstance(shadowComparison, MLFilterShadowComparisonReport)
        else MLFilterShadowComparisonReport(**shadowComparison)
    )
    return MLFilterRolloutReport(
        generatedAt=generated,
        symbol=filtered.symbol,
        sessionDate=filtered.sessionDate,
        stage=stage,
        rolloutConfig=config,
        snapshotId=filtered.snapshotId,
        deterministicDecisionSnapshot=_snapshot_payload(baseline, "DETERMINISTIC_V2_STATIC_BASELINE"),
        mlFilteredDecisionSnapshot=_snapshot_payload(filtered, f"ML_FILTER_{stage}"),
        deterministicSignal=deterministic_signal,
        finalSignal=final_signal,
        deterministicCandidateWouldTrade=signal_value(deterministic_signal) != Signal.HOLD.value and baseline.deterministicCandidate is not None,
        mlCandidateAccepted=bool(ml.get("candidateAccepted")),
        mlAppliedToOrder=bool(ml.get("appliedToOrder")),
        globalGateDecision=filtered.gateDecision,
        orderPlan=filtered.orderPlan,
        staticRiskSizing=_static_risk_check(baseline.effectivePolicy, filtered.effectivePolicy, ml),
        fallback=_fallback_check(ml, fallbackBehavior),
        shadowComparison=comparison,
        automaticPaperEntryAllowed=automatic_allowed,
        submittedPaperOrder=False,
        reasonCodes=reason_codes,
        explanation=(
            "ML filter rollout evaluated the Meta-Model V2 as a candidate filter. "
            "Shadow mode records accept/reject decisions only; filter-active mode may only reject or accept "
            "the deterministic paper candidate while static risk sizing remains unchanged."
        ),
    )


def _rollout_reason_codes(stage: MLFilterStage, ml: dict[str, Any], automatic_allowed: bool, shadow_passed: bool) -> list[str]:
    codes = set(str(code) for code in ml.get("reasonCodes", []) if isinstance(ml.get("reasonCodes"), list))
    if stage == "SHADOW":
        codes.add("ml_filter.shadow_record_only")
    else:
        codes.add("ml_filter.filter_active_accept_reject_only")
        if not shadow_passed:
            codes.add("ml_filter.shadow_comparison_required_before_active_entries")
    if ml.get("candidateAccepted"):
        codes.add("ml_filter.candidate_accepted")
    else:
        codes.add("ml_filter.candidate_rejected_or_unavailable")
    if str(ml.get("effectiveMode")) == OperatingMode.FALLBACK.value:
        codes.add("ml_filter.fallback_explicit")
    if automatic_allowed:
        codes.add("ml_filter.paper_entry_allowed_after_filter_and_gates")
    else:
        codes.add("ml_filter.paper_entry_not_allowed")
    codes.add("ml_filter.static_risk_sizing_preserved")
    return sorted(codes)


def _static_risk_check(baseline_policy: dict[str, Any], effective_policy: dict[str, Any], ml: dict[str, Any]) -> StaticRiskSizingCheck:
    baseline_risk = float(baseline_policy.get("riskDollars") or 0.0)
    effective_risk = float(effective_policy.get("riskDollars") or 0.0)
    baseline_qty = int(baseline_policy.get("maxQuantity") or 0)
    effective_qty = int(effective_policy.get("maxQuantity") or 0)
    baseline_notional = float(baseline_policy.get("maxNotional") or 0.0)
    effective_notional = float(effective_policy.get("maxNotional") or 0.0)
    unchanged = (
        abs(baseline_risk - effective_risk) <= 1e-9
        and baseline_qty == effective_qty
        and abs(baseline_notional - effective_notional) <= 1e-9
    )
    return StaticRiskSizingCheck(
        baselineRiskDollars=baseline_risk,
        effectiveRiskDollars=effective_risk,
        baselineMaxQuantity=baseline_qty,
        effectiveMaxQuantity=effective_qty,
        baselineMaxNotional=baseline_notional,
        effectiveMaxNotional=effective_notional,
        unchanged=unchanged,
        recommendedRiskCapIgnored=float(ml.get("recommendedRiskCap") or 1.0) == 1.0,
        explanation="Filter-stage ML accepts or rejects candidates only; static baseline risk, quantity, and notional caps are unchanged.",
    )


def _fallback_check(ml: dict[str, Any], fallback_behavior: str) -> MLFilterFallbackCheck:
    reason_codes = [str(code) for code in ml.get("reasonCodes", [])] if isinstance(ml.get("reasonCodes"), list) else []
    exercised = str(ml.get("effectiveMode")) == OperatingMode.FALLBACK.value or any("fallback" in code for code in reason_codes)
    return MLFilterFallbackCheck(
        exercised=exercised,
        fallbackBehavior=fallback_behavior,  # type: ignore[arg-type]
        effectiveMode=str(ml.get("effectiveMode") or ""),
        modelHealthStatus=str((ml.get("modelHealth") or {}).get("status") or "UNKNOWN"),
        reasonCodes=reason_codes,
        explanation="Fallback behavior is explicit when the model is unavailable, unhealthy, out-of-distribution, or schema-incompatible.",
    )


def _snapshot_payload(snapshot: ReplayDecisionSnapshot, stage: str) -> dict[str, Any]:
    payload = snapshot.model_dump(mode="json")
    payload["rolloutStage"] = stage
    payload["submittedPaperOrder"] = False
    payload["reasonCodes"] = sorted(set([*snapshot.reasonCodes, "ml_filter_rollout.submission_separated"]))
    return payload


def signal_value(signal: Signal | str) -> str:
    return signal.value if isinstance(signal, Signal) else str(signal)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _regime_acceptance_instability(samples: list[MLFilterOutcomeSample]) -> float:
    by_regime: dict[str, list[MLFilterOutcomeSample]] = {}
    for sample in samples:
        by_regime.setdefault(sample.regime or "unknown", []).append(sample)
    rates = [sum(1 for row in rows if row.mlWouldAcceptCandidate) / len(rows) for rows in by_regime.values() if rows]
    return max(rates) - min(rates) if len(rates) >= 2 else 0.0


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
