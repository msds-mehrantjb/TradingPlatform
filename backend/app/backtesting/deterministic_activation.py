from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from backend.app.config import ApplicationConfig, FeatureFlags
from backend.app.domain.models import DomainModel, _require_utc

from .event_replay import ReplayDecisionSnapshot


DETERMINISTIC_V2_BASELINE_VERSION = "deterministic_v2_static_baseline_v1"
RollbackMode = Literal["NONE", "V1", "DISABLE_AUTOMATIC_ENTRIES"]


class DeterministicV2ActivationConfig(DomainModel):
    activationVersion: str = DETERMINISTIC_V2_BASELINE_VERSION
    strategyEngineV2Enabled: bool = True
    familyEnsembleV2Enabled: bool = True
    globalGateEngineEnabled: bool = True
    staticBaselineSettingsEnabled: bool = True
    metaModelV2Mode: Literal["SHADOW"] = "SHADOW"
    dynamicTradingPolicyMode: Literal["SHADOW"] = "SHADOW"
    mlMayAffectExecution: bool = False
    dynamicPolicyMayAffectExecution: bool = False
    rollbackMode: RollbackMode = "NONE"
    configurationHash: str

    @model_validator(mode="after")
    def enforce_activation_posture(self) -> "DeterministicV2ActivationConfig":
        if not self.strategyEngineV2Enabled or not self.familyEnsembleV2Enabled or not self.globalGateEngineEnabled:
            raise ValueError("deterministic V2 activation requires V2 strategy, family ensemble, and global gates")
        if not self.staticBaselineSettingsEnabled:
            raise ValueError("deterministic V2 activation requires static baseline settings")
        if self.mlMayAffectExecution or self.dynamicPolicyMayAffectExecution:
            raise ValueError("ML and dynamic policy must remain shadow-only during deterministic V2 activation")
        return self


class ActivationRollbackState(DomainModel):
    rollbackMode: RollbackMode
    effectiveExecutionPath: Literal["DETERMINISTIC_V2_STATIC_BASELINE", "V1_ROLLBACK", "AUTOMATIC_ENTRIES_DISABLED"]
    automaticEntriesDisabled: bool
    reasonCodes: list[str]
    explanation: str


class ShadowPredictionRecord(DomainModel):
    component: Literal["ML", "DYNAMIC_POLICY"]
    mode: Literal["SHADOW"]
    appliedToExecution: bool = False
    payload: dict[str, Any]
    reasonCodes: list[str]
    explanation: str

    @model_validator(mode="after")
    def shadow_record_cannot_apply_to_execution(self) -> "ShadowPredictionRecord":
        if self.appliedToExecution:
            raise ValueError(f"{self.component} shadow record cannot apply to execution")
        return self


class DeterministicV2ActivationReport(DomainModel):
    version: str = DETERMINISTIC_V2_BASELINE_VERSION
    generatedAt: datetime
    symbol: str
    sessionDate: date
    activationConfig: DeterministicV2ActivationConfig
    rollback: ActivationRollbackState
    snapshotId: str
    deterministicDecisionSnapshot: dict[str, Any]
    globalGateDecision: dict[str, Any]
    staticBaselinePolicy: dict[str, Any]
    orderPlan: dict[str, Any] | None
    mlShadow: ShadowPredictionRecord
    dynamicPolicyShadow: ShadowPredictionRecord
    automaticPaperEntryAllowed: bool
    submittedPaperOrder: bool = False
    newDeterministicBaseline: bool = True
    baselineComparisonKey: str
    reasonCodes: list[str]
    explanation: str

    @field_validator("generatedAt")
    @classmethod
    def generated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def enforce_submission_guardrails(self) -> "DeterministicV2ActivationReport":
        if self.submittedPaperOrder:
            if not self.globalGateDecision.get("eligible"):
                raise ValueError("submitted deterministic V2 paper orders must pass global gates")
            if not (self.orderPlan and self.orderPlan.get("eligible")):
                raise ValueError("submitted deterministic V2 paper orders require an eligible order plan")
        if self.automaticPaperEntryAllowed and not self.globalGateDecision.get("eligible"):
            raise ValueError("automatic deterministic V2 entries require global gates to pass")
        if self.automaticPaperEntryAllowed and self.rollback.automaticEntriesDisabled:
            raise ValueError("rollback mode must suppress deterministic V2 automatic entries")
        if self.mlShadow.appliedToExecution or self.dynamicPolicyShadow.appliedToExecution:
            raise ValueError("shadow ML and dynamic policy records cannot affect execution")
        settings_version = str(((self.staticBaselinePolicy.get("baselineSettings") or {}).get("settingsVersion") or ""))
        if "baseline" not in settings_version:
            raise ValueError("deterministic V2 activation must use static baseline risk settings")
        return self


