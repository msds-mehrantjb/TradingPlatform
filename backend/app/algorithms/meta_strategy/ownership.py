"""Ownership declarations for the Meta-Strategy package boundary."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID, ALGORITHM_NAME
from backend.app.algorithms.meta_strategy.versions import META_STRATEGY_OWNERSHIP_VERSION


META_STRATEGY_DEFAULT_CAPITAL_PARTITION = "meta_strategy.paper.default"


def assert_meta_strategy_ownership(record: Any) -> None:
    algorithm_id = _algorithm_id(record)
    if algorithm_id != ALGORITHM_ID:
        raise ValueError(f"Meta-Strategy cannot mutate records owned by {algorithm_id or 'unknown'}")


def is_meta_strategy_owned(record: Any) -> bool:
    return _algorithm_id(record) == ALGORITHM_ID


def meta_strategy_ownership_boundary() -> dict[str, Any]:
    return {
        "algorithmId": ALGORITHM_ID,
        "algorithmName": ALGORITHM_NAME,
        "ownershipVersion": META_STRATEGY_OWNERSHIP_VERSION,
        "defaultCapitalPartition": META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
        "mayMutateForeignAlgorithmState": False,
        "mayReadSiblingPrivateState": False,
        "ownsPositions": True,
        "ownsOrderIntents": True,
        "ownsPersistenceNamespace": True,
        "reasonCodes": ("meta_strategy.ownership.boundary_ready",),
        "explanation": "Meta-Strategy records must carry meta_strategy ownership before this package mutates them.",
    }


def _algorithm_id(record: Any) -> str | None:
    if isinstance(record, dict):
        value = record.get("algorithmId", record.get("algorithm_id"))
    else:
        value = getattr(record, "algorithmId", getattr(record, "algorithm_id", None))
    return str(value) if value is not None else None
