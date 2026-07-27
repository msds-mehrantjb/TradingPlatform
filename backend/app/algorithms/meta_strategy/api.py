"""FastAPI boundary for the Meta-Strategy application service."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, status

from backend.app.algorithms.meta_strategy.service import MetaStrategyApplicationService


router = APIRouter(prefix="/api/meta-strategy", tags=["meta-strategy"])
META_STRATEGY_SERVICE = MetaStrategyApplicationService()


@router.get("/status")
def get_meta_strategy_status() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.status()


@router.get("/configuration")
def get_meta_strategy_configuration() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.configuration()


@router.post("/configuration/drafts")
def create_meta_strategy_settings_draft(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.create_settings_draft(payload)


@router.post("/configuration/promote", status_code=status.HTTP_202_ACCEPTED)
def promote_meta_strategy_settings_draft(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.promote_settings_draft(payload)


@router.post("/configuration/rollback")
def rollback_meta_strategy_settings(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.rollback_settings(payload)


@router.post("/evaluate", status_code=status.HTTP_202_ACCEPTED)
def evaluate_meta_strategy(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.evaluate(payload)


@router.post("/prediction/evaluate", status_code=status.HTTP_202_ACCEPTED)
def evaluate_meta_strategy_prediction(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.predict(payload)


@router.post("/meta-model/predict", status_code=status.HTTP_202_ACCEPTED)
def predict_meta_strategy_model(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.predict(payload)


@router.post("/training/run", status_code=status.HTTP_202_ACCEPTED)
def run_meta_strategy_training(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.train(payload)


@router.post("/artifacts/load")
def load_meta_strategy_artifact(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.load_artifact(payload)


@router.get("/artifacts/status")
def get_meta_strategy_artifact_status() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.status()


@router.post("/backtests/run", status_code=status.HTTP_202_ACCEPTED)
def run_meta_strategy_backtest(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.backtest(payload)


@router.post("/replay/run", status_code=status.HTTP_202_ACCEPTED)
def run_meta_strategy_deterministic_replay(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.deterministic_replay(payload)


@router.post("/walk-forward/run", status_code=status.HTTP_202_ACCEPTED)
def run_meta_strategy_walk_forward(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.walk_forward_evaluation(payload)


@router.post("/holdout/run", status_code=status.HTTP_202_ACCEPTED)
def run_meta_strategy_holdout(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.holdout_evaluation(payload)


@router.post("/cost-slippage/run", status_code=status.HTTP_202_ACCEPTED)
def run_meta_strategy_cost_slippage_analysis(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.cost_slippage_analysis(payload)


@router.post("/model-inference/validate", status_code=status.HTTP_202_ACCEPTED)
def validate_meta_strategy_model_inference(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.model_inference_validation(payload)


@router.post("/shadow/evaluate", status_code=status.HTTP_202_ACCEPTED)
def evaluate_meta_strategy_shadow(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.shadow_evaluate(payload)


@router.post("/paper/evaluate", status_code=status.HTTP_202_ACCEPTED)
def evaluate_meta_strategy_paper(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.paper_evaluate(payload)


@router.post("/events/finalised-bars", status_code=status.HTTP_202_ACCEPTED)
def enqueue_meta_strategy_finalised_bar(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.enqueue_finalised_bar(payload)


@router.post("/activation/deterministic/evaluate", status_code=status.HTTP_202_ACCEPTED)
def evaluate_meta_strategy_deterministic_activation(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.deterministic_activation(payload)


@router.post("/ml-filter/rollout/evaluate", status_code=status.HTTP_202_ACCEPTED)
def evaluate_meta_strategy_ml_filter_rollout(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.ml_filter_rollout(payload)


@router.post("/dynamic-policy/shadow/evaluate", status_code=status.HTTP_202_ACCEPTED)
def evaluate_meta_strategy_dynamic_policy_shadow(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.dynamic_policy_shadow(payload)


@router.post("/dynamic-policy/activation/evaluate", status_code=status.HTTP_202_ACCEPTED)
def evaluate_meta_strategy_dynamic_policy_activation(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.dynamic_policy_activation(payload)


@router.post("/ml-risk-modifier/experiment/evaluate", status_code=status.HTTP_202_ACCEPTED)
def evaluate_meta_strategy_ml_risk_modifier_experiment(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.ml_risk_modifier_experiment(payload)


@router.post("/promotion/evaluate", status_code=status.HTTP_202_ACCEPTED)
def evaluate_meta_strategy_promotion(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.promote(payload)


@router.post("/paper-stability/validate", status_code=status.HTTP_202_ACCEPTED)
def validate_meta_strategy_paper_stability(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.validate_paper_stability(payload)


@router.post("/reports/generate", status_code=status.HTTP_202_ACCEPTED)
def generate_meta_strategy_report(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.generate_report(payload)


@router.post("/reconciliation/run", status_code=status.HTTP_202_ACCEPTED)
def run_meta_strategy_reconciliation(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.reconciliation(payload)


@router.get("/jobs/status")
def get_meta_strategy_job_status() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.job_status({})


@router.get("/jobs/status/{queue_name}")
def get_meta_strategy_queue_status(queue_name: str) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.job_status({"queueName": queue_name})


@router.get("/jobs/{job_id}")
def get_meta_strategy_job(job_id: str) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.query_job(job_id)


@router.get("/jobs/{job_id}/progress")
def get_meta_strategy_job_progress(job_id: str) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.query_job_progress(job_id)


@router.get("/jobs/{job_id}/results")
def get_meta_strategy_job_results(job_id: str) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.query_job_results(job_id)


@router.post("/jobs/{job_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_meta_strategy_job(job_id: str) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.cancel_job(job_id)


@router.get("/settings/active")
def get_meta_strategy_active_settings() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.query_settings_active()


@router.get("/settings/history")
def get_meta_strategy_settings_history() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.query_settings_history()


@router.get("/settings/effective-profile")
def get_meta_strategy_effective_profile() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.query_effective_profile()


@router.get("/models/active")
def get_meta_strategy_active_model() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.query_model_active()


@router.get("/models/history")
def get_meta_strategy_model_history() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.query_model_history()


@router.get("/inventory")
def get_meta_strategy_inventory() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.query_inventory()


@router.get("/positions")
def get_meta_strategy_positions() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.query_inventory_records("positions")


@router.get("/orders")
def get_meta_strategy_orders() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.query_inventory_records("orders")


@router.get("/fills")
def get_meta_strategy_fills() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.query_inventory_records("fills")


@router.get("/trades")
def get_meta_strategy_trades() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.query_inventory_records("trades")


@router.get("/pnl")
def get_meta_strategy_pnl() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.query_pnl()


@router.get("/risk-reservations")
def get_meta_strategy_risk_reservations() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.query_inventory_records("risk_reservations")


@router.get("/workers/health")
def get_meta_strategy_worker_health() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.worker_health()


@router.get("/queues/lag")
def get_meta_strategy_queue_lag() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.queue_lag()


@router.get("/observability")
def get_meta_strategy_observability() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.observability()


@router.get("/readiness")
def get_meta_strategy_readiness() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.readiness_report()


@router.post("/controls/{control_name}", status_code=status.HTTP_202_ACCEPTED)
def apply_meta_strategy_control(control_name: str, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.apply_control(control_name, payload)


@router.post("/evidence/tests")
def record_meta_strategy_test_evidence(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return META_STRATEGY_SERVICE.record_test_evidence(payload)


@router.get("/decisions/blocked")
def get_meta_strategy_blocked_decisions() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.blocked_decisions()


@router.get("/api-docs")
def get_meta_strategy_api_documentation() -> dict[str, Any]:
    return META_STRATEGY_SERVICE.api_documentation()


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
