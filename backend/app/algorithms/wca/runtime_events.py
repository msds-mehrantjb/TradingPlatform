"""Immutable WCA runtime event contracts."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from pydantic import Field, model_validator

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, WcaContractModel, WcaMarketSnapshot


WCA_RUNTIME_EVENT_SCHEMA_VERSION = "wca_runtime_finalized_bar_event_v1"
WCA_RUNTIME_SUBSCRIPTION_ID = "wca.spy.one_minute.finalized_bars"
WCA_RUNTIME_EVENT_TIMEFRAME = "1Min"


class WcaFinalizedBarEvent(WcaContractModel):
    algorithm_id: str = WCA_ALGORITHM_ID
    schema_version: str = WCA_RUNTIME_EVENT_SCHEMA_VERSION
    event_id: str = Field(min_length=1)
    algorithm_subscription_id: str = Field(default=WCA_RUNTIME_SUBSCRIPTION_ID, min_length=1)
    symbol: str = Field(default="SPY", min_length=1)
    timeframe: str = WCA_RUNTIME_EVENT_TIMEFRAME
    candle_open_timestamp: datetime | None = None
    candle_close_timestamp: datetime | None = None
    finalized_candle_timestamp: datetime
    data_manifest_hash: str = Field(min_length=1)
    publication_timestamp: datetime
    event_version: str = WCA_RUNTIME_EVENT_SCHEMA_VERSION
    market_data_source: str = Field(default="neutral_market_data", min_length=1)
    source: str = Field(min_length=1)
    replay_or_recovery: bool = False
    is_finalized: bool = True
    snapshot: WcaMarketSnapshot | None = None
    immutable_snapshot_reference: str | None = None
    data_readiness_result: str = "UNKNOWN"
    missing_input_reason_codes: tuple[str, ...] = ()
    payload: dict[str, Any] = Field(default_factory=dict)
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def populate_finalized_candle_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        populated = dict(data)
        finalized = populated.get("finalized_candle_timestamp")
        populated.setdefault("timeframe", WCA_RUNTIME_EVENT_TIMEFRAME)
        populated.setdefault("candle_close_timestamp", finalized)
        populated.setdefault("candle_open_timestamp", finalized)
        populated.setdefault("event_version", WCA_RUNTIME_EVENT_SCHEMA_VERSION)
        populated.setdefault("market_data_source", populated.get("source") or "neutral_market_data")
        if populated.get("snapshot") is not None:
            snapshot = populated["snapshot"]
            ready = snapshot.get("data_ready") if isinstance(snapshot, dict) else getattr(snapshot, "data_ready", None)
            populated.setdefault("data_readiness_result", "READY" if ready else "BLOCKED")
        else:
            populated.setdefault("data_readiness_result", "BLOCKED")
        return populated

    @model_validator(mode="after")
    def validate_runtime_event(self) -> "WcaFinalizedBarEvent":
        if self.algorithm_id != WCA_ALGORITHM_ID:
            raise ValueError("WCA finalized-bar event must be WCA scoped")
        if self.symbol.upper() != "SPY":
            raise ValueError("WCA runtime currently subscribes only to finalized SPY one-minute bars")
        if self.timeframe != WCA_RUNTIME_EVENT_TIMEFRAME:
            raise ValueError("WCA runtime finalized-bar events must use timeframe 1Min")
        if not self.is_finalized:
            raise ValueError("WCA runtime rejects incomplete finalized-bar events")
        close_timestamp = self.candle_close_timestamp or self.finalized_candle_timestamp
        open_timestamp = self.candle_open_timestamp or close_timestamp
        if close_timestamp != self.finalized_candle_timestamp:
            raise ValueError("candle close timestamp must match finalized candle timestamp")
        if open_timestamp.astimezone(timezone.utc) > close_timestamp.astimezone(timezone.utc):
            raise ValueError("candle open timestamp cannot be after close/finalisation timestamp")
        if self.publication_timestamp.astimezone(timezone.utc) < self.finalized_candle_timestamp.astimezone(timezone.utc):
            raise ValueError("publication timestamp cannot precede the finalized candle timestamp")
        if self.snapshot is not None and self.snapshot.symbol != self.symbol:
            raise ValueError("event snapshot symbol must match event symbol")
        if self.snapshot is not None and self.snapshot.data_timestamp != self.finalized_candle_timestamp:
            raise ValueError("event snapshot data timestamp must match finalized candle timestamp")
        return self

    @property
    def checkpoint_key(self) -> str:
        return f"wca.runtime.finalized_bar.{self.symbol}"


def deterministic_finalized_bar_event_id(
    *,
    symbol: str,
    timeframe: str,
    candle_timestamp: datetime,
    source: str,
    event_version: str = WCA_RUNTIME_EVENT_SCHEMA_VERSION,
) -> str:
    key = "|".join(
        (
            WCA_ALGORITHM_ID,
            symbol.upper(),
            timeframe,
            candle_timestamp.astimezone(timezone.utc).isoformat(),
            source,
            event_version,
        )
    )
    return f"wca-finalized-1m-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]}"


def finalized_bar_event_from_payload(payload: dict[str, Any] | str) -> WcaFinalizedBarEvent:
    if isinstance(payload, str):
        return WcaFinalizedBarEvent.model_validate_json(payload)
    return WcaFinalizedBarEvent.model_validate(payload)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "WCA_RUNTIME_EVENT_SCHEMA_VERSION",
    "WCA_RUNTIME_EVENT_TIMEFRAME",
    "WCA_RUNTIME_SUBSCRIPTION_ID",
    "WcaFinalizedBarEvent",
    "deterministic_finalized_bar_event_id",
    "finalized_bar_event_from_payload",
    "utc_now",
]
