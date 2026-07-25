from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from backend.app.algorithms.voting_ensemble.execution_adapter import (
    VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE,
    VotingEnsembleExecutionAdapter,
    VotingEnsembleExecutionStateStore,
    voting_ensemble_client_order_id,
)
from backend.app.algorithms.voting_ensemble.ml_contracts import SafeMLInferenceResult
from backend.app.domain.models import (
    AccountRiskState,
    BaselineTradingSettings,
    Direction,
    DynamicPolicyBounds,
    EffectiveTradePolicy,
    GateStatus,
    GlobalGateDecision,
    HardRiskLimits,
    OperatingMode,
    OrderPlan,
    Signal,
    TradeCandidate,
)
from backend.app.execution.broker_reconciliation import BrokerFillUpdate, BrokerOrderAck
from backend.app.gates import BrokerAccountSnapshot, BrokerOrderState, BrokerPositionState


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)


class VotingEnsembleExecutionAdapterTest(unittest.TestCase):
    def test_translates_candidate_to_limit_order_without_ml_result(self) -> None:
        plan = VotingEnsembleExecutionAdapter().translate_candidate_to_order(
            candidate=candidate(quantity=10),
            policy=policy(),
            gateDecision=gate_decision(True),
            decidedAt=NOW,
            sessionDate=SESSION_DATE,
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.orderType, "LIMIT")
        self.assertEqual(plan.timeInForce, "DAY")
        self.assertEqual(plan.limitPrice, 100.0)
        self.assertTrue(plan.eligible)

    def test_active_ml_can_block_but_shadow_or_missing_ml_does_not(self) -> None:
        adapter = VotingEnsembleExecutionAdapter()

        shadow = adapter.translate_candidate_to_order(
            candidate=candidate(quantity=10),
            policy=policy(),
            gateDecision=gate_decision(True),
            mlDecision=ml_result(OperatingMode.SHADOW, accepted=False),
            decidedAt=NOW,
            sessionDate=SESSION_DATE,
        )
        active = adapter.translate_candidate_to_order(
            candidate=candidate(quantity=10),
            policy=policy(),
            gateDecision=gate_decision(True),
            mlDecision=ml_result(OperatingMode.ACTIVE, accepted=False),
            decidedAt=NOW,
            sessionDate=SESSION_DATE,
        )

        self.assertTrue(shadow.eligible)
        self.assertFalse(active.eligible)
        self.assertEqual(active.orderType, "NO_ORDER")
        self.assertIn("voting_ensemble.order_planner.ml_filter_block", active.validationErrors)

    def test_duplicate_decisions_do_not_create_duplicate_broker_orders(self) -> None:
        store = VotingEnsembleExecutionStateStore()
        adapter = VotingEnsembleExecutionAdapter(state_store=store)
        broker = FakeVotingEnsembleBroker()
        plan = order_plan()

        first = adapter.submit_order_once(orderPlan=plan, broker=broker, idempotencyKey="decision-1", evaluatedAt=NOW)
        second = VotingEnsembleExecutionAdapter(state_store=store).submit_order_once(orderPlan=plan, broker=broker, idempotencyKey="decision-1", evaluatedAt=NOW + timedelta(seconds=1))

        self.assertTrue(first.submitted)
        self.assertFalse(first.duplicate)
        self.assertTrue(second.duplicate)
        self.assertEqual(first.clientOrderId, second.clientOrderId)
        self.assertEqual(broker.submit_count, 1)
        self.assertEqual(store.get(first.clientOrderId).namespace, VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE)

    def test_partial_fill_resizes_protective_exits(self) -> None:
        fill = BrokerFillUpdate(
            clientOrderId="placeholder",
            filledQuantity=4,
            averageFillPrice=100.01,
            status="PARTIALLY_FILLED",
            updatedAt=NOW,
        )
        adapter = VotingEnsembleExecutionAdapter()
        result = adapter.submit_order_once(orderPlan=order_plan(quantity=10), broker=FakeVotingEnsembleBroker(fill=fill), idempotencyKey="partial", evaluatedAt=NOW)

        self.assertEqual(result.status, "PARTIALLY_FILLED")
        self.assertEqual(result.protectiveOrder.quantity, 4)
        self.assertIn("voting_ensemble.execution_adapter.partial_fill_tracked", result.reasonCodes)
        self.assertIn("voting_ensemble.execution_adapter.protective_exits_resized_to_fill", result.reasonCodes)

    def test_fill_event_keeps_protective_exits_when_entries_are_blocked(self) -> None:
        adapter = VotingEnsembleExecutionAdapter()
        result = adapter.submit_order_once(orderPlan=order_plan(quantity=10), broker=FakeVotingEnsembleBroker(), idempotencyKey="entry", evaluatedAt=NOW)
        fill = BrokerFillUpdate(
            clientOrderId=result.clientOrderId,
            filledQuantity=6,
            averageFillPrice=100.02,
            status="PARTIALLY_FILLED",
            updatedAt=NOW + timedelta(seconds=2),
        )

        state = adapter.process_fill_event(
            clientOrderId=result.clientOrderId,
            fillUpdate=fill,
            evaluatedAt=NOW + timedelta(seconds=2),
            entriesBlockedByProfile=True,
        )

        self.assertEqual(state.protectiveOrder["quantity"], 6)
        self.assertIn("voting_ensemble.execution_adapter.protective_exits_survive_entry_blocks", state.reasonCodes)

    def test_market_order_and_expired_order_are_blocked_or_expired(self) -> None:
        adapter = VotingEnsembleExecutionAdapter(max_order_age_seconds=5)
        market = order_plan().model_copy(update={"orderType": "MARKET", "limitPrice": None})

        blocked = adapter.submit_order_once(orderPlan=market, broker=FakeVotingEnsembleBroker(), idempotencyKey="market", evaluatedAt=NOW)
        accepted = adapter.submit_order_once(orderPlan=order_plan(), broker=FakeVotingEnsembleBroker(), idempotencyKey="expire", evaluatedAt=NOW)
        expired = adapter.expire_stale_orders(evaluatedAt=NOW + timedelta(seconds=10))

        self.assertFalse(blocked.submitted)
        self.assertIn("voting_ensemble.execution_adapter.market_orders_not_authorized", blocked.reasonCodes)
        self.assertTrue(accepted.submitted)
        self.assertEqual(expired[0].status, "EXPIRED")
        self.assertIn("voting_ensemble.execution_adapter.entry_order_expired", expired[0].reasonCodes)

    def test_unknown_voting_ensemble_order_state_requires_reconciliation_and_blocks_entries(self) -> None:
        adapter = VotingEnsembleExecutionAdapter()
        other_algorithm = BrokerOrderState(
            algorithmId="meta_strategy",
            symbol="SPY",
            side=Signal.BUY,
            clientOrderId="meta-order",
            orderType="LIMIT",
            quantity=10,
            entryPrice=100.0,
            submittedAt=NOW,
        )
        voting_order = other_algorithm.model_copy(update={"algorithmId": "voting_ensemble", "clientOrderId": "ve-orphan"})

        ignored = adapter.reconcile_broker_state(openOrders=[other_algorithm], positions=[], observedAt=NOW)
        reconciled = adapter.reconcile_broker_state(openOrders=[voting_order], positions=[], observedAt=NOW)

        self.assertEqual(ignored, ())
        self.assertEqual(reconciled[0].status, "RECONCILIATION_REQUIRED")
        self.assertTrue(adapter.state_store.entries_blocked("SPY", NOW))

    def test_rejection_creates_execution_cooldown(self) -> None:
        adapter = VotingEnsembleExecutionAdapter(execution_cooldown_seconds=30)
        first = adapter.submit_order_once(orderPlan=order_plan(), broker=FakeVotingEnsembleBroker(ack_status="REJECTED"), idempotencyKey="reject", evaluatedAt=NOW)
        second = adapter.submit_order_once(orderPlan=order_plan(), broker=FakeVotingEnsembleBroker(), idempotencyKey="after-reject", evaluatedAt=NOW + timedelta(seconds=5))

        self.assertEqual(first.status, "REJECTED")
        self.assertTrue(first.blocksAdditionalEntries)
        self.assertFalse(second.submitted)
        self.assertIn("voting_ensemble.execution_adapter.entries_blocked_by_unknown_state_or_cooldown", second.reasonCodes)


