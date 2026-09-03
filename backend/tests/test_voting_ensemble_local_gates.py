import unittest
from datetime import UTC, date, datetime

import backend.app.algorithms.voting_ensemble.service as service_module
from backend.app.algorithms.voting_ensemble.gates import VotingEnsembleLocalGateEngine
from backend.app.algorithms.voting_ensemble.models import VotingEnsembleEvaluateRequest
from backend.app.algorithms.voting_ensemble.service import VotingEnsembleService, _vote, voting_ensemble_service_runtime_bindings
from backend.app.algorithms.voting_ensemble.strategies.registry import StrategyCollection
from backend.app.domain.models import AccountRiskState, Direction, EnsembleDecision, RegimeState, Signal
from backend.app.gates import GlobalGateInput
from backend.tests.test_voting_ensemble_snapshot import candles, snapshot_payload


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)


class VotingEnsembleLocalGatesTest(unittest.TestCase):
    def test_active_safety_module_is_actual_runtime_binding(self) -> None:
        runtime = voting_ensemble_service_runtime_bindings()

        self.assertEqual(runtime["actualRuntimeBindings"][StrategyCollection.SAFETY.value], ("cash_avoid_trading_filter",))
        self.assertTrue(runtime["validation"]["valid"])

    def test_unknown_mandatory_safety_state_fails_closed(self) -> None:
        gate_input = base_gate_input().model_copy(update={"dataState": {"freshCandle": True}})
        decision = VotingEnsembleLocalGateEngine().evaluate(
            gate_input
        ).to_global_gate_decision()

        self.assertFalse(decision.eligible)
        self.assertIn("voting_ensemble.local_gate.stale_or_missing_quote:unknown_mandatory_state", decision.reasonCodes)

    def test_local_gate_catalog_blocks_required_conditions(self) -> None:
        cases = (
            ("feed_degradation", {"dataState": {"feedHealthy": False}}, "voting_ensemble.local_gate.feed_degradation"),
            ("clock_disagreement", {"operationalState": {"clockDisagreement": True}}, "voting_ensemble.local_gate.clock_disagreement"),
            ("market_halt", {"marketState": {"symbolHalt": True}}, "voting_ensemble.local_gate.market_halt"),
            ("event_blackout", {"marketState": {"eventBlackout": True}}, "voting_ensemble.local_gate.event_blackout"),
            ("spread", {"marketState": {"spreadBps": 50.0}}, "voting_ensemble.local_gate.excessive_spread"),
            ("slippage", {"executionState": {"expectedSlippageDollars": 0.50}}, "voting_ensemble.local_gate.excessive_expected_slippage"),
            ("liquidity", {"executionState": {"liquidityShares": 0}}, "voting_ensemble.local_gate.insufficient_liquidity"),
            ("daily_loss", {"accountRiskState": account_state(realized=-600.0)}, "voting_ensemble.local_gate.daily_loss"),
            ("drawdown", {"accountRiskState": account_state(drawdown=8.0)}, "voting_ensemble.local_gate.intraday_drawdown"),
            ("open_risk", {"accountRiskState": account_state(open_risk=4.0)}, "voting_ensemble.local_gate.open_risk"),
            ("notional", {"accountRiskState": account_state(notional=60.0)}, "voting_ensemble.local_gate.notional_exposure"),
            ("same_direction", {"accountRiskState": account_state(same_direction=60.0)}, "voting_ensemble.local_gate.same_direction_exposure"),
            ("losses", {"riskState": {"consecutiveLosses": 3}}, "voting_ensemble.local_gate.consecutive_loss_limit"),
            ("cooldown", {"executionState": {"cooldownActive": True}}, "voting_ensemble.local_gate.cooldown_after_execution_failure"),
            ("position_conflict", {"riskState": {"existingPositionConflict": True}}, "voting_ensemble.local_gate.existing_position_conflict"),
        )
        engine = VotingEnsembleLocalGateEngine()
        for name, overrides, reason_code in cases:
            with self.subTest(name=name):
                decision = engine.evaluate(base_gate_input(**overrides)).to_global_gate_decision()
                self.assertFalse(decision.eligible)
                self.assertIn(reason_code, decision.reasonCodes)

    def test_trade_count_is_not_capped_unless_a_cap_is_configured(self) -> None:
        """The day's activity is bounded by the daily-loss and exposure limits, not a count."""
        from backend.app.algorithms.voting_ensemble.gates import voting_ensemble_local_gate_config

        uncapped = VotingEnsembleLocalGateEngine().evaluate(base_gate_input(accountRiskState=account_state(trades=12))).to_global_gate_decision()
        self.assertNotIn("voting_ensemble.local_gate.trade_count_limit", uncapped.reasonCodes)

        capped_engine = VotingEnsembleLocalGateEngine(voting_ensemble_local_gate_config().model_copy(update={"maximumTradesPerDay": 3}))
        capped = capped_engine.evaluate(base_gate_input(accountRiskState=account_state(trades=3))).to_global_gate_decision()
        self.assertFalse(capped.eligible)
        self.assertIn("voting_ensemble.local_gate.trade_count_limit", capped.reasonCodes)

        # The daily-loss limit is what stops the day.
        lost = VotingEnsembleLocalGateEngine().evaluate(base_gate_input(accountRiskState=account_state(trades=12, realized=-600.0))).to_global_gate_decision()
        self.assertFalse(lost.eligible)
        self.assertIn("voting_ensemble.local_gate.daily_loss", lost.reasonCodes)

    def test_upstream_global_gate_failure_is_advisory_for_local_paper(self) -> None:
        gate_input = base_gate_input(
            operationalState={
                "upstreamGlobalGateDecision": {
                    "status": "FAIL",
                    "eligible": False,
                    "dataReady": True,
                    "gateResults": [],
                    "reasonCodes": ["global.risk.blocked_elsewhere"],
                    "explanation": "External read-only portfolio gate reported a block.",
                    "checkedAt": NOW,
                    "sessionDate": SESSION_DATE,
                    "configurationHash": "global-test",
                }
            }
        )

        decision = VotingEnsembleLocalGateEngine().evaluate(gate_input).to_global_gate_decision()

        self.assertTrue(decision.eligible)
        self.assertIn("voting_ensemble.local_gate.global_upstream_advisory_block", decision.reasonCodes)
        self.assertNotIn("voting_ensemble.local_gate.global_hard_gate_block", decision.reasonCodes)

    def test_local_account_risk_still_blocks_local_paper_entries(self) -> None:
        gate_input = base_gate_input(accountRiskState=account_state(realized=-600.0))

        decision = VotingEnsembleLocalGateEngine().evaluate(gate_input).to_global_gate_decision()

        self.assertFalse(decision.eligible)
        self.assertIn("voting_ensemble.local_gate.daily_loss", decision.reasonCodes)
        self.assertNotIn("voting_ensemble.local_gate.global_hard_gate_block", decision.reasonCodes)

    def test_service_pre_gate_block_skips_directional_evaluation(self) -> None:
        original_directional = service_module.DIRECTIONAL_STRATEGIES
        calls: list[str] = []

        def recorder(request: VotingEnsembleEvaluateRequest):
            calls.append("called")
            return _vote("Multi-Timeframe Trend Alignment", "trend", "Buy", 80, "should not run", "test.should_not_run")

        payload = snapshot_payload(candles(30))
        payload["market_context"]["operationalHealthSnapshot"]["feedDegraded"] = True
        service_module.DIRECTIONAL_STRATEGIES = (recorder,)
        try:
            result = VotingEnsembleService().evaluate(payload)
        finally:
            service_module.DIRECTIONAL_STRATEGIES = original_directional

        self.assertEqual(calls, [])
        self.assertEqual(result["final_signal"], "Hold")
        self.assertTrue(result["safety_gate_failed"])
        self.assertIn("data.feed_health", result["blocked_gate_ids"])
        self.assertEqual(result["decision_trace"][2]["status"], "blocked")

    def test_service_post_gate_blocks_candidate_after_family_aggregation(self) -> None:
        original_directional = service_module.DIRECTIONAL_STRATEGIES
        original_context = service_module.CONTEXT_STRATEGIES
        original_classifier = service_module.REGIME_CLASSIFIER
        calls: list[str] = []

        def trend_buy(request: VotingEnsembleEvaluateRequest):
            calls.append("trend")
            return _vote("Multi-Timeframe Trend Alignment", "trend", "Buy", 80, "trend", "test.trend", features={"strategyId": "multi_timeframe_trend_alignment"})

        def reversal_buy(request: VotingEnsembleEvaluateRequest):
            calls.append("reversal")
            return _vote("Failed Breakout Reversal", "reversal", "Buy", 80, "reversal", "test.reversal", features={"strategyId": "failed_breakout_reversal"})

        payload = snapshot_payload(candles(30))
        payload["nbbo"]["bid"] = 100.00
        payload["nbbo"]["ask"] = 101.00
        service_module.DIRECTIONAL_STRATEGIES = (trend_buy, reversal_buy)
        service_module.CONTEXT_STRATEGIES = ()
        service_module.REGIME_CLASSIFIER = FixedHighFitClassifier()
        try:
            result = VotingEnsembleService().evaluate(payload)
        finally:
            service_module.DIRECTIONAL_STRATEGIES = original_directional
            service_module.CONTEXT_STRATEGIES = original_context
            service_module.REGIME_CLASSIFIER = original_classifier

        self.assertEqual(calls, ["trend", "reversal"])
        self.assertEqual(result["base_score"], 0.4)
        self.assertEqual(result["final_signal"], "Hold")
        self.assertTrue(result["safety_gate_failed"])
        self.assertIn("market.maximum_spread_bps", result["blocked_gate_ids"])
        self.assertIn("voting_ensemble.local_gate.excessive_spread", result["reason_codes"])

    def test_service_persists_execution_economics_from_point_in_time_quote(self) -> None:
        result = evaluate_service_candidate({"predictedGrossEdgeDollars": 0.75, "currentOneMinuteVolume": 100000})

        self.assertEqual(result["final_signal"], "Buy")
        self.assertFalse(result["safety_gate_failed"])
        economics = result["execution_economics"]
        self.assertIsNotNone(economics)
        self.assertEqual(economics["sourceQuote"]["bid"], 100.49)
        self.assertEqual(economics["sourceQuote"]["ask"], 100.51)
        self.assertEqual(economics["expectedSpreadCostDollars"], 0.02)
        self.assertGreater(economics["predictedNetEdgeDollars"], economics["minimumNetEdgeDollars"])
        self.assertGreaterEqual(economics["edgeToCostRatio"], economics["minimumEdgeToCostRatio"])
        self.assertIn("strategyEvaluationDurationMs", economics["latency"])
        self.assertIn("gateDurationMs", economics["latency"])
        self.assertIn("voting_ensemble.execution_economics.point_in_time_quote", economics["reasonCodes"])
        risk_budget = result["risk_budget"]
        self.assertGreater(risk_budget["quantity"], 0)
        self.assertIn("voting_ensemble.risk_budget.authoritative_sizing", risk_budget["reason_codes"])
        self.assertTrue(risk_budget["caps"])
        self.assertTrue(risk_budget["selected_cap_ids"])

    def test_service_blocks_gross_positive_net_negative_candidate(self) -> None:
        result = evaluate_service_candidate({"predictedGrossEdgeDollars": 0.03, "currentOneMinuteVolume": 100000})

        economics = result["execution_economics"]
        self.assertGreater(economics["predictedGrossEdgeDollars"], 0.0)
        self.assertLess(economics["predictedNetEdgeDollars"], 0.0)
        self.assertEqual(result["final_signal"], "Hold")
        self.assertTrue(result["safety_gate_failed"])
        self.assertIn("execution.minimum_net_edge", result["blocked_gate_ids"])
        self.assertIn("voting_ensemble.local_gate.minimum_net_edge", result["reason_codes"])
        self.assertEqual(result["risk_budget"]["quantity"], 0)
        self.assertIn("voting_ensemble.risk_budget.gates_failed", result["risk_budget"]["reason_codes"])

    def test_service_blocks_expired_decision_deadline(self) -> None:
        result = evaluate_service_candidate(
            {"predictedGrossEdgeDollars": 0.75, "currentOneMinuteVolume": 100000, "decisionAgeSeconds": 999}
        )

        self.assertEqual(result["final_signal"], "Hold")
        self.assertTrue(result["safety_gate_failed"])
        self.assertIn("data.decision_deadline", result["blocked_gate_ids"])
        self.assertTrue(result["execution_economics"]["latency"]["decisionDeadlineExpired"])
        self.assertEqual(result["execution_economics"]["latency"]["decisionAgeSeconds"], 999.0)

    def test_service_blocks_insufficient_fillable_quantity(self) -> None:
        result = evaluate_service_candidate(
            {"predictedGrossEdgeDollars": 0.75, "currentOneMinuteVolume": 100000, "minimumFillableQuantity": 50},
            nbbo={"askSize": 10},
        )

        self.assertEqual(result["final_signal"], "Hold")
        self.assertTrue(result["safety_gate_failed"])
        self.assertIn("execution.minimum_fillable_quantity", result["blocked_gate_ids"])
        self.assertIn("voting_ensemble.local_gate.insufficient_fillable_quantity", result["reason_codes"])


