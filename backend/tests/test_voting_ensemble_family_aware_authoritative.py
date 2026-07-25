from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

import backend.app.algorithms.voting_ensemble.service as service_module
from backend.app.algorithms.voting_ensemble.ensemble.family_aware import FamilyAwareDeterministicEnsemble, FamilyAwareEnsembleConfig
from backend.app.algorithms.voting_ensemble.models import VotingEnsembleEvaluateRequest
from backend.app.algorithms.voting_ensemble.service import VotingEnsembleService, _vote
from backend.app.domain.models import ContextSignal, Direction, RegimeState, Signal, StrategyFamily, StrategyRole, StrategySignal
from backend.tests.test_voting_ensemble_snapshot import candles, snapshot_payload


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)
CONFIG_HASH = "test-family-aware"


class VotingEnsembleFamilyAwareAuthoritativeTest(unittest.TestCase):
    def test_strong_agreement_among_populated_families_exceeds_threshold(self) -> None:
        decision = aggregate(
            [
                strategy_signal("multi_timeframe_trend_alignment", StrategyFamily.TREND, Signal.BUY, confidence=0.8),
                strategy_signal("failed_breakout_reversal", StrategyFamily.REVERSAL, Signal.BUY, confidence=0.8),
            ]
        )

        self.assertEqual(decision.signal, Signal.BUY.value)
        self.assertGreaterEqual(decision.rawScore, 0.8)
        self.assertEqual(set(decision.supportingFamilies), {StrategyFamily.TREND.value, StrategyFamily.REVERSAL.value})
        self.assertEqual(decision.eligibleStrategyCount, 2)

    def test_empty_families_do_not_dilute_result(self) -> None:
        decision = aggregate(
            [
                strategy_signal("multi_timeframe_trend_alignment", StrategyFamily.TREND, Signal.BUY, confidence=0.8),
                strategy_signal("failed_breakout_reversal", StrategyFamily.REVERSAL, Signal.BUY, confidence=0.8),
            ]
        )

        self.assertAlmostEqual(decision.rawScore, 0.8, places=4)
        self.assertEqual(len(decision.familyScores), 2)
        self.assertNotIn(StrategyFamily.MEAN_REVERSION.value, {score.family for score in decision.familyScores})

    def test_context_cannot_create_direction(self) -> None:
        decision = aggregate(
            [],
            [
                context_signal("relative_strength_qqq_iwm", Signal.BUY, "confirm_or_strengthen_long_candidates"),
                context_signal("market_breadth_momentum", Signal.BUY, "confirm_or_strengthen_long_candidates"),
            ],
        )

        self.assertEqual(decision.signal, Signal.HOLD.value)
        self.assertEqual(decision.rawScore, 0.0)
        self.assertEqual(decision.finalScore, 0.0)
        self.assertTrue(all(row["adjustment"] == 0.0 for row in decision.contextAdjustments))

    def test_aggregator_never_appears_as_input_vote(self) -> None:
        with self.assertRaisesRegex(ValueError, "aggregator cannot vote for itself"):
            aggregate([strategy_signal("ensemble_strategy_voting", StrategyFamily.MARKET_CONTEXT, Signal.BUY, role=StrategyRole.AGGREGATOR)])

    def test_hold_abstention_is_not_zero_directional_vote(self) -> None:
        decision = aggregate(
            [
                strategy_signal("multi_timeframe_trend_alignment", StrategyFamily.TREND, Signal.BUY, confidence=0.8),
                strategy_signal("failed_breakout_reversal", StrategyFamily.REVERSAL, Signal.BUY, confidence=0.8),
                strategy_signal("atr_overextension_reversion", StrategyFamily.MEAN_REVERSION, Signal.HOLD, confidence=1.0, eligible=False),
            ]
        )

        self.assertEqual(decision.signal, Signal.BUY.value)
        self.assertEqual(decision.eligibleStrategyCount, 2)
        self.assertAlmostEqual(decision.rawScore, 0.8, places=4)

    def test_service_and_engine_outputs_are_identical(self) -> None:
        original_directional = service_module.DIRECTIONAL_STRATEGIES
        original_context = service_module.CONTEXT_STRATEGIES
        original_classifier = service_module.REGIME_CLASSIFIER
        regime_state = fixed_regime_state()

        def trend_buy(request: VotingEnsembleEvaluateRequest):
            return _vote(
                "Multi-Timeframe Trend Alignment",
                "trend",
                "Buy",
                80,
                "test trend buy",
                "test.trend_buy",
                features={"strategyId": "multi_timeframe_trend_alignment", "strategyVersion": "test"},
            )

        def reversal_buy(request: VotingEnsembleEvaluateRequest):
            return _vote(
                "Failed Breakout Reversal",
                "reversal",
                "Buy",
                80,
                "test reversal buy",
                "test.reversal_buy",
                features={"strategyId": "failed_breakout_reversal", "strategyVersion": "test"},
            )

        service_module.DIRECTIONAL_STRATEGIES = (trend_buy, reversal_buy)
        service_module.CONTEXT_STRATEGIES = ()
        service_module.REGIME_CLASSIFIER = FixedRegimeClassifier(regime_state)
        try:
            result = VotingEnsembleService().evaluate(snapshot_payload(candles(30)))
        finally:
            service_module.DIRECTIONAL_STRATEGIES = original_directional
            service_module.CONTEXT_STRATEGIES = original_context
            service_module.REGIME_CLASSIFIER = original_classifier

        engine_decision = aggregate(
            [
                strategy_signal("multi_timeframe_trend_alignment", StrategyFamily.TREND, Signal.BUY, confidence=0.8, reliability=0.5),
                strategy_signal("failed_breakout_reversal", StrategyFamily.REVERSAL, Signal.BUY, confidence=0.8, reliability=0.5),
            ],
            regime_state=regime_state,
        )

        self.assertEqual(result["final_signal"], engine_decision.signal.title())
        self.assertEqual(result["base_score"], engine_decision.rawScore)
        self.assertEqual(result["context_adjusted_score"], engine_decision.finalScore)
        self.assertEqual(result["eligible_strategy_count"], engine_decision.eligibleStrategyCount)
        self.assertEqual(result["family_scores"], {"trend": 0.4, "reversal": 0.4})

    def test_trend_overlap_controls_mtf_pullback_and_vwap_same_event(self) -> None:
        one = aggregate(
            [strategy_signal("multi_timeframe_trend_alignment", StrategyFamily.TREND, Signal.BUY, confidence=0.8, features=correlation("trend-event-1", "vwap_reclaim"))],
            minimum_strategies=1,
            minimum_families=1,
        )
        three = aggregate(
            [
                strategy_signal("multi_timeframe_trend_alignment", StrategyFamily.TREND, Signal.BUY, confidence=0.8, features=correlation("trend-event-1", "timeframe_agreement")),
                strategy_signal("first_pullback_after_open", StrategyFamily.TREND, Signal.BUY, confidence=0.8, features=correlation("trend-event-1", "pattern_first_pullback")),
                strategy_signal("vwap_trend_continuation", StrategyFamily.TREND, Signal.BUY, confidence=0.8, features=correlation("trend-event-1", "anchor_behavior")),
            ],
            minimum_strategies=1,
            minimum_families=1,
        )

        self.assertEqual(three.eligibleStrategyCount, 1)
        self.assertLessEqual(abs(three.rawScore - one.rawScore), 0.25)
        trace = three.strategySignals[0].features["familyOverlapControl"]
        self.assertEqual(trace["family"], StrategyFamily.TREND.value)
        self.assertEqual(trace["eventGroupCount"] if "eventGroupCount" in trace else len(trace["strategyIds"]), 3)
        self.assertIn("leaveOneStrategyOutGroupValue", trace)

    def test_reversal_overlap_groups_failed_breakout_and_liquidity_sweep_at_same_level(self) -> None:
        decision = aggregate(
            [
                strategy_signal("failed_breakout_reversal", StrategyFamily.REVERSAL, Signal.BUY, confidence=0.75, features=correlation("level-event-1", "failed_breakout_level_rejection", reference="prior_high")),
                strategy_signal("liquidity_sweep_reversal", StrategyFamily.REVERSAL, Signal.BUY, confidence=0.75, features=correlation("different-event-id", "liquidity_sweep_level_rejection", reference="prior_high")),
            ],
            minimum_strategies=1,
            minimum_families=1,
        )

        self.assertEqual(decision.eligibleStrategyCount, 1)
        trace = decision.strategySignals[0].features["familyOverlapControl"]
        self.assertEqual(trace["family"], StrategyFamily.REVERSAL.value)
        self.assertEqual(set(trace["strategyIds"]), {"failed_breakout_reversal", "liquidity_sweep_reversal"})
        self.assertEqual(trace["referenceLevelIds"], ["prior_high"])

    def test_reversal_conflicting_same_event_reduces_family_confidence(self) -> None:
        decision = aggregate(
            [
                strategy_signal("failed_breakout_reversal", StrategyFamily.REVERSAL, Signal.BUY, confidence=0.8, features=correlation("level-event-2", "failed_breakout_level_rejection", reference="premarket_high")),
                strategy_signal("liquidity_sweep_reversal", StrategyFamily.REVERSAL, Signal.SELL, confidence=0.8, features=correlation("level-event-2", "liquidity_sweep_level_rejection", reference="premarket_high")),
            ],
            minimum_strategies=1,
            minimum_families=1,
        )

        self.assertEqual(decision.signal, Signal.HOLD.value)
        self.assertLess(abs(decision.rawScore), 0.25)
        self.assertEqual(decision.strategySignals[0].features["familyOverlapControl"]["adjustment"], "conflicting_same_event_evidence_reduced_family_confidence")

    def test_mean_reversion_overlap_controls_bollinger_and_atr_same_overextension(self) -> None:
        decision = aggregate(
            [
                strategy_signal("bollinger_band_reversion", StrategyFamily.MEAN_REVERSION, Signal.SELL, confidence=0.76, features=correlation("mean-reversion-event-1", "bollinger_band_overextension")),
                strategy_signal("atr_overextension_reversion", StrategyFamily.MEAN_REVERSION, Signal.SELL, confidence=0.76, features=correlation("mean-reversion-event-1", "atr_overextension")),
            ],
            minimum_strategies=1,
            minimum_families=1,
        )

        self.assertEqual(decision.eligibleStrategyCount, 1)
        self.assertEqual(decision.strategySignals[0].features["familyOverlapControl"]["family"], StrategyFamily.MEAN_REVERSION.value)
        self.assertLessEqual(abs(decision.rawScore), 0.85)

    def test_breakout_overlap_discards_duplicate_opening_range_observations(self) -> None:
        decision = aggregate(
            [
                strategy_signal("opening_range_breakout", StrategyFamily.BREAKOUT, Signal.BUY, confidence=0.70, features=correlation("orb-event-1", "opening_range_break", reference="opening_range_high")),
                strategy_signal("opening_range_breakout", StrategyFamily.BREAKOUT, Signal.BUY, confidence=0.80, features=correlation("orb-event-1", "opening_range_break", reference="opening_range_high")),
            ],
            minimum_strategies=1,
            minimum_families=1,
        )

        trace = decision.strategySignals[0].features["familyOverlapControl"]
        self.assertEqual(decision.eligibleStrategyCount, 1)
        self.assertEqual(trace["family"], StrategyFamily.BREAKOUT.value)
        self.assertEqual(trace["discardedDuplicateStrategyIds"], ["opening_range_breakout"])
        self.assertAlmostEqual(decision.rawScore, 0.8, places=4)

    def test_gap_session_overlap_deduplicates_one_opening_event(self) -> None:
        decision = aggregate(
            [
                strategy_signal("gap_continuation_fade", StrategyFamily.GAP_SESSION, Signal.BUY, confidence=0.72, features=correlation("gap-open-1", "opening_gap_session", reference="session_open_gap")),
                strategy_signal("gap_continuation_fade", StrategyFamily.GAP_SESSION, Signal.SELL, confidence=0.74, features=correlation("gap-open-1", "opening_gap_session", reference="session_open_gap")),
            ],
            minimum_strategies=1,
            minimum_families=1,
        )

        trace = decision.strategySignals[0].features["familyOverlapControl"]
        self.assertEqual(decision.eligibleStrategyCount, 1)
        self.assertEqual(trace["family"], StrategyFamily.GAP_SESSION.value)
        self.assertEqual(trace["discardedDuplicateStrategyIds"], ["gap_continuation_fade"])


