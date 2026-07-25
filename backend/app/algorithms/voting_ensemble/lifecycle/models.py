from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


VotingEnsembleLifecycleState = Literal[
    "unavailable",
    "not_data_ready",
    "shadow",
    "candidate",
    "active",
    "disabled",
    "deprecated_alias",
]

VOTING_ENSEMBLE_PROMOTION_POLICY_VERSION = "voting_ensemble_lifecycle_promotion_policy_v1"
PROMOTION_CANDIDATE_EVIDENCE_MARKER = "promotion.candidate_evidence:"
PROMOTION_APPROVAL_MARKER = "promotion.approval:"
VOTING_ENSEMBLE_PROTECTED_SHADOW_MODULE_IDS: tuple[str, ...] = (
    "opening_range_breakout",
    "vwap_trend_continuation",
    "gap_continuation_fade",
    "economic_event_context",
    "market_structure_context",
    "volume_confirmation_context",
    "vwap_position_context",
)


class CostStressEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    baselineNetExpectancy: float
    twoTimesCostNetExpectancy: float
    threeTimesCostNetExpectancy: float
    maximumStressDrawdownPct: float = Field(ge=0.0)
    promotionBlockedByStress: bool = False


class StabilityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    walkForwardStabilityScore: float = Field(ge=0.0, le=1.0)
    untouchedHoldoutNetExpectancy: float
    paperShadowDays: int = Field(ge=0)
    paperShadowDecisionCount: int = Field(ge=0)
    paperShadowStabilityScore: float = Field(ge=0.0, le=1.0)


class LatencyEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    p50EvaluationLatencyMs: float = Field(ge=0.0)
    p95EvaluationLatencyMs: float = Field(ge=0.0)
    p99EvaluationLatencyMs: float = Field(ge=0.0)
    maximumObservedLatencyMs: float = Field(ge=0.0)
    assumptionsValid: bool

    @model_validator(mode="after")
    def percentiles_must_be_ordered(self) -> "LatencyEvidence":
        if not (self.p50EvaluationLatencyMs <= self.p95EvaluationLatencyMs <= self.p99EvaluationLatencyMs <= self.maximumObservedLatencyMs):
            raise ValueError("latency percentiles must be ordered")
        return self


class OverlapEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    maximumFamilyContributionShare: float = Field(ge=0.0, le=1.0)
    maximumSameEventOverlapShare: float = Field(ge=0.0, le=1.0)
    unacceptableOverlapDetected: bool = False
    concentrationDetected: bool = False


class PromotionEvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    algorithmId: Literal["voting_ensemble"] = "voting_ensemble"
    policyVersion: Literal["voting_ensemble_lifecycle_promotion_policy_v1"] = VOTING_ENSEMBLE_PROMOTION_POLICY_VERSION
    moduleId: str = Field(min_length=1)
    fromLifecycle: VotingEnsembleLifecycleState
    requestedLifecycle: VotingEnsembleLifecycleState
    evidenceWindowStart: datetime
    evidenceWindowEnd: datetime
    sampleSize: int = Field(ge=0)
    regimesTested: tuple[str, ...]
    netExpectancy: float
    maximumDrawdownPct: float = Field(ge=0.0)
    costStress: CostStressEvidence
    stability: StabilityEvidence
    latency: LatencyEvidence
    overlap: OverlapEvidence
    focusedUnitTestsPassed: bool
    pointInTimeReplayPassed: bool
    minimumSampleSizeMet: bool
    walkForwardResultsStable: bool
    untouchedHoldoutAcceptable: bool
    netResultsAcceptableUnderCostStress: bool
    latencyAssumptionsValid: bool
    noUnacceptableOverlapOrConcentration: bool
    paperShadowStabilityDemonstrated: bool
    approvalReason: str = Field(min_length=1)
    configurationHash: str = Field(min_length=8)

    @model_validator(mode="after")
    def evidence_window_must_be_ordered(self) -> "PromotionEvidenceRecord":
        if self.evidenceWindowEnd <= self.evidenceWindowStart:
            raise ValueError("evidenceWindowEnd must be after evidenceWindowStart")
        if not self.regimesTested:
            raise ValueError("regimesTested must not be empty")
        return self


class PromotionPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policyVersion: Literal["voting_ensemble_lifecycle_promotion_policy_v1"] = VOTING_ENSEMBLE_PROMOTION_POLICY_VERSION
    minimumSampleSize: int = Field(default=100, ge=1)
    minimumNetExpectancy: float = 0.0
    maximumDrawdownPct: float = Field(default=12.0, ge=0.0)
    maximumStressDrawdownPct: float = Field(default=16.0, ge=0.0)
    minimumStabilityScore: float = Field(default=0.60, ge=0.0, le=1.0)
    minimumPaperShadowDays: int = Field(default=5, ge=0)
    minimumPaperShadowDecisions: int = Field(default=25, ge=0)
    maximumP95EvaluationLatencyMs: float = Field(default=250.0, ge=0.0)
    maximumFamilyContributionShare: float = Field(default=0.65, ge=0.0, le=1.0)
    maximumSameEventOverlapShare: float = Field(default=0.50, ge=0.0, le=1.0)


class PromotionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    algorithmId: Literal["voting_ensemble"] = "voting_ensemble"
    policyVersion: Literal["voting_ensemble_lifecycle_promotion_policy_v1"] = VOTING_ENSEMBLE_PROMOTION_POLICY_VERSION
    moduleId: str
    fromLifecycle: VotingEnsembleLifecycleState
    requestedLifecycle: VotingEnsembleLifecycleState
    approved: bool
    requiresExplicitInventoryChange: bool
    explicitInventoryChangeId: str | None = None
    reasonCodes: tuple[str, ...]
    evidenceRecord: PromotionEvidenceRecord | None = None
