"""Regime application service boundary."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.regime.broker_adapter import regime_broker_adapter_inventory
from backend.app.algorithms.regime.global_risk_adapter import regime_global_risk_adapter_inventory
from backend.app.algorithms.regime.repository import RegimeRepository, regime_repository_inventory

REGIME_SERVICE_VERSION = "regime_service_v1"
REGIME_BACKEND_FILE_INVENTORY = (
    "__init__.py",
    "api.py",
    "service.py",
    "repository.py",
    "global_risk_adapter.py",
    "broker_adapter.py",
    "rollout.py",
    "final_acceptance.py",
)


class RegimeApplicationService:
    def __init__(self, repository: RegimeRepository | None = None) -> None:
        self.repository = repository or RegimeRepository()

    def record_decision_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        return self.repository.record_decision_snapshot(snapshot)

    def record_backtest_result(self, result: dict[str, Any]) -> dict[str, Any]:
        return self.repository.record_backtest_result(result)

    def persistence_schema(self) -> dict[str, Any]:
        inventory = self.repository.persistence_inventory()
        return {
            "algorithmId": "regime",
            "ownedTables": inventory["ownedTables"],
            "sharedAttributedTables": inventory["sharedAttributedTables"],
            "requiredSharedAttributionColumns": inventory["requiredSharedAttributionColumns"],
            "ownedVersionColumns": inventory["ownedVersionColumns"],
            "inventoryPassed": inventory["passed"],
            "tables": {table: self.repository.table_columns(table) for table in inventory["ownedTables"] + inventory["sharedAttributedTables"]},
        }

    def backend_inventory(self) -> dict[str, Any]:
        return regime_backend_inventory()


def regime_backend_inventory() -> dict[str, Any]:
    return {
        "algorithmId": "regime",
        "version": REGIME_SERVICE_VERSION,
        "files": REGIME_BACKEND_FILE_INVENTORY,
        "service": "backend.app.algorithms.regime.service.RegimeApplicationService",
        "repository": regime_repository_inventory(),
        "globalRiskAdapter": regime_global_risk_adapter_inventory(),
        "brokerAdapter": regime_broker_adapter_inventory(),
        "apiTransportOnly": True,
    }


__all__ = [
    "REGIME_BACKEND_FILE_INVENTORY",
    "REGIME_SERVICE_VERSION",
    "RegimeApplicationService",
    "regime_backend_inventory",
]
