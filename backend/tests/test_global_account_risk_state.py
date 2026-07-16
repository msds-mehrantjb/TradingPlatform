from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from backend.app.domain.models import AccountRiskState, Direction, EnsembleDecision, OrderPlan, Signal, TradeCandidate
from backend.app.gates import (
    BrokerAccountSnapshot,
    BrokerOrderState,
    BrokerPositionState,
    GlobalGateEngine,
    GlobalGateInput,
    aggregate_global_account_risk,
)


NOW = datetime(2026, 1, 5, 16, 0, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)


class GlobalAccountRiskStateTest(unittest.TestCase):
    def test_aggregates_positions_pending_orders_and_partial_fills_across_algorithms(self) -> None:
        risk = aggregate_global_account_risk(broker_snapshot(), candidateSymbol="SPY", candidateSide=Signal.BUY)

        self.assertAlmostEqual(risk.riskState["totalOpenRiskDollars"], 1360.0)
        self.assertAlmostEqual(risk.riskState["totalOpenRiskPercent"], 13.6)
        self.assertAlmostEqual(risk.riskState["globalSpyNotionalDollars"], 24760.0)
        self.assertAlmostEqual(risk.riskState["globalDailyUnrealizedPnl"], -500.0)
        self.assertAlmostEqual(risk.riskState["estimatedExitCosts"], 2.0)
        self.assertAlmostEqual(risk.riskState["dailyNetPnlAfterExitCosts"], -502.0)
        self.assertTrue(risk.riskState["duplicateSpyExposure"])
        self.assertFalse(risk.riskState["conflictingSpyExposure"])
        self.assertEqual(risk.brokerState["positionsReconciled"], True)

    def test_two_algorithms_cannot_each_consume_the_complete_account_risk_budget(self) -> None:
        risk = aggregate_global_account_risk(broker_snapshot(), candidateSymbol="SPY", candidateSide=Signal.BUY)

        decision = GlobalGateEngine().evaluate(gate_input(risk.accountRiskState, risk.riskState, risk.brokerState))

        self.assertFalse(decision.allowed)
        self.assertIn("gate.risk.total_open_risk", decision.reasonCodes)
        self.assertIn("gate.risk.duplicate_spy_exposure", decision.reasonCodes)

    def test_global_loss_limit_uses_unrealized_losses_and_exit_costs(self) -> None:
        snapshot = broker_snapshot(realized_pnl_today=190.0)
        risk = aggregate_global_account_risk(snapshot, candidateSymbol="SPY", candidateSide=Signal.BUY)

        self.assertEqual(risk.accountRiskState.realizedPnlToday, 190.0)
        self.assertLess(risk.accountRiskState.dailyNetPnlAfterExitCosts or 0.0, -300.0)
        decision = GlobalGateEngine().evaluate(gate_input(risk.accountRiskState, risk.riskState, risk.brokerState))

        self.assertFalse(decision.allowed)
        self.assertIn("gate.risk.daily_loss", decision.reasonCodes)

    def test_protective_exits_remain_permitted_when_global_loss_limit_is_hit(self) -> None:
        risk = aggregate_global_account_risk(broker_snapshot(), candidateSymbol="SPY", candidateSide=Signal.BUY)

        decision = GlobalGateEngine().evaluate(
            gate_input(risk.accountRiskState, risk.riskState, risk.brokerState, orderIntent="protective_exit")
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.hardBlockers, [])
        self.assertIn("gate.risk.daily_loss", decision.reasonCodes)

    def test_broker_state_not_local_ui_history_is_final_authority(self) -> None:
        risk = aggregate_global_account_risk(
            broker_snapshot(source_authority="local_ui_history"),
            candidateSymbol="SPY",
            candidateSide=Signal.BUY,
        )

        self.assertFalse(risk.brokerState["brokerConnected"])
        self.assertIn("risk.authority.local_ui_history", risk.reasonCodes)
        decision = GlobalGateEngine().evaluate(gate_input(risk.accountRiskState, risk.riskState, risk.brokerState))

        self.assertFalse(decision.allowed)
        self.assertIn("gate.broker.disconnected", decision.reasonCodes)
        self.assertIn("gate.broker.positions_not_reconciled", decision.reasonCodes)

    def test_conflicting_spy_position_blocks_new_entry_without_portfolio_netting(self) -> None:
        risk = aggregate_global_account_risk(broker_snapshot(), candidateSymbol="SPY", candidateSide=Signal.SELL)

        decision = GlobalGateEngine().evaluate(gate_input(risk.accountRiskState, risk.riskState, risk.brokerState, candidate_side=Signal.SELL))

        self.assertFalse(decision.allowed)
        self.assertTrue(risk.riskState["conflictingSpyExposure"])
        self.assertIn("gate.risk.conflicting_spy_exposure", decision.reasonCodes)


