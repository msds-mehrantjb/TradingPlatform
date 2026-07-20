"""Model artifact helpers for Meta-Strategy models."""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass
from importlib import metadata
from types import MappingProxyType
from typing import Any

from backend.app.algorithms.meta_strategy.models.base import MetaStrategyModelBase
from backend.app.algorithms.meta_strategy.versions import (
    META_STRATEGY_FEATURE_SCHEMA_VERSION,
    META_STRATEGY_MODEL_ARTIFACT_VERSION,
    META_STRATEGY_MODEL_VERSION,
    META_STRATEGY_STRATEGY_CATALOG_VERSION,
)


def stable_json_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MetaStrategyRuntimeModelArtifact:
    artifactId: str
    artifactHash: str
    modelVersion: str
    featureSchemaVersion: str
    featureSchemaHash: str
    labelVersion: str
    strategyCatalogVersion: str
    trainingWindow: MappingProxyType
    validationWindows: tuple[MappingProxyType, ...]
    holdoutWindow: MappingProxyType
    calibrationMethod: str
    economicMetrics: MappingProxyType
    randomSeed: int
    libraryVersions: MappingProxyType
    promotionStatus: str
    rollbackArtifact: MappingProxyType
    payload: MappingProxyType


def model_artifact_payload(
    model: MetaStrategyModelBase,
    *,
    feature_schema_hash: str,
    label_version: str,
    training_window: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "modelVersion": META_STRATEGY_MODEL_VERSION,
        "modelArtifactVersion": META_STRATEGY_MODEL_ARTIFACT_VERSION,
        "modelId": model.model_id,
        "role": model.role,
        "available": model.available,
        "kind": model.kind,
        "featureSchemaHash": feature_schema_hash,
        "labelVersion": label_version,
        "trainingWindow": training_window,
        "calibrationMethod": str((model.calibration or {}).get("method") or "none"),
        "modelPayload": model.fitted_payload,
    }
    return {**payload, "modelHash": stable_json_hash(payload)}


def runtime_model_artifact_payload(
    *,
    artifact_id: str,
    feature_schema_hash: str,
    label_version: str,
    training_window: dict[str, Any],
    validation_windows: list[dict[str, Any]],
    holdout_window: dict[str, Any],
    calibration_method: str,
    economic_metrics: dict[str, Any],
    random_seed: int,
    promotion_status: str,
    rollback_artifact: dict[str, Any],
    models: dict[str, dict[str, Any]],
    feature_schema_version: str = META_STRATEGY_FEATURE_SCHEMA_VERSION,
    model_version: str = META_STRATEGY_MODEL_VERSION,
    model_artifact_version: str = META_STRATEGY_MODEL_ARTIFACT_VERSION,
    strategy_catalog_version: str = META_STRATEGY_STRATEGY_CATALOG_VERSION,
    library_versions: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload = {
        "artifactId": artifact_id,
        "modelVersion": model_version,
        "modelArtifactVersion": model_artifact_version,
        "featureSchemaVersion": feature_schema_version,
        "featureSchemaHash": feature_schema_hash,
        "labelVersion": label_version,
        "strategyCatalogVersion": strategy_catalog_version,
        "trainingWindow": training_window,
        "validationWindows": validation_windows,
        "holdoutWindow": holdout_window,
        "calibrationMethod": calibration_method,
        "economicMetrics": economic_metrics,
        "randomSeed": int(random_seed),
        "libraryVersions": library_versions or model_library_versions(),
        "promotionStatus": promotion_status,
        "rollbackArtifact": rollback_artifact,
        "models": models,
        "approved": promotion_status == "approved",
        "retired": promotion_status == "retired",
    }
    return {**payload, "artifactHash": artifact_hash(payload)}


def artifact_hash(payload: dict[str, Any]) -> str:
    return stable_json_hash({key: value for key, value in payload.items() if key != "artifactHash"})


def freeze_runtime_model_artifact(artifact: dict[str, Any]) -> MetaStrategyRuntimeModelArtifact:
    return MetaStrategyRuntimeModelArtifact(
        artifactId=str(artifact["artifactId"]),
        artifactHash=str(artifact["artifactHash"]),
        modelVersion=str(artifact["modelVersion"]),
        featureSchemaVersion=str(artifact["featureSchemaVersion"]),
        featureSchemaHash=str(artifact["featureSchemaHash"]),
        labelVersion=str(artifact["labelVersion"]),
        strategyCatalogVersion=str(artifact["strategyCatalogVersion"]),
        trainingWindow=_freeze_mapping(artifact["trainingWindow"]),
        validationWindows=tuple(_freeze_mapping(window) for window in artifact["validationWindows"]),
        holdoutWindow=_freeze_mapping(artifact["holdoutWindow"]),
        calibrationMethod=str(artifact["calibrationMethod"]),
        economicMetrics=_freeze_mapping(artifact["economicMetrics"]),
        randomSeed=int(artifact["randomSeed"]),
        libraryVersions=_freeze_mapping(artifact["libraryVersions"]),
        promotionStatus=str(artifact["promotionStatus"]),
        rollbackArtifact=_freeze_mapping(artifact["rollbackArtifact"]),
        payload=_freeze_mapping(artifact),
    )


def model_library_versions() -> dict[str, str]:
    versions = {"python": platform.python_version()}
    for package in ("pydantic", "xgboost", "lightgbm"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "missing"
    return versions


def _freeze_mapping(value: dict[str, Any]) -> MappingProxyType:
    return MappingProxyType(dict(value))


__all__ = [
    "MetaStrategyRuntimeModelArtifact",
    "artifact_hash",
    "freeze_runtime_model_artifact",
    "model_artifact_payload",
    "model_library_versions",
    "runtime_model_artifact_payload",
    "stable_json_hash",
]
