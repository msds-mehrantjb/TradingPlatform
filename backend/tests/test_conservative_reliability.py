from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from backend.app.domain.models import (
    ContextSignal,
    Direction,
    GateResult,
    GateStatus,
    GlobalGateDecision,
    OperatingMode,
    Signal,
    StrategyFamily,
    StrategySignal,
)
from backend.app.ensemble import (
    ConservativeReliabilityConfig,
    ConservativeStrategyReliabilityEstimator,
    FamilyAwareDeterministicEnsemble,
    FamilyAwareEnsembleConfig,
)
from backend.app.strategies.registry import resolve_strategy


NOW = datetime(2026, 1, 5, 16, 0, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)
CONFIG_HASH = "reliability-test"


def outcome(
    strategy_id: str,
    days_ago: int,
    outcome_r: float,
    *,
    costs_r: float = 0.05,
    drawdown_r: float = 0.0,
    uncertainty: float = 0.25,
    regime: str = "strong_trend",
    source: str = "prior_out_of_sample",
    completed_after_decision: bool = False,
) -> dict:
    completed_at = NOW + timedelta(minutes=1) if completed_after_decision else NOW - timedelta(days=days_ago)
    return {
        "strategyId": strategy_id,
        "family": StrategyFamily.TREND,
        "regimeLabel": regime,
        "outcomeR": outcome_r,
        "costsR": costs_r,
        "maxDrawdownContributionR": drawdown_r,
        "probabilityUncertainty": uncertainty,
        "decisionTimestamp": completed_at - timedelta(minutes=15),
        "completedAt": completed_at,
        "source": source,
    }


