from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.app.algorithms.session import (
    DataQualityState,
    EventRiskState,
    LiquidityState,
    SessionBehavior,
    SessionClassification,
    SessionPhase,
    VolatilityState,
    build_session_candidate_decision,
    evaluate_session_candidate_order_gate,
    resolve_session_profile,
)
from backend.app.domain.models import Signal
from backend.app.gates import GlobalGateResponse


NOW = datetime(2026, 7, 23, 14, 5, tzinfo=UTC)


def test_session_step13_profitable_gross_edge_rejected_when_net_edge_is_negative() -> None:
    classification = _classification()
    profile = resolve_session_profile(classification)
    candidate = _candidate(classification, profile, expected_gross_edge=0.03, spread_estimate=0.025, slippage_estimate=0.02)

    decision = evaluate_session_candidate_order_gate(
        candidate=candidate,
        profile=profile,
        current_classification=classification,
        current_price=100.0,
        current_time=NOW + timedelta(milliseconds=100),
        quote_age_seconds=0.1,
        global_gate_response=_global_response("ALLOW", 10, 25.0),
    )

    assert decision.accepted is False
    assert decision.status == "REJECTED"
    assert decision.expectedNetEdge < 0
    assert "session.execution.expected_net_edge_below_profile_minimum" in decision.reasonCodes
    assert decision.submitted is False


def test_session_step13_stale_signal_is_rejected() -> None:
    classification = _classification()
    profile = resolve_session_profile(classification)
    candidate = _candidate(classification, profile)

    decision = _evaluate(candidate, profile, classification, current_time=candidate.validUntil + timedelta(milliseconds=1))

    assert decision.accepted is False
    assert "session.execution.signal_stale" in decision.reasonCodes


def test_session_step13_price_moving_outside_permitted_range_is_rejected() -> None:
    classification = _classification()
    profile = resolve_session_profile(classification)
    candidate = _candidate(classification, profile)

    decision = _evaluate(candidate, profile, classification, current_price=100.08)

    assert decision.accepted is False
    assert "session.execution.price_left_permitted_entry_range" in decision.reasonCodes


def test_session_step13_phase_change_to_blocked_phase_rejects_before_submission() -> None:
    original = _classification()
    blocked_now = _classification(phase=SessionPhase.CLOSING_AUCTION, behavior=SessionBehavior.TREND_UP)
    profile = resolve_session_profile(original)
    candidate = _candidate(original, profile)

    decision = _evaluate(candidate, profile, blocked_now)

    assert decision.accepted is False
    assert "session.execution.current_phase_or_profile_blocks_entries" in decision.reasonCodes
    assert decision.submitted is False


def test_session_step13_global_rejection_is_authoritative() -> None:
    classification = _classification()
    profile = resolve_session_profile(classification)
    candidate = _candidate(classification, profile)

    decision = evaluate_session_candidate_order_gate(
        candidate=candidate,
        profile=profile,
        current_classification=classification,
        current_price=100.0,
        current_time=NOW + timedelta(milliseconds=100),
        quote_age_seconds=0.1,
        global_gate_response=_global_response("REJECT_NEW_ENTRY", 0, 0.0, reasons=("global.risk.daily_loss_limit",)),
    )

    assert decision.accepted is False
    assert decision.approvedQuantity == 0
    assert "global.risk.daily_loss_limit" in decision.reasonCodes


def test_session_step13_global_quantity_reduction_remains_accepted_but_reduced() -> None:
    classification = _classification()
    profile = resolve_session_profile(classification)
    candidate = _candidate(classification, profile)

    decision = evaluate_session_candidate_order_gate(
        candidate=candidate,
        profile=profile,
        current_classification=classification,
        current_price=100.0,
        current_time=NOW + timedelta(milliseconds=100),
        quote_age_seconds=0.1,
        global_gate_response=_global_response("REDUCE_QUANTITY", 5, 12.5, reasons=("global.risk.quantity_reduced",)),
    )

    assert decision.accepted is True
    assert decision.status == "REDUCED"
    assert decision.quantityReduced is True
    assert decision.approvedQuantity == 5
    assert "global.risk.quantity_reduced" in decision.reasonCodes
    assert decision.validatedOrderIntent["status"] == "VALIDATED"
    assert decision.submitted is False


def test_session_step13_latency_budget_violation_rejects_candidate() -> None:
    classification = _classification()
    profile = resolve_session_profile(classification)
    candidate = _candidate(classification, profile)

    decision = _evaluate(candidate, profile, classification, current_time=NOW + timedelta(seconds=2))

    assert decision.accepted is False
    assert "session.execution.latency_budget_violation" in decision.reasonCodes
    assert decision.latencies.decisionToSubmitLatencyMs == 2000.0


