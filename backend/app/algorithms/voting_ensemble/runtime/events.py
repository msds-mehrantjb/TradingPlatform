"""Event contracts for Voting Ensemble runtime ingestion."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.app.algorithms.voting_ensemble.finalized_bar_producer import VotingEnsembleFinalizedBarMarketEvent
from backend.app.algorithms.voting_ensemble.runtime.commands import VotingEnsembleRuntimeCommand, finalized_bar_evaluation_command


class FinalizedOneMinuteBarEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1)
    barEndTimestamp: datetime
    finalized: bool
    settingsHash: str = Field(default="voting_ensemble_default_settings", min_length=1)
    evaluationPayload: dict[str, Any]
    correlationId: str | None = None
    deadlineSeconds: int = Field(default=20, ge=1, le=300)

    @classmethod
    def from_market_event(
        cls,
        event: VotingEnsembleFinalizedBarMarketEvent,
        *,
        settings_hash: str,
        deadline_seconds: int,
    ) -> "FinalizedOneMinuteBarEvent":
        return cls(
            symbol=event.symbol,
            barEndTimestamp=event.barEndTimestamp,
            finalized=True,
            settingsHash=settings_hash,
            evaluationPayload={
                "eventType": event.eventType,
                "marketEvent": event.snapshot(),
                "source": "backend_authoritative_finalized_bar_producer",
                "sourceAuthority": event.sourceAuthority,
            },
            correlationId=event.eventId,
            deadlineSeconds=deadline_seconds,
        )

    def to_command(self) -> VotingEnsembleRuntimeCommand:
        if not self.finalized:
            raise ValueError("Voting Ensemble ignores partial one-minute bars")
        if self.symbol.upper() != "SPY":
            raise ValueError("Voting Ensemble automatic paper evaluation only accepts finalized one-minute SPY bars")
        market_event = self.evaluationPayload.get("marketEvent")
        if isinstance(market_event, dict):
            VotingEnsembleFinalizedBarMarketEvent.model_validate(market_event)
        timeframe = self.evaluationPayload.get("timeframe") or self.evaluationPayload.get("barTimeframe")
        candles = self.evaluationPayload.get("candles")
        if timeframe and str(timeframe) != "1Min":
            raise ValueError("Voting Ensemble automatic paper evaluation requires one-minute bars")
        if isinstance(candles, list) and candles:
            candle_timeframe = candles[-1].get("timeframe") if isinstance(candles[-1], dict) else None
            if candle_timeframe and str(candle_timeframe) != "1Min":
                raise ValueError("Voting Ensemble automatic paper evaluation requires one-minute bars")
        return finalized_bar_evaluation_command(
            self.evaluationPayload,
            symbol=self.symbol,
            bar_end_timestamp=self.barEndTimestamp,
            settings_hash=self.settingsHash,
            correlation_id=self.correlationId,
            deadline_seconds=self.deadlineSeconds,
        )
