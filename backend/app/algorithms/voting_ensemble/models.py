"""Contracts for the backend-authoritative Voting Ensemble."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AlgoSignal = Literal["Buy", "Sell", "Hold"]
SignalFamily = Literal["trend", "breakout", "reversal", "mean_reversion", "event"]
SignalRole = Literal["directional", "context", "safety"]
SignalDirection = Literal[-1, 0, 1]
FeatureValue = int | float | bool | str


class VotingCandle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_ohlc(self) -> "VotingCandle":
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise ValueError("candle OHLC geometry is invalid")
        return self


class VotingEnsembleEvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(default="SPY", min_length=1)
    data_timestamp: datetime | None = None
    candles: tuple[VotingCandle, ...] = Field(min_length=1)
    market_context: dict[str, Any] | None = None
    qqq_candles: tuple[VotingCandle, ...] = ()
    iwm_candles: tuple[VotingCandle, ...] = ()
    breadth_components: dict[str, tuple[VotingCandle, ...]] = Field(default_factory=dict)
    external_breadth_feed: dict[str, Any] | None = None


class VotingStrategyVote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: str
    family: SignalFamily
    role: SignalRole
    signal: AlgoSignal
    direction: SignalDirection
    confidence: float = Field(ge=0, le=1)
    active: bool
    eligible: bool
    dataReady: bool
    regimeFit: float = Field(ge=0, le=1)
    reliability: float = Field(ge=0, le=1)
    reason: str
    features: dict[str, FeatureValue] = Field(default_factory=dict)


class VotingContextConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["confirms", "weakens", "mixed", "not_applicable"]
    detail: str
    evidence: tuple[str, ...] = ()
    confirmations: int = Field(ge=0)
    conflicts: int = Field(ge=0)


class VotingEnsembleEvaluateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm_id: Literal["voting_ensemble"] = "voting_ensemble"
    service_version: str
    symbol: str
    evaluated_at: datetime
    data_timestamp: datetime
    final_signal: AlgoSignal
    votes: tuple[VotingStrategyVote, ...]
    context_signals: tuple[VotingStrategyVote, ...]
    context_confirmation: VotingContextConfirmation
    counts: dict[str, int]
    eligible_counts: dict[str, int]
    family_scores: dict[str, float] = Field(default_factory=dict)
    base_score: float = 0.0
    context_adjusted_score: float = 0.0
    context_agreements: int = 0
    context_conflicts: int = 0
    context_adjustment_reason: str = ""
    family_support: dict[str, int] = Field(default_factory=dict)
    safety_gate_failed: bool = False
    removed_voters: tuple[str, ...] = ("Ensemble Strategy Voting",)
    reason_codes: tuple[str, ...] = ()
