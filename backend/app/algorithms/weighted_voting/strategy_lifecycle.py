"""After-market promotion and demotion gates for Weighted Voting strategies."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Literal

from backend.app.algorithms.weighted_voting.catalog import (
    WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS,
    WEIGHTED_VOTING_CATALOG_VERSION,
    WEIGHTED_VOTING_SHADOW_STRATEGY_IDS,
    WEIGHTED_VOTING_STRATEGY_CATALOG,
    WeightedVotingStrategyLifecycleStatus,
)
from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.persistence import WeightedVotingStateStore


WEIGHTED_VOTING_STRATEGY_LIFECYCLE_VERSION = "weighted_voting_strategy_lifecycle_v1"
WEIGHTED_VOTING_STRATEGY_LIFECYCLE_LATEST_KEY = "weighted_voting.strategy_lifecycle.snapshot.latest"
WEIGHTED_VOTING_STRATEGY_LIFECYCLE_SNAPSHOT_PREFIX = "weighted_voting.strategy_lifecycle.snapshot."
WEIGHTED_VOTING_STRATEGY_LIFECYCLE_AUDIT_PREFIX = "weighted_voting.strategy_lifecycle.audit."
WEIGHTED_VOTING_LIFECYCLE_AFTER_MARKET_WORKFLOW = "after_market_admin"
WEIGHTED_VOTING_PROMOTION_PRIORITY_CANDIDATES = ("S1", "S3")


@dataclass(frozen=True)
class WeightedVotingStrategyLifecycleRequirements:
    minimum_eligible_opportunities: int = 120
    minimum_completed_trades: int = 40
    minimum_net_expectancy_after_costs: float = 0.0
    minimum_conservative_expectancy_lower_bound: float = 0.0
    maximum_drawdown: float = 0.08
    minimum_mae_quality: float = 0.55
    minimum_mfe_quality: float = 0.55
    minimum_walk_forward_stability: float = 0.60
    minimum_holdout_stability: float = 0.60
    minimum_paper_shadow_stability: float = 0.60
    minimum_session_consistency: float = 0.60
    minimum_regime_consistency: float = 0.60
    maximum_correlation_with_active: float = 0.70
    minimum_incremental_portfolio_value: float = 0.0
    minimum_data_quality_stability: float = 0.95
    demotion_recent_expectancy_floor: float = -0.001
    demotion_maximum_drawdown: float = 0.12
    demotion_minimum_data_readiness: float = 0.90
    demotion_maximum_execution_cost_edge_ratio: float = 0.70
    demotion_maximum_paper_backtest_divergence: float = 0.25
    demotion_maximum_strategy_error_rate: float = 0.02


@dataclass(frozen=True)
class WeightedVotingStrategyLifecycleEvidence:
    algorithm_id: Literal["weighted_voting"]
    strategy_id: str
    evidence_id: str
    evaluated_at: datetime
    workflow: str
    after_market_session_complete: bool
    eligible_opportunities: int
    completed_trades: int
    net_expectancy_after_costs: float
    conservative_expectancy_lower_bound: float
    maximum_drawdown: float
    mae_quality: float
    mfe_quality: float
    walk_forward_stability: float
    holdout_stability: float
    paper_shadow_stability: float
    session_consistency: float
    regime_consistency: float
    severe_tail_loss_pattern: bool
    correlation_with_active_strategies: float
    incremental_portfolio_value: float
    data_quality_stability: float
    recent_net_expectancy_after_costs: float
    data_readiness_rate: float
    execution_cost_edge_ratio: float
    paper_backtest_divergence: float
    strategy_error_rate: float
    duplicate_of_strategy_id: str | None = None
    duplicate_correlation: float | None = None
    breakout_family_correlation: float | None = None
    requested_lifecycle: WeightedVotingStrategyLifecycleStatus | None = None
    evidence_version: str = "weighted_voting_strategy_lifecycle_evidence_v1"

    def __post_init__(self) -> None:
        if self.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("foreign algorithm evidence cannot change Weighted Voting strategy lifecycle")
        if self.strategy_id not in _catalog_ids():
            raise ValueError(f"unknown Weighted Voting strategy lifecycle evidence target: {self.strategy_id}")


@dataclass(frozen=True)
class WeightedVotingStrategyLifecycleGate:
    gate_id: str
    passed: bool
    actual: float | int | bool | str | None
    required: float | int | bool | str | None
    reason_codes: tuple[str, ...]
    explanation: str


@dataclass(frozen=True)
class WeightedVotingStrategyLifecycleSnapshot:
    algorithm_id: Literal["weighted_voting"]
    lifecycle_version: str
    catalog_version: str
    strategy_states: dict[str, WeightedVotingStrategyLifecycleStatus]
    created_at: datetime
    previous_lifecycle_version: str | None
    approval_evidence_id: str | None
    reason_codes: tuple[str, ...]
    explanation: str

    def as_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class WeightedVotingStrategyLifecycleDecision:
    algorithm_id: Literal["weighted_voting"]
    decision_id: str
    strategy_id: str
    action: Literal["promote", "demote", "disable", "no_change", "reject"]
    previous_lifecycle: WeightedVotingStrategyLifecycleStatus
    target_lifecycle: WeightedVotingStrategyLifecycleStatus
    approved: bool
    evidence_id: str
    evaluated_at: datetime
    previous_lifecycle_version: str
    candidate_lifecycle_version: str
    gates: tuple[WeightedVotingStrategyLifecycleGate, ...]
    reason_codes: tuple[str, ...]
    explanation: str

    def as_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


def strategy_lifecycle_status() -> dict[str, Any]:
    return {
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "lifecycleVersion": WEIGHTED_VOTING_STRATEGY_LIFECYCLE_VERSION,
        "latestSnapshotKey": WEIGHTED_VOTING_STRATEGY_LIFECYCLE_LATEST_KEY,
        "auditPrefix": WEIGHTED_VOTING_STRATEGY_LIFECYCLE_AUDIT_PREFIX,
        "workflow": WEIGHTED_VOTING_LIFECYCLE_AFTER_MARKET_WORKFLOW,
        "promotionPriorityCandidates": WEIGHTED_VOTING_PROMOTION_PRIORITY_CANDIDATES,
        "activeStrategies": WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS,
        "shadowStrategies": WEIGHTED_VOTING_SHADOW_STRATEGY_IDS,
        "rules": (
            "rule_based_only_no_ml",
            "after_market_admin_only",
            "promotion_requires_evidence",
            "no_intraday_self_promotion",
            "rollback_restores_prior_lifecycle_version",
            "immutable_audit_for_every_lifecycle_change",
        ),
    }


def initial_strategy_lifecycle_snapshot(*, created_at: datetime | None = None) -> WeightedVotingStrategyLifecycleSnapshot:
    created = created_at or datetime.now(timezone.utc)
    return WeightedVotingStrategyLifecycleSnapshot(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        lifecycle_version=f"{WEIGHTED_VOTING_CATALOG_VERSION}.lifecycle.initial",
        catalog_version=WEIGHTED_VOTING_CATALOG_VERSION,
        strategy_states={entry.strategy_id: entry.lifecycle for entry in WEIGHTED_VOTING_STRATEGY_CATALOG},
        created_at=created,
        previous_lifecycle_version=None,
        approval_evidence_id=None,
        reason_codes=("weighted_voting.strategy_lifecycle.initial_catalog",),
        explanation="Initial Weighted Voting strategy lifecycle snapshot mirrors the static authoritative catalogue.",
    )


def evaluate_strategy_lifecycle_change(
    evidence: WeightedVotingStrategyLifecycleEvidence | None,
    *,
    current_snapshot: WeightedVotingStrategyLifecycleSnapshot | None = None,
    requirements: WeightedVotingStrategyLifecycleRequirements | None = None,
) -> WeightedVotingStrategyLifecycleDecision:
    active_requirements = requirements or WeightedVotingStrategyLifecycleRequirements()
    snapshot = current_snapshot or initial_strategy_lifecycle_snapshot(created_at=(evidence.evaluated_at if evidence else None))
    if evidence is None:
        return _reject_without_evidence(snapshot, active_requirements)
    _validate_owned_snapshot(snapshot)
    previous = snapshot.strategy_states[evidence.strategy_id]
    target = _target_lifecycle(previous, evidence)
    if not _workflow_allowed(evidence):
        gates = (
            _gate(
                "after_market_admin_workflow",
                False,
                evidence.workflow,
                WEIGHTED_VOTING_LIFECYCLE_AFTER_MARKET_WORKFLOW,
                "weighted_voting.strategy_lifecycle.after_market_required",
                "Lifecycle changes are restricted to the after-market administrative workflow.",
            ),
        )
        return _decision(evidence, snapshot, previous, previous, "reject", False, gates)
    if previous == "shadow" and target == "active":
        gates = _promotion_gates(evidence, active_requirements)
        return _decision(evidence, snapshot, previous, target, "promote", all(gate.passed for gate in gates), gates)
    if previous == "active":
        gates = _demotion_gates(evidence, active_requirements)
        failed = tuple(gate for gate in gates if not gate.passed)
        if not failed:
            return _decision(evidence, snapshot, previous, previous, "no_change", False, gates)
        demotion_target: WeightedVotingStrategyLifecycleStatus = "disabled" if _severe_disable(failed) else "shadow"
        action: Literal["demote", "disable"] = "disable" if demotion_target == "disabled" else "demote"
        return _decision(evidence, snapshot, previous, demotion_target, action, True, gates)
    gates = (
        _gate(
            "supported_transition",
            False,
            f"{previous}->{target}",
            "shadow->active or active demotion",
            "weighted_voting.strategy_lifecycle.unsupported_transition",
            "Only shadow promotion and active demotion/disable transitions are supported by this workflow.",
        ),
    )
    return _decision(evidence, snapshot, previous, previous, "reject", False, gates)


def apply_strategy_lifecycle_decision(
    store: WeightedVotingStateStore,
    decision: WeightedVotingStrategyLifecycleDecision,
    *,
    current_snapshot: WeightedVotingStrategyLifecycleSnapshot | None = None,
    approved_by: str,
) -> WeightedVotingStrategyLifecycleSnapshot:
    if decision.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
        raise ValueError("foreign lifecycle decision cannot be applied to Weighted Voting")
    if not approved_by:
        raise ValueError("Weighted Voting lifecycle changes require an approving operator")
    snapshot = current_snapshot or initial_strategy_lifecycle_snapshot(created_at=decision.evaluated_at)
    _validate_owned_snapshot(snapshot)
    _write_immutable_snapshot(store, _audit_key(decision.decision_id), _audit_payload(decision, approved_by))
    if not decision.approved:
        return snapshot
    states = dict(snapshot.strategy_states)
    states[decision.strategy_id] = decision.target_lifecycle
    updated = WeightedVotingStrategyLifecycleSnapshot(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        lifecycle_version=decision.candidate_lifecycle_version,
        catalog_version=WEIGHTED_VOTING_CATALOG_VERSION,
        strategy_states=states,
        created_at=decision.evaluated_at,
        previous_lifecycle_version=snapshot.lifecycle_version,
        approval_evidence_id=decision.evidence_id,
        reason_codes=("weighted_voting.strategy_lifecycle.change_applied", *decision.reason_codes),
        explanation=f"Weighted Voting strategy {decision.strategy_id} lifecycle changed from {decision.previous_lifecycle} to {decision.target_lifecycle}.",
    )
    store.write_snapshot(WEIGHTED_VOTING_STRATEGY_LIFECYCLE_LATEST_KEY, updated.as_dict())
    _write_immutable_snapshot(store, _snapshot_key(updated.lifecycle_version), updated.as_dict())
    return updated


def rollback_strategy_lifecycle_version(
    store: WeightedVotingStateStore,
    *,
    target_lifecycle_version: str,
    rolled_back_at: datetime,
    approved_by: str,
) -> WeightedVotingStrategyLifecycleSnapshot:
    if not approved_by:
        raise ValueError("Weighted Voting lifecycle rollback requires an approving operator")
    target = _snapshot_from_dict(store.read_snapshot(_snapshot_key(target_lifecycle_version)))
    _validate_owned_snapshot(target)
    current = _read_latest_snapshot(store, rolled_back_at)
    rollback_id = _stable_id(
        {
            "action": "rollback",
            "target_lifecycle_version": target_lifecycle_version,
            "rolled_back_at": rolled_back_at.isoformat(),
            "current_lifecycle_version": current.lifecycle_version,
        }
    )
    audit = {
        "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
        "lifecycle_service_version": WEIGHTED_VOTING_STRATEGY_LIFECYCLE_VERSION,
        "action": "rollback",
        "rollback_id": rollback_id,
        "approved_by": approved_by,
        "rolled_back_at": rolled_back_at.isoformat(),
        "previous_lifecycle_version": current.lifecycle_version,
        "restored_lifecycle_version": target.lifecycle_version,
        "reason_codes": ("weighted_voting.strategy_lifecycle.rollback_applied",),
    }
    _write_immutable_snapshot(store, _audit_key(rollback_id), audit)
    store.write_snapshot(WEIGHTED_VOTING_STRATEGY_LIFECYCLE_LATEST_KEY, target.as_dict())
    return target


def load_latest_strategy_lifecycle_snapshot(store: WeightedVotingStateStore, *, timestamp: datetime | None = None) -> WeightedVotingStrategyLifecycleSnapshot:
    return _read_latest_snapshot(store, timestamp or datetime.now(timezone.utc))


def _promotion_gates(
    evidence: WeightedVotingStrategyLifecycleEvidence,
    requirements: WeightedVotingStrategyLifecycleRequirements,
) -> tuple[WeightedVotingStrategyLifecycleGate, ...]:
    gates = [
        _gate("minimum_eligible_opportunities", evidence.eligible_opportunities >= requirements.minimum_eligible_opportunities, evidence.eligible_opportunities, requirements.minimum_eligible_opportunities, "weighted_voting.strategy_lifecycle.insufficient_opportunities", "Promotion requires enough eligible shadow opportunities."),
        _gate("minimum_completed_trades", evidence.completed_trades >= requirements.minimum_completed_trades, evidence.completed_trades, requirements.minimum_completed_trades, "weighted_voting.strategy_lifecycle.insufficient_completed_trades", "Promotion requires completed paper-shadow or replay trades."),
        _gate("positive_net_expectancy_after_costs", evidence.net_expectancy_after_costs > requirements.minimum_net_expectancy_after_costs, evidence.net_expectancy_after_costs, requirements.minimum_net_expectancy_after_costs, "weighted_voting.strategy_lifecycle.expectancy_not_positive", "Promotion requires positive net expectancy after costs."),
        _gate("conservative_lower_bound", evidence.conservative_expectancy_lower_bound > requirements.minimum_conservative_expectancy_lower_bound, evidence.conservative_expectancy_lower_bound, requirements.minimum_conservative_expectancy_lower_bound, "weighted_voting.strategy_lifecycle.lower_bound_not_positive", "Promotion requires an acceptable conservative lower bound."),
        _gate("maximum_drawdown", evidence.maximum_drawdown <= requirements.maximum_drawdown, evidence.maximum_drawdown, requirements.maximum_drawdown, "weighted_voting.strategy_lifecycle.drawdown_too_high", "Promotion requires drawdown within limits."),
        _gate("mae_quality", evidence.mae_quality >= requirements.minimum_mae_quality, evidence.mae_quality, requirements.minimum_mae_quality, "weighted_voting.strategy_lifecycle.mae_quality_low", "Promotion requires acceptable MAE quality."),
        _gate("mfe_quality", evidence.mfe_quality >= requirements.minimum_mfe_quality, evidence.mfe_quality, requirements.minimum_mfe_quality, "weighted_voting.strategy_lifecycle.mfe_quality_low", "Promotion requires acceptable MFE quality."),
        _gate("walk_forward_stability", evidence.walk_forward_stability >= requirements.minimum_walk_forward_stability, evidence.walk_forward_stability, requirements.minimum_walk_forward_stability, "weighted_voting.strategy_lifecycle.walk_forward_unstable", "Promotion requires walk-forward stability."),
        _gate("holdout_stability", evidence.holdout_stability >= requirements.minimum_holdout_stability, evidence.holdout_stability, requirements.minimum_holdout_stability, "weighted_voting.strategy_lifecycle.holdout_unstable", "Promotion requires holdout stability."),
        _gate("paper_shadow_stability", evidence.paper_shadow_stability >= requirements.minimum_paper_shadow_stability, evidence.paper_shadow_stability, requirements.minimum_paper_shadow_stability, "weighted_voting.strategy_lifecycle.paper_shadow_unstable", "Promotion requires paper-shadow stability."),
        _gate("session_consistency", evidence.session_consistency >= requirements.minimum_session_consistency, evidence.session_consistency, requirements.minimum_session_consistency, "weighted_voting.strategy_lifecycle.session_inconsistent", "Promotion requires session consistency."),
        _gate("regime_consistency", evidence.regime_consistency >= requirements.minimum_regime_consistency, evidence.regime_consistency, requirements.minimum_regime_consistency, "weighted_voting.strategy_lifecycle.regime_inconsistent", "Promotion requires regime consistency."),
        _gate("no_severe_tail_loss", not evidence.severe_tail_loss_pattern, evidence.severe_tail_loss_pattern, False, "weighted_voting.strategy_lifecycle.tail_loss_pattern", "Promotion rejects severe tail-loss patterns."),
        _gate("correlation_with_active", evidence.correlation_with_active_strategies <= requirements.maximum_correlation_with_active, evidence.correlation_with_active_strategies, requirements.maximum_correlation_with_active, "weighted_voting.strategy_lifecycle.correlation_too_high", "Promotion requires acceptable correlation with active strategies."),
        _gate("incremental_portfolio_value", evidence.incremental_portfolio_value > requirements.minimum_incremental_portfolio_value, evidence.incremental_portfolio_value, requirements.minimum_incremental_portfolio_value, "weighted_voting.strategy_lifecycle.no_incremental_value", "Promotion requires demonstrated incremental portfolio value."),
        _gate("data_quality_stability", evidence.data_quality_stability >= requirements.minimum_data_quality_stability, evidence.data_quality_stability, requirements.minimum_data_quality_stability, "weighted_voting.strategy_lifecycle.data_quality_unstable", "Promotion requires data-quality stability."),
    ]
    if evidence.strategy_id == "S4" and evidence.duplicate_of_strategy_id == "S7":
        gates.append(_gate("vwap_reversion_incremental_to_s7", (evidence.duplicate_correlation or 0.0) <= requirements.maximum_correlation_with_active, evidence.duplicate_correlation, requirements.maximum_correlation_with_active, "weighted_voting.strategy_lifecycle.vwap_reversion_duplicates_s7", "VWAP Mean Reversion remains shadow when it duplicates S7."))
    if evidence.strategy_id == "S8":
        gates.append(_gate("volatility_breakout_independent_from_opening_range", (evidence.breakout_family_correlation or evidence.correlation_with_active_strategies) <= requirements.maximum_correlation_with_active, evidence.breakout_family_correlation, requirements.maximum_correlation_with_active, "weighted_voting.strategy_lifecycle.volatility_breakout_correlation_high", "Volatility Breakout remains shadow until breakout-family correlation is acceptable."))
    return tuple(gates)


def _demotion_gates(
    evidence: WeightedVotingStrategyLifecycleEvidence,
    requirements: WeightedVotingStrategyLifecycleRequirements,
) -> tuple[WeightedVotingStrategyLifecycleGate, ...]:
    return (
        _gate("recent_net_expectancy", evidence.recent_net_expectancy_after_costs >= requirements.demotion_recent_expectancy_floor, evidence.recent_net_expectancy_after_costs, requirements.demotion_recent_expectancy_floor, "weighted_voting.strategy_lifecycle.recent_expectancy_negative", "Active strategy remains active only while recent expectancy is not materially negative."),
        _gate("drawdown_within_demotion_limit", evidence.maximum_drawdown <= requirements.demotion_maximum_drawdown, evidence.maximum_drawdown, requirements.demotion_maximum_drawdown, "weighted_voting.strategy_lifecycle.active_drawdown_exceeded", "Active strategy drawdown must remain within demotion limits."),
        _gate("data_readiness", evidence.data_readiness_rate >= requirements.demotion_minimum_data_readiness, evidence.data_readiness_rate, requirements.demotion_minimum_data_readiness, "weighted_voting.strategy_lifecycle.data_readiness_deteriorated", "Active strategy data readiness must remain stable."),
        _gate("active_correlation_not_redundant", evidence.correlation_with_active_strategies <= requirements.maximum_correlation_with_active, evidence.correlation_with_active_strategies, requirements.maximum_correlation_with_active, "weighted_voting.strategy_lifecycle.active_correlation_redundant", "Active strategy cannot remain active when correlation makes it redundant."),
        _gate("costs_do_not_consume_edge", evidence.execution_cost_edge_ratio <= requirements.demotion_maximum_execution_cost_edge_ratio, evidence.execution_cost_edge_ratio, requirements.demotion_maximum_execution_cost_edge_ratio, "weighted_voting.strategy_lifecycle.costs_consume_edge", "Execution costs must not consume the strategy edge."),
        _gate("paper_backtest_alignment", evidence.paper_backtest_divergence <= requirements.demotion_maximum_paper_backtest_divergence, evidence.paper_backtest_divergence, requirements.demotion_maximum_paper_backtest_divergence, "weighted_voting.strategy_lifecycle.paper_backtest_diverged", "Paper behaviour must not materially diverge from backtest."),
        _gate("strategy_error_rate", evidence.strategy_error_rate <= requirements.demotion_maximum_strategy_error_rate, evidence.strategy_error_rate, requirements.demotion_maximum_strategy_error_rate, "weighted_voting.strategy_lifecycle.strategy_errors_exceeded", "Strategy errors must remain within tolerance."),
    )


def _target_lifecycle(
    previous: WeightedVotingStrategyLifecycleStatus,
    evidence: WeightedVotingStrategyLifecycleEvidence,
) -> WeightedVotingStrategyLifecycleStatus:
    if evidence.requested_lifecycle is not None:
        return evidence.requested_lifecycle
    return "active" if previous == "shadow" else previous


def _workflow_allowed(evidence: WeightedVotingStrategyLifecycleEvidence) -> bool:
    return evidence.workflow == WEIGHTED_VOTING_LIFECYCLE_AFTER_MARKET_WORKFLOW and evidence.after_market_session_complete


def _severe_disable(failed_gates: tuple[WeightedVotingStrategyLifecycleGate, ...]) -> bool:
    severe = {"strategy_error_rate", "data_readiness"}
    return any(gate.gate_id in severe for gate in failed_gates)


def _decision(
    evidence: WeightedVotingStrategyLifecycleEvidence,
    snapshot: WeightedVotingStrategyLifecycleSnapshot,
    previous: WeightedVotingStrategyLifecycleStatus,
    target: WeightedVotingStrategyLifecycleStatus,
    action: Literal["promote", "demote", "disable", "no_change", "reject"],
    approved: bool,
    gates: tuple[WeightedVotingStrategyLifecycleGate, ...],
) -> WeightedVotingStrategyLifecycleDecision:
    payload = {
        "strategy_id": evidence.strategy_id,
        "evidence_id": evidence.evidence_id,
        "previous": previous,
        "target": target,
        "evaluated_at": evidence.evaluated_at.isoformat(),
        "gates": tuple((gate.gate_id, gate.passed, gate.actual, gate.required) for gate in gates),
    }
    decision_id = _stable_id(payload)
    reasons = tuple(dict.fromkeys(code for gate in gates for code in gate.reason_codes if not gate.passed))
    return WeightedVotingStrategyLifecycleDecision(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        decision_id=decision_id,
        strategy_id=evidence.strategy_id,
        action=action if approved or action in ("reject", "no_change") else "reject",
        previous_lifecycle=previous,
        target_lifecycle=target if approved else previous,
        approved=approved,
        evidence_id=evidence.evidence_id,
        evaluated_at=evidence.evaluated_at,
        previous_lifecycle_version=snapshot.lifecycle_version,
        candidate_lifecycle_version=f"{snapshot.lifecycle_version}.{decision_id[:12]}",
        gates=gates,
        reason_codes=reasons or (f"weighted_voting.strategy_lifecycle.{action}_approved",),
        explanation="Weighted Voting strategy lifecycle decision was evaluated by deterministic rule gates.",
    )


def _reject_without_evidence(
    snapshot: WeightedVotingStrategyLifecycleSnapshot,
    requirements: WeightedVotingStrategyLifecycleRequirements,
) -> WeightedVotingStrategyLifecycleDecision:
    evaluated_at = snapshot.created_at
    evidence = WeightedVotingStrategyLifecycleEvidence(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        strategy_id="S1",
        evidence_id="missing",
        evaluated_at=evaluated_at,
        workflow="missing",
        after_market_session_complete=False,
        eligible_opportunities=0,
        completed_trades=0,
        net_expectancy_after_costs=0.0,
        conservative_expectancy_lower_bound=0.0,
        maximum_drawdown=0.0,
        mae_quality=0.0,
        mfe_quality=0.0,
        walk_forward_stability=0.0,
        holdout_stability=0.0,
        paper_shadow_stability=0.0,
        session_consistency=0.0,
        regime_consistency=0.0,
        severe_tail_loss_pattern=False,
        correlation_with_active_strategies=0.0,
        incremental_portfolio_value=0.0,
        data_quality_stability=0.0,
        recent_net_expectancy_after_costs=0.0,
        data_readiness_rate=0.0,
        execution_cost_edge_ratio=0.0,
        paper_backtest_divergence=0.0,
        strategy_error_rate=0.0,
    )
    gate = _gate("evidence_required", False, None, "complete evidence", "weighted_voting.strategy_lifecycle.evidence_required", "Promotion and demotion cannot occur without approval evidence.")
    return _decision(evidence, snapshot, "shadow", "shadow", "reject", False, (gate,))


def _gate(
    gate_id: str,
    passed: bool,
    actual: float | int | bool | str | None,
    required: float | int | bool | str | None,
    reason_code: str,
    explanation: str,
) -> WeightedVotingStrategyLifecycleGate:
    return WeightedVotingStrategyLifecycleGate(
        gate_id=gate_id,
        passed=bool(passed),
        actual=actual,
        required=required,
        reason_codes=() if passed else (reason_code,),
        explanation=explanation,
    )


def _read_latest_snapshot(store: WeightedVotingStateStore, timestamp: datetime) -> WeightedVotingStrategyLifecycleSnapshot:
    try:
        return _snapshot_from_dict(store.read_snapshot(WEIGHTED_VOTING_STRATEGY_LIFECYCLE_LATEST_KEY))
    except KeyError:
        snapshot = initial_strategy_lifecycle_snapshot(created_at=timestamp)
        store.write_snapshot(WEIGHTED_VOTING_STRATEGY_LIFECYCLE_LATEST_KEY, snapshot.as_dict())
        _write_immutable_snapshot(store, _snapshot_key(snapshot.lifecycle_version), snapshot.as_dict())
        return snapshot


def _audit_payload(decision: WeightedVotingStrategyLifecycleDecision, approved_by: str) -> dict[str, Any]:
    return {
        **decision.as_dict(),
        "lifecycle_service_version": WEIGHTED_VOTING_STRATEGY_LIFECYCLE_VERSION,
        "approved_by": approved_by,
        "immutable_audit": True,
        "owned_namespace": "weighted_voting.strategy_lifecycle",
    }


def _write_immutable_snapshot(store: WeightedVotingStateStore, key: str, payload: dict[str, Any]) -> None:
    try:
        store.read_snapshot(key)
    except KeyError:
        store.write_snapshot(key, payload)
        return
    raise ValueError(f"immutable Weighted Voting lifecycle record already exists: {key}")


def _snapshot_key(version: str) -> str:
    return f"{WEIGHTED_VOTING_STRATEGY_LIFECYCLE_SNAPSHOT_PREFIX}{version}"


def _audit_key(decision_id: str) -> str:
    return f"{WEIGHTED_VOTING_STRATEGY_LIFECYCLE_AUDIT_PREFIX}{decision_id}"


def _stable_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(_json_ready(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _catalog_ids() -> set[str]:
    return {entry.strategy_id for entry in WEIGHTED_VOTING_STRATEGY_CATALOG}


def _validate_owned_snapshot(snapshot: WeightedVotingStrategyLifecycleSnapshot) -> None:
    if snapshot.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
        raise ValueError("foreign strategy lifecycle snapshot cannot be used by Weighted Voting")
    if set(snapshot.strategy_states) != _catalog_ids():
        raise ValueError("Weighted Voting strategy lifecycle snapshot does not match the authoritative catalogue")


def _snapshot_from_dict(payload: dict[str, Any]) -> WeightedVotingStrategyLifecycleSnapshot:
    data = dict(payload)
    created_at = data.get("created_at")
    if isinstance(created_at, str):
        data["created_at"] = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    return WeightedVotingStrategyLifecycleSnapshot(**data)


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


__all__ = [
    "WEIGHTED_VOTING_LIFECYCLE_AFTER_MARKET_WORKFLOW",
    "WEIGHTED_VOTING_PROMOTION_PRIORITY_CANDIDATES",
    "WEIGHTED_VOTING_STRATEGY_LIFECYCLE_AUDIT_PREFIX",
    "WEIGHTED_VOTING_STRATEGY_LIFECYCLE_LATEST_KEY",
    "WEIGHTED_VOTING_STRATEGY_LIFECYCLE_VERSION",
    "WeightedVotingStrategyLifecycleDecision",
    "WeightedVotingStrategyLifecycleEvidence",
    "WeightedVotingStrategyLifecycleGate",
    "WeightedVotingStrategyLifecycleRequirements",
    "WeightedVotingStrategyLifecycleSnapshot",
    "apply_strategy_lifecycle_decision",
    "evaluate_strategy_lifecycle_change",
    "initial_strategy_lifecycle_snapshot",
    "load_latest_strategy_lifecycle_snapshot",
    "rollback_strategy_lifecycle_version",
    "strategy_lifecycle_status",
]
