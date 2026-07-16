"""Neutral reconciliation boundary."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ReconciliationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reconciled: bool
    reason_codes: tuple[str, ...] = ()