class FakeVotingEnsembleBroker:
    def __init__(self, *, ack_status: str = "ACCEPTED", fill: BrokerFillUpdate | None = None) -> None:
        self.ack_status = ack_status
        self.fill = fill
        self.submit_count = 0
        self.orders: list[BrokerOrderState] = []

    def refresh_account_snapshot(self) -> BrokerAccountSnapshot:
        return BrokerAccountSnapshot(
            accountId="paper",
            equity=100_000,
            buyingPower=100_000,
            observedAt=NOW,
            sessionDate=SESSION_DATE,
            sourceAuthority="broker",
        )

    def verify_symbol_tradable(self, symbol: str) -> bool:
        return symbol.upper() == "SPY"

    def verify_buying_power(self, order_plan: OrderPlan) -> bool:
        return order_plan.quantity * order_plan.entryPrice <= 100_000

    def submit_order(self, order_plan: OrderPlan, client_order_id: str) -> BrokerOrderAck:
        self.submit_count += 1
        self.orders.append(
            BrokerOrderState(
                algorithmId="voting_ensemble",
                symbol=order_plan.symbol,
                side=order_plan.side,
                clientOrderId=client_order_id,
                orderType=order_plan.orderType,
                status="ACCEPTED",
                quantity=order_plan.quantity,
                entryPrice=order_plan.entryPrice,
                stopPrice=order_plan.stopPrice,
                submittedAt=NOW,
            )
        )
        return BrokerOrderAck(
            clientOrderId=client_order_id,
            brokerOrderId=f"broker-{client_order_id}",
            status=self.ack_status,
            acceptedAt=NOW if self.ack_status != "REJECTED" else None,
            rejectedReason="test rejection" if self.ack_status == "REJECTED" else None,
        )

    def refresh_order(self, client_order_id: str) -> BrokerFillUpdate | None:
        return self.fill.model_copy(update={"clientOrderId": client_order_id}) if self.fill else None

    def refresh_positions(self) -> list[BrokerPositionState]:
        return []

    def refresh_open_orders(self) -> list[BrokerOrderState]:
        return self.orders


