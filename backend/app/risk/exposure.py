"""Neutral exposure contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ExposureSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gross_exposure: float = Field(ge=0)
    net_exposure: float
    open_risk: float = Field(ge=0)
    buying_power: float = Field(ge=0)
