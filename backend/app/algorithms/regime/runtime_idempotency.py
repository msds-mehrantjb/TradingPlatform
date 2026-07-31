"""Durable Regime runtime idempotency contracts."""

from __future__ import annotations

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
    "position_management",
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
    del algorithm_instance_id, data_manifest_hash
    return regime_bar_idempotency_key(
        runtime_mode=runtime_mode,
        symbol=symbol,
        finalised_bar_timestamp=finalised_bar_timestamp,
        algorithm_version="regime_stateful_completed_bar_v1",
        settings_version=settings_version,
    )


def regime_bar_idempotency_key(
    *,
    runtime_mode: str,
    symbol: str,
    finalised_bar_timestamp: str,
    algorithm_version: str,
    settings_version: str,
) -> str:
    return ":".join(
        (
            "regime",
            runtime_mode,
            symbol.upper(),
            finalised_bar_timestamp,
            algorithm_version,
            settings_version,
        )
    )


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
    "regime_bar_idempotency_key",
]