def broker_snapshot(realized_pnl_today: float = 0.0, source_authority: str = "broker") -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        accountId="paper-account",
        equity=10_000,
        buyingPower=5_000,
        realizedPnlToday=realized_pnl_today,
        intradayEquityHigh=10_600,
        positions=[
            BrokerPositionState(
                algorithmId="voting_ensemble",
                symbol="SPY",
                side=Signal.BUY,
                quantity=100,
                averageEntryPrice=100.0,
                markPrice=95.0,
                stopPrice=90.0,
                openedAt=NOW,
            )
        ],
        pendingOrders=[
            BrokerOrderState(
                algorithmId="weighted_voting",
                symbol="SPY",
                side=Signal.BUY,
                orderType="LIMIT",
                quantity=100,
                entryPrice=95.0,
                stopPrice=90.0,
                submittedAt=NOW,
            )
        ],
        partiallyFilledOrders=[
            BrokerOrderState(
                algorithmId="confidence_aggregation",
                symbol="SPY",
                side=Signal.BUY,
                orderType="LIMIT",
                status="PARTIALLY_FILLED",
                quantity=100,
                filledQuantity=40,
                entryPrice=96.0,
                stopPrice=90.0,
                submittedAt=NOW,
            )
        ],
        exitCostPerShare=0.02,
        observedAt=NOW,
        sessionDate=SESSION_DATE,
        sourceAuthority=source_authority,
    )


def gate_input(
    account: AccountRiskState,
    risk_state: dict,
    broker_state: dict,
    *,
    orderIntent: str = "new_entry",
    candidate_side: Signal = Signal.BUY,
) -> GlobalGateInput:
    return GlobalGateInput(
        orderIntent=orderIntent,
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        symbol="SPY",
        accountRiskState=account,
        candidate=candidate(candidate_side),
        candidateStrategyFamily="TREND",
        setupSubtype="vwap_trend_continuation",
        ensembleDecision=ensemble_decision(candidate_side),
        orderPlan=order_plan(candidate_side),
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
        brokerState=broker_state,
        marketState={
            "symbolHalt": False,
            "luldPause": False,
            "marketWideCircuitBreaker": False,
            "lockedOrCrossedQuote": False,
            "spreadBps": 2.0,
            "weeklyDailyDirection": candidate_side.value,
            "oneHourDirection": candidate_side.value,
            "adx": 24.0,
            "economicEventState": {"state": "none", "importance": "low"},
            "relativeStrengthScore": 0.2 if candidate_side == Signal.BUY else -0.2,
            "breadthScore": 0.7 if candidate_side == Signal.BUY else 0.3,
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
        riskState={**risk_state, "consecutiveLosses": 0, "modelHealthy": True},
    )


def candidate(side: Signal) -> TradeCandidate:
    return TradeCandidate(
        candidateId=f"candidate-{side.value.lower()}",
        symbol="SPY",
        signal=side,
        direction=Direction.LONG if side == Signal.BUY else Direction.SHORT,
        entryPrice=95.0,
        stopPrice=90.0 if side == Signal.BUY else 100.0,
        targetPrice=105.0 if side == Signal.BUY else 85.0,
        quantity=10,
        confidence=0.75,
        expectedValue=0.1,
        features={"strategyFamily": "TREND", "setupSubtype": "vwap_trend_continuation"},
        explanation="Synthetic candidate.",
        generatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=f"candidate-{side.value}",
    )


def ensemble_decision(side: Signal) -> EnsembleDecision:
    return EnsembleDecision(
        decisionId=f"ensemble-{side.value.lower()}",
        signal=side,
        direction=Direction.LONG if side == Signal.BUY else Direction.SHORT,
        confidence=0.75,
        rawScore=0.5 if side == Signal.BUY else -0.5,
        finalScore=0.5 if side == Signal.BUY else -0.5,
        buyConfidence=0.75 if side == Signal.BUY else 0.0,
        sellConfidence=0.75 if side == Signal.SELL else 0.0,
        holdConfidence=0.25,
        supportingFamilies=["TREND", "BREAKOUT"],
        eligibleStrategyCount=4,
        reasonCodes=["test.candidate"],
        explanation="Synthetic ensemble.",
        dataReady=True,
        eligible=True,
        decidedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=f"ensemble-{side.value}",
        engineVersion="ensemble-v2-test",
    )


def order_plan(side: Signal) -> OrderPlan:
    return OrderPlan(
        orderPlanId=f"order-{side.value.lower()}",
        candidateId=f"candidate-{side.value.lower()}",
        symbol="SPY",
        side=side,
        orderType="LIMIT",
        quantity=10,
        entryPrice=95.0,
        stopPrice=90.0 if side == Signal.BUY else 100.0,
        targetPrice=105.0 if side == Signal.BUY else 85.0,
        limitPrice=95.0,
        timeInForce="DAY",
        eligible=True,
        explanation="Synthetic order.",
        generatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=f"order-{side.value}",
    )


if __name__ == "__main__":
    unittest.main()
