"""Dedicated Regime settings repository boundary."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.regime.persistence import REGIME_ALGORITHM_ID, RegimeSqliteRepository


REGIME_SETTINGS_REPOSITORY_VERSION = "regime_settings_repository_v1"


class RegimeSettingsRepository:
    """Settings-only facade over the Regime-owned persistence store."""

    algorithm_id = REGIME_ALGORITHM_ID

    def __init__(self, repository: RegimeSqliteRepository) -> None:
        self._repository = repository

    def ensure_active_settings_snapshot(self, identity: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._repository.ensure_active_settings_snapshot(_regime_identity(identity))

    def active_settings_snapshot(self, identity: dict[str, Any] | None = None, *, create_default: bool = True) -> dict[str, Any] | None:
        return self._repository.active_settings_snapshot(_regime_identity(identity), create_default=create_default)

    def validate_settings_snapshot_command(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._repository.validate_settings_snapshot_command(_regime_command(command))

    def create_settings_version(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._repository.create_settings_version(_regime_command(command))

    def activate_settings_snapshot(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._repository.activate_settings_snapshot(_regime_command(command))

    def rollback_settings_snapshot(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._repository.rollback_settings_snapshot(_regime_command(command))

    def settings_version_snapshot(self, identity: dict[str, Any], settings_version: str) -> dict[str, Any] | None:
        return self._repository.settings_version_snapshot(_regime_identity(identity), settings_version)


def regime_settings_repository_inventory() -> dict[str, Any]:
    return {
        "algorithmId": REGIME_ALGORITHM_ID,
        "version": REGIME_SETTINGS_REPOSITORY_VERSION,
        "implementation": "backend.app.algorithms.regime.settings_repository.RegimeSettingsRepository",
        "algorithmIdHardCoded": True,
        "ownedTables": ("regime_settings_versions", "regime_active_settings", "regime_strategy_settings"),
        "singleActiveVersionPerMode": True,
        "apiRequestsMaySupplyAuthoritativeRuntimeSettings": False,
    }


def _regime_identity(identity: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(identity or {})
    supplied = payload.get("algorithmId") or payload.get("algorithm_id")
    if supplied is not None and supplied != REGIME_ALGORITHM_ID:
        raise ValueError("Regime settings repository rejects non-regime algorithm_id")
    payload["algorithmId"] = REGIME_ALGORITHM_ID
    return payload


def _regime_command(command: dict[str, Any]) -> dict[str, Any]:
    payload = dict(command or {})
    supplied = payload.get("algorithmId") or payload.get("algorithm_id")
    if supplied is not None and supplied != REGIME_ALGORITHM_ID:
        raise ValueError("Regime settings repository rejects non-regime algorithm_id")
    payload["algorithmId"] = REGIME_ALGORITHM_ID
    if isinstance(payload.get("identity"), dict):
        payload["identity"] = _regime_identity(payload["identity"])
    if isinstance(payload.get("settings"), dict):
        payload["settings"] = _regime_identity(payload["settings"])
        if isinstance(payload["settings"].get("identity"), dict):
            payload["settings"]["identity"] = _regime_identity(payload["settings"]["identity"])
    if isinstance(payload.get("settingsSnapshot"), dict):
        payload["settingsSnapshot"] = _regime_identity(payload["settingsSnapshot"])
        if isinstance(payload["settingsSnapshot"].get("identity"), dict):
            payload["settingsSnapshot"]["identity"] = _regime_identity(payload["settingsSnapshot"]["identity"])
    return payload


__all__ = [
    "REGIME_SETTINGS_REPOSITORY_VERSION",
    "RegimeSettingsRepository",
    "regime_settings_repository_inventory",
]
