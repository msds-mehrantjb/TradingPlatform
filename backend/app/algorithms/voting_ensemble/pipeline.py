from __future__ import annotations

from typing import Any, Literal, Protocol


VOTING_ENSEMBLE_PIPELINE_VERSION = "voting_ensemble_unified_pipeline_v1"
VotingEnsemblePipelineMode = Literal["paper", "manual", "replay", "backtest", "shadow"]


class VotingEnsembleDecisionService(Protocol):
    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


class VotingEnsemblePipeline:
    """Authoritative pre-execution pipeline facade for every execution mode."""

    componentOrder = (
        "snapshot_contract",
        "trading_settings_resolver",
        "regime_classifier",
        "global_and_local_gates",
        "directional_strategies",
        "context_modules",
        "family_aggregator",
        "cost_model",
        "risk_budget",
        "order_planner",
        "execution_simulator_contract",
    )

    def __init__(self, *, service: VotingEnsembleDecisionService | None = None) -> None:
        if service is None:
            from backend.app.algorithms.voting_ensemble.service import VotingEnsembleService

            service = VotingEnsembleService()
        self.service = service

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.run(payload, mode="paper")["decision"]

    def run(self, payload: dict[str, Any], *, mode: VotingEnsemblePipelineMode) -> dict[str, Any]:
        decision = self.service.evaluate(payload)
        order_plan = decision.get("order_plan") if isinstance(decision, dict) else None
        return {
            "algorithmId": "voting_ensemble",
            "pipelineVersion": VOTING_ENSEMBLE_PIPELINE_VERSION,
            "mode": mode,
            "componentOrder": self.componentOrder,
            "decision": decision,
            "preExecutionDecision": _pre_execution_decision(decision),
            "orderPlan": order_plan,
            "modeSpecificResponsibilities": _mode_responsibilities(mode),
            "reasonCodes": [
                "voting_ensemble.pipeline.authoritative_pre_execution_path",
                f"voting_ensemble.pipeline.mode:{mode}",
            ],
        }


def run_voting_ensemble_pipeline(payload: dict[str, Any], *, mode: VotingEnsemblePipelineMode) -> dict[str, Any]:
    return VotingEnsemblePipeline().run(payload, mode=mode)


def _pre_execution_decision(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "finalSignal": decision.get("final_signal"),
        "safetyGateFailed": decision.get("safety_gate_failed"),
        "baseScore": decision.get("base_score"),
        "contextAdjustedScore": decision.get("context_adjusted_score"),
        "familyScores": decision.get("family_scores"),
        "familySupport": decision.get("family_support"),
        "candidate": decision.get("candidate"),
        "riskBudget": decision.get("risk_budget"),
        "orderPlan": decision.get("order_plan"),
        "settingsHash": _settings_hash(decision),
        "reasonCodes": decision.get("reason_codes") or [],
    }


def _settings_hash(decision: dict[str, Any]) -> str | None:
    risk_budget = decision.get("risk_budget")
    if isinstance(risk_budget, dict):
        config = risk_budget.get("config")
        if isinstance(config, dict) and config.get("settingsHash"):
            return str(config["settingsHash"])
    profile = decision.get("resolved_trading_profile")
    if isinstance(profile, dict):
        source_inputs = profile.get("sourceInputs")
        if isinstance(source_inputs, dict) and source_inputs.get("settingsHash"):
            return str(source_inputs["settingsHash"])
    return None


def _mode_responsibilities(mode: VotingEnsemblePipelineMode) -> tuple[str, ...]:
    if mode == "backtest":
        return (
            "historical_event_delivery",
            "simulated_clock",
            "simulated_broker_fills",
            "deterministic_latency_and_cost_scenarios",
            "result_reporting",
        )
    if mode == "replay":
        return ("historical_event_delivery", "simulated_clock", "result_reporting")
    if mode == "shadow":
        return ("diagnostic_capture", "no_active_order_submission")
    return ("paper_order_submission_transport",)