class FixedHighFitClassifier:
    def evaluate_snapshot(self, snapshot):
        return RegimeState(
            regimeId="adx_atr_regime",
            label="test_high_fit",
            direction=Direction.FLAT,
            volatility="NORMAL",
            confidence=1.0,
            features={
                "dataReady": True,
                "trendFit": 1.0,
                "breakoutFit": 1.0,
                "reversalFit": 1.0,
                "meanReversionFit": 1.0,
                "gapSessionFit": 1.0,
                "eventRiskState": "event_risk_clear",
                "reasonCodes": ["regime.test_high_fit"],
            },
            evaluatedAt=NOW,
            sessionDate=SESSION_DATE,
            configurationHash="test-regime",
        )


def base_gate_input(**overrides):
    payload = {
        "orderIntent": "new_entry",
        "evaluatedAt": NOW,
        "sessionDate": SESSION_DATE,
        "symbol": "SPY",
        "accountRiskState": account_state(),
        "candidate": None,
        "ensembleDecision": ensemble_decision(),
        "regimeState": FixedHighFitClassifier().evaluate_snapshot(None),
        "contextSignals": [],
        "dataState": {
            "freshCandle": True,
            "freshQuote": True,
            "validBidAsk": True,
            "monotonicTimestamps": True,
            "requiredTimeframeSynchronized": True,
            "requiredAuxiliaryDataReady": True,
            "featureSchemaValid": True,
            "feedHealthy": True,
            "clockSynchronized": True,
            "decisionDeadlineValid": True,
        },
        "operationalState": {
            # Mandatory like the rest of this group: an instrument the platform cannot size
            # is refused before sizing. Production supplies it from the active instrument.
            "instrumentTradeable": True,
            "tradingEnabled": True,
            "paperTradingMode": True,
            "marketOpen": True,
            "entryWindowOpen": True,
            "validSession": True,
            "feedDegraded": False,
            "clockDisagreement": False,
            "executionFailureCooldownActive": False,
            "eventRiskState": "event_risk_clear",
        },
        "marketState": {
            "symbolHalt": False,
            "luldPause": False,
            "marketWideCircuitBreaker": False,
            "eventBlackout": False,
            "spreadBps": 2.0,
            "spreadDollars": 0.02,
            "maximumSpreadDollars": 0.25,
        },
        "executionState": {
            "liquidityShares": 2000,
            "expectedSlippageDollars": 0.02,
            "duplicateOrder": False,
            "conflictingOrder": False,
            "cooldownActive": False,
        },
        "riskState": {
            "consecutiveLosses": 0,
            "existingPositionConflict": False,
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(payload.get(key), dict):
            payload[key] = {**payload[key], **value}
        else:
            payload[key] = value
    return GlobalGateInput.model_validate(payload)


def account_state(
    *,
    realized: float = 0.0,
    drawdown: float = 0.0,
    open_risk: float = 0.0,
    notional: float = 0.0,
    same_direction: float = 0.0,
    trades: int = 0,
) -> AccountRiskState:
    return AccountRiskState(
        accountId="paper",
        equity=25000.0,
        buyingPower=25000.0,
        openPositionNotional=0.0,
        realizedPnlToday=realized,
        drawdownFromIntradayHighPercent=drawdown,
        totalOpenRiskPercent=open_risk,
        totalSpyNotionalPercent=notional,
        sameDirectionExposurePercent=same_direction,
        tradesToday=trades,
        observedAt=NOW,
        sessionDate=SESSION_DATE,
    )


def ensemble_decision() -> EnsembleDecision:
    return EnsembleDecision(
        decisionId="decision",
        signal=Signal.BUY,
        direction=Direction.LONG,
        confidence=0.8,
        rawScore=0.4,
        finalScore=0.4,
        buyConfidence=0.4,
        sellConfidence=0.0,
        holdConfidence=0.6,
        supportingFamilies=["TREND", "REVERSAL"],
        opposingFamilies=[],
        eligibleStrategyCount=2,
        familyScores=[],
        strategySignals=[],
        contextAdjustments=[],
        reasonCodes=["ensemble.family_aware_candidate"],
        explanation="test",
        dataReady=True,
        eligible=True,
        decidedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="ensemble",
        engineVersion="test",
    )


def evaluate_service_candidate(operational_overrides: dict[str, object], *, nbbo: dict[str, object] | None = None) -> dict:
    original_directional = service_module.DIRECTIONAL_STRATEGIES
    original_context = service_module.CONTEXT_STRATEGIES
    original_classifier = service_module.REGIME_CLASSIFIER

    def trend_buy(request: VotingEnsembleEvaluateRequest):
        return _vote("Multi-Timeframe Trend Alignment", "trend", "Buy", 80, "trend", "test.trend", features={"strategyId": "multi_timeframe_trend_alignment"})

    def reversal_buy(request: VotingEnsembleEvaluateRequest):
        return _vote("Failed Breakout Reversal", "reversal", "Buy", 80, "reversal", "test.reversal", features={"strategyId": "failed_breakout_reversal"})

    payload = snapshot_payload(candles(30))
    payload["market_context"]["operationalHealthSnapshot"].update(operational_overrides)
    if nbbo:
        payload["nbbo"].update(nbbo)
    service_module.DIRECTIONAL_STRATEGIES = (trend_buy, reversal_buy)
    service_module.CONTEXT_STRATEGIES = ()
    service_module.REGIME_CLASSIFIER = FixedHighFitClassifier()
    try:
        return VotingEnsembleService().evaluate(payload)
    finally:
        service_module.DIRECTIONAL_STRATEGIES = original_directional
        service_module.CONTEXT_STRATEGIES = original_context
        service_module.REGIME_CLASSIFIER = original_classifier


if __name__ == "__main__":
    unittest.main()
