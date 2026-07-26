from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from backend.app.algorithms.wca.contracts import WcaOrderValidationContext, WcaSide
from backend.app.algorithms.wca.execution_pipeline import WcaExecutionPipelineInput, run_wca_paper_pipeline_adapter
from backend.app.algorithms.wca.global_risk import (
    SharedGlobalRiskReservationEngine,
    WcaGlobalRiskAdapter,
    WcaGlobalRiskDecisionStatus,
    build_wca_global_risk_proposal,
)
from backend.app.algorithms.wca.order_validation import WCA_ORDER_VALIDATION_PASSED, apply_wca_final_order_validation, validate_wca_final_order
from backend.tests.test_wca_paper_execution_pipeline import decision_with_order
from backend.tests.test_wca_step5_production_pipeline import FakeVoter, market_snapshot
from backend.app.algorithms.wca.configuration import default_wca_configuration
from backend.app.algorithms.wca.weights import baseline_weight_snapshot


def test_wca_global_risk_proposal_contains_neutral_required_fields() -> None:
    proposal = build_wca_global_risk_proposal(
        account_id="acct-1",
        symbol="SPY",
        side=WcaSide.BUY,
        requested_quantity=12,
        requested_risk=60,
        stop_distance=5,
        expected_holding_period_seconds=1800,
        current_wca_attributed_exposure=2500,
        total_account_exposure_snapshot={"maximum_open_risk_dollars": 1000, "current_open_risk_dollars": 200},
        configuration_version="cfg-v1",
        configuration_hash="cfg-hash",
        decision_id="decision-1",
        idempotency_key="wca-key-1",
    )

    assert proposal.algorithm_id == "wca"
    assert proposal.account_id == "acct-1"
    assert proposal.requested_quantity == 12
    assert proposal.current_wca_attributed_exposure == 2500
    assert proposal.total_account_exposure_snapshot["maximum_open_risk_dollars"] == 1000


def test_pipeline_applies_shared_global_risk_reduction_without_rewriting_wca_decision_fields() -> None:
    configuration = default_wca_configuration()
    snapshot = market_snapshot()
    command = WcaExecutionPipelineInput(
        run_id="step9",
        decision_id="step9-decision",
        order_intent_id="step9-intent",
        snapshot=snapshot,
        configuration_version=configuration.configuration_version,
        configuration=configuration,
        weight_snapshot=baseline_weight_snapshot(cutoff=snapshot.decision_timestamp),
        account_id="acct-1",
        approved_risk_budget=12,
    )

    result = run_wca_paper_pipeline_adapter(command, voters=_all_fake_voters(WcaSide.BUY)).decision

    assert result.global_gate_result is not None
    assert result.global_gate_result.allowed_quantity <= result.global_gate_result.proposed_quantity
    assert "wca.global_risk.reduced_risk" in result.global_gate_result.reason_codes
    assert result.aggregation.signal == WcaSide.BUY.value
    assert result.aggregation.post_local_gate_decision == WcaSide.BUY.value
    assert result.called_module_versions["global_risk_adapter"] == "wca_global_risk_adapter_v1"
    if result.proposed_order is not None:
        assert result.proposed_order.side == WcaSide.BUY.value
        assert result.proposed_order.account_id == "acct-1"
        assert result.proposed_order.idempotency_key


def test_final_validation_rejects_stale_quote_duplicate_idempotency_and_buying_power() -> None:
    decision = decision_with_order()
    assert decision.proposed_order is not None
    quote = decision.market_snapshot.quote or _quote_for_decision(decision)
    stale_snapshot = decision.market_snapshot.model_copy(update={"quote": quote.model_copy(update={"timestamp": decision.decision_timestamp - timedelta(minutes=5)})})
    stale_decision = decision.model_copy(update={"market_snapshot": stale_snapshot})
    context = WcaOrderValidationContext(
        evaluation_timestamp=decision.decision_timestamp,
        quote_freshness_seconds=15,
        available_buying_power=1,
        idempotency_required=True,
        idempotency_key_seen=True,
    )

    validation = validate_wca_final_order(stale_decision, context)

    assert not validation.valid
    assert "wca.order_validation.stale_quote" in validation.reason_codes
    assert "wca.order_validation.buying_power_exceeded" in validation.reason_codes
    assert "wca.order_validation.missing_idempotency_key" in validation.reason_codes