def candidate(*, quantity: int) -> TradeCandidate:
    return TradeCandidate(
        candidateId="candidate-1",
        symbol="SPY",
        signal=Signal.BUY,
        direction=Direction.LONG,
        entryPrice=100.0,
        stopPrice=99.0,
        targetPrice=101.5,
        quantity=quantity,
        confidence=0.8,
        expectedValue=0.25,
        explanation="test candidate",
        generatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="candidate",
    )


def policy() -> EffectiveTradePolicy:
    return EffectiveTradePolicy(
        mode=OperatingMode.OFF,
        baselineSettings=BaselineTradingSettings(configurationHash="baseline"),
        hardRiskLimits=HardRiskLimits(configurationHash="limits"),
        dynamicBounds=DynamicPolicyBounds(
            minConfidence=0.0,
            minReliability=0.0,
            minRegimeFit=0.0,
            maxSpreadPercent=100.0,
            maxParticipationPercent=100.0,
            minLiquidityShares=0,
            configurationHash="bounds",
        ),
        accountRiskState=AccountRiskState(
            accountId="paper",
            equity=25_000,
            buyingPower=25_000,
            openPositionNotional=0,
            realizedPnlToday=0,
            tradesToday=0,
            observedAt=NOW,
            sessionDate=SESSION_DATE,
        ),
        maxQuantity=100,
        maxNotional=10_000.0,
        riskDollars=100.0,
        explanation="test policy",
        effectiveAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="policy",
    )


def gate_decision(eligible: bool) -> GlobalGateDecision:
    return GlobalGateDecision(
        status=GateStatus.PASS if eligible else GateStatus.FAIL,
        eligible=eligible,
        dataReady=True,
        reasonCodes=["test.gate"],
        explanation="test gate",
        checkedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="gate",
    )


def ml_result(mode: OperatingMode, *, accepted: bool) -> SafeMLInferenceResult:
    return SafeMLInferenceResult(
        mode=mode,
        effectiveMode=mode,
        deterministicSignal=Signal.BUY,
        finalSignal=Signal.BUY,
        candidateAccepted=accepted,
        mlWouldAcceptCandidate=accepted,
        appliedToOrder=mode == OperatingMode.ACTIVE,
        featureMissingness=0.0,
        modelHealth={"status": "test"},
        recommendedRiskCap=1.0,
        reasonCodes=["test.ml"],
        predictedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="ml",
    )


def order_plan(*, quantity: int = 10) -> OrderPlan:
    plan = VotingEnsembleExecutionAdapter().translate_candidate_to_order(
        candidate=candidate(quantity=quantity),
        policy=policy(),
        gateDecision=gate_decision(True),
        decidedAt=NOW,
        sessionDate=SESSION_DATE,
    )
    assert plan is not None
    assert voting_ensemble_client_order_id(orderPlan=plan, idempotencyKey="plan").startswith("ve-")
    return plan


if __name__ == "__main__":
    unittest.main()
