from __future__ import annotations

import unittest

from backend.app.domain.models import ContextSignal, Direction, FamilyScore, MetaModelPrediction, OrderPlan, RegimeState, Signal, StrategyFamily
from backend.app.ml.features import ForbiddenMLFeatureFieldError, build_candidate_meta_features, candidate_meta_feature_schema_hash
from backend.app.strategies.registry import directional_strategy_input_ids
from backend.tests.test_decision_snapshot_v2_archive import CONFIG_HASH, NOW, SESSION_DATE, ensemble, snapshot
from backend.tests.test_family_aware_ensemble import strategy_signal


def order_plan() -> OrderPlan:
    return OrderPlan(
        orderPlanId="order-plan-ml-features",
        candidateId="candidate-buy",
        symbol="SPY",
        side=Signal.BUY,
        orderType="STOP_LIMIT",
        quantity=10,
        entryPrice=100,
        stopPrice=99,
        targetPrice=102,
        limitPrice=100.02,
        timeInForce="DAY",
        eligible=True,
        explanation="Synthetic order plan.",
        generatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def context_signal(context_id: str, features: dict) -> ContextSignal:
    return ContextSignal(
        contextId=context_id,
        signal=Signal.HOLD,
        direction=Direction.FLAT,
        confidence=0.7,
        dataReady=True,
        explanation="Synthetic context.",
        features=features,
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def rich_snapshot():
    signals = [
        strategy_signal(strategy_id, Signal.BUY if index % 2 == 0 else Signal.HOLD, confidence=0.6 + (index * 0.01))
        for index, strategy_id in enumerate(directional_strategy_input_ids())
    ]
    decision = ensemble(Signal.BUY).model_copy(
        update={
            "strategySignals": signals,
            "eligibleStrategyCount": 5,
            "supportingFamilies": [StrategyFamily.TREND, StrategyFamily.BREAKOUT],
            "opposingFamilies": [StrategyFamily.REVERSAL],
            "familyScores": [
                FamilyScore(family=StrategyFamily.TREND, buyScore=0.6, sellScore=0.0, holdScore=0.4, confidence=0.6, reliability=0.8, explanation="Trend supports."),
                FamilyScore(family=StrategyFamily.BREAKOUT, buyScore=0.4, sellScore=0.0, holdScore=0.6, confidence=0.4, reliability=0.8, explanation="Breakout supports."),
                FamilyScore(family=StrategyFamily.REVERSAL, buyScore=0.0, sellScore=0.2, holdScore=0.8, confidence=0.2, reliability=0.8, explanation="Reversal opposes."),
                FamilyScore(family=StrategyFamily.MEAN_REVERSION, buyScore=0.1, sellScore=0.0, holdScore=0.9, confidence=0.1, reliability=0.8, explanation="Mean reversion weak."),
                FamilyScore(family=StrategyFamily.GAP_SESSION, buyScore=0.0, sellScore=0.0, holdScore=1.0, confidence=0.0, reliability=0.8, explanation="Gap neutral."),
            ],
        }
    )
    regime = RegimeState(
        regimeId="adx_atr_regime",
        label="weak_trend",
        direction=Direction.LONG,
        volatility="NORMAL",
        confidence=0.66,
        features={
            "trendStrengthAdx": 22.5,
            "atrPercentile": 0.62,
            "realizedVolatilityPercentile": 0.58,
            "trendFit": 0.7,
            "breakoutFit": 0.6,
            "reversalFit": 0.3,
            "meanReversionFit": 0.4,
            "gapSessionFit": 0.5,
        },
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )
    contexts = [
        context_signal("relative_strength_qqq_iwm", {"primaryRelativeReturn": 0.004, "normalizedRelativeStrengthScore": 0.7}),
        context_signal("market_breadth_momentum", {"dataCoverage": 0.9}),
        context_signal("economic_event_context", {"eventState": "none", "eventImportance": "low"}),
        context_signal("market_structure_context", {"breakOfStructure": "bullish_break", "structureQuality": 0.8}),
        context_signal("volume_confirmation", {"volumeTrend": "rising"}),
        context_signal("vwap_position_context", {"reclaimRejectionState": "above_rising_vwap", "distanceFromVwapAtr": 0.45}),
    ]
    return snapshot(
        strategySignals=signals,
        directionalStrategyOutputs=signals,
        contextSignals=contexts,
        contextOutputs=contexts,
        regimeState=regime,
        ensembleDecision=decision,
        orderPlan=order_plan(),
        featureSnapshot={
            "features": {
                "spreadDollars": {"value": 0.02},
                "spy1mRelativeVolume": {"value": 1.35},
                "spy1mClose": {"value": 100.1},
            }
        },
    )


class CandidateMetaFeatureBuilderTest(unittest.TestCase):
    def test_feature_generation_is_deterministic_and_schema_hash_is_stable(self) -> None:
        first = build_candidate_meta_features(rich_snapshot())
        second = build_candidate_meta_features(rich_snapshot())

        self.assertEqual(first.model_dump(mode="json"), second.model_dump(mode="json"))
        self.assertEqual(first.schemaHash, candidate_meta_feature_schema_hash())
        self.assertEqual(first.featureValues["candidate_side"], Signal.BUY.value)
        self.assertEqual(first.featureValues["regime_category"], "weak_trend")
        self.assertAlmostEqual(first.featureValues["family_trend_score"], 0.6)
        self.assertAlmostEqual(first.featureValues["reward_risk_ratio"], 2.0)
        self.assertNotIn("finalOutcome", first.featureValues)

    def test_missing_values_have_consistent_defaults_and_indicators(self) -> None:
        features = build_candidate_meta_features(snapshot(featureSnapshot={"features": {}}))

        missing_strategy = "strategy_first_pullback_after_open_confidence"
        self.assertEqual(features.featureValues[missing_strategy], 0.0)
        self.assertEqual(features.featureValues[f"{missing_strategy}__missing"], 1)
        self.assertTrue(features.missingIndicators[missing_strategy])
        self.assertEqual(features.featureValues["strongest_family"], "__MISSING__")
        self.assertEqual(features.featureValues["strongest_family__missing"], 1)

    def test_leakage_rejects_final_outcomes_future_fields_and_upstream_predictions(self) -> None:
        with self.assertRaisesRegex(ForbiddenMLFeatureFieldError, "finalOutcome"):
            build_candidate_meta_features(snapshot(finalOutcome={"pnl": 12.5}))

        with self.assertRaisesRegex(ForbiddenMLFeatureFieldError, "futureHigh"):
            build_candidate_meta_features(snapshot(featureSnapshot={"features": {"futureHigh": {"value": 103}}}))

        with self.assertRaisesRegex(ForbiddenMLFeatureFieldError, "metaModelPrediction"):
            build_candidate_meta_features(
                snapshot(
                    metaModelPrediction=MetaModelPrediction(
                        modelId="prior-meta-model",
                        modelVersion="same-period",
                        signal=Signal.BUY,
                        probabilityBuy=0.8,
                        probabilitySell=0.1,
                        probabilityHold=0.1,
                        confidence=0.8,
                        reliability=0.5,
                        predictedAt=NOW,
                        sessionDate=SESSION_DATE,
                        configurationHash=CONFIG_HASH,
                    )
                )
            )


if __name__ == "__main__":
    unittest.main()

