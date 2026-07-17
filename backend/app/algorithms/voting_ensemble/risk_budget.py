from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Any


VOTING_ENSEMBLE_RISK_BUDGET_VERSION = "voting_ensemble_risk_budget_v1"
VOTING_ENSEMBLE_VOTE_EDGE_SIZING_VERSION = "voting_ensemble_vote_edge_sizing_v1"


@dataclass(frozen=True)
class VotingEnsembleRiskBudget:
    quantity: int
    planned_risk: float
    sizing_mode: str
    risk_budget: float
    order_limit: float
    vote_edge: float | None
    vote_edge_multiplier: float
    reason_codes: tuple[str, ...]


def resolve_voting_ensemble_risk_budget(
    config: dict[str, Any],
    *,
    equity: float,
    entry_price: float,
    stop_distance: float,
) -> VotingEnsembleRiskBudget:
    if stop_distance <= 0 or entry_price <= 0 or equity <= 0:
        return VotingEnsembleRiskBudget(
            quantity=0,
            planned_risk=0.0,
            sizing_mode="invalid",
            risk_budget=0.0,
            order_limit=0.0,
            vote_edge=None,
            vote_edge_multiplier=0.0,
            reason_codes=("voting_ensemble.risk_budget.invalid_inputs",),
        )
    if bool(config.get("entriesBlocked")):
        return VotingEnsembleRiskBudget(
            quantity=0,
            planned_risk=0.0,
            sizing_mode="blocked",
            risk_budget=0.0,
            order_limit=0.0,
            vote_edge=None,
            vote_edge_multiplier=0.0,
            reason_codes=("voting_ensemble.risk_budget.entries_blocked_by_profile",),
        )

    vote_edge = _vote_edge(config)
    vote_edge_multiplier = _vote_edge_size_multiplier(vote_edge)
    vote_edge_reason_codes = _vote_edge_reason_codes(vote_edge, vote_edge_multiplier)
    if vote_edge is not None and vote_edge_multiplier <= 0.0:
        return VotingEnsembleRiskBudget(
            quantity=0,
            planned_risk=0.0,
            sizing_mode="vote_edge_blocked",
            risk_budget=0.0,
            order_limit=0.0,
            vote_edge=vote_edge,
            vote_edge_multiplier=vote_edge_multiplier,
            reason_codes=tuple(["voting_ensemble.risk_budget.vote_edge_below_minimum", *vote_edge_reason_codes]),
        )

    mode = str(config.get("positionSizingMode") or "allocation")
    if mode == "allocation":
        starting_capital = _positive_float(config.get("startingCapital"), equity)
        order_limit = starting_capital * (_bounded_percent(config.get("orderAllocationPercent"), 10.0) / 100.0)
        daily_limit = starting_capital * (_bounded_percent(config.get("dailyAllocationPercent"), 30.0) / 100.0)
        order_limit = min(order_limit, daily_limit, equity) * vote_edge_multiplier
        risk_budget = order_limit * (_bounded_percent(config.get("riskBudgetPercentOfOrder"), 50.0) / 100.0)
        allocation_shares = floor(order_limit / entry_price)
        planned_risk = allocation_shares * stop_distance
        reason_codes = ["voting_ensemble.risk_budget.allocation_mode", *vote_edge_reason_codes]
        if planned_risk > risk_budget:
            allocation_shares = floor(risk_budget / stop_distance)
            planned_risk = allocation_shares * stop_distance
            reason_codes.append("voting_ensemble.risk_budget.capped_by_order_risk_budget")
        if allocation_shares <= 0:
            reason_codes.append("voting_ensemble.risk_budget.zero_quantity")
        return VotingEnsembleRiskBudget(
            quantity=max(0, allocation_shares),
            planned_risk=round(max(0.0, planned_risk), 6),
            sizing_mode="allocation",
            risk_budget=round(max(0.0, risk_budget), 6),
            order_limit=round(max(0.0, order_limit), 6),
            vote_edge=vote_edge,
            vote_edge_multiplier=vote_edge_multiplier,
            reason_codes=tuple(reason_codes),
        )

    risk_budget = equity * (_bounded_percent(config.get("riskPerTradePercent"), 0.5) / 100.0) * vote_edge_multiplier
    risk_shares = floor(risk_budget / stop_distance)
    capital_shares = floor(equity / entry_price)
    shares = max(0, min(risk_shares, capital_shares))
    reason_codes = ["voting_ensemble.risk_budget.risk_mode", *vote_edge_reason_codes]
    if shares == capital_shares and capital_shares < risk_shares:
        reason_codes.append("voting_ensemble.risk_budget.capped_by_available_equity")
    if shares <= 0:
        reason_codes.append("voting_ensemble.risk_budget.zero_quantity")
    return VotingEnsembleRiskBudget(
        quantity=shares,
        planned_risk=round(shares * stop_distance, 6),
        sizing_mode="risk",
        risk_budget=round(max(0.0, risk_budget), 6),
        order_limit=round(max(0.0, equity), 6),
        vote_edge=vote_edge,
        vote_edge_multiplier=vote_edge_multiplier,
        reason_codes=tuple(reason_codes),
    )


def position_size_for_config(
    config: dict[str, Any],
    *,
    equity: float,
    entry_price: float,
    stop_distance: float,
) -> tuple[int, float, str]:
    budget = resolve_voting_ensemble_risk_budget(
        config,
        equity=equity,
        entry_price=entry_price,
        stop_distance=stop_distance,
    )
    return budget.quantity, budget.planned_risk, budget.sizing_mode


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return parsed if parsed > 0 else default


def _bounded_percent(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(100.0, parsed))


def _vote_edge(config: dict[str, Any]) -> float | None:
    nested = []
    for key in ("voteSummary", "ensembleDecision", "decision", "voting"):
        value = config.get(key)
        if isinstance(value, dict):
            nested.append(value)
    for payload in (config, *nested):
        for key in (
            "voteEdge",
            "winnerEdge",
            "edge",
            "voteStrength",
            "finalScore",
            "contextAdjustedScore",
            "baseScore",
            "confidence",
        ):
            if key not in payload:
                continue
            edge = _optional_abs_float(payload.get(key))
            if edge is not None:
                return max(0.0, min(1.0, edge))
    return None


def _vote_edge_size_multiplier(vote_edge: float | None) -> float:
    if vote_edge is None:
        return 1.0
    if vote_edge >= 0.60:
        return 1.0
    if vote_edge >= 0.45:
        return 0.75
    if vote_edge >= 0.30:
        return 0.50
    if vote_edge >= 0.20:
        return 0.25
    return 0.0


def _vote_edge_reason_codes(vote_edge: float | None, multiplier: float) -> tuple[str, ...]:
    if vote_edge is None:
        return ()
    return (
        VOTING_ENSEMBLE_VOTE_EDGE_SIZING_VERSION,
        f"voting_ensemble.vote_edge.multiplier:{multiplier:.2f}",
    )


def _optional_abs_float(value: Any) -> float | None:
    try:
        parsed = abs(float(value))
    except (TypeError, ValueError):
        return None
    return parsed
