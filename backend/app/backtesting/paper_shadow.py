from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from backend.app.config import ApplicationConfig, FeatureFlags
from backend.app.domain.models import DomainModel, Signal, _require_utc

from .event_replay import ReplayDecisionSnapshot


PAPER_SHADOW_VERSION = "deterministic_v2_paper_shadow_v1"


class PaperShadowModeConfig(DomainModel):
    strategyEngineV2Enabled: bool = True
    familyEnsembleV2Enabled: bool = True
    globalGateEngineEnabled: bool = True
    metaModelV2Enabled: bool = False
    dynamicTradingPolicyEnabled: bool = False
    policyMode: Literal["STATIC_BASELINE"] = "STATIC_BASELINE"
    mlMode: Literal["OFF"] = "OFF"
    paperOrderSubmissionEnabled: bool = False
    configurationVersion: str = "paper_shadow_mode_v1"
    configurationHash: str

    @model_validator(mode="after")
    def enforce_shadow_only(self) -> "PaperShadowModeConfig":
        if not self.strategyEngineV2Enabled or not self.familyEnsembleV2Enabled or not self.globalGateEngineEnabled:
            raise ValueError("paper-shadow mode requires strategy, family ensemble, and global gate engines")
        if self.metaModelV2Enabled or self.dynamicTradingPolicyEnabled:
            raise ValueError("paper-shadow mode cannot allow ML or dynamic settings to affect orders")
        if self.paperOrderSubmissionEnabled:
            raise ValueError("paper-shadow mode cannot submit paper orders automatically")
        return self


class CurrentBaselineDecision(DomainModel):
    baselineVersion: str = Field(default="current_baseline_v1", min_length=1)
    decisionTimestampUtc: datetime
    signal: Signal
    wouldTrade: bool
    orderQuantity: int | None = Field(default=None, ge=0)
    expectedNotional: float | None = Field(default=None, ge=0)
    rawDecision: dict[str, Any] = Field(default_factory=dict)
    explanation: str = Field(min_length=1)

    @field_validator("decisionTimestampUtc")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class PaperShadowInputFreshness(DomainModel):
    fresh: bool
    reproducible: bool
    maxInputTimestampUtc: datetime | None
    decisionTimestampUtc: datetime
    featureSnapshotHash: str
    reasonCodes: list[str]
    explanation: str

    @field_validator("maxInputTimestampUtc", "decisionTimestampUtc")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_utc(value)


class PaperShadowOperationalProblem(DomainModel):
    problemId: str = Field(min_length=1)
    severity: Literal["INFO", "CAUTION", "BLOCKER"]
    source: str = Field(min_length=1)
    mustResolveBeforeActivation: bool
    reasonCodes: list[str]
    explanation: str = Field(min_length=1)


class PaperShadowBaselineComparison(DomainModel):
    baselineSignal: Signal
    v2Signal: Signal
    signalChanged: bool
    baselineWouldTrade: bool
    v2WouldPlanOrder: bool
    orderQuantityDelta: int | None
    notionalDelta: float | None
    explanation: str


class PaperShadowReport(DomainModel):
    version: str = PAPER_SHADOW_VERSION
    generatedAt: datetime
    symbol: str
    sessionDate: date
    mode: PaperShadowModeConfig
    snapshotId: str
    v2DecisionSnapshot: dict[str, Any]
    baselineDecision: CurrentBaselineDecision
    comparison: PaperShadowBaselineComparison
    inputFreshness: PaperShadowInputFreshness
    gateDecision: dict[str, Any]
    effectivePolicy: dict[str, Any]
    wouldHaveOrderPlan: dict[str, Any] | None
    automaticPaperSubmission: bool = False
    operationalProblems: list[PaperShadowOperationalProblem]
    activationReady: bool
    reasonCodes: list[str]
    explanation: str

    @field_validator("generatedAt")
    @classmethod
    def generated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def enforce_no_submission_and_no_ml_or_dynamic_policy(self) -> "PaperShadowReport":
        ml = self.v2DecisionSnapshot.get("mlInference") or {}
        policy = self.effectivePolicy or {}
        if self.automaticPaperSubmission:
            raise ValueError("paper-shadow report cannot submit paper orders automatically")
        if ml.get("appliedToOrder"):
            raise ValueError("paper-shadow report cannot allow ML to affect paper orders")
        if str(ml.get("effectiveMode") or ml.get("mode")) != "OFF":
            raise ValueError("paper-shadow report requires ML OFF")
        if str(policy.get("mode")) not in {"OFF", "SHADOW", ""}:
            raise ValueError("paper-shadow report requires static baseline policy without dynamic activation")
        if self.activationReady and any(problem.mustResolveBeforeActivation for problem in self.operationalProblems):
            raise ValueError("paper-shadow report cannot be activation-ready with unresolved operational blockers")
        return self


