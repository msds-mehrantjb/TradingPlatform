from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from backend.app.config import ApplicationConfig, FeatureFlags
from backend.app.domain.models import DomainModel, OperatingMode, OrderPlan, Signal, _require_utc

from .deterministic_activation import DETERMINISTIC_V2_BASELINE_VERSION
from .dynamic_policy_shadow import DynamicPolicyShadowReport, build_dynamic_policy_shadow_report
from .event_replay import ReplayDecisionSnapshot


DYNAMIC_POLICY_ACTIVATION_VERSION = "deterministic_dynamic_policy_activation_v1"
DynamicPolicyStage = Literal[
    "RISK_REDUCTION",
    "STOP_AND_QUANTITY",
    "STRATEGY_FAMILY_ENTRY",
    "TARGET_AND_TIME_STOP",
    "TRAILING_BEHAVIOR",
]
ORDERED_DYNAMIC_POLICY_STAGES: tuple[DynamicPolicyStage, ...] = (
    "RISK_REDUCTION",
    "STOP_AND_QUANTITY",
    "STRATEGY_FAMILY_ENTRY",
    "TARGET_AND_TIME_STOP",
    "TRAILING_BEHAVIOR",
)


class DynamicPolicyStageComparisonReport(DomainModel):
    stage: DynamicPolicyStage
    comparisonVersion: str = "dynamic_policy_stage_comparison_v1"
    walkForwardReplayWindow: str = Field(min_length=1)
    paperShadowWindow: str = Field(min_length=1)
    walkForwardRiskAdjustedDelta: float
    paperShadowRiskAdjustedDelta: float
    walkForwardSampleCount: int = Field(ge=0)
    paperShadowSampleCount: int = Field(ge=0)
    improvesOrPreservesRiskAdjustedResults: bool
    reasonCodes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def stage_must_have_positive_evidence(self) -> "DynamicPolicyStageComparisonReport":
        if not self.improvesOrPreservesRiskAdjustedResults:
            raise ValueError(f"{self.stage} cannot activate without improved or preserved risk-adjusted results")
        if self.walkForwardRiskAdjustedDelta < 0 or self.paperShadowRiskAdjustedDelta < 0:
            raise ValueError(f"{self.stage} cannot activate with negative walk-forward or paper-shadow risk-adjusted delta")
        if self.walkForwardSampleCount <= 0 or self.paperShadowSampleCount <= 0:
            raise ValueError(f"{self.stage} requires walk-forward replay and paper-shadow samples")
        return self


class DynamicPolicyRollbackControls(DomainModel):
    disableRiskReduction: bool = False
    disableStopAndQuantity: bool = False
    disableStrategyFamilyEntry: bool = False
    disableTargetAndTimeStop: bool = False
    disableTrailingBehavior: bool = True
    disableAllDynamicPolicy: bool = False
    reasonCodes: list[str] = Field(default_factory=list)

    def disables(self, stage: DynamicPolicyStage) -> bool:
        return bool(
            self.disableAllDynamicPolicy
            or (stage == "RISK_REDUCTION" and self.disableRiskReduction)
            or (stage == "STOP_AND_QUANTITY" and self.disableStopAndQuantity)
            or (stage == "STRATEGY_FAMILY_ENTRY" and self.disableStrategyFamilyEntry)
            or (stage == "TARGET_AND_TIME_STOP" and self.disableTargetAndTimeStop)
            or (stage == "TRAILING_BEHAVIOR" and self.disableTrailingBehavior)
        )


class DynamicPolicyActivationConfig(DomainModel):
    activationVersion: str = DYNAMIC_POLICY_ACTIVATION_VERSION
    activeStages: list[DynamicPolicyStage] = Field(default_factory=lambda: ["RISK_REDUCTION"])
    strategyEngineV2Enabled: bool = True
    familyEnsembleV2Enabled: bool = True
    globalGateEngineEnabled: bool = True
    metaModelV2Mode: Literal["FILTER"] = "FILTER"
    dynamicTradingPolicyMode: Literal["ACTIVE"] = "ACTIVE"
    staticFallbackAvailable: bool = True
    globalRiskAuthoritative: bool = True
    brokerReconciliationAuthoritative: bool = True
    pyramidingEnabled: bool = False
    partialExitsEnabled: bool = False
    trailingBehaviorEnabled: bool = False
    configurationHash: str

    @model_validator(mode="after")
    def enforce_activation_posture(self) -> "DynamicPolicyActivationConfig":
        if not self.strategyEngineV2Enabled or not self.familyEnsembleV2Enabled or not self.globalGateEngineEnabled:
            raise ValueError("dynamic policy activation requires deterministic V2 and global gates")
        if not self.globalRiskAuthoritative or not self.brokerReconciliationAuthoritative:
            raise ValueError("global risk and broker reconciliation must remain authoritative")
        if self.pyramidingEnabled or self.partialExitsEnabled:
            raise ValueError("initial dynamic policy activation cannot enable pyramiding or partial exits")
        if "TRAILING_BEHAVIOR" in self.activeStages and not self.trailingBehaviorEnabled:
            raise ValueError("trailing behavior requires separate validation before activation")
        return self


