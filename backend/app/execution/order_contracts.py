"""Neutral order contracts shared by backend algorithms."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderIntentStatus(str, Enum):
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    SUBMITTED_PAPER = "SUBMITTED_PAPER"


class OrderIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    order_intent_id: str = Field(min_length=1)
    algorithm_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: OrderSide
    quantity: int = Field(ge=0)
    limit_price: float | None = Field(default=None, gt=0)
    status: OrderIntentStatus = OrderIntentStatus.CREATED
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
