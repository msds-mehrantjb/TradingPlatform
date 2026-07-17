from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from backend.app.domain.models import (
    ContextSignal,
    Direction,
    GateResult,
    GateStatus,
    GlobalGateDecision,
    Signal,
    StrategyFamily,
    StrategyRole,
    StrategySignal,
)
from backend.app.ensemble import FamilyAwareDeterministicEnsemble, FamilyAwareEnsembleConfig
from backend.app.strategies.registry import resolve_strategy


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)
CONFIG_HASH = "ensemble-test"


def strategy_signal(
    strategy_id: str,
    signal: Signal,
    *,
    confidence: float = 0.8,
    reliability: float = 1.0,
    regime_fit: float = 1.0,
    eligible: bool = True,
    data_ready: bool = True,
    active: bool = True,
    features: dict | None = None,
) -> StrategySignal:
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
        confidence=confidence,
        active=active,
        eligible=eligible,
        dataReady=data_ready,
        setupDetected=signal != Signal.HOLD,
        regimeFit=regime_fit,
        reliability=reliability,
        structuralInvalidationPrice=None,
        reasonCodes=[f"test.{signal.value.lower()}"],
        explanation="Synthetic ensemble test signal.",
        features=features or {},
        requiredInputs=list(entry.requiredInputs),
        inputTimestamps={},
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def aggregator_signal() -> StrategySignal:
    entry = resolve_strategy("ensemble_strategy_voting")
    return StrategySignal(
        strategyId=entry.strategyId,
        strategyName=entry.strategyName,
        strategyVersion=entry.strategyVersion,
        family=entry.family,
        role=entry.role,
        signal=Signal.BUY,
        direction=Direction.LONG,
        confidence=0.9,
        active=True,
        eligible=True,
        dataReady=True,
        setupDetected=True,
        regimeFit=1.0,
        reliability=1.0,
        structuralInvalidationPrice=None,
        reasonCodes=["test.self_vote"],
        explanation="Invalid aggregator self vote.",
        features={},
        requiredInputs=list(entry.requiredInputs),
        inputTimestamps={},
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def context_signal(context_id: str, effect: str, confidence: float = 1.0) -> ContextSignal:
    return ContextSignal(
        contextId=context_id,
        signal=Signal.HOLD,
        direction=Direction.FLAT,
        confidence=confidence,
        dataReady=True,
        explanation="Synthetic context signal.",
        features={"contextEffect": effect, "maxConfidenceAdjustment": 0.08},
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def safety(status: GateStatus = GateStatus.PASS, eligible: bool = True) -> GlobalGateDecision:
    return GlobalGateDecision(
        status=status,
        eligible=eligible,
        dataReady=True,
        gateResults=[
            GateResult(
                gateId="cash_avoid_trading_filter",
                gateName="Cash / Avoid Trading Filter",
                status=status,
                blocksTrading=not eligible,
                reasonCodes=["test.safety"],
                explanation="Synthetic safety.",
                checkedAt=NOW,
                configurationHash=CONFIG_HASH,
            )
        ],
        reasonCodes=["test.safety"],
        explanation="Synthetic safety decision.",
        checkedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def aggregate(signals: list[StrategySignal], contexts: list[ContextSignal] | None = None, config: FamilyAwareEnsembleConfig | None = None):
    return FamilyAwareDeterministicEnsemble(config).aggregate(
        strategySignals=signals,
        contextSignals=contexts or [],
        regimeState=None,
        safetyDecision=safety(),
        decidedAt=NOW,
        sessionDate=SESSION_DATE,
    )


class FamilyAwareDeterministicEnsembleTest(unittest.TestCase):
    def test_aggregator_cannot_vote_for_itself(self) -> None:
        with self.assertRaisesRegex(ValueError, "aggregator cannot vote for itself"):
            aggregate([aggregator_signal()])

    def test_relative_strength_and_breadth_context_cannot_cast_full_votes(self) -> None:
        result = aggregate(
            [],
            [
                context_signal("relative_strength_qqq_iwm", "confirm_or_strengthen_long_candidates"),
                context_signal("market_breadth_momentum", "confirm_or_strengthen_long_candidates"),
            ],
        )

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertEqual(result.finalScore, 0.0)
        self.assertEqual(result.eligibleStrategyCount, 0)
        self.assertTrue(all(row["adjustment"] == 0.0 for row in result.contextAdjustments))

    def test_three_trend_strategies_are_averaged_not_counted_three_times(self) -> None:
        result = aggregate(
            [
                strategy_signal("multi_timeframe_trend_alignment", Signal.BUY, confidence=0.9),
                strategy_signal("first_pullback_after_open", Signal.BUY, confidence=0.9),
                strategy_signal("vwap_trend_continuation", Signal.BUY, confidence=0.9),
                strategy_signal("opening_range_breakout", Signal.BUY, confidence=0.8),
            ]
        )

        trend_score = next(score for score in result.familyScores if score.family == StrategyFamily.TREND.value)
        self.assertAlmostEqual(trend_score.buyScore, 0.9, places=4)
        self.assertAlmostEqual(result.rawScore, 0.85, places=4)
        self.assertNotAlmostEqual(result.rawScore, 0.875, places=4)

    def test_duplicating_strategy_inside_one_family_does_not_increase_family_influence(self) -> None:
        base = aggregate(
            [
                strategy_signal("multi_timeframe_trend_alignment", Signal.BUY, confidence=0.6),
                strategy_signal("opening_range_breakout", Signal.BUY, confidence=0.6),
            ]
        )
        duplicated = aggregate(
            [
                strategy_signal("multi_timeframe_trend_alignment", Signal.BUY, confidence=0.6),
                strategy_signal("multi_timeframe_trend_alignment", Signal.BUY, confidence=0.6),
                strategy_signal("opening_range_breakout", Signal.BUY, confidence=0.6),
            ]
        )

        self.assertAlmostEqual(base.rawScore, duplicated.rawScore, places=4)
        self.assertAlmostEqual(base.finalScore, duplicated.finalScore, places=4)

    def test_same_opening_continuation_event_is_capped_inside_trend_family(self) -> None:
        event_id = "SPY|2026-01-05|opening-continuation|09:37|BUY"
        result = aggregate(
            [
                strategy_signal(
                    "multi_timeframe_trend_alignment",
                    Signal.BUY,
                    confidence=0.9,
                    features={"eventCorrelationId": event_id, "trendEvidenceRole": "timeframe_agreement"},
                ),
                strategy_signal(
                    "first_pullback_after_open",
                    Signal.BUY,
                    confidence=0.9,
                    features={"eventCorrelationId": event_id, "trendEvidenceRole": "pattern_first_pullback"},
                ),
                strategy_signal(
                    "vwap_trend_continuation",
                    Signal.BUY,
                    confidence=0.9,
                    features={"eventCorrelationId": event_id, "trendEvidenceRole": "anchor_behavior"},
                ),
                strategy_signal("opening_range_breakout", Signal.BUY, confidence=0.8),
            ],
            config=FamilyAwareEnsembleConfig(minimumFinalScore=0.20),
        )

        trend_score = next(score for score in result.familyScores if score.family == StrategyFamily.TREND.value)
        trend_signal = next(signal for signal in result.strategySignals if signal.strategyId == "multi_timeframe_trend_alignment")
        overlap = trend_signal.features["trendOverlapControl"]
        self.assertAlmostEqual(trend_score.buyScore, 0.85, places=4)
        self.assertEqual(overlap["eventCorrelationId"], event_id)
        self.assertEqual(overlap["adjustment"], "same_direction_confidence_aggregation")
        self.assertEqual(overlap["trendFamilyVoteCap"], 0.85)
        self.assertEqual(set(overlap["evidenceRoles"]), {"timeframe_agreement", "pattern_first_pullback", "anchor_behavior"})
        self.assertIn("first_pullback_after_open", overlap["leaveOneStrategyOutGroupValue"])
        self.assertAlmostEqual(result.rawScore, 0.825, places=4)

    def test_distinct_trend_events_are_not_collapsed_into_one_correlation_group(self) -> None:
        result = aggregate(
            [
                strategy_signal(
                    "multi_timeframe_trend_alignment",
                    Signal.BUY,
                    confidence=0.8,
                    features={"eventCorrelationId": "SPY|2026-01-05|mtf-trigger|09:35|BUY"},
                ),
                strategy_signal(
                    "first_pullback_after_open",
                    Signal.BUY,
                    confidence=0.6,
                    features={"eventCorrelationId": "SPY|2026-01-05|first-pullback|09:42|BUY"},
                ),
                strategy_signal("opening_range_breakout", Signal.BUY, confidence=0.7),
            ]
        )

        trend_score = next(score for score in result.familyScores if score.family == StrategyFamily.TREND.value)
        self.assertAlmostEqual(trend_score.buyScore, 0.7, places=4)
        self.assertAlmostEqual(result.rawScore, 0.7, places=4)

    def test_buy_requires_independent_family_support(self) -> None:
        only_trend = aggregate(
            [
                strategy_signal("multi_timeframe_trend_alignment", Signal.BUY, confidence=0.9),
                strategy_signal("first_pullback_after_open", Signal.BUY, confidence=0.9),
                strategy_signal("vwap_trend_continuation", Signal.BUY, confidence=0.9),
            ]
        )
        independent = aggregate(
            [
                strategy_signal("multi_timeframe_trend_alignment", Signal.BUY, confidence=0.9),
                strategy_signal("opening_range_breakout", Signal.BUY, confidence=0.8),
            ]
        )

        self.assertEqual(only_trend.signal, Signal.HOLD.value)
        self.assertIn("ensemble.insufficient_independent_family_support", only_trend.reasonCodes)
        self.assertEqual(independent.signal, Signal.BUY.value)
        self.assertEqual(set(independent.supportingFamilies), {StrategyFamily.TREND.value, StrategyFamily.BREAKOUT.value})

    def test_sell_requires_independent_family_support(self) -> None:
        result = aggregate(
            [
                strategy_signal("failed_breakout_reversal", Signal.SELL, confidence=0.8),
                strategy_signal("vwap_mean_reversion", Signal.SELL, confidence=0.7),
            ]
        )

        self.assertEqual(result.signal, Signal.SELL.value)
        self.assertLess(result.finalScore, 0)
        self.assertEqual(set(result.supportingFamilies), {StrategyFamily.REVERSAL.value, StrategyFamily.MEAN_REVERSION.value})

    def test_hold_for_ties_and_weak_evidence(self) -> None:
        tied = aggregate(
            [
                strategy_signal("multi_timeframe_trend_alignment", Signal.BUY, confidence=0.8),
                strategy_signal("opening_range_breakout", Signal.SELL, confidence=0.8),
            ]
        )
        weak = aggregate(
            [
                strategy_signal("multi_timeframe_trend_alignment", Signal.BUY, confidence=0.15),
                strategy_signal("opening_range_breakout", Signal.BUY, confidence=0.15),
            ]
        )

        self.assertEqual(tied.signal, Signal.HOLD.value)
        self.assertIn("ensemble.weak_raw_score", tied.reasonCodes)
        self.assertEqual(weak.signal, Signal.HOLD.value)
        self.assertIn("ensemble.weak_final_score", weak.reasonCodes)

    def test_context_conflict_is_bounded_and_can_force_hold(self) -> None:
        result = aggregate(
            [
                strategy_signal("multi_timeframe_trend_alignment", Signal.BUY, confidence=0.9),
                strategy_signal("opening_range_breakout", Signal.BUY, confidence=0.9),
            ],
            [
                context_signal("relative_strength_qqq_iwm", "strong_short_conflict", confidence=1.0),
                context_signal("market_breadth_momentum", "strong_short_conflict", confidence=1.0),
            ],
            FamilyAwareEnsembleConfig(maximumContextConflict=0.10),
        )

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertLess(result.finalScore, result.rawScore)
        self.assertIn("ensemble.context_conflict_exceeds_limit", result.reasonCodes)

    def test_safety_result_blocks_candidate(self) -> None:
        module = FamilyAwareDeterministicEnsemble()
        result = module.aggregate(
            strategySignals=[
                strategy_signal("multi_timeframe_trend_alignment", Signal.BUY, confidence=0.9),
                strategy_signal("opening_range_breakout", Signal.BUY, confidence=0.9),
            ],
            contextSignals=[],
            regimeState=None,
            safetyDecision=safety(GateStatus.FAIL, eligible=False),
            decidedAt=NOW,
            sessionDate=SESSION_DATE,
        )

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertEqual(result.safetyStatus, GateStatus.FAIL.value)
        self.assertIn("ensemble.safety_blocked_new_entry", result.reasonCodes)


if __name__ == "__main__":
    unittest.main()