class PolicyOrderMatchCheck(DomainModel):
    matchesDisplayedPolicy: bool
    checkedFields: list[str]
    mismatches: list[str]
    explanation: str

    @model_validator(mode="after")
    def require_match(self) -> "PolicyOrderMatchCheck":
        if not self.matchesDisplayedPolicy:
            raise ValueError("paper orders must match the activated policy shown in the UI")
        return self


class DynamicPolicyActivationReport(DomainModel):
    version: str = DYNAMIC_POLICY_ACTIVATION_VERSION
    generatedAt: datetime
    symbol: str
    sessionDate: date
    snapshotId: str
    activationConfig: DynamicPolicyActivationConfig
    rollback: DynamicPolicyRollbackControls
    deterministicBaselineVersion: str = DETERMINISTIC_V2_BASELINE_VERSION
    shadowReport: dict[str, Any]
    stageComparisonReports: list[DynamicPolicyStageComparisonReport]
    activeStages: list[DynamicPolicyStage]
    rolledBackStages: list[DynamicPolicyStage]
    deterministicDecisionSnapshot: dict[str, Any]
    staticPolicy: dict[str, Any]
    dynamicPolicy: dict[str, Any] | None
    activatedPaperOrderPlan: dict[str, Any] | None
    globalGateDecision: dict[str, Any]
    globalRiskAuthoritative: bool
    brokerReconciliationAuthoritative: bool
    mlLimitedToTradeFiltering: bool
    pyramidingEnabled: bool
    partialExitsEnabled: bool
    trailingBehaviorEnabled: bool
    orderPolicyMatch: PolicyOrderMatchCheck
    submittedPaperOrder: bool = False
    reasonCodes: list[str]
    explanation: str

    @field_validator("generatedAt")
    @classmethod
    def generated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def enforce_activation_guardrails(self) -> "DynamicPolicyActivationReport":
        if not self.globalRiskAuthoritative or not self.brokerReconciliationAuthoritative:
            raise ValueError("global risk and broker reconciliation must remain authoritative")
        if not self.mlLimitedToTradeFiltering:
            raise ValueError("ML must remain limited to trade filtering during initial dynamic policy activation")
        if self.pyramidingEnabled or self.partialExitsEnabled:
            raise ValueError("pyramiding and partial exits cannot be enabled during initial dynamic policy activation")
        if self.trailingBehaviorEnabled and "TRAILING_BEHAVIOR" not in self.activeStages:
            raise ValueError("trailing behavior cannot be enabled outside its validated stage")
        return self


def dynamic_policy_activation_application_config() -> ApplicationConfig:
    return ApplicationConfig(
        version="application-config-v1-dynamic-policy-active",
        featureFlags=FeatureFlags(
            strategyEngineV2Enabled=True,
            familyEnsembleV2Enabled=True,
            metaModelV2Enabled=True,
            dynamicTradingPolicyEnabled=True,
            globalGateEngineEnabled=True,
        ),
    )


def dynamic_policy_activation_config(
    *,
    active_stages: list[DynamicPolicyStage],
    trailing_behavior_enabled: bool = False,
) -> DynamicPolicyActivationConfig:
    payload = {
        "applicationConfig": dynamic_policy_activation_application_config().as_dict(),
        "activeStages": active_stages,
        "trailingBehaviorEnabled": trailing_behavior_enabled,
    }
    return DynamicPolicyActivationConfig(
        activeStages=active_stages,
        trailingBehaviorEnabled=trailing_behavior_enabled,
        configurationHash=_hash_payload(payload),
    )


