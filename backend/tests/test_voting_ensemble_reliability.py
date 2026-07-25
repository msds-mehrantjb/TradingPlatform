import unittest
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.voting_ensemble.ensemble.family_aware import FamilyAwareDeterministicEnsemble, FamilyAwareEnsembleConfig
from backend.app.algorithms.voting_ensemble.ml_contracts import SafeMLInferenceResult
from backend.app.algorithms.voting_ensemble.ml_model import voting_ensemble_ml_config
from backend.app.algorithms.voting_ensemble.order_planner import VotingEnsembleOrderPlanner
from backend.app.algorithms.voting_ensemble.reliability import (
    StrategyReliabilityEstimate,
    VotingEnsembleReliabilityConfig,
    VotingEnsembleReliabilityEstimator,
)
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
    Signal,
    StrategyFamily,
    StrategyRole,
    StrategySignal,
    TradeCandidate,
)


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


class VotingEnsembleReliabilityTest(unittest.TestCase):
    def test_reliability_is_reproducible_point_in_time_and_isolated(self) -> None:
        estimator = VotingEnsembleReliabilityEstimator(
            VotingEnsembleReliabilityConfig(minimumSampleSize=3, minimumEffectiveSampleSize=1.0, maximumReliability=0.65)
        )
        observations = [
            observation(days_ago=index + 1, outcome=0.8)
            for index in range(5)
        ]
        observations.extend(
            [
                observation(days_ago=-1, outcome=-5.0),
                observation(days_ago=1, outcome=-5.0, algorithm_id="weighted_voting"),
                observation(days_ago=1, outcome=-5.0, direction=Signal.SELL),
                observation(days_ago=1, outcome=-5.0, regime="sideways"),
                observation(days_ago=1, outcome=-5.0, session_segment="late"),
                observation(days_ago=1, outcome=-5.0, volatility_state="high"),
            ]
        )

        first = estimator.estimate_one(
            observations=observations,
            strategy_id="multi_timeframe_trend_alignment",
            direction=Signal.BUY,
            regime="trend",
            session_segment="regular",
            volatility_state="normal",
            sample_window="rolling_60_trades",
            evaluation_timestamp=NOW,
            mode=OperatingMode.SHADOW,
        )
        second = estimator.estimate_one(
            observations=observations,
            strategy_id="multi_timeframe_trend_alignment",
            direction=Signal.BUY,
            regime="trend",
            session_segment="regular",
            volatility_state="normal",
            sample_window="rolling_60_trades",
            evaluation_timestamp=NOW,
            mode=OperatingMode.SHADOW,
        )

        self.assertEqual(first.model_dump(mode="json"), second.model_dump(mode="json"))
        self.assertGreater(first.reliability, 0.5)
        self.assertLessEqual(first.reliability, 0.65)
        self.assertLess(first.sourceWindowEnd, NOW)
        self.assertEqual(first.sampleSize, 5)
        self.assertEqual(first.appliedReliability, 0.5)
        self.assertIn("voting_ensemble.reliability.point_in_time_history", first.reasonCodes)

    def test_insufficient_effective_sample_uses_neutral_fallback(self) -> None:
        estimator = VotingEnsembleReliabilityEstimator(VotingEnsembleReliabilityConfig(minimumSampleSize=5, minimumEffectiveSampleSize=4.0))

        estimate = estimator.estimate_one(
            observations=[observation(days_ago=1, outcome=2.0)],
            strategy_id="multi_timeframe_trend_alignment",
            direction=Signal.BUY,
            regime="trend",
            session_segment="regular",
            volatility_state="normal",
            sample_window="rolling_60_trades",
            evaluation_timestamp=NOW,
            mode=OperatingMode.ACTIVE,
        )

        self.assertEqual(estimate.reliability, 0.5)
        self.assertEqual(estimate.appliedReliability, 0.5)
        self.assertIn("voting_ensemble.reliability.insufficient_effective_sample_neutral_fallback", estimate.reasonCodes)

    def test_reliability_modes_shadow_fallback_and_active(self) -> None:
        signal = strategy_signal(reliability=1.0)
        active = aggregate(signal, estimate(mode=OperatingMode.ACTIVE, reliability=0.75))
        shadow = aggregate(signal, estimate(mode=OperatingMode.SHADOW, reliability=0.75))
        fallback = aggregate(signal, estimate(mode=OperatingMode.FALLBACK, reliability=0.75))

        self.assertAlmostEqual(active.rawScore, 0.6, places=4)
        self.assertAlmostEqual(shadow.rawScore, 0.8, places=4)
        self.assertAlmostEqual(fallback.rawScore, 0.4, places=4)
        self.assertEqual(active.strategySignals[0].features["reliabilityMode"], OperatingMode.ACTIVE.value)
        self.assertEqual(shadow.strategySignals[0].features["shadowReliability"], 0.75)

    def test_ml_off_shadow_and_fallback_do_not_block_deterministic_order(self) -> None:
        for mode in (OperatingMode.OFF, OperatingMode.SHADOW, OperatingMode.FALLBACK):
            with self.subTest(mode=mode):
                plan = VotingEnsembleOrderPlanner().order_plan(
                    candidate=candidate(),
                    policy=policy(),
                    gateDecision=gate_decision(),
                    mlDecision=ml_result(mode=mode, accepted=False),
                    decidedAt=NOW,
                    sessionDate=NOW.date(),
                )

                self.assertTrue(plan.eligible)
                self.assertEqual(plan.orderType, "LIMIT")
                self.assertNotIn("voting_ensemble.order_planner.ml_filter_block", plan.validationErrors)

    def test_active_ml_can_block_only_when_explicitly_active(self) -> None:
        blocked = VotingEnsembleOrderPlanner().order_plan(
            candidate=candidate(),
            policy=policy(),
            gateDecision=gate_decision(),
            mlDecision=ml_result(mode=OperatingMode.ACTIVE, accepted=False),
            decidedAt=NOW,
            sessionDate=NOW.date(),
        )
        accepted = VotingEnsembleOrderPlanner().order_plan(
            candidate=candidate(),
            policy=policy(),
            gateDecision=gate_decision(),
            mlDecision=ml_result(mode=OperatingMode.ACTIVE, accepted=True),
            decidedAt=NOW,
            sessionDate=NOW.date(),
        )

        self.assertFalse(blocked.eligible)
        self.assertIn("voting_ensemble.order_planner.ml_filter_block", blocked.validationErrors)
        self.assertTrue(accepted.eligible)

    def test_default_ml_config_is_off_with_deterministic_baseline_fallback(self) -> None:
        config = voting_ensemble_ml_config()

        self.assertEqual(config.mode, OperatingMode.OFF.value)
        self.assertEqual(config.fallbackBehavior, "DETERMINISTIC_BASELINE")