def deterministic_v2_active_application_config(*, rollback_mode: RollbackMode = "NONE") -> ApplicationConfig:
    # Rollback mode is intentionally held by DeterministicV2ActivationConfig so
    # the default application config can remain safe until an activation caller opts in.
    return ApplicationConfig(
        version=f"application-config-v1-deterministic-v2-{rollback_mode.lower()}",
        featureFlags=FeatureFlags(
            strategyEngineV2Enabled=True,
            familyEnsembleV2Enabled=True,
            metaModelV2Enabled=False,
            dynamicTradingPolicyEnabled=False,
            globalGateEngineEnabled=True,
        ),
    )


def deterministic_v2_activation_config(*, rollback_mode: RollbackMode = "NONE") -> DeterministicV2ActivationConfig:
    payload = deterministic_v2_active_application_config(rollback_mode=rollback_mode).as_dict()
    return DeterministicV2ActivationConfig(
        rollbackMode=rollback_mode,
        configurationHash=_hash_payload({"applicationConfig": payload, "rollbackMode": rollback_mode}),
    )


def build_deterministic_v2_activation_report(
    *,
    snapshot: ReplayDecisionSnapshot | dict[str, Any],
    rollbackMode: RollbackMode = "NONE",
    generatedAt: datetime | None = None,
) -> DeterministicV2ActivationReport:
    v2_snapshot = snapshot if isinstance(snapshot, ReplayDecisionSnapshot) else ReplayDecisionSnapshot(**snapshot)
    generated = generatedAt or datetime.now(UTC)
    config = deterministic_v2_activation_config(rollback_mode=rollbackMode)
    rollback = _rollback_state(rollbackMode)
    gate_eligible = bool(v2_snapshot.gateDecision.get("eligible"))
    order_eligible = bool(v2_snapshot.orderPlan and v2_snapshot.orderPlan.get("eligible") and v2_snapshot.orderPlan.get("orderType") != "NO_ORDER")
    automatic_allowed = bool(rollbackMode == "NONE" and gate_eligible and order_eligible)
    snapshot_payload = _activation_snapshot_payload(v2_snapshot)
    ml_shadow = ShadowPredictionRecord(
        component="ML",
        mode="SHADOW",
        appliedToExecution=False,
        payload=v2_snapshot.mlInference,
        reasonCodes=sorted(set([*_payload_reason_codes(v2_snapshot.mlInference), "ml.shadow_recorded_no_execution_effect"])),
        explanation="ML prediction was recorded in shadow mode and did not accept, reject, flip, or size the order.",
    )
    dynamic_shadow = ShadowPredictionRecord(
        component="DYNAMIC_POLICY",
        mode="SHADOW",
        appliedToExecution=False,
        payload={
            "mode": "SHADOW",
            "staticBaselinePolicyHash": v2_snapshot.effectivePolicy.get("configurationHash"),
            "wouldUseMostRestrictiveCapsOnly": True,
        },
        reasonCodes=["policy.shadow_dynamic_adjustments_not_applied", "policy.static_baseline_used_for_execution"],
        explanation="Dynamic trading policy was recorded as shadow-only; static baseline policy is the execution baseline.",
    )
    reason_codes = [
        "deterministic_v2.corrected_family_ensemble_active",
        "deterministic_v2.global_hard_gates_active",
        "deterministic_v2.static_baseline_settings_active",
        "deterministic_v2.ml_shadow_no_execution_effect",
        "deterministic_v2.dynamic_policy_shadow_no_execution_effect",
    ]
    if automatic_allowed:
        reason_codes.append("deterministic_v2.paper_entry_allowed_after_global_gates")
    else:
        reason_codes.append("deterministic_v2.paper_entry_not_allowed")
    return DeterministicV2ActivationReport(
        generatedAt=generated,
        symbol=v2_snapshot.symbol,
        sessionDate=v2_snapshot.sessionDate,
        activationConfig=config,
        rollback=rollback,
        snapshotId=v2_snapshot.snapshotId,
        deterministicDecisionSnapshot=snapshot_payload,
        globalGateDecision=v2_snapshot.gateDecision,
        staticBaselinePolicy=v2_snapshot.effectivePolicy,
        orderPlan=v2_snapshot.orderPlan,
        mlShadow=ml_shadow,
        dynamicPolicyShadow=dynamic_shadow,
        automaticPaperEntryAllowed=automatic_allowed,
        submittedPaperOrder=False,
        baselineComparisonKey=_hash_payload(
            {
                "baseline": DETERMINISTIC_V2_BASELINE_VERSION,
                "snapshotId": v2_snapshot.snapshotId,
                "policyHash": v2_snapshot.effectivePolicy.get("configurationHash"),
                "gateHash": v2_snapshot.gateDecision.get("configurationHash"),
            }
        ),
        reasonCodes=reason_codes,
        explanation=(
            "Deterministic V2 static baseline is active for paper-entry eligibility. "
            "Orders are eligible only after global hard gates pass; ML and dynamic policy are shadow records only."
        ),
    )