def build_dynamic_policy_activation_report(
    *,
    snapshot: ReplayDecisionSnapshot | dict[str, Any],
    stageComparisons: list[DynamicPolicyStageComparisonReport | dict[str, Any]],
    requestedStages: list[DynamicPolicyStage] | None = None,
    rollback: DynamicPolicyRollbackControls | dict[str, Any] | None = None,
    generatedAt: datetime | None = None,
) -> DynamicPolicyActivationReport:
    static_snapshot = snapshot if isinstance(snapshot, ReplayDecisionSnapshot) else ReplayDecisionSnapshot(**snapshot)
    comparisons = [
        comparison if isinstance(comparison, DynamicPolicyStageComparisonReport) else DynamicPolicyStageComparisonReport(**comparison)
        for comparison in stageComparisons
    ]
    rollback_controls = rollback if isinstance(rollback, DynamicPolicyRollbackControls) else DynamicPolicyRollbackControls(**(rollback or {}))
    stages = requestedStages or ["RISK_REDUCTION"]
    active_stages = [stage for stage in stages if not rollback_controls.disables(stage)]
    rolled_back = [stage for stage in ORDERED_DYNAMIC_POLICY_STAGES if stage in stages and rollback_controls.disables(stage)]
    _require_stage_evidence(active_stages, comparisons)
    shadow = build_dynamic_policy_shadow_report(snapshot=static_snapshot, generatedAt=generatedAt)
    config = dynamic_policy_activation_config(
        active_stages=active_stages,
        trailing_behavior_enabled="TRAILING_BEHAVIOR" in active_stages,
    )
    activated_order = _activated_order_plan(static_snapshot, shadow, active_stages)
    match_check = _order_policy_match_check(activated_order, shadow.dynamicPolicy, active_stages)
    ml_mode = str(static_snapshot.mlInference.get("mode") or static_snapshot.mlInference.get("effectiveMode") or "")
    ml_filter_only = ml_mode in {OperatingMode.FILTER.value, OperatingMode.SHADOW.value, OperatingMode.FALLBACK.value, ""}
    reason_codes = [
        "dynamic_policy.activation_staged",
        "dynamic_policy.global_risk_authoritative",
        "dynamic_policy.broker_reconciliation_authoritative",
        "dynamic_policy.ml_filter_only",
        "dynamic_policy.pyramiding_disabled",
        "dynamic_policy.partial_exits_disabled",
    ]
    reason_codes.extend(f"dynamic_policy.stage_active.{stage.lower()}" for stage in active_stages)
    reason_codes.extend(f"dynamic_policy.stage_rollback.{stage.lower()}" for stage in rolled_back)
    reason_codes.extend(code for comparison in comparisons for code in comparison.reasonCodes)
    return DynamicPolicyActivationReport(
        generatedAt=generatedAt or datetime.now(UTC),
        symbol=static_snapshot.symbol,
        sessionDate=static_snapshot.sessionDate,
        snapshotId=static_snapshot.snapshotId,
        activationConfig=config,
        rollback=rollback_controls,
        shadowReport=shadow.model_dump(mode="json"),
        stageComparisonReports=comparisons,
        activeStages=active_stages,
        rolledBackStages=rolled_back,
        deterministicDecisionSnapshot=shadow.deterministicDecisionSnapshot,
        staticPolicy=shadow.staticPolicy,
        dynamicPolicy=shadow.dynamicPolicy,
        activatedPaperOrderPlan=activated_order.model_dump(mode="json") if activated_order else None,
        globalGateDecision=static_snapshot.gateDecision,
        globalRiskAuthoritative=True,
        brokerReconciliationAuthoritative=True,
        mlLimitedToTradeFiltering=ml_filter_only,
        pyramidingEnabled=False,
        partialExitsEnabled=False,
        trailingBehaviorEnabled="TRAILING_BEHAVIOR" in active_stages,
        orderPolicyMatch=match_check,
        reasonCodes=sorted(set(reason_codes)),
        explanation=(
            "Deterministic dynamic policy activation is staged by capability and requires walk-forward replay plus paper-shadow evidence. "
            "Global gates, account risk, and broker reconciliation remain authoritative; ML remains a trade filter."
        ),
    )


def _require_stage_evidence(
    active_stages: list[DynamicPolicyStage],
    comparisons: list[DynamicPolicyStageComparisonReport],
) -> None:
    by_stage = {comparison.stage: comparison for comparison in comparisons}
    missing = [stage for stage in active_stages if stage not in by_stage]
    if missing:
        raise ValueError(f"missing dynamic policy stage comparison report: {', '.join(missing)}")
    for stage in active_stages:
        comparison = by_stage[stage]
        if not comparison.improvesOrPreservesRiskAdjustedResults:
            raise ValueError(f"{stage} cannot activate without preserving risk-adjusted results")
    if "TRAILING_BEHAVIOR" in active_stages:
        raise ValueError("trailing behavior requires separate validation and remains disabled initially")