def aggregate(
    signals: list[StrategySignal],
    contexts: list[ContextSignal] | None = None,
    regime_state: RegimeState | None = None,
    *,
    minimum_strategies: int = 2,
    minimum_families: int = 2,
):
    return FamilyAwareDeterministicEnsemble(
        FamilyAwareEnsembleConfig(minimumEligibleDirectionalStrategies=minimum_strategies, minimumIndependentSupportingFamilies=minimum_families)
    ).aggregate(
        strategySignals=signals,
        contextSignals=contexts or [],
        regimeState=regime_state,
        safetyDecision=None,
        decidedAt=NOW,
        sessionDate=SESSION_DATE,
    )


class FixedRegimeClassifier:
    def __init__(self, regime_state: RegimeState) -> None:
        self.regime_state = regime_state

    def evaluate_snapshot(self, snapshot):
        return self.regime_state


def fixed_regime_state() -> RegimeState:
    return RegimeState(
        regimeId="adx_atr_regime",
        label="test_high_fit",
        direction=Direction.FLAT,
        volatility="NORMAL",
        confidence=0.9,
        features={
            "trendFit": 1.0,
            "breakoutFit": 1.0,
            "reversalFit": 1.0,
            "meanReversionFit": 1.0,
            "gapSessionFit": 1.0,
            "transitionState": "stable",
            "reasonCodes": ["regime.test_high_fit"],
        },
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="test-regime-hash",
    )


