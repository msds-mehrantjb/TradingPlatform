from __future__ import annotations

import unittest
import shutil
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from backend.app.domain.models import AccountRiskState, Direction, EnsembleDecision, OrderPlan, Signal, TradeCandidate
from backend.app.execution import cost_model
from backend.app.execution import BrokerFillUpdate, BrokerOrderAck, BrokerReconciliationEngine, BrokerSubmissionRequest, deterministic_client_order_id
from backend.app.gates import BrokerAccountSnapshot, BrokerOrderState, BrokerPositionState, GlobalGateInput


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)


class BrokerReconciliationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = Path("backend/.test_artifacts") / f"broker_execution_cost_{uuid.uuid4().hex}"
        shutil.rmtree(self.scratch, ignore_errors=True)
        self.previous_dirs = (
            cost_model.EXECUTION_COST_LEDGER_DIR,
            cost_model.EXECUTION_COST_CANDIDATE_DIR,
            cost_model.EXECUTION_COST_ACTIVE_DIR,
            cost_model.EXECUTION_COST_ACTIVE_HISTORY_DIR,
        )
        cost_model.EXECUTION_COST_LEDGER_DIR = self.scratch / "ledger"
        cost_model.EXECUTION_COST_CANDIDATE_DIR = self.scratch / "artifacts" / "candidates"
        cost_model.EXECUTION_COST_ACTIVE_DIR = self.scratch / "artifacts" / "active"
        cost_model.EXECUTION_COST_ACTIVE_HISTORY_DIR = self.scratch / "artifacts" / "active_history"

    def tearDown(self) -> None:
        (
            cost_model.EXECUTION_COST_LEDGER_DIR,
            cost_model.EXECUTION_COST_CANDIDATE_DIR,
            cost_model.EXECUTION_COST_ACTIVE_DIR,
            cost_model.EXECUTION_COST_ACTIVE_HISTORY_DIR,
        ) = self.previous_dirs
        shutil.rmtree(self.scratch, ignore_errors=True)

    def test_replaying_same_submission_request_is_idempotent(self) -> None:
        broker = FakePaperBroker(ack_status="ACCEPTED")
        engine = BrokerReconciliationEngine(broker)
        request = submission_request()

        first = engine.submit_once(request)
        second = engine.submit_once(request)

        self.assertTrue(first.submitted)
        self.assertFalse(first.duplicate)
        self.assertTrue(second.duplicate)
        self.assertEqual(first.clientOrderId, second.clientOrderId)
        self.assertEqual(broker.submit_count, 1)
        self.assertIn("broker.idempotent_duplicate_request", second.reasonCodes)

    def test_partial_fill_updates_protective_order_quantity(self) -> None:
        broker = FakePaperBroker(
            ack_status="ACCEPTED",
            fill=BrokerFillUpdate(
                clientOrderId="placeholder",
                filledQuantity=4,
                averageFillPrice=100.01,
                status="PARTIALLY_FILLED",
                updatedAt=NOW,
            ),
            expose_fill_as_position=True,
        )
        result = BrokerReconciliationEngine(broker).submit_once(submission_request(quantity=10))

        self.assertTrue(result.brokerAccepted)
        self.assertEqual(result.fillUpdate.filledQuantity, 4)
        self.assertEqual(result.protectiveOrder.quantity, 4)
        self.assertEqual(result.orderSubmissionTimestamp, NOW)
        self.assertEqual(result.actualBrokerFillLifecycle["sourceAuthority"], "broker")
        self.assertEqual(result.actualBrokerFillLifecycle["filledQuantity"], 4)
        self.assertIsNotNone(result.executionCostObservation)
        rows = cost_model.load_observations("SPY")
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["labels"]["partialFill"])
        self.assertEqual(rows[0]["orderSubmissionTimestamp"], NOW.isoformat())
        self.assertTrue(result.localPositionCreated)
        self.assertIn("broker.partial_fill_tracked", result.reasonCodes)
        self.assertIn("broker.protective_quantity_matches_fill", result.reasonCodes)

    def test_rejected_entry_does_not_create_fictional_local_position(self) -> None:
        broker = FakePaperBroker(ack_status="REJECTED", rejected_reason="paper broker rejected order")
        result = BrokerReconciliationEngine(broker).submit_once(submission_request())

        self.assertTrue(result.submitted)
        self.assertFalse(result.brokerAccepted)
        self.assertEqual(result.brokerStatus, "REJECTED")
        self.assertFalse(result.localPositionCreated)
        self.assertIn("broker.submission_rejected", result.reasonCodes)

    def test_local_and_broker_state_divergence_generates_hard_warning(self) -> None:
        broker = FakePaperBroker(
            ack_status="ACCEPTED",
            fill=BrokerFillUpdate(
                clientOrderId="placeholder",
                filledQuantity=10,
                averageFillPrice=100.01,
                status="FILLED",
                updatedAt=NOW,
            ),
            expose_fill_as_position=False,
        )
        result = BrokerReconciliationEngine(broker).submit_once(submission_request())

        self.assertTrue(result.hardOperationalWarning)
        self.assertFalse(result.localPositionCreated)
        self.assertIn("broker.local_broker_state_divergence", result.reasonCodes)

    def test_multiple_algorithms_same_candidate_are_blocked_by_broker_visible_pending_order(self) -> None:
        broker = FakePaperBroker(ack_status="ACCEPTED")
        engine = BrokerReconciliationEngine(broker)

        first = engine.submit_once(submission_request(algorithm_version="ensemble-v2"))
        second = engine.submit_once(submission_request(algorithm_version="meta-strategy-v2"))

        self.assertTrue(first.brokerAccepted)
        self.assertFalse(second.submitted)
        self.assertEqual(broker.submit_count, 1)
        self.assertIn("gate.risk.duplicate_spy_exposure", second.reasonCodes)

    def test_deterministic_client_order_id_uses_required_fields(self) -> None:
        one = deterministic_client_order_id(
            symbol="SPY",
            decision_timestamp=NOW,
            algorithm_version="ensemble-v2",
            setup_id="setup-a",
            side=Signal.BUY,
        )
        two = deterministic_client_order_id(
            symbol="SPY",
            decision_timestamp=NOW,
            algorithm_version="ensemble-v2",
            setup_id="setup-a",
            side=Signal.BUY,
        )
        changed_side = deterministic_client_order_id(
            symbol="SPY",
            decision_timestamp=NOW,
            algorithm_version="ensemble-v2",
            setup_id="setup-a",
            side=Signal.SELL,
        )

        self.assertEqual(one, two)
        self.assertNotEqual(one, changed_side)