def test_session_step13_paper_and_replay_gate_results_are_deterministic_and_neutral() -> None:
    classification = _classification()
    profile = resolve_session_profile(classification)
    candidate = _candidate(classification, profile)
    response = _global_response("ALLOW", 10, 25.0)

    first = _evaluate(candidate, profile, classification, global_gate_response=response)
    second = _evaluate(candidate, profile, classification, global_gate_response=response)

    assert first.accepted is True
    assert first.status == "ACCEPTED"
    assert first.submitted is False
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.validatedOrderIntent["status"] == "VALIDATED"
    assert first.globalOrderProposal["algorithmId"] == "session"
    assert "session.execution.no_direct_submission" in first.immutableChecks


def _evaluate(
    candidate,
    profile,
    classification,
    *,
    current_price: float = 100.0,
    current_time: datetime = NOW + timedelta(milliseconds=100),
    global_gate_response: GlobalGateResponse | None = None,
):
    return evaluate_session_candidate_order_gate(
        candidate=candidate,
        profile=profile,
        current_classification=classification,
        current_price=current_price,
        current_time=current_time,
        quote_age_seconds=0.1,
        global_gate_response=global_gate_response or _global_response("ALLOW", 10, 25.0),
    )


def _candidate(
    classification: SessionClassification,
    profile,
    *,
    expected_gross_edge: float = 0.08,
    spread_estimate: float = 0.005,
    slippage_estimate: float = 0.005,
):
    return build_session_candidate_decision(
        classification=classification,
        profile=profile,
        originating_strategy_candidate_id="strategy-candidate-1",
        side=Signal.BUY,
        order_type="limit",
        desired_quantity=10,
        entry_price=100.0,
        permitted_entry_price_range=(99.95, 100.05),
        expected_gross_edge=expected_gross_edge,
        spread_estimate=spread_estimate,
        slippage_estimate=slippage_estimate,
        fees=0.001,
        market_impact_estimate=0.001,
        adverse_selection_buffer=0.002,
        fill_probability=0.80,
        quantity_cap=10,
        stop_price=99.5,
        target_price=101.0,
        planned_risk_dollars=25.0,
        feature_ready_latency_ms=80.0,
        inference_classification_latency_ms=25.0,
    )


def _global_response(
    action: str,
    quantity: int,
    risk: float,
    *,
    reasons: tuple[str, ...] = (),
) -> GlobalGateResponse:
    return GlobalGateResponse(
        action=action,
        maximumAllowedQuantity=quantity,
        maximumAdditionalRiskDollars=risk,
        rejectionReasons=reasons,
        evaluatedAt=NOW + timedelta(milliseconds=90),
        configurationHash=f"global-{action.lower()}-{quantity}-{risk}",
    )


def _classification(
    *,
    phase: SessionPhase = SessionPhase.MORNING,
    behavior: SessionBehavior = SessionBehavior.TREND_UP,
    volatility: VolatilityState = VolatilityState.NORMAL,
    liquidity: LiquidityState = LiquidityState.HEALTHY,
    data_quality: DataQualityState = DataQualityState.READY,
    event_risk: EventRiskState = EventRiskState.CLEAR,
    block: bool = False,
) -> SessionClassification:
    return SessionClassification(
        symbol="SPY",
        session_date="2026-07-23",
        exchange_timezone="America/New_York",
        market_event_time=NOW - timedelta(milliseconds=120),
        feature_snapshot_time=NOW - timedelta(milliseconds=40),
        decision_time=NOW,
        valid_until=NOW + timedelta(seconds=60),
        phase=phase,
        behavior=behavior,
        volatility_state=volatility,
        liquidity_state=liquidity,
        data_quality_state=data_quality,
        event_risk_state=event_risk,
        direction_bias="cash" if block else "long",
        phase_confidence=0.9,
        behavior_confidence=0.8,
        volatility_confidence=0.8,
        liquidity_confidence=0.9,
        data_quality_confidence=0.95,
        overall_confidence=0.8,
        safety_block_confidence=0.9 if block else 0.0,
        reason_codes=(f"fixture.{phase.value}.{behavior.value}",),
        evidence={"fixture": True, "classificationId": "session-classification-step13"},
        allowed_strategy_families=("trend", "pullback", "vwap"),
        blocked_strategy_families=(),
        block_new_entries=block,
    )
