"""Event contracts for Voting Ensemble runtime ingestion."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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

    def to_command(self) -> VotingEnsembleRuntimeCommand:
        if not self.finalized:
            raise ValueError("Voting Ensemble ignores partial one-minute bars")
        return finalized_bar_evaluation_command(
            self.evaluationPayload,
            symbol=self.symbol,
            bar_end_timestamp=self.barEndTimestamp,
            settings_hash=self.settingsHash,
            correlation_id=self.correlationId,
            deadline_seconds=self.deadlineSeconds,
        )
