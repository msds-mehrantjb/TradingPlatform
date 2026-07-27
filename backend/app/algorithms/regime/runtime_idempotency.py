"""Durable Regime runtime idempotency contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Any


REGIME_RUNTIME_IDEMPOTENCY_VERSION = "regime_runtime_idempotency_v1"

REGIME_RUNTIME_STAGES = (
    "event_received",
    "snapshot_validated",
    "decision_completed",
    "decision_persisted",
    "risk_requested",
    "risk_reserved",
    "outbox_created",
    "order_submitted",
    "broker_acknowledged",
    "fill_observed",
    "inventory_reconciled",
    "position_closed",
)

ECONOMIC_IDEMPOTENCY_STAGES = (
    "decision_persisted",
    "risk_reserved",
    "outbox_created",
    "order_submitted",
    "fill_observed",
    "position_closed",
)


def deterministic_regime_event_id(
    *,
    algorithm_instance_id: str,
    runtime_mode: str,
    symbol: str,
    finalised_bar_timestamp: str,
    data_manifest_hash: str,
    settings_version: str,
) -> str:
    encoded = json.dumps(
        {
            "algorithmInstanceId": algorithm_instance_id,
            "runtimeMode": runtime_mode,
            "symbol": symbol.upper(),
            "finalisedBarTimestamp": finalised_bar_timestamp,
            "dataManifestHash": data_manifest_hash,
            "settingsVersion": settings_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]
    return f"regime-event-{digest}"


def event_identity_payload(
    *,
    algorithm_instance_id: str,
    account_id: str,
    runtime_mode: str,
    symbol: str,
    finalised_bar_timestamp: str,
    data_manifest_hash: str,
    settings_version: str,
) -> dict[str, Any]:
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": algorithm_instance_id,
        "accountId": account_id,
        "runtimeMode": runtime_mode,
        "symbol": symbol.upper(),
        "finalisedBarTimestamp": finalised_bar_timestamp,
        "dataManifestHash": data_manifest_hash,
        "settingsVersion": settings_version,
    }


__all__ = [
    "ECONOMIC_IDEMPOTENCY_STAGES",
    "REGIME_RUNTIME_IDEMPOTENCY_VERSION",
    "REGIME_RUNTIME_STAGES",
    "deterministic_regime_event_id",
    "event_identity_payload",
]
