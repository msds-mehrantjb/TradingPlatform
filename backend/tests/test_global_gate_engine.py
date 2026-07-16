from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from backend.app.domain.models import AccountRiskState, Direction, EnsembleDecision, GateStatus, OrderPlan, Signal, TradeCandidate
from backend.app.domain.models import StrategyFamily
from backend.app.gates import GLOBAL_GATE_ENGINE_VERSION, GlobalGateEngine, GlobalGateInput


NOW = datetime(2026, 1, 5, 15, 30, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)


class GlobalGateEngineTest(unittest.TestCase):
    def test_all_hard_gate_groups_pass_for_valid_new_entry(self) -> None:
        decision = GlobalGateEngine().evaluate(gate_input(orderPlan=order_plan()))

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.gateVersion, GLOBAL_GATE_ENGINE_VERSION)
        self.assertEqual(decision.hardBlockers, [])
        self.assertEqual(decision.maximumRiskDollars, 100.0)
        self.assertEqual(decision.maximumNotionalDollars, 1000.0)
        groups = {result.group for result in [*decision.informationalResults, *decision.cautions, *decision.hardBlockers]}
        self.assertTrue(
            {
                "Operational",
                "Data health",
                "Broker and account health",
                "Market safety",
                "Global account risk",
                "Execution safety",
                "Candidate quality",
                "Order integrity",
            }.issubset(groups)
        )
        canonical = decision.to_global_gate_decision()
        self.assertEqual(canonical.status, GateStatus.PASS.value)
        self.assertTrue(canonical.eligible)
        self.assertTrue(canonical.dataReady)

    def test_automatic_new_entry_fails_closed_when_critical_feed_is_unavailable(self) -> None:
        data_state = pass_data_state()
        data_state.pop("freshQuote")

        decision = GlobalGateEngine().evaluate(gate_input(dataState=data_state, orderPlan=order_plan()))
        canonical = decision.to_global_gate_decision()

        self.assertFalse(decision.allowed)
        self.assertFalse(canonical.eligible)
        self.assertFalse(canonical.dataReady)
        self.assertTrue(any("critical_feed_unavailable" in code for code in decision.reasonCodes))
        self.assertIn("data.fresh_quote", {result.gateId for result in decision.hardBlockers})

    def test_non_entry_intents_are_allowed_but_preserve_failed_gate_reasons(self) -> None:
        data_state = pass_data_state()
        data_state["freshQuote"] = False

        decision = GlobalGateEngine().evaluate(
            gate_input(orderIntent="protective_exit", dataState=data_state, orderPlan=order_plan())
        )
        canonical = decision.to_global_gate_decision()

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.hardBlockers, [])
        self.assertTrue(any("gate.data_health.fresh_quote_unavailable" in code for code in decision.reasonCodes))
        self.assertEqual(canonical.status, GateStatus.CAUTION.value)
        self.assertTrue(canonical.eligible)

    def test_market_and_broker_failure_reasons_are_preserved_for_display(self) -> None:
        broker_state = pass_broker_state()
        broker_state["accountNotRestricted"] = False
        market_state = pass_market_state()
        market_state["lockedOrCrossedQuote"] = True

        decision = GlobalGateEngine().evaluate(
            gate_input(brokerState=broker_state, marketState=market_state, orderPlan=order_plan())
        )

        self.assertFalse(decision.allowed)
        self.assertIn("gate.broker.account_restricted", decision.reasonCodes)
        self.assertIn("gate.market.locked_or_crossed_quote", decision.reasonCodes)
        canonical_codes = {code for result in decision.to_global_gate_decision().gateResults for code in result.reasonCodes}
        self.assertIn("gate.broker.account_restricted", canonical_codes)
        self.assertIn("gate.market.locked_or_crossed_quote", canonical_codes)

    def test_order_integrity_blocks_zero_quantity_new_entry(self) -> None:
        decision = GlobalGateEngine().evaluate(gate_input(orderPlan=order_plan(quantity=0)))

        self.assertFalse(decision.allowed)
        self.assertIn("gate.order.positive_quantity", decision.reasonCodes)
        self.assertIn("order.positive_quantity", {result.gateId for result in decision.hardBlockers})

    def test_daily_loss_hard_stop_caps_risk_to_zero(self) -> None:
        account = account_state(realized_pnl_today=-350.0)

        decision = GlobalGateEngine().evaluate(gate_input(accountRiskState=account, orderPlan=order_plan()))

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.riskMultiplierCap, 0.0)
        self.assertIn("gate.risk.daily_loss", decision.reasonCodes)

    def test_bearish_one_hour_direction_blocks_long_trend_continuation(self) -> None:
        decision = GlobalGateEngine().evaluate(
            gate_input(
                candidateStrategyFamily=StrategyFamily.TREND,
                setupSubtype="vwap_trend_continuation",
                marketState=conditional_market_state(oneHourDirection="SELL"),
                executionState=conditional_execution_state(),
                orderPlan=order_plan(),
            )
        )

        self.assertFalse(decision.allowed)
        self.assertIn("gate.conditional.one_hour.conflict", decision.reasonCodes)
        self.assertIn("conditional.one_hour_direction", {result.gateId for result in decision.hardBlockers})

    def test_bearish_one_hour_direction_only_reduces_long_reversal_setup(self) -> None:
        decision = GlobalGateEngine().evaluate(
            gate_input(
                candidateStrategyFamily=StrategyFamily.REVERSAL,
                setupSubtype="liquidity_sweep_reversal",
                marketState=conditional_market_state(oneHourDirection="SELL"),
                executionState=conditional_execution_state(),
                orderPlan=order_plan(),
            )
        )

        self.assertTrue(decision.allowed)
        self.assertIn("gate.conditional.one_hour.conflict", decision.reasonCodes)
        self.assertIn("conditional.one_hour_direction", {result.gateId for result in decision.cautions})

    def test_regime_compatibility_varies_by_family(self) -> None:
        trend = GlobalGateEngine().evaluate(
            gate_input(
                candidateStrategyFamily=StrategyFamily.TREND,
                setupSubtype="multi_timeframe_trend_alignment",
                marketState=conditional_market_state(adx=36.0),
                executionState=conditional_execution_state(),
                orderPlan=order_plan(),
            )
        )
        mean_reversion = GlobalGateEngine().evaluate(
            gate_input(
                candidateStrategyFamily=StrategyFamily.MEAN_REVERSION,
                setupSubtype="vwap_mean_reversion",
                marketState=conditional_market_state(adx=36.0),
                executionState=conditional_execution_state(),
                orderPlan=order_plan(),
            )
        )

        self.assertTrue(trend.allowed)
        self.assertIn("gate.conditional.regime.high_adx_supports_family", trend.reasonCodes)
        self.assertTrue(mean_reversion.allowed)
        self.assertIn("gate.conditional.regime.high_adx_weakens_reversion", mean_reversion.reasonCodes)
        self.assertIn("conditional.market_regime_compatibility", {result.gateId for result in mean_reversion.cautions})

    def test_event_context_conflict_does_not_replace_ensemble_direction(self) -> None:
        trade_candidate = candidate()
        decision = GlobalGateEngine().evaluate(
            gate_input(
                candidate=trade_candidate,
                candidateStrategyFamily=StrategyFamily.TREND,
                setupSubtype="first_pullback_after_open",
                marketState=conditional_market_state(event_direction="SELL"),
                executionState=conditional_execution_state(),
                orderPlan=order_plan(),
            )
        )

        self.assertEqual(trade_candidate.signal, Signal.BUY.value)
        self.assertTrue(decision.allowed)
        self.assertIn("gate.conditional.event.directional_conflict_context_only", decision.reasonCodes)
        self.assertIn("conditional.economic_event_context", {result.gateId for result in decision.cautions})