def strategy_signal(
    strategy_id: str,
    family: StrategyFamily,
    signal: Signal,
    *,
    confidence: float = 0.8,
    reliability: float = 1.0,
    eligible: bool = True,
    role: StrategyRole = StrategyRole.DIRECTIONAL,
    features: dict | None = None,
) -> StrategySignal:
    return StrategySignal(
        strategyId=strategy_id,
        strategyName=strategy_id.replace("_", " ").title(),
        strategyVersion="test_v1",
        family=family,
        role=role,
        signal=signal,
        direction=direction(signal),
        confidence=confidence,
        active=True,
        eligible=eligible,
        dataReady=True,
        setupDetected=signal != Signal.HOLD,
        regimeFit=1.0,
        reliability=reliability,
        reasonCodes=[f"test.{strategy_id}"],
        explanation="test signal",
        features=features or {},
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def correlation(event_id: str, role: str, *, reference: str = "") -> dict:
    return {
        "eventCorrelationId": event_id,
        "setupId": f"setup:{event_id}",
        "evidenceRole": role,
        "referenceLevelId": reference,
        "triggerTimestamp": NOW.isoformat(),
        "confirmationTimestamp": NOW.isoformat(),
    }


def context_signal(context_id: str, signal: Signal, effect: str) -> ContextSignal:
    return ContextSignal(
        contextId=context_id,
        signal=signal,
        direction=direction(signal),
        confidence=1.0,
        dataReady=True,
        explanation="test context",
        features={"contextEffect": effect, "maxConfidenceAdjustment": 0.08},
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def direction(signal: Signal) -> Direction:
    if signal == Signal.BUY:
        return Direction.LONG
    if signal == Signal.SELL:
        return Direction.SHORT
    return Direction.FLAT


if __name__ == "__main__":
    unittest.main()