def paper_shadow_application_config() -> ApplicationConfig:
    return ApplicationConfig(
        version="application-config-v1-paper-shadow",
        featureFlags=FeatureFlags(
            strategyEngineV2Enabled=True,
            familyEnsembleV2Enabled=True,
            metaModelV2Enabled=False,
            dynamicTradingPolicyEnabled=False,
            globalGateEngineEnabled=True,
        ),
    )


def paper_shadow_mode_config() -> PaperShadowModeConfig:
    payload = paper_shadow_application_config().as_dict()
    return PaperShadowModeConfig(configurationHash=str(payload["configurationHash"]))


def build_paper_shadow_report(
    *,
    v2Snapshot: ReplayDecisionSnapshot | dict[str, Any],
    baselineDecision: CurrentBaselineDecision | dict[str, Any],
    generatedAt: datetime | None = None,
) -> PaperShadowReport:
    snapshot = v2Snapshot if isinstance(v2Snapshot, ReplayDecisionSnapshot) else ReplayDecisionSnapshot(**v2Snapshot)
    baseline = baselineDecision if isinstance(baselineDecision, CurrentBaselineDecision) else CurrentBaselineDecision(**baselineDecision)
    generated = generatedAt or datetime.now(UTC)
    freshness = _input_freshness(snapshot)
    problems = _operational_problems(snapshot, freshness)
    snapshot_payload = _shadow_snapshot_payload(snapshot)
    comparison = _baseline_comparison(snapshot, baseline)
    return PaperShadowReport(
        generatedAt=generated,
        symbol=snapshot.symbol,
        sessionDate=snapshot.sessionDate,
        mode=paper_shadow_mode_config(),
        snapshotId=snapshot.snapshotId,
        v2DecisionSnapshot=snapshot_payload,
        baselineDecision=baseline,
        comparison=comparison,
        inputFreshness=freshness,
        gateDecision=snapshot.gateDecision,
        effectivePolicy=snapshot.effectivePolicy,
        wouldHaveOrderPlan=snapshot.orderPlan,
        automaticPaperSubmission=False,
        operationalProblems=problems,
        activationReady=not any(problem.mustResolveBeforeActivation for problem in problems),
        reasonCodes=[
            "paper_shadow.v2_record_only",
            "paper_shadow.complete_deterministic_path",
            "paper_shadow.ml_off",
            "paper_shadow.static_baseline_policy",
            "paper_shadow.no_automatic_submission",
        ],
        explanation=(
            "Paper-shadow mode ran strategies, context, regime, family ensemble, global gates, and static baseline policy. "
            "The order plan is recorded as would-have-done output only."
        ),
    )


def _shadow_snapshot_payload(snapshot: ReplayDecisionSnapshot) -> dict[str, Any]:
    payload = snapshot.model_dump(mode="json")
    payload["paperShadowMode"] = True
    payload["automaticPaperSubmission"] = False
    payload["submissionStatus"] = "NOT_SUBMITTED_SHADOW_ONLY"
    payload["reasonCodes"] = sorted(set([*snapshot.reasonCodes, "paper_shadow.record_only"]))
    return payload


def _input_freshness(snapshot: ReplayDecisionSnapshot) -> PaperShadowInputFreshness:
    max_input = snapshot.maxInputTimestampUtc
    reason_codes: list[str] = []
    if max_input is None:
        reason_codes.append("paper_shadow.max_input_timestamp_missing")
    elif max_input > snapshot.decisionTimestampUtc:
        reason_codes.append("paper_shadow.future_input_detected")
    feature_ready = bool(snapshot.featureSnapshot.get("dataReady"))
    if not feature_ready:
        reason_codes.extend(str(code) for code in snapshot.featureSnapshot.get("reasonCodes", []))
        reason_codes.append("paper_shadow.feature_snapshot_not_ready")
    reproducible = bool(
        snapshot.featureSnapshot.get("engineVersion")
        and snapshot.ensembleDecision.get("configurationHash")
        and snapshot.effectivePolicy.get("configurationHash")
        and snapshot.gateDecision.get("configurationHash")
    )
    if not reproducible:
        reason_codes.append("paper_shadow.reproducibility_metadata_missing")
    return PaperShadowInputFreshness(
        fresh=not reason_codes,
        reproducible=reproducible,
        maxInputTimestampUtc=max_input,
        decisionTimestampUtc=snapshot.decisionTimestampUtc,
        featureSnapshotHash=_hash_json(snapshot.featureSnapshot),
        reasonCodes=sorted(set(reason_codes)),
        explanation="Input freshness checks require point-in-time max input timestamp and reproducibility metadata.",
    )


