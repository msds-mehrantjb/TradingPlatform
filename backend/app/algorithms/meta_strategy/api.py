"""FastAPI boundary for the Meta-Strategy application service."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from backend.app.algorithms.meta_strategy.service import MetaStrategyApplicationService


router = APIRouter(prefix="/api/meta-strategy", tags=["meta-strategy"])
META_STRATEGY_SERVICE = MetaStrategyApplicationService()


@router.get("/status")
def get_meta_strategy_status() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.status()


@router.get("/configuration")
def get_meta_strategy_configuration() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.configuration()


@router.post("/evaluate")
def evaluate_meta_strategy(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.evaluate(payload)


@router.post("/prediction/evaluate")
def evaluate_meta_strategy_prediction(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.predict(payload)


@router.post("/meta-model/predict")
def predict_meta_strategy_model(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.predict(payload)


@router.post("/training/run")
def run_meta_strategy_training(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.train(payload)


@router.post("/artifacts/load")
def load_meta_strategy_artifact(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.load_artifact(payload)


@router.get("/artifacts/status")
def get_meta_strategy_artifact_status() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.status()


@router.post("/backtests/run")
def run_meta_strategy_backtest(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.backtest(payload)


@router.post("/shadow/evaluate")
def evaluate_meta_strategy_shadow(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.shadow_evaluate(payload)


@router.post("/paper/evaluate")
def evaluate_meta_strategy_paper(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.paper_evaluate(payload)


@router.post("/activation/deterministic/evaluate")
def evaluate_meta_strategy_deterministic_activation(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.deterministic_activation(payload)


@router.post("/ml-filter/rollout/evaluate")
def evaluate_meta_strategy_ml_filter_rollout(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.ml_filter_rollout(payload)


@router.post("/dynamic-policy/shadow/evaluate")
def evaluate_meta_strategy_dynamic_policy_shadow(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.dynamic_policy_shadow(payload)


@router.post("/dynamic-policy/activation/evaluate")
def evaluate_meta_strategy_dynamic_policy_activation(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.dynamic_policy_activation(payload)


@router.post("/ml-risk-modifier/experiment/evaluate")
def evaluate_meta_strategy_ml_risk_modifier_experiment(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.ml_risk_modifier_experiment(payload)


@router.post("/promotion/evaluate")
def evaluate_meta_strategy_promotion(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.promote(payload)


@router.post("/paper-stability/validate")
def validate_meta_strategy_paper_stability(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.validate_paper_stability(payload)


@router.get("/diagnostics")
def get_meta_strategy_diagnostics() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.diagnostics()


@router.get("/models/status")
def get_meta_strategy_models_status() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.status()


@router.get("/final-acceptance")
def get_meta_strategy_final_acceptance() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.final_acceptance()


__all__ = [
    "META_STRATEGY_SERVICE",
    "router",
]