def gate_input(**overrides):
    payload = {
        "orderIntent": "new_entry",
        "evaluatedAt": NOW,
        "sessionDate": SESSION_DATE,
        "symbol": "SPY",
        "accountRiskState": account_state(),
        "candidate": candidate(),
        "ensembleDecision": ensemble_decision(),
        "orderPlan": None,
        "operationalState": pass_operational_state(),
        "dataState": pass_data_state(),
        "brokerState": pass_broker_state(),
        "marketState": pass_market_state(),
        "executionState": pass_execution_state(),
        "riskState": pass_risk_state(),
    }
    payload.update(overrides)
    return GlobalGateInput(**payload)


def pass_operational_state() -> dict[str, bool]:
    return {
        "tradingEnabled": True,
        "paperTradingMode": True,
        "marketOpen": True,
        "entryWindowOpen": True,
        "validSession": True,
    }


def pass_data_state() -> dict[str, bool]:
    return {
        "freshCandle": True,
        "freshQuote": True,
        "validBidAsk": True,
        "monotonicTimestamps": True,
        "requiredTimeframeSynchronized": True,
        "requiredAuxiliaryDataReady": True,
        "featureSchemaValid": True,
    }


def pass_broker_state() -> dict[str, bool]:
    return {
        "brokerConnected": True,
        "paperAccountActive": True,
        "accountNotRestricted": True,
        "symbolTradable": True,
        "buyingPowerCurrent": True,
        "positionsReconciled": True,
        "openOrdersReconciled": True,
    }


