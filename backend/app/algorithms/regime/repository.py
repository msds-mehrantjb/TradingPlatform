"""Regime-owned repository boundary.

The SQLite implementation lives in persistence.py; this module gives the
Regime backend package the explicit repository boundary expected by the
application service and API layer.
"""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.regime.persistence import (
    REGIME_OWNED_TABLES,
    REGIME_PERSISTENCE_TABLES,
    REGIME_SHARED_ATTRIBUTED_TABLES,
    RegimeSqliteRepository,
)

REGIME_REPOSITORY_BOUNDARY_VERSION = "regime_repository_v1"


class RegimeRepository(RegimeSqliteRepository):
    """Regime-specific repository facade over the durable SQLite schema."""


def regime_repository_inventory() -> dict[str, Any]:
    return {
        "algorithmId": "regime",
        "version": REGIME_REPOSITORY_BOUNDARY_VERSION,
        "implementation": "backend.app.algorithms.regime.persistence.RegimeSqliteRepository",
        "ownedTables": REGIME_OWNED_TABLES,
        "sharedAttributedTables": REGIME_SHARED_ATTRIBUTED_TABLES,
        "allTables": REGIME_PERSISTENCE_TABLES,
        "sharedTablesAreInfrastructureOnly": True,
        "sharedInfrastructureIsAttributionOnly": True,
        "authoritativeOrderFillPositionTradeInventory": (
            "regime_inventory_events",
            "regime_inventory_snapshots",
            "regime_orders",
            "regime_fills",
            "regime_positions",
            "regime_trades",
        ),
        "nonAuthoritativeShadowExecutionRecords": (
            "regime_hypothetical_fills",
        ),
        "ownershipKey": ("algorithm_id", "algorithm_instance_id", "account_id", "runtime_mode", "symbol"),
        "optimisticLockingColumn": "sequence_version",
    }


__all__ = [
    "REGIME_REPOSITORY_BOUNDARY_VERSION",
    "RegimeRepository",
    "regime_repository_inventory",
]
