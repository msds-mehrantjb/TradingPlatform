"""Validation helpers for the Meta-Strategy package boundary."""

from __future__ import annotations

from typing import Iterable

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyBoundaryManifest, meta_strategy_version_compatibility
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID, ALGORITHM_NAME
from backend.app.algorithms.meta_strategy.versions import META_STRATEGY_VALIDATION_VERSION


def validate_meta_strategy_identity() -> dict[str, object]:
    return {
        "algorithmId": ALGORITHM_ID,
        "algorithmName": ALGORITHM_NAME,
        "validationVersion": META_STRATEGY_VALIDATION_VERSION,
        "valid": ALGORITHM_ID == "meta_strategy" and ALGORITHM_NAME == "Meta-Strategy",
        "reasonCodes": ("meta_strategy.validation.identity_ready",),
    }


def validate_algorithm_id_unique(existing_algorithm_ids: Iterable[str]) -> dict[str, object]:
    ids = [str(value) for value in existing_algorithm_ids]
    duplicates = sorted(value for value in set(ids) if ids.count(value) > 1)
    conflicts = [value for value in ids if value == ALGORITHM_ID]
    valid = len(conflicts) == 1 and not duplicates
    return {
        "algorithmId": ALGORITHM_ID,
        "validationVersion": META_STRATEGY_VALIDATION_VERSION,
        "valid": valid,
        "conflicts": conflicts[1:],
        "duplicates": duplicates,
        "reasonCodes": ("meta_strategy.validation.algorithm_id_unique" if valid else "meta_strategy.validation.algorithm_id_conflict",),
    }


def validate_boundary_manifest(manifest: MetaStrategyBoundaryManifest) -> dict[str, object]:
    compatibility = meta_strategy_version_compatibility(manifest.versions)
    valid = (
        manifest.algorithmId == ALGORITHM_ID
        and manifest.algorithmName == ALGORITHM_NAME
        and not manifest.productionBehaviorChanged
        and bool(compatibility["valid"])
        and bool(manifest.ownedCapabilities)
        and bool(manifest.allowedSharedServices)
        and bool(manifest.forbiddenPrivateState)
    )
    return {
        "algorithmId": ALGORITHM_ID,
        "validationVersion": META_STRATEGY_VALIDATION_VERSION,
        "valid": valid,
        "versionCompatibility": compatibility,
        "reasonCodes": ("meta_strategy.validation.boundary_manifest_ready" if valid else "meta_strategy.validation.boundary_manifest_invalid",),
    }