def strategy_signal(strategy_id: str, signal: Signal = Signal.BUY, reliability: float = 1.0) -> StrategySignal:
    entry = resolve_strategy(strategy_id)
    direction = {Signal.BUY: Direction.LONG, Signal.SELL: Direction.SHORT, Signal.HOLD: Direction.FLAT}[signal]
    return StrategySignal(
        strategyId=entry.strategyId,
        strategyName=entry.strategyName,
        strategyVersion=entry.strategyVersion,
        family=entry.family,
        role=entry.role,
        signal=signal,
        direction=direction,
        confidence=0.8,
        active=True,
        eligible=True,
        dataReady=True,
        setupDetected=True,
        regimeFit=1.0,
        reliability=reliability,
        structuralInvalidationPrice=None,
        reasonCodes=["test.signal"],
        explanation="Synthetic reliability signal.",
        features={},
        requiredInputs=list(entry.requiredInputs),
        inputTimestamps={},
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def safety() -> GlobalGateDecision:
    return GlobalGateDecision(
        status=GateStatus.PASS,
        eligible=True,
        dataReady=True,
        gateResults=[
            GateResult(
                gateId="cash_avoid_trading_filter",
                gateName="Cash / Avoid Trading Filter",
                status=GateStatus.PASS,
                blocksTrading=False,
                reasonCodes=["test.pass"],
                explanation="Synthetic safety pass.",
                checkedAt=NOW,
                configurationHash=CONFIG_HASH,
            )
        ],
        reasonCodes=["test.pass"],
        explanation="Synthetic safety pass.",
        checkedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def aggregate(signals, estimates=None, mode: OperatingMode = OperatingMode.SHADOW):
    return FamilyAwareDeterministicEnsemble(FamilyAwareEnsembleConfig(reliabilityMode=mode)).aggregate(
        strategySignals=signals,
        contextSignals=[],
        regimeState=None,
        safetyDecision=safety(),
        reliabilityEstimates=estimates,
        decidedAt=NOW,
        sessionDate=SESSION_DATE,
    )


class ConservativeReliabilityEstimatorTest(unittest.TestCase):
    def test_new_strategy_remains_near_neutral(self) -> None:
        estimator = ConservativeStrategyReliabilityEstimator()
        estimate = estimator.estimate(
            strategyIds=["multi_timeframe_trend_alignment"],
            outcomes=[],
            decisionTimestamp=NOW,
            currentRegimeLabel="strong_trend",
        )["multi_timeframe_trend_alignment"]

        self.assertEqual(estimate.reliability, 0.5)
        self.assertEqual(estimate.sampleSize, 0)
        self.assertIn("reliability.no_prior_completed_outcomes", estimate.reasonCodes)

    def test_reliability_uses_only_timestamps_before_decision(self) -> None:
        estimator = ConservativeStrategyReliabilityEstimator()
        estimate = estimator.estimate(
            strategyIds=["multi_timeframe_trend_alignment"],
            outcomes=[
                outcome("multi_timeframe_trend_alignment", 3, -0.2),
                outcome("multi_timeframe_trend_alignment", 0, 20.0, completed_after_decision=True),
            ],
            decisionTimestamp=NOW,
            currentRegimeLabel="strong_trend",
        )["multi_timeframe_trend_alignment"]

        self.assertEqual(estimate.sampleSize, 1)
        self.assertLess(estimate.sourceWindowEnd, NOW)
        self.assertLess(estimate.reliability, 0.5)

    def test_small_sample_extreme_outcomes_shrink_toward_neutral(self) -> None:
        estimator = ConservativeStrategyReliabilityEstimator()
        estimate = estimator.estimate(
            strategyIds=["multi_timeframe_trend_alignment"],
            outcomes=[outcome("multi_timeframe_trend_alignment", day, 8.0, costs_r=0.0, uncertainty=0.0) for day in [1, 2, 3]],
            decisionTimestamp=NOW,
            currentRegimeLabel="strong_trend",
        )["multi_timeframe_trend_alignment"]

        self.assertLess(estimate.reliability, 0.56)
        self.assertGreater(estimate.reliability, 0.5)
        self.assertLess(estimate.components["sampleShrinkage"], 0.06)

    def test_extreme_recent_outcomes_are_bounded(self) -> None:
        estimator = ConservativeStrategyReliabilityEstimator(ConservativeReliabilityConfig(upperBound=0.68))
        estimate = estimator.estimate(
            strategyIds=["multi_timeframe_trend_alignment"],
            outcomes=[outcome("multi_timeframe_trend_alignment", day, 10.0, costs_r=0.0, uncertainty=0.0) for day in range(1, 121)],
            decisionTimestamp=NOW,
            currentRegimeLabel="strong_trend",
            mode=OperatingMode.ACTIVE,
        )["multi_timeframe_trend_alignment"]

        self.assertLessEqual(estimate.reliability, 0.68)
        self.assertEqual(estimate.mode, OperatingMode.ACTIVE.value)
        self.assertEqual(estimate.reliabilityVersion, "conservative_strategy_reliability_v1")

    def test_shadow_mode_records_estimate_without_affecting_decision_weight(self) -> None:
        estimator = ConservativeStrategyReliabilityEstimator()
        estimates = estimator.estimate(
            strategyIds=["multi_timeframe_trend_alignment", "opening_range_breakout"],
            outcomes=[outcome("multi_timeframe_trend_alignment", day, -2.0, drawdown_r=0.5, uncertainty=0.8) for day in range(1, 90)],
            decisionTimestamp=NOW,
            currentRegimeLabel="strong_trend",
        )
        signals = [
            strategy_signal("multi_timeframe_trend_alignment"),
            strategy_signal("opening_range_breakout"),
        ]

        baseline = aggregate(signals)
        shadow = aggregate(signals, estimates, OperatingMode.SHADOW)
        active = aggregate(signals, estimates, OperatingMode.ACTIVE)

        self.assertEqual(shadow.rawScore, baseline.rawScore)
        self.assertEqual(shadow.strategySignals[0].reliability, 1.0)
        self.assertIn("shadowReliability", shadow.strategySignals[0].features)
        self.assertLess(active.rawScore, baseline.rawScore)
        self.assertLess(active.strategySignals[0].reliability, 1.0)

    def test_equal_weight_fallback_is_available(self) -> None:
        estimator = ConservativeStrategyReliabilityEstimator()
        estimates = estimator.estimate(
            strategyIds=["multi_timeframe_trend_alignment", "opening_range_breakout"],
            outcomes=[outcome("multi_timeframe_trend_alignment", day, 4.0) for day in range(1, 80)],
            decisionTimestamp=NOW,
            currentRegimeLabel="strong_trend",
        )
        result = aggregate(
            [
                strategy_signal("multi_timeframe_trend_alignment", reliability=0.9),
                strategy_signal("opening_range_breakout", reliability=0.9),
            ],
            estimates,
            OperatingMode.FALLBACK,
        )

        self.assertEqual(result.strategySignals[0].reliability, 0.5)
        self.assertEqual(result.strategySignals[0].reliabilityVersion, "family_aware_equal_weight_fallback")


if __name__ == "__main__":
    unittest.main()
