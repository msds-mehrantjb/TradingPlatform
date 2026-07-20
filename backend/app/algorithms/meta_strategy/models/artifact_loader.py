"""Artifact loading and validation for Meta-Strategy models."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.app.algorithms.meta_strategy.models.artifact import (
    MetaStrategyRuntimeModelArtifact,
    artifact_hash,
    freeze_runtime_model_artifact,
    stable_json_hash,
)


REQUIRED_RUNTIME_ARTIFACT_FIELDS = (
    "artifactId",
    "artifactHash",
    "modelVersion",
    "featureSchemaVersion",
    "featureSchemaHash",
    "labelVersion",
    "strategyCatalogVersion",
    "trainingWindow",
    "validationWindows",
    "holdoutWindow",
    "calibrationMethod",
    "economicMetrics",
    "randomSeed",
    "libraryVersions",
    "promotionStatus",
    "rollbackArtifact",
    "models",
)

RUNTIME_ARTIFACT_RULE_IDS = (
    "required_runtime_artifact_fields",
    "feature_schema_hash_compatibility",
    "approved_promotion_status",
    "retired_artifact_rejection",
    "rollback_artifact_required",
    "validation_windows_required",
    "non_empty_training_holdout_metrics_and_library_metadata",
    "runtime_artifact_hash_integrity",
    "nested_model_feature_schema_compatibility",
    "nested_model_hash_integrity",
    "runtime_artifact_immutability",
)


def load_meta_strategy_model_artifact(path: Path, *, expected_feature_schema_hash: str) -> dict[str, Any]:
    return load_meta_strategy_model_artifact_data(
        json.loads(path.read_text(encoding="utf-8")),
        expected_feature_schema_hash=expected_feature_schema_hash,
    )


def load_meta_strategy_model_artifact_data(artifact: dict[str, Any], *, expected_feature_schema_hash: str) -> dict[str, Any]:
    actual = str(artifact.get("featureSchemaHash") or "")
    if actual != expected_feature_schema_hash:
        raise ValueError(f"Meta-strategy artifact feature schema mismatch: expected {expected_feature_schema_hash}, got {actual or 'missing'}")
    if artifact.get("artifactHash"):
        validate_runtime_artifact_hash(artifact)
    for model_name, model in (artifact.get("models") or {}).items():
        model_schema_hash = str(model.get("featureSchemaHash") or "")
        if model_schema_hash and model_schema_hash != expected_feature_schema_hash:
            raise ValueError(f"Meta-strategy artifact model {model_name} uses a different feature schema")
        model_hash = model.get("modelHash")
        if model_hash and model_hash != stable_json_hash({key: value for key, value in model.items() if key != "modelHash"}):
            raise ValueError(f"Meta-strategy artifact model hash mismatch for {model_name}")
    return artifact


def load_runtime_model_artifact(path: Path, *, expected_feature_schema_hash: str) -> MetaStrategyRuntimeModelArtifact:
    return load_runtime_model_artifact_data(
        json.loads(path.read_text(encoding="utf-8")),
        expected_feature_schema_hash=expected_feature_schema_hash,
    )


def load_runtime_model_artifact_data(artifact: dict[str, Any], *, expected_feature_schema_hash: str) -> MetaStrategyRuntimeModelArtifact:
    validate_runtime_artifact_manifest(artifact, expected_feature_schema_hash=expected_feature_schema_hash)
    return freeze_runtime_model_artifact(artifact)


def validate_runtime_artifact_manifest(artifact: dict[str, Any], *, expected_feature_schema_hash: str) -> tuple[str, ...]:
    missing = [field for field in REQUIRED_RUNTIME_ARTIFACT_FIELDS if field not in artifact]
    if missing:
        raise ValueError(f"Meta-strategy runtime artifact missing mandatory fields: {', '.join(missing)}")
    if str(artifact["featureSchemaHash"]) != expected_feature_schema_hash:
        raise ValueError(
            f"Meta-strategy runtime artifact feature schema mismatch: expected {expected_feature_schema_hash}, got {artifact['featureSchemaHash']}"
        )
    if str(artifact.get("promotionStatus")) != "approved" or artifact.get("approved") is not True:
        raise ValueError("Meta-strategy runtime artifact is not approved for loading")
    if artifact.get("retired") is True or str(artifact.get("promotionStatus")) == "retired":
        raise ValueError("Meta-strategy runtime artifact is retired and cannot load")
    if not isinstance(artifact.get("rollbackArtifact"), dict) or not artifact["rollbackArtifact"].get("artifactId"):
        raise ValueError("Meta-strategy runtime artifact requires a rollback artifact")
    if not isinstance(artifact.get("validationWindows"), list) or not artifact["validationWindows"]:
        raise ValueError("Meta-strategy runtime artifact requires validation windows")
    for field in ("trainingWindow", "holdoutWindow", "economicMetrics", "libraryVersions"):
        if not isinstance(artifact.get(field), dict) or not artifact[field]:
            raise ValueError(f"Meta-strategy runtime artifact requires non-empty {field}")
    validate_runtime_artifact_hash(artifact)
    load_meta_strategy_model_artifact_data(artifact, expected_feature_schema_hash=expected_feature_schema_hash)
    return ("meta_strategy.runtime_artifact.valid",)


def validate_runtime_artifact_hash(artifact: dict[str, Any]) -> None:
    expected = artifact_hash(artifact)
    actual = str(artifact.get("artifactHash") or "")
    if actual != expected:
        raise ValueError("Meta-strategy runtime artifact hash mismatch")


__all__ = [
    "REQUIRED_RUNTIME_ARTIFACT_FIELDS",
    "RUNTIME_ARTIFACT_RULE_IDS",
    "load_meta_strategy_model_artifact",
    "load_meta_strategy_model_artifact_data",
    "load_runtime_model_artifact",
    "load_runtime_model_artifact_data",
    "validate_runtime_artifact_hash",
    "validate_runtime_artifact_manifest",
]