def _activated_order_plan(
    snapshot: ReplayDecisionSnapshot,
    shadow: DynamicPolicyShadowReport,
    active_stages: list[DynamicPolicyStage],
) -> OrderPlan | None:
    if not snapshot.orderPlan or not shadow.dynamicPolicy:
        return None
    base_order = OrderPlan.model_validate(snapshot.orderPlan)
    if base_order.orderType == "NO_ORDER" or not base_order.eligible:
        return base_order
    policy = shadow.dynamicPolicy
    entry_plan = policy.get("entryPlan") or {}
    updates: dict[str, Any] = {
        "validationErrors": sorted(set([*base_order.validationErrors, "dynamic_policy.activated_staged_plan"])),
        "configurationHash": str(policy.get("configurationHash") or base_order.configurationHash),
    }
    if "RISK_REDUCTION" in active_stages and float(policy.get("approvedRiskDollars") or 0.0) <= 0:
        updates.update(
            {
                "orderType": "NO_ORDER",
                "quantity": 0,
                "eligible": False,
                "validationErrors": sorted(set([*updates["validationErrors"], "dynamic_policy.risk_reduction_blocks_entry"])),
                "explanation": "Dynamic risk reduction stage blocked the paper order because approved risk is zero.",
            }
        )
    if "STOP_AND_QUANTITY" in active_stages:
        updates["quantity"] = int(policy.get("quantity") or 0)
        if policy.get("stop") is not None:
            updates["stopPrice"] = float(policy["stop"])
    if "STRATEGY_FAMILY_ENTRY" in active_stages and entry_plan:
        updates["orderType"] = entry_plan.get("orderType") or base_order.orderType
        updates["entryPrice"] = float(entry_plan.get("entryPrice") or base_order.entryPrice)
        updates["limitPrice"] = float(entry_plan.get("limitPrice") or base_order.limitPrice or base_order.entryPrice)
    if "TARGET_AND_TIME_STOP" in active_stages:
        if policy.get("target") is not None:
            updates["targetPrice"] = float(policy["target"])
        updates["maximumHoldingMinutes"] = int(policy.get("holdingPeriodMinutes") or base_order.maximumHoldingMinutes or 1)
    if "TRAILING_BEHAVIOR" in active_stages:
        updates["validationErrors"] = sorted(set([*updates["validationErrors"], "dynamic_policy.trailing_not_initially_enabled"]))
    if updates.get("quantity") == 0:
        updates.setdefault("orderType", "NO_ORDER")
        updates["eligible"] = False
    return OrderPlan.model_validate({**base_order.model_dump(mode="json"), **updates})


def _order_policy_match_check(
    order_plan: OrderPlan | None,
    dynamic_policy: dict[str, Any] | None,
    active_stages: list[DynamicPolicyStage],
) -> PolicyOrderMatchCheck:
    mismatches: list[str] = []
    checked: list[str] = []
    if order_plan is None or dynamic_policy is None:
        return PolicyOrderMatchCheck(
            matchesDisplayedPolicy=True,
            checkedFields=[],
            mismatches=[],
            explanation="No dynamic paper order was produced, so there is no order/policy mismatch.",
        )
    entry_plan = dynamic_policy.get("entryPlan") or {}
    if "STOP_AND_QUANTITY" in active_stages:
        checked.extend(["quantity", "stopPrice"])
        if order_plan.quantity != int(dynamic_policy.get("quantity") or 0):
            mismatches.append("quantity")
        if dynamic_policy.get("stop") is not None and not _same_number(order_plan.stopPrice, dynamic_policy.get("stop")):
            mismatches.append("stopPrice")
    if "STRATEGY_FAMILY_ENTRY" in active_stages and entry_plan:
        checked.extend(["orderType", "entryPrice", "limitPrice"])
        if order_plan.orderType != entry_plan.get("orderType"):
            mismatches.append("orderType")
        if not _same_number(order_plan.entryPrice, entry_plan.get("entryPrice")):
            mismatches.append("entryPrice")
        if not _same_number(order_plan.limitPrice, entry_plan.get("limitPrice")):
            mismatches.append("limitPrice")
    if "TARGET_AND_TIME_STOP" in active_stages:
        checked.extend(["targetPrice", "maximumHoldingMinutes"])
        if not _same_number(order_plan.targetPrice, dynamic_policy.get("target")):
            mismatches.append("targetPrice")
        if int(order_plan.maximumHoldingMinutes or 0) != int(dynamic_policy.get("holdingPeriodMinutes") or 0):
            mismatches.append("maximumHoldingMinutes")
    return PolicyOrderMatchCheck(
        matchesDisplayedPolicy=not mismatches,
        checkedFields=checked,
        mismatches=mismatches,
        explanation="Activated paper order fields were compared against the staged dynamic policy shown to the UI.",
    )


def _same_number(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left == right
    try:
        return abs(float(left) - float(right)) <= 1e-9
    except (TypeError, ValueError):
        return left == right


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
