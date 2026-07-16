"""Neutral account-risk ledger contracts."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class AccountRiskReservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reservation_id: str = Field(min_length=1)
    algorithm_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    planned_risk: float = Field(ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
