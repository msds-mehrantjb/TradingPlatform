"""Immutable WCA runtime event contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import Field, model_validator

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, WcaContractModel, WcaMarketSnapshot


WCA_RUNTIME_EVENT_SCHEMA_VERSION = "wca_runtime_finalized_bar_event_v1"
WCA_RUNTIME_SUBSCRIPTION_ID = "wca.spy.one_minute.finalized_bars"


class WcaFinalizedBarEvent(WcaContractModel):
    algorithm_id: str = WCA_ALGORITHM_ID
    schema_version: str = WCA_RUNTIME_EVENT_SCHEMA_VERSION
    event_id: str = Field(min_length=1)
    algorithm_subscription_id: str = Field(default=WCA_RUNTIME_SUBSCRIPTION_ID, min_length=1)
    symbol: str = Field(default="SPY", min_length=1)
    finalized_candle_timestamp: datetime
    data_manifest_hash: str = Field(min_length=1)
    publication_timestamp: datetime
    source: str = Field(min_length=1)
    replay_or_recovery: bool = False
    is_finalized: bool = True
    snapshot: WcaMarketSnapshot | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_runtime_event(self) -> "WcaFinalizedBarEvent":
        if self.algorithm_id != WCA_ALGORITHM_ID:
            raise ValueError("WCA finalized-bar event must be WCA scoped")
        if self.symbol.upper() != "SPY":
            raise ValueError("WCA runtime currently subscribes only to finalized SPY one-minute bars")
        if not self.is_finalized:
            raise ValueError("WCA runtime rejects incomplete finalized-bar events")
        if self.publication_timestamp.astimezone(timezone.utc) < self.finalized_candle_timestamp.astimezone(timezone.utc):
            raise ValueError("publication timestamp cannot precede the finalized candle timestamp")
        if self.snapshot is not None and self.snapshot.symbol != self.symbol:
            raise ValueError("event snapshot symbol must match event symbol")
        return self

    @property
    def checkpoint_key(self) -> str:
        return f"wca.runtime.finalized_bar.{self.symbol}"


def finalized_bar_event_from_payload(payload: dict[str, Any] | str) -> WcaFinalizedBarEvent:
    if isinstance(payload, str):
        return WcaFinalizedBarEvent.model_validate_json(payload)
    return WcaFinalizedBarEvent.model_validate(payload)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "WCA_RUNTIME_EVENT_SCHEMA_VERSION",
    "WCA_RUNTIME_SUBSCRIPTION_ID",
    "WcaFinalizedBarEvent",
    "finalized_bar_event_from_payload",
    "utc_now",
]