def test_final_validation_separates_new_entry_and_risk_reducing_exit_permission() -> None:
    decision = _decision_with_quote_and_key()
    blocked_entry = validate_wca_final_order(
        decision,
        WcaOrderValidationContext(
            evaluation_timestamp=decision.decision_timestamp,
            quote_freshness_seconds=15,
            idempotency_required=True,
            new_entry_permitted=False,
            risk_reducing_exit_permitted=True,
            is_risk_reducing_exit=False,
        ),
    )
    allowed_exit = validate_wca_final_order(
        decision,
        WcaOrderValidationContext(
            evaluation_timestamp=decision.decision_timestamp,
            quote_freshness_seconds=15,
            idempotency_required=True,
            new_entry_permitted=False,
            risk_reducing_exit_permitted=True,
            is_risk_reducing_exit=True,
        ),
    )

    assert "wca.order_validation.new_entry_not_permitted" in blocked_entry.reason_codes
    assert allowed_exit.valid
    assert WCA_ORDER_VALIDATION_PASSED in allowed_exit.reason_codes


def test_concurrent_global_risk_proposals_cannot_over_allocate_account_risk() -> None:
    engine = SharedGlobalRiskReservationEngine()
    adapter = WcaGlobalRiskAdapter(engine)

    def evaluate(index: int):
        proposal = build_wca_global_risk_proposal(
            account_id="acct-risk",
            symbol="SPY",
            side=WcaSide.BUY,
            requested_quantity=80,
            requested_risk=80,
            stop_distance=1,
            expected_holding_period_seconds=1800,
            current_wca_attributed_exposure=0,
            total_account_exposure_snapshot={"maximum_open_risk_dollars": 100, "current_open_risk_dollars": 0},
            configuration_version="cfg-risk",
            configuration_hash="hash-risk",
            decision_id=f"decision-{index}",
            idempotency_key=f"risk-key-{index}",
        )
        return adapter.evaluate_wca_proposal(proposal)

    with ThreadPoolExecutor(max_workers=2) as pool:
        decisions = tuple(pool.map(evaluate, (1, 2)))

    assert sum(decision.approved_risk for decision in decisions) <= 100
    assert {decision.status for decision in decisions} <= {
        WcaGlobalRiskDecisionStatus.APPROVED.value,
        WcaGlobalRiskDecisionStatus.APPROVED_REDUCED_QUANTITY.value,
        WcaGlobalRiskDecisionStatus.APPROVED_REDUCED_RISK.value,
        WcaGlobalRiskDecisionStatus.REJECTED_ENTRY.value,
    }
    assert engine.reserved_risk(account_id="acct-risk", symbol="SPY") <= 100


def test_final_validation_drops_cross_algorithm_position_mutation() -> None:
    decision = _decision_with_quote_and_key()
    blocked = apply_wca_final_order_validation(
        decision,
        WcaOrderValidationContext(
            evaluation_timestamp=decision.decision_timestamp,
            quote_freshness_seconds=15,
            idempotency_required=True,
            cross_algorithm_position_mutation=True,
        ),
    )

    assert blocked.proposed_order is None
    assert "wca.order_validation.cross_algorithm_position_mutation" in blocked.reason_codes


def test_wca_global_risk_module_does_not_import_sibling_algorithm_state() -> None:
    source = __import__("pathlib").Path("backend/app/algorithms/wca/global_risk.py").read_text()

    assert "weighted_voting" not in source
    assert "voting_ensemble" not in source
    assert "regime" not in source
    assert "meta_strategy" not in source


def _decision_with_quote_and_key():
    decision = decision_with_order()
    assert decision.proposed_order is not None
    quote = _quote_for_decision(decision)
    order = decision.proposed_order.model_copy(update={"idempotency_key": "wca-test-key"})
    return decision.model_copy(update={"market_snapshot": decision.market_snapshot.model_copy(update={"quote": quote}), "proposed_order": order})


def _quote_for_decision(decision):
    from backend.app.algorithms.wca.contracts import WcaQuote

    price = decision.sizing.entry_price
    return WcaQuote(timestamp=decision.decision_timestamp, bid=price - 0.01, ask=price + 0.01)


def _all_fake_voters(side: WcaSide):
    return tuple(FakeVoter(strategy_id, side) for strategy_id in ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10", "C11"))
