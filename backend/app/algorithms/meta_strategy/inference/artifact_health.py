"""Artifact and champion model health checks for Meta-Strategy inference."""

from __future__ import annotations

from typing import Any


def select_champion_model(model_artifact: dict[str, Any] | None) -> dict[str, Any] | None:
    if not model_artifact:
        return None
    models = model_artifact.get("models") or {}
    champion_name = str(model_artifact.get("championModel") or "logistic_regression_champion")
    model = models.get(champion_name) or models.get("logistic_regression_champion")
    if not model or model.get("available") is False:
        return None
    return model


def artifact_schema_compatible(model_artifact: dict[str, Any] | None, expected_schema_hash: str) -> bool:
    if not model_artifact:
        return False
    actual = str(model_artifact.get("featureSchemaHash") or "")
    return bool(actual and actual == expected_schema_hash)


def model_health_status(model_artifact: dict[str, Any] | None, model: dict[str, Any] | None) -> dict[str, Any]:
    if not model_artifact:
        return {"status": "UNAVAILABLE", "score": 0.0, "reasonCodes": ("meta_strategy.inference.artifact_missing",)}
    if not model:
        return {"status": "UNAVAILABLE", "score": 0.0, "reasonCodes": ("meta_strategy.inference.champion_unavailable",)}
    if model.get("available") is False:
        return {"status": "UNAVAILABLE", "score": 0.0, "reasonCodes": ("meta_strategy.inference.model_unavailable",)}
    explicit_score = model.get("modelHealthScore", model_artifact.get("modelHealthScore"))
    score = _bounded(float(explicit_score)) if explicit_score is not None else (1.0 if model.get("modelHash") or model.get("fixedProbabilities") else 0.75)
    return {"status": "OK" if score >= 0.7 else "DEGRADED", "score": score, "reasonCodes": ()}


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


__all__ = ["artifact_schema_compatible", "model_health_status", "select_champion_model"]