def _operational_problems(
    snapshot: ReplayDecisionSnapshot,
    freshness: PaperShadowInputFreshness,
) -> list[PaperShadowOperationalProblem]:
    problems: list[PaperShadowOperationalProblem] = []
    if not freshness.fresh or not freshness.reproducible:
        problems.append(
            PaperShadowOperationalProblem(
                problemId="input_freshness_or_reproducibility",
                severity="BLOCKER",
                source="data",
                mustResolveBeforeActivation=True,
                reasonCodes=freshness.reasonCodes,
                explanation="V2 inputs must be fresh and reproducible before paper-shadow can be promoted.",
            )
        )
    gate_reason_codes = [str(code) for code in snapshot.gateDecision.get("reasonCodes", [])]
    if not snapshot.gateDecision.get("eligible", False):
        problems.append(
            PaperShadowOperationalProblem(
                problemId="global_gate_block",
                severity="BLOCKER",
                source="global_gates",
                mustResolveBeforeActivation=True,
                reasonCodes=gate_reason_codes or ["paper_shadow.global_gate_not_eligible"],
                explanation="Global gates blocked the V2 candidate; resolve blockers before activation.",
            )
        )
    order_plan = snapshot.orderPlan or {}
    if order_plan and not order_plan.get("eligible", False) and order_plan.get("orderType") != "NO_ORDER":
        problems.append(
            PaperShadowOperationalProblem(
                problemId="order_plan_invalid",
                severity="BLOCKER",
                source="static_policy",
                mustResolveBeforeActivation=True,
                reasonCodes=[str(code) for code in order_plan.get("validationErrors", [])],
                explanation="Static baseline policy produced an invalid order plan.",
            )
        )
    if not problems:
        problems.append(
            PaperShadowOperationalProblem(
                problemId="no_unresolved_operational_problem",
                severity="INFO",
                source="paper_shadow",
                mustResolveBeforeActivation=False,
                reasonCodes=["paper_shadow.operational_checks_passed"],
                explanation="No operational blocker was detected in paper-shadow mode.",
            )
        )
    return problems


def _baseline_comparison(snapshot: ReplayDecisionSnapshot, baseline: CurrentBaselineDecision) -> PaperShadowBaselineComparison:
    v2_signal = Signal(snapshot.ensembleDecision.get("signal", Signal.HOLD.value))
    order_plan = snapshot.orderPlan or {}
    v2_would_plan = bool(order_plan.get("eligible") and order_plan.get("orderType") != "NO_ORDER")
    v2_quantity = int(order_plan.get("quantity") or 0) if v2_would_plan else 0
    baseline_quantity = baseline.orderQuantity if baseline.orderQuantity is not None else (1 if baseline.wouldTrade else 0)
    v2_notional = _order_notional(order_plan) if v2_would_plan else None
    notional_delta = None
    if baseline.expectedNotional is not None and v2_notional is not None:
        notional_delta = v2_notional - baseline.expectedNotional
    return PaperShadowBaselineComparison(
        baselineSignal=baseline.signal,
        v2Signal=v2_signal,
        signalChanged=Signal(baseline.signal) != v2_signal,
        baselineWouldTrade=baseline.wouldTrade,
        v2WouldPlanOrder=v2_would_plan,
        orderQuantityDelta=v2_quantity - baseline_quantity,
        notionalDelta=notional_delta,
        explanation=(
            f"Current baseline {Signal(baseline.signal).value} compared with V2 deterministic {v2_signal.value}; "
            f"V2 order plan was {'eligible' if v2_would_plan else 'not eligible'} and not submitted."
        ),
    )


def _order_notional(order_plan: dict[str, Any]) -> float | None:
    price = order_plan.get("limitPrice") or order_plan.get("entryPrice")
    quantity = order_plan.get("quantity")
    if price is None or quantity is None:
        return None
    return float(price) * int(quantity)


def _hash_json(payload: Any) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
