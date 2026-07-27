"""Regime-owned runtime event contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from backend.app.algorithms.regime.contracts import normalize_regime_runtime_mode
from backend.app.algorithms.regime.runtime_idempotency import deterministic_regime_event_id


REGIME_RUNTIME_EVENT_VERSION = "regime_runtime_events_v1"


@dataclass(frozen=True)
class RegimeFinalisedBarEvent:
    algorithm_id: Literal["regime"]
    algorithm_instance_id: str
    account_id: str
    runtime_mode: Literal["shadow", "paper", "backtest", "replay"]
    symbol: str
    completed_bar_timestamp: datetime
    market_payload: dict[str, Any]
    published_at: datetime
    data_manifest_hash: str | None = None
    settings_version: str | None = None
    event_id: str = ""
    completed: bool = True
    replay_recovery: bool = False

    def __post_init__(self) -> None:
        if self.algorithm_id != "regime":
            raise ValueError("Regime finalised-bar events require algorithm_id=regime")
        if self.symbol.upper() != "SPY":
            raise ValueError("Regime finalised-bar events currently support SPY only")
        runtime_mode = normalize_regime_runtime_mode(self.runtime_mode).value
        completed_ts = _aware(self.completed_bar_timestamp)
        published = _aware(self.published_at)
        object.__setattr__(self, "completed_bar_timestamp", completed_ts)
        object.__setattr__(self, "published_at", published)
        object.__setattr__(self, "runtime_mode", runtime_mode)
        object.__setattr__(self, "symbol", self.symbol.upper())
        if completed_ts.second != 0 or completed_ts.microsecond != 0:
            raise ValueError("Regime finalised-bar events must reference a completed one-minute bar boundary")
        if not self.completed:
            raise ValueError("Regime finalised-bar events must be completed bars")
        if self.data_manifest_hash and self.settings_version:
            object.__setattr__(
                self,
                "event_id",
                deterministic_regime_event_id(
                    algorithm_instance_id=self.algorithm_instance_id,
                    runtime_mode=runtime_mode,
                    symbol=self.symbol,
                    finalised_bar_timestamp=completed_ts.isoformat().replace("+00:00", "Z"),
                    data_manifest_hash=self.data_manifest_hash,
                    settings_version=self.settings_version,
                ),
            )
        elif not self.event_id:
            object.__setattr__(self, "event_id", _hash_payload(self.as_dict(exclude_event_id=True)))

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "RegimeFinalisedBarEvent":
        market_payload = payload.get("marketData") if isinstance(payload.get("marketData"), dict) else payload.get("market_payload")
        if not isinstance(market_payload, dict):
            market_payload = {key: value for key, value in payload.items() if key not in _CONTROL_KEYS}
        timestamp = payload.get("completedBarTimestamp") or payload.get("completed_bar_timestamp") or payload.get("finalisedCandleTimestamp")
        if timestamp is None:
            candles = market_payload.get("primaryCandles") or market_payload.get("candles") or []
            if candles:
                timestamp = candles[-1].get("timestamp")
        return cls(
            algorithm_id="regime",
            algorithm_instance_id=str(payload.get("algorithmInstanceId") or payload.get("algorithm_instance_id") or "regime-default"),
            account_id=str(payload.get("accountId") or payload.get("account_id") or "default"),
            runtime_mode=normalize_regime_runtime_mode(payload.get("runtimeMode") or payload.get("runtime_mode") or "shadow").value,  # type: ignore[arg-type]
            symbol=str(payload.get("symbol") or market_payload.get("symbol") or "SPY").upper(),
            completed_bar_timestamp=_parse_datetime(timestamp),
            market_payload=market_payload,
            published_at=_parse_datetime(payload.get("publishedAt") or payload.get("published_at")),
            data_manifest_hash=str(payload.get("dataManifestHash") or payload.get("data_manifest_hash") or "") or None,
            settings_version=str(payload.get("settingsVersion") or payload.get("settings_version") or "") or None,
            event_id=str(payload.get("eventId") or payload.get("event_id") or ""),
            completed=bool(payload.get("completed", True)),
            replay_recovery=bool(payload.get("replayRecovery") or payload.get("replay_recovery") or False),
        )

    def with_runtime_identity(self, *, data_manifest_hash: str, settings_version: str) -> "RegimeFinalisedBarEvent":
        return replace(self, data_manifest_hash=data_manifest_hash, settings_version=settings_version, event_id="")

    @property
    def identity(self) -> dict[str, str]:
        return {
            "algorithmId": "regime",
            "algorithmInstanceId": self.algorithm_instance_id,
            "accountId": self.account_id,
            "runtimeMode": self.runtime_mode,
            "symbol": self.symbol,
        }

    def as_dict(self, *, exclude_event_id: bool = False) -> dict[str, Any]:
        payload = {
            "algorithmId": self.algorithm_id,
            "algorithmInstanceId": self.algorithm_instance_id,
            "accountId": self.account_id,
            "runtimeMode": self.runtime_mode,
            "symbol": self.symbol,
            "completedBarTimestamp": self.completed_bar_timestamp.isoformat().replace("+00:00", "Z"),
            "publishedAt": self.published_at.isoformat().replace("+00:00", "Z"),
            "dataManifestHash": self.data_manifest_hash,
            "settingsVersion": self.settings_version,
            "marketPayload": self.market_payload,
            "eventId": self.event_id,
            "completed": self.completed,
            "replayRecovery": self.replay_recovery,
            "eventVersion": REGIME_RUNTIME_EVENT_VERSION,
        }
        if exclude_event_id:
            payload.pop("eventId", None)
        return payload


_CONTROL_KEYS = {
    "account",
    "accountState",
    "accountSnapshot",
    "availableBuyingPower",
    "buyingPower",
    "fills",
    "inventory",
    "inventorySnapshot",
    "orders",
    "position",
    "positionState",
    "positionSnapshot",
    "positions",
    "riskCapacity",
    "runtimeState",
    "settings",
    "settingsSnapshot",
    "trades",
    "__regime_settings_snapshot",
    "__regime_inventory_snapshot",
}


def event_payload_has_forbidden_operational_state(payload: dict[str, Any]) -> bool:
    return _contains_forbidden_operational_state(payload)


def _contains_forbidden_operational_state(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in _CONTROL_KEYS:
                return True
            if _contains_forbidden_operational_state(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_operational_state(item) for item in value)
    return False


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _aware(value)
    if not value:
        return datetime.now(timezone.utc).replace(second=0, microsecond=0)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _aware(parsed)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hash_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
    return f"regime-event-{digest}"


__all__ = [
    "REGIME_RUNTIME_EVENT_VERSION",
    "RegimeFinalisedBarEvent",
    "event_payload_has_forbidden_operational_state",
]
