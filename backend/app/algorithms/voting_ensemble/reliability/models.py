from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.domain.models import OperatingMode, Signal


VOTING_ENSEMBLE_RELIABILITY_VERSION = "voting_ensemble_point_in_time_reliability_v1"
ReliabilitySampleWindow = Literal["rolling_20_trades", "rolling_60_trades", "rolling_120_trades"]


class VotingEnsembleReliabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = VOTING_ENSEMBLE_RELIABILITY_VERSION
    neutralReliability: float = Field(default=0.50, ge=0.0, le=1.0)
    minimumReliability: float = Field(default=0.35, ge=0.0, le=1.0)
    maximumReliability: float = Field(default=0.75, ge=0.0, le=1.0)
    minimumSampleSize: int = Field(default=5, ge=1)
    minimumEffectiveSampleSize: float = Field(default=3.0, ge=0.0)
    recencyHalfLifeSamples: float = Field(default=20.0, gt=0.0)
    sampleWindow: ReliabilitySampleWindow = "rolling_60_trades"
    mode: OperatingMode = OperatingMode.SHADOW

    @model_validator(mode="after")
    def bounds_must_include_neutral(self) -> "VotingEnsembleReliabilityConfig":
        if self.minimumReliability > self.neutralReliability or self.maximumReliability < self.neutralReliability:
            raise ValueError("reliability bounds must include neutralReliability")
        return self

    @property
    def configurationHash(self) -> str:
        serialized = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


class VotingEnsembleReliabilityObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    algorithmId: Literal["voting_ensemble"] = "voting_ensemble"
    strategyId: str = Field(min_length=1)
    direction: Signal
    regime: str = Field(min_length=1)
    sessionSegment: str = Field(min_length=1)
    volatilityState: str = Field(min_length=1)
    sampleWindow: ReliabilitySampleWindow
    outcomeR: float
    transactionCostR: float = 0.0
    decisionTimestamp: datetime
    completedAt: datetime
    source: Literal["paper_trade", "point_in_time_backtest", "replay"] = "paper_trade"

    @field_validator("decisionTimestamp", "completedAt")
    @classmethod
    def timestamps_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("timestamp must be timezone-aware UTC")
        return value.astimezone(UTC)


class StrategyReliabilityEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    strategyId: str = Field(min_length=1)
    direction: Signal
    regime: str
    sessionSegment: str
    volatilityState: str
    sampleWindow: ReliabilitySampleWindow
    reliability: float = Field(ge=0.0, le=1.0)
    appliedReliability: float = Field(ge=0.0, le=1.0)
    neutralReliability: float = Field(ge=0.0, le=1.0)
    sampleSize: int = Field(ge=0)
    effectiveSampleSize: float = Field(ge=0.0)
    sourceWindowStart: datetime | None = None
    sourceWindowEnd: datetime | None = None
    mode: OperatingMode
    reliabilityVersion: str
    configurationHash: str
    components: dict[str, float] = Field(default_factory=dict)
    reasonCodes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)

    @field_validator("sourceWindowStart", "sourceWindowEnd")
    @classmethod
    def source_window_must_be_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("timestamp must be timezone-aware UTC")
        return value.astimezone(UTC)