def pass_market_state() -> dict[str, float | bool]:
    return {
        "symbolHalt": False,
        "luldPause": False,
        "marketWideCircuitBreaker": False,
        "lockedOrCrossedQuote": False,
        "spreadBps": 3.0,
        "realizedVolatilityPercentile": 0.5,
    }


def conditional_market_state(
    *,
    oneHourDirection: str = "BUY",
    weeklyDailyDirection: str = "BUY",
    adx: float = 22.0,
    event_direction: str = "HOLD",
) -> dict[str, object]:
    state = pass_market_state()
    state.update(
        {
            "weeklyDailyDirection": weeklyDailyDirection,
            "oneHourDirection": oneHourDirection,
            "marketRegimeLabel": "strong_trend" if adx >= 30 else "weak_trend",
            "adx": adx,
            "economicEventState": {
                "state": "active" if event_direction != "HOLD" else "none",
                "importance": "medium",
                "directionalReaction": event_direction,
            },
            "relativeStrengthScore": 0.25,
            "breadthScore": 0.70,
            "breadthCoverage": 0.90,
            "minutesUntilClose": 90,
        }
    )
    return state


def pass_execution_state() -> dict[str, float | bool]:
    return {
        "liquidityShares": 100_000,
        "spreadBps": 3.0,
        "expectedSlippageDollars": 0.01,
        "entryDistanceDollars": 0.05,
        "duplicateOrder": False,
        "conflictingOrder": False,
        "cooldownActive": False,
        "riskWithinBudget": True,
        "notionalWithinCap": True,
        "protectiveOrderPossible": True,
        "uniqueClientOrderId": True,
    }


def conditional_execution_state() -> dict[str, float | bool]:
    state = pass_execution_state()
    state.update(
        {
            "oneMinuteExecutionTrigger": True,
            "fiveMinuteExecutionConfirmation": True,
        }
    )
    return state


def pass_risk_state() -> dict[str, float | int | bool]:
    return {
        "drawdownFromIntradayHighPercent": 0.0,
        "totalOpenRiskPercent": 0.0,
        "totalSpyNotionalPercent": 0.0,
        "sameDirectionExposurePercent": 0.0,
        "consecutiveLosses": 0,
        "modelHealthy": True,
    }


def account_state(realized_pnl_today: float = 0.0) -> AccountRiskState:
    return AccountRiskState(
        accountId="paper-account",
        equity=10_000,
        buyingPower=10_000,
        openPositionNotional=0,
        realizedPnlToday=realized_pnl_today,
        tradesToday=0,
        observedAt=NOW,
        sessionDate=SESSION_DATE,
    )


def ensemble_decision() -> EnsembleDecision:
    return EnsembleDecision(
        decisionId="ensemble-buy",
        signal=Signal.BUY,
        direction=Direction.LONG,
        confidence=0.72,
        rawScore=0.56,
        finalScore=0.56,
        buyConfidence=0.72,
        sellConfidence=0.0,
        holdConfidence=0.28,
        supportingFamilies=["TREND", "BREAKOUT"],
        opposingFamilies=[],
        eligibleStrategyCount=4,
        reasonCodes=["test.buy"],
        explanation="Synthetic ensemble decision.",
        dataReady=True,
        eligible=True,
        decidedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="ensemble-test",
        engineVersion="ensemble-v2-test",
    )


def candidate() -> TradeCandidate:
    return TradeCandidate(
        candidateId="candidate-buy",
        symbol="SPY",
        signal=Signal.BUY,
        direction=Direction.LONG,
        entryPrice=100.0,
        stopPrice=99.0,
        targetPrice=102.0,
        quantity=10,
        confidence=0.72,
        expectedValue=0.10,
        reasonCodes=["test.candidate"],
        explanation="Synthetic candidate.",
        generatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="candidate-test",
    )


def order_plan(quantity: int = 10) -> OrderPlan:
    return OrderPlan(
        orderPlanId="order-buy",
        candidateId="candidate-buy",
        symbol="SPY",
        side=Signal.BUY,
        orderType="LIMIT",
        quantity=quantity,
        entryPrice=100.0,
        stopPrice=99.0,
        targetPrice=102.0,
        limitPrice=100.0,
        timeInForce="DAY",
        eligible=quantity > 0,
        validationErrors=[] if quantity > 0 else ["test.zero_quantity"],
        explanation="Synthetic order plan.",
        generatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="order-test",
    )


if __name__ == "__main__":
    unittest.main()