def observation(
    *,
    days_ago: int,
    outcome: float,
    algorithm_id: str = "voting_ensemble",
    direction: Signal = Signal.BUY,
    regime: str = "trend",
    session_segment: str = "regular",
    volatility_state: str = "normal",
) -> dict:
    completed = NOW - timedelta(days=days_ago)
    return {
        "algorithmId": algorithm_id,
        "strategyId": "multi_timeframe_trend_alignment",
        "direction": direction.value,
        "regime": regime,
        "sessionSegment": session_segment,
        "volatilityState": volatility_state,
        "sampleWindow": "rolling_60_trades",
        "outcomeR": outcome,
        "transactionCostR": 0.05,
        "decisionTimestamp": (completed - timedelta(minutes=10)).isoformat(),
        "completedAt": completed.isoformat(),
        "source": "paper_trade",
    }


def estimate(*, mode: OperatingMode, reliability: float) -> StrategyReliabilityEstimate:
    return StrategyReliabilityEstimate(
        strategyId="multi_timeframe_trend_alignment",
        direction=Signal.BUY,
        regime="trend",
        sessionSegment="regular",
        volatilityState="normal",
        sampleWindow="rolling_60_trades",
        reliability=reliability,
        appliedReliability=reliability if mode == OperatingMode.ACTIVE else 0.5,
        neutralReliability=0.5,
        sampleSize=20,
        effectiveSampleSize=15.0,
        sourceWindowStart=NOW - timedelta(days=30),
        sourceWindowEnd=NOW - timedelta(days=1),
        mode=mode,
        reliabilityVersion="test",
        configurationHash="test",
        reasonCodes=[f"test.mode.{mode.value}"],
        explanation="test estimate",
    )


