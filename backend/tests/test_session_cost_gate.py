from __future__ import annotations

from datetime import timedelta

import pytest

from backend.app.algorithms.session import build_session_candidate_decision, evaluate_session_candidate_order_gate, resolve_session_profile, session_expected_net_edge
from backend.app.domain.models import Signal
from backend.app.gates import GlobalGateResponse
from session_test_fixtures import NOW, classification_fixture


def test_session_cost_gate_rejects_positive_gross_negative_net_edge() -> None:
    classification = classification_fixture()
    profile = resolve_session_profile(classification)
    candidate = _candidate(classification, profile, expected_gross_edge=0.02, spread_estimate=0.03)

    decision = evaluate_session_candidate_order_gate(candidate=candidate, profile=profile, current_classification=classification, current_price=100, current_time=NOW + timedelta(milliseconds=100), quote_age_seconds=0.1, global_gate_response=_global_response("ALLOW", 10, 25.0))

    assert decision.accepted is False
    assert decision.expectedNetEdge < profile.minimum_net_expected_edge
    assert "session.execution.expected_net_edge_below_profile_minimum" in decision.reasonCodes


def test_session_cost_gate_rejects_stale_price_moved_and_low_fill_probability() -> None:
    classification = classification_fixture()
    profile = resolve_session_profile(classification)
    candidate = _candidate(classification, profile, fill_probability=0.1)

    stale = evaluate_session_candidate_order_gate(candidate=candidate, profile=profile, current_classification=classification, current_price=100, current_time=candidate.validUntil + timedelta(seconds=1), quote_age_seconds=0.1, global_gate_response=_global_response("ALLOW", 10, 25.0))
    moved = evaluate_session_candidate_order_gate(candidate=candidate, profile=profile, current_classification=classification, current_price=101, current_time=NOW + timedelta(milliseconds=100), quote_age_seconds=0.1, global_gate_response=_global_response("ALLOW", 10, 25.0))

    assert "session.execution.signal_stale" in stale.reasonCodes
    assert "session.execution.price_left_permitted_entry_range" in moved.reasonCodes
    assert "session.execution.fill_probability_too_low" in moved.reasonCodes


def test_session_cost_gate_global_gate_is_one_way_boundary() -> None:
    classification = classification_fixture()
    profile = resolve_session_profile(classification)
    candidate = _candidate(classification, profile)

    decision = evaluate_session_candidate_order_gate(candidate=candidate, profile=profile, current_classification=classification, current_price=100, current_time=NOW + timedelta(milliseconds=100), quote_age_seconds=0.1, global_gate_response=_global_response("REJECT_NEW_ENTRY", 0, 0.0, reasons=("global.risk.block",)))

    assert decision.accepted is False
    assert decision.submitted is False
    assert decision.globalOrderProposal["algorithmId"] == "session"
    assert "session.execution.no_direct_submission" in decision.immutableChecks
    assert "global.risk.block" in decision.reasonCodes


def test_session_cost_formula_keeps_costs_in_eligibility() -> None:
    assert session_expected_net_edge(expected_gross_edge=0.10, spread_cost=0.01, estimated_slippage=0.02, fees=0.005, estimated_market_impact=0.004, adverse_selection_buffer=0.003) == pytest.approx(0.058)


def _candidate(classification, profile, *, expected_gross_edge: float = 0.10, spread_estimate: float = 0.01, fill_probability: float = 0.8):
    return build_session_candidate_decision(
        classification=classification,
        profile=profile,
        originating_strategy_candidate_id="fixture-candidate",
        side=Signal.BUY,
        order_type="limit",
        desired_quantity=10,
        entry_price=100,
        permitted_entry_price_range=(99.95, 100.05),
        expected_gross_edge=expected_gross_edge,
        spread_estimate=spread_estimate,
        slippage_estimate=0.01,
        fees=0.001,
        market_impact_estimate=0.001,
        adverse_selection_buffer=0.001,
        fill_probability=fill_probability,
        quantity_cap=10,
        stop_price=99.5,
        target_price=101,
        planned_risk_dollars=25.0,
    )


def _global_response(action: str, quantity: int, risk: float, *, reasons: tuple[str, ...] = ()) -> GlobalGateResponse:
    return GlobalGateResponse(action=action, maximumAllowedQuantity=quantity, maximumAdditionalRiskDollars=risk, rejectionReasons=reasons, evaluatedAt=NOW, configurationHash="global-gate-fixture")
