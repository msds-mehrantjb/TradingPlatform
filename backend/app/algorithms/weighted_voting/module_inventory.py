"""The complete Weighted Voting module inventory, across every role.

`catalog.WEIGHTED_VOTING_MODULE_INVENTORY` covers the directional strategies, which is the
part that votes. It is not the whole algorithm: the published inventory reported only those
strategies and left safety, regime and aggregation empty, so 29 local gates, the
market-condition classifier and the aggregator were invisible to anything reading the
inventory.

This lives in its own module rather than in `catalog` because `decision_gates` reaches
`catalog` through `config`, so assembling this inside `catalog` would close an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.algorithms.weighted_voting.aggregation import WEIGHTED_VOTING_AGGREGATION_VERSION
from backend.app.algorithms.weighted_voting.catalog import (
    WEIGHTED_VOTING_CATALOG_VERSION,
    weighted_voting_dedicated_strategy_inventory,
)
from backend.app.algorithms.weighted_voting.decision_gates import WEIGHTED_VOTING_LOCAL_GATE_INVENTORY
from backend.app.algorithms.weighted_voting.identity import (
    WEIGHTED_VOTING_ALGORITHM_ID,
    WEIGHTED_VOTING_SERVICE_VERSION,
)
from backend.app.algorithms.weighted_voting.market_condition import (
    WEIGHTED_VOTING_MARKET_CONDITION_VERSION,
)


WEIGHTED_VOTING_FULL_INVENTORY_VERSION = "weighted_voting_full_module_inventory_v1"
WEIGHTED_VOTING_INVENTORY_CONTRACT_VERSION = "weighted_voting_isolated_inventory_contract_v1"

# Weighted Voting has no context voters: every directional strategy reads its own inputs and
# market context enters through the regime classifier instead. Reported as genuinely empty
# rather than omitted, so a reader can tell "none" from "not published".
WEIGHTED_VOTING_CONTEXT_MODULES: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class WeightedVotingSupportModule:
    """A non-directional module: it shapes or blocks a decision but casts no vote."""

    id: str
    name: str
    role: str
    collection: str
    version: str
    status: str = "active"
    enabled: bool = True


WEIGHTED_VOTING_REGIME_MODULES: tuple[WeightedVotingSupportModule, ...] = (
    WeightedVotingSupportModule(
        id="market_condition_classifier",
        name="Market Condition Classifier",
        role="REGIME",
        collection="regime",
        version=WEIGHTED_VOTING_MARKET_CONDITION_VERSION,
    ),
)

WEIGHTED_VOTING_AGGREGATOR_MODULES: tuple[WeightedVotingSupportModule, ...] = (
    WeightedVotingSupportModule(
        id="weighted_signal_aggregator",
        name="Weighted Signal Aggregator",
        role="AGGREGATOR",
        collection="aggregator",
        version=WEIGHTED_VOTING_AGGREGATION_VERSION,
    ),
)

WEIGHTED_VOTING_SAFETY_MODULES: tuple[WeightedVotingSupportModule, ...] = tuple(
    WeightedVotingSupportModule(
        id=gate.gate_id,
        name=gate.gate_name,
        role="SAFETY",
        collection="safety",
        version=WEIGHTED_VOTING_SERVICE_VERSION,
    )
    for gate in WEIGHTED_VOTING_LOCAL_GATE_INVENTORY
)


def _directional_payload(item: Any) -> dict[str, Any]:
    return {
        "id": item.strategy_id,
        "name": item.name,
        "version": item.version,
        "family": getattr(item.family, "value", item.family),
        "role": "DIRECTIONAL",
        "collection": "directional",
        "status": item.lifecycle,
        "enabled": item.enabled,
        "votingInfluence": item.voting_influence,
        "requiredInputs": list(item.required_data),
        "minimumWarmup": int(item.required_candle_history.split(" ", 1)[0]),
        "aliases": [],
    }


def _support_payload(module: WeightedVotingSupportModule) -> dict[str, Any]:
    return {
        "id": module.id,
        "name": module.name,
        "version": module.version,
        "family": module.collection,
        "role": module.role,
        "collection": module.collection,
        "status": module.status,
        "enabled": module.enabled,
        # A gate, classifier or aggregator shapes a decision but casts no weighted vote.
        "votingInfluence": 0.0,
        "requiredInputs": [],
        "aliases": [],
    }


def weighted_voting_module_groups() -> dict[str, list[dict[str, Any]]]:
    """Every module the algorithm runs, grouped by the role it plays."""
    return {
        "directional": [_directional_payload(item) for item in weighted_voting_dedicated_strategy_inventory()],
        "context": [dict(item) for item in WEIGHTED_VOTING_CONTEXT_MODULES],
        "regime": [_support_payload(module) for module in WEIGHTED_VOTING_REGIME_MODULES],
        "safety": [_support_payload(module) for module in WEIGHTED_VOTING_SAFETY_MODULES],
        "aggregator": [_support_payload(module) for module in WEIGHTED_VOTING_AGGREGATOR_MODULES],
    }


def weighted_voting_strategy_counts() -> dict[str, int]:
    """How the directional roster splits by lifecycle, without reading the module list."""
    directional = weighted_voting_dedicated_strategy_inventory()
    return {
        "directional": len(directional),
        "active": sum(1 for item in directional if item.lifecycle == "active"),
        "shadow": sum(1 for item in directional if item.lifecycle == "shadow"),
        "safety": len(WEIGHTED_VOTING_SAFETY_MODULES),
        "regime": len(WEIGHTED_VOTING_REGIME_MODULES),
        "aggregator": len(WEIGHTED_VOTING_AGGREGATOR_MODULES),
    }


def weighted_voting_full_inventory() -> dict[str, Any]:
    """The inventory payload the v2 endpoint serves."""
    return {
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "engineVersion": WEIGHTED_VOTING_SERVICE_VERSION,
        "contractVersion": WEIGHTED_VOTING_INVENTORY_CONTRACT_VERSION,
        "inventoryVersion": WEIGHTED_VOTING_FULL_INVENTORY_VERSION,
        "displayName": "Weighted Voting",
        "isolatedInventory": {
            "catalogVersion": WEIGHTED_VOTING_CATALOG_VERSION,
            "algorithmOwnsInventory": True,
            "capitalPartitionId": f"{WEIGHTED_VOTING_ALGORITHM_ID}.paper.default",
            "settingsNamespace": "weighted_voting.settings",
            "stateNamespace": "weighted_voting.state",
            "persistenceNamespace": "weighted_voting.inventory",
        },
        "strategyCounts": weighted_voting_strategy_counts(),
        "modules": weighted_voting_module_groups(),
    }


__all__ = [
    "WEIGHTED_VOTING_AGGREGATOR_MODULES",
    "WEIGHTED_VOTING_CONTEXT_MODULES",
    "WEIGHTED_VOTING_FULL_INVENTORY_VERSION",
    "WEIGHTED_VOTING_INVENTORY_CONTRACT_VERSION",
    "WEIGHTED_VOTING_REGIME_MODULES",
    "WEIGHTED_VOTING_SAFETY_MODULES",
    "WeightedVotingSupportModule",
    "weighted_voting_full_inventory",
    "weighted_voting_module_groups",
    "weighted_voting_strategy_counts",
]
