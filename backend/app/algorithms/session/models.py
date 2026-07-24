"""Immutable contracts for the backend-authoritative Session subsystem."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SESSION_ALGORITHM_ID = "session"
SESSION_CLASSIFIER_VERSION = "session_classifier_v1"
SESSION_FEATURE_SCHEMA_VERSION = "session_feature_schema_v1"


class SessionContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def deterministic_json(self) -> str:
        return json.dumps(self.model_dump(mode="json", exclude_none=True), sort_keys=True, separators=(",", ":"))

    def deterministic_hash(self) -> str:
        return hashlib.sha256(self.deterministic_json().encode("utf-8")).hexdigest()


class SessionPhase(str, Enum):
    PREMARKET = "premarket"
    OPENING_AUCTION = "opening_auction"
    OPENING_DISCOVERY = "opening_discovery"
    OPENING_RANGE = "opening_range"
    MORNING = "morning"
    MIDDAY = "midday"
    AFTERNOON = "afternoon"
    POWER_HOUR = "power_hour"
    CLOSING_AUCTION = "closing_auction"
    POSTMARKET = "postmarket"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class SessionBehavior(str, Enum):
    BUILDING = "building"
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    BALANCED_RANGE = "balanced_range"
    OPENING_DRIVE = "opening_drive"
    MEAN_REVERTING = "mean_reverting"
    CHOPPY = "choppy"
    BREAKOUT_UP = "breakout_up"
    BREAKOUT_DOWN = "breakout_down"
    FAILED_BREAKOUT_UP = "failed_breakout_up"
    FAILED_BREAKOUT_DOWN = "failed_breakout_down"
    REVERSAL_UP = "reversal_up"
    REVERSAL_DOWN = "reversal_down"
    EXPANSION = "expansion"
    COMPRESSION = "compression"
    EVENT_DRIVEN = "event_driven"
    LIQUIDITY_STRESS = "liquidity_stress"
    UNKNOWN = "unknown"


class VolatilityState(str, Enum):
    COMPRESSED = "compressed"
    NORMAL = "normal"
    EXPANDING = "expanding"
    EXTREME = "extreme"
    UNKNOWN = "unknown"


class LiquidityState(str, Enum):
    HEALTHY = "healthy"
    CONSTRAINED = "constrained"
    STRESSED = "stressed"
    STALE = "stale"
    UNKNOWN = "unknown"


class DataQualityState(str, Enum):
    READY = "ready"
    WARMING_UP = "warming_up"
    INCOMPLETE = "incomplete"
    STALE = "stale"
    INVALID = "invalid"


class EventRiskState(str, Enum):
    CLEAR = "clear"
    ELEVATED = "elevated"
    BLACKOUT = "blackout"
    UNKNOWN = "unknown"


DirectionBias = Literal["long", "short", "neutral", "cash"]


class SessionClassification(SessionContractModel):
    symbol: str = Field(min_length=1, max_length=12)
    session_date: str | None
    exchange_timezone: str = "America/New_York"
    market_event_time: datetime | None
    feature_snapshot_time: datetime | None
    decision_time: datetime
    valid_until: datetime
    phase: SessionPhase
    behavior: SessionBehavior
    volatility_state: VolatilityState
    liquidity_state: LiquidityState
    data_quality_state: DataQualityState
    event_risk_state: EventRiskState = EventRiskState.UNKNOWN
    direction_bias: DirectionBias
    phase_confidence: float = Field(ge=0, le=1)
    behavior_confidence: float = Field(ge=0, le=1)
    volatility_confidence: float = Field(ge=0, le=1)
    liquidity_confidence: float = Field(ge=0, le=1)
    data_quality_confidence: float = Field(ge=0, le=1)
    overall_confidence: float = Field(ge=0, le=1)
    safety_block_confidence: float = Field(default=0.0, ge=0, le=1)
    reason_codes: tuple[str, ...]
    evidence: dict[str, Any]
    allowed_strategy_families: tuple[str, ...]
    blocked_strategy_families: tuple[str, ...]
    block_new_entries: bool
    classifier_version: str = SESSION_CLASSIFIER_VERSION
    feature_schema_version: str = SESSION_FEATURE_SCHEMA_VERSION

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.upper()

    @field_validator("market_event_time", "feature_snapshot_time", "decision_time", "valid_until")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("session timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_timestamp_order(self) -> SessionClassification:
        if self.valid_until < self.decision_time:
            raise ValueError("valid_until must be greater than or equal to decision_time")
        if self.feature_snapshot_time and self.market_event_time and self.feature_snapshot_time < self.market_event_time:
            raise ValueError("feature_snapshot_time must not precede market_event_time")
        if self.decision_time and self.feature_snapshot_time and self.decision_time < self.feature_snapshot_time:
            raise ValueError("decision_time must not precede feature_snapshot_time")
        return self