def aggregate(signal: StrategySignal, reliability: StrategyReliabilityEstimate):
    return FamilyAwareDeterministicEnsemble(
        FamilyAwareEnsembleConfig(
            minimumEligibleDirectionalStrategies=1,
            minimumIndependentSupportingFamilies=1,
            reliabilityMode=OperatingMode(reliability.mode),
        )
    ).aggregate(
        strategySignals=[signal],
        contextSignals=[],
        regimeState=None,
        safetyDecision=None,
        reliabilityEstimates={signal.strategyId: reliability},
        decidedAt=NOW,
        sessionDate=NOW.date(),
    )


def strategy_signal(*, reliability: float) -> StrategySignal:
    return StrategySignal(
        strategyId="multi_timeframe_trend_alignment",
        strategyName="MTF",
        strategyVersion="test",
        family=StrategyFamily.TREND,
        role=StrategyRole.DIRECTIONAL,
        signal=Signal.BUY,
        direction=Direction.LONG,
        confidence=0.8,
        active=True,
        eligible=True,
        dataReady=True,
        setupDetected=True,
        regimeFit=1.0,
        reliability=reliability,
        reasonCodes=["test.buy"],
        explanation="test",
        evaluatedAt=NOW,
        sessionDate=NOW.date(),
        configurationHash="test",
    )


def candidate() -> TradeCandidate:
    return TradeCandidate(
        candidateId="candidate-1",
        symbol="SPY",
        signal=Signal.BUY,
        direction=Direction.LONG,
        entryPrice=100.0,
        stopPrice=99.0,
        targetPrice=101.5,
        quantity=5,
        confidence=0.8,
        expectedValue=0.2,
        reasonCodes=["test.candidate"],
        explanation="test candidate",
        generatedAt=NOW,
        sessionDate=NOW.date(),
        configurationHash="candidate",
    )


def policy() -> EffectiveTradePolicy:
    account = AccountRiskState(
        accountId="paper",
        equity=25000.0,
        buyingPower=25000.0,
        openPositionNotional=0.0,
        realizedPnlToday=0.0,
        tradesToday=0,
        observedAt=NOW,
        sessionDate=NOW.date(),
    )
    return EffectiveTradePolicy(
        mode=OperatingMode.ACTIVE,
        baselineSettings=BaselineTradingSettings(configurationHash="baseline"),
        hardRiskLimits=HardRiskLimits(configurationHash="hard"),
        dynamicBounds=DynamicPolicyBounds(
            minConfidence=0.0,
            minReliability=0.0,
            minRegimeFit=0.0,
            maxSpreadPercent=1.0,
            maxParticipationPercent=1.0,
            minLiquidityShares=1,
            configurationHash="dynamic",
        ),
        accountRiskState=account,
        maxQuantity=10,
        maxNotional=2000.0,
        riskDollars=100.0,
        explanation="test policy",
        effectiveAt=NOW,
        sessionDate=NOW.date(),
        configurationHash="policy",
    )


def gate_decision() -> GlobalGateDecision:
    return GlobalGateDecision(
        status=GateStatus.PASS,
        eligible=True,
        dataReady=True,
        gateResults=[],
        reasonCodes=["test.gate"],
        explanation="test gate",
        checkedAt=NOW,
        sessionDate=NOW.date(),
        configurationHash="gate",
    )


def ml_result(*, mode: OperatingMode, accepted: bool) -> SafeMLInferenceResult:
    return SafeMLInferenceResult(
        mode=mode,
        effectiveMode=mode,
        deterministicSignal=Signal.BUY,
        finalSignal=Signal.BUY,
        candidateAccepted=accepted,
        mlWouldAcceptCandidate=accepted,
        appliedToOrder=mode == OperatingMode.ACTIVE,
        successProbability=0.40 if not accepted else 0.70,
        calibratedProbability=0.40 if not accepted else 0.70,
        featureMissingness=0.0,
        modelHealth={"status": "test"},
        recommendedRiskCap=0.25 if mode == OperatingMode.ACTIVE else 1.0,
        reasonCodes=[f"test.ml.{mode.value}"],
        predictedAt=NOW,
        sessionDate=NOW.date(),
        configurationHash="ml",
    )


if __name__ == "__main__":
    unittest.main()
