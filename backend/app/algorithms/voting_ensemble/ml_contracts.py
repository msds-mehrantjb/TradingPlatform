"""Voting Ensemble-owned ML shadow contracts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field

from backend.app.domain.models import DomainModel, OperatingMode, Signal


class VotingEnsembleMLFeatureSpec(DomainModel):
    name: str = Field(min_length=1)
    group: Literal["directional_strategy", "family", "context", "regime", "execution", "candidate", "upstream_forecast"]
    valueType: Literal["numeric", "categorical"]


class VotingEnsembleMLFeatureSet(DomainModel):
    schemaVersion: Literal["voting_ensemble_candidate_feature_schema_v1"] = "voting_ensemble_candidate_feature_schema_v1"
    schemaHash: str = Field(min_length=1)
    snapshotId: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    decisionTimestampUtc: str = Field(min_length=1)
    featureValues: dict[str, Any]
    missingIndicators: dict[str, bool]
    forbiddenFieldsChecked: list[str]
    explanation: str = Field(min_length=1)


class VotingEnsembleSafeMLInferenceConfig(DomainModel):
    mode: OperatingMode = OperatingMode.OFF
    fallbackBehavior: Literal["DETERMINISTIC_BASELINE", "NO_TRADE"] = "DETERMINISTIC_BASELINE"
    fallbackOnModelUnavailable: bool = True
    fallbackOnSchemaMismatch: bool = True
    minSuccessProbability: float = Field(default=0.52, ge=0.0, le=1.0)
    minCalibratedProbability: float = Field(default=0.52, ge=0.0, le=1.0)
    maxFeatureMissingness: float = Field(default=0.25, ge=0.0, le=1.0)
    maxOutOfDistributionScore: float = Field(default=0.70, ge=0.0, le=1.0)
    minModelHealthScore: float = Field(default=0.70, ge=0.0, le=1.0)
    activeMinRiskCap: float = Field(default=0.25, ge=0.0, le=1.0)
    activeMaxRiskCap: float = Field(default=1.0, ge=0.0, le=1.0)
    configurationHash: str = Field(default="voting_ensemble_safe_ml_inference_config_v1", min_length=1)


class VotingEnsembleSafeMLInferenceResult(DomainModel):
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


MLFeatureSpec = VotingEnsembleMLFeatureSpec
MLFeatureSet = VotingEnsembleMLFeatureSet
SafeMLInferenceConfig = VotingEnsembleSafeMLInferenceConfig
SafeMLInferenceResult = VotingEnsembleSafeMLInferenceResult