def _rollback_state(rollback_mode: RollbackMode) -> ActivationRollbackState:
    if rollback_mode == "V1":
        return ActivationRollbackState(
            rollbackMode=rollback_mode,
            effectiveExecutionPath="V1_ROLLBACK",
            automaticEntriesDisabled=False,
            reasonCodes=["rollback.v1_baseline_selected"],
            explanation="Rollback flag routes automatic decisions back to the V1 baseline immediately.",
        )
    if rollback_mode == "DISABLE_AUTOMATIC_ENTRIES":
        return ActivationRollbackState(
            rollbackMode=rollback_mode,
            effectiveExecutionPath="AUTOMATIC_ENTRIES_DISABLED",
            automaticEntriesDisabled=True,
            reasonCodes=["rollback.automatic_entries_disabled"],
            explanation="Rollback flag disables automatic entries immediately while preserving manual/protective workflows.",
        )
    return ActivationRollbackState(
        rollbackMode=rollback_mode,
        effectiveExecutionPath="DETERMINISTIC_V2_STATIC_BASELINE",
        automaticEntriesDisabled=False,
        reasonCodes=["rollback.none_deterministic_v2_active"],
        explanation="No rollback flag is active; deterministic V2 static baseline controls paper-entry eligibility.",
    )


def _activation_snapshot_payload(snapshot: ReplayDecisionSnapshot) -> dict[str, Any]:
    payload = snapshot.model_dump(mode="json")
    payload["activationBaseline"] = DETERMINISTIC_V2_BASELINE_VERSION
    payload["submittedPaperOrder"] = False
    payload["reasonCodes"] = sorted(set([*snapshot.reasonCodes, "activation.deterministic_v2_static_baseline"]))
    return payload


def _payload_reason_codes(payload: dict[str, Any]) -> list[str]:
    codes = payload.get("reasonCodes") if isinstance(payload, dict) else []
    return [str(code) for code in codes] if isinstance(codes, list) else []


def _hash_payload(payload: Any) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