class FakePaperBroker:
    def __init__(
        self,
        *,
        ack_status: str,
        rejected_reason: str | None = None,
        fill: BrokerFillUpdate | None = None,
        expose_fill_as_position: bool = False,
    ) -> None:
        self.ack_status = ack_status
        self.rejected_reason = rejected_reason
        self.fill = fill
        self.expose_fill_as_position = expose_fill_as_position
        self.submit_count = 0
        self.open_orders: list[BrokerOrderState] = []
        self.positions: list[BrokerPositionState] = []

    def refresh_account_snapshot(self) -> BrokerAccountSnapshot:
        return BrokerAccountSnapshot(
            accountId="paper-account",
            equity=100_000,
            buyingPower=100_000,
            realizedPnlToday=0,
            positions=self.positions,
            pendingOrders=self.open_orders,
            partiallyFilledOrders=[],
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
        if self.ack_status == "ACCEPTED":
            self.open_orders.append(
                BrokerOrderState(
                    algorithmId="meta_strategy",
                    symbol=order_plan.symbol,
                    side=order_plan.side,
                    clientOrderId=client_order_id,
                    orderType=order_plan.orderType,
                    status="ACCEPTED",
                    quantity=order_plan.quantity,
                    filledQuantity=0,
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
            rejectedReason=self.rejected_reason,
        )

    def refresh_order(self, client_order_id: str) -> BrokerFillUpdate | None:
        if self.fill is None:
            return None
        fill = self.fill.model_copy(update={"clientOrderId": client_order_id})
        if self.expose_fill_as_position and not self.positions:
            self.positions.append(
                BrokerPositionState(
                    algorithmId="meta_strategy",
                    symbol="SPY",
                    side=Signal.BUY,
                    quantity=fill.filledQuantity,
                    averageEntryPrice=fill.averageFillPrice or 100.0,
                    markPrice=fill.averageFillPrice or 100.0,
                    stopPrice=99.0,
                    openedAt=NOW,
                )
            )
        return fill

    def refresh_positions(self) -> list[BrokerPositionState]:
        return self.positions

    def refresh_open_orders(self) -> list[BrokerOrderState]:
        return self.open_orders


def submission_request(
    *,
    quantity: int = 10,
    algorithm_version: str = "ensemble-v2",
    setup_id: str = "setup-a",
) -> BrokerSubmissionRequest:
    order = order_plan(quantity=quantity)
    return BrokerSubmissionRequest(
        orderPlan=order,
        decisionTimestampUtc=NOW,
        algorithmVersion=algorithm_version,
        setupId=setup_id,
        gateInputTemplate=gate_input(order),
    )


def gate_input(order: OrderPlan) -> GlobalGateInput:
    return GlobalGateInput(
        orderIntent="new_entry",
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        symbol="SPY",
        accountRiskState=account_state(),
        candidate=candidate(),
        candidateStrategyFamily="TREND",
        setupSubtype="vwap_trend_continuation",
        ensembleDecision=ensemble_decision(),
        orderPlan=order,
        operationalState={
            "tradingEnabled": True,
            "paperTradingMode": True,
            "marketOpen": True,
            "entryWindowOpen": True,
            "validSession": True,
        },
        dataState={
            "freshCandle": True,
            "freshQuote": True,
            "validBidAsk": True,
            "monotonicTimestamps": True,
            "requiredTimeframeSynchronized": True,
            "requiredAuxiliaryDataReady": True,
            "featureSchemaValid": True,
        },
        brokerState={
            "brokerConnected": True,
            "paperAccountActive": True,
            "accountNotRestricted": True,
            "symbolTradable": True,
            "buyingPowerCurrent": True,
            "positionsReconciled": True,
            "openOrdersReconciled": True,
        },
        marketState={
            "symbolHalt": False,
            "luldPause": False,
            "marketWideCircuitBreaker": False,
            "lockedOrCrossedQuote": False,
            "spreadBps": 2.0,
            "weeklyDailyDirection": "BUY",
            "oneHourDirection": "BUY",
            "adx": 24.0,
            "economicEventState": {"state": "none", "importance": "low"},
            "relativeStrengthScore": 0.2,
            "breadthScore": 0.7,
            "breadthCoverage": 0.9,
            "minutesUntilClose": 120,
        },
        executionState={
            "liquidityShares": 100_000,
            "spreadBps": 2.0,
            "expectedSlippageDollars": 0.01,
            "entryDistanceDollars": 0.01,
            "duplicateOrder": False,
            "conflictingOrder": False,
            "cooldownActive": False,
            "oneMinuteExecutionTrigger": True,
            "fiveMinuteExecutionConfirmation": True,
            "riskWithinBudget": True,
            "notionalWithinCap": True,
            "protectiveOrderPossible": True,
            "uniqueClientOrderId": True,
        },
        riskState={
            "drawdownFromIntradayHighPercent": 0.0,
            "totalOpenRiskPercent": 0.0,
            "totalSpyNotionalPercent": 0.0,
            "sameDirectionExposurePercent": 0.0,
            "consecutiveLosses": 0,
            "modelHealthy": True,
        },
    )


def account_state() -> AccountRiskState:
    return AccountRiskState(
        accountId="paper-account",
        equity=100_000,
        buyingPower=100_000,
        openPositionNotional=0,
        realizedPnlToday=0,
        tradesToday=0,
        observedAt=NOW,
        sessionDate=SESSION_DATE,
    )


def candidate() -> TradeCandidate:
    return TradeCandidate(
        candidateId="candidate-buy",
        symbol="SPY",
        signal=Signal.BUY,
        direction=Direction.LONG,
        entryPrice=100,
        stopPrice=99,
        targetPrice=102,
        quantity=10,
        confidence=0.75,
        expectedValue=0.1,
        features={"strategyFamily": "TREND", "setupSubtype": "vwap_trend_continuation"},
        explanation="Synthetic candidate.",
        generatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="candidate",
    )


def ensemble_decision() -> EnsembleDecision:
    return EnsembleDecision(
        decisionId="ensemble-buy",
        signal=Signal.BUY,
        direction=Direction.LONG,
        confidence=0.75,
        rawScore=0.5,
        finalScore=0.5,
        buyConfidence=0.75,
        sellConfidence=0.0,
        holdConfidence=0.25,
        supportingFamilies=["TREND", "BREAKOUT"],
        eligibleStrategyCount=4,
        reasonCodes=["test.buy"],
        explanation="Synthetic ensemble.",
        dataReady=True,
        eligible=True,
        decidedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="ensemble",
        engineVersion="ensemble-v2-test",
    )


def order_plan(quantity: int = 10) -> OrderPlan:
    return OrderPlan(
        orderPlanId="order-buy",
        candidateId="candidate-buy",
        symbol="SPY",
        side=Signal.BUY,
        orderType="LIMIT",
        quantity=quantity,
        entryPrice=100,
        stopPrice=99,
        targetPrice=102,
        limitPrice=100,
        timeInForce="DAY",
        eligible=True,
        explanation="Synthetic order.",
        generatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="order",
    )


if __name__ == "__main__":
    unittest.main()
