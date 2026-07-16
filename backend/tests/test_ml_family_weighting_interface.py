from __future__ import annotations

import unittest

from pydantic import ValidationError

from backend.app.config import ApplicationConfig
from backend.app.domain.models import OperatingMode, Signal, StrategyFamily
from backend.app.ensemble import (
    FamilyAwareEnsembleConfig,
    MLFamilyWeightSuggestion,
    MLFamilyWeightingConfig,
    deterministic_equal_family_weights,
    evaluate_ml_family_weight_suggestion,
)
from backend.tests.test_family_aware_ensemble import aggregate, strategy_signal


class MLFamilyWeightingInterfaceTest(unittest.TestCase):
    def test_dynamic_family_weighting_flag_and_interface_are_disabled_by_default(self) -> None:
        self.assertFalse(ApplicationConfig().featureFlags.mlFamilyWeightingEnabled)

        decision = evaluate_ml_family_weight_suggestion(valid_suggestion())

        self.assertFalse(decision.enabled)
        self.assertEqual(decision.appliedWeights, deterministic_equal_family_weights())
        self.assertIn("family_weighting.disabled_by_default", decision.reasonCodes)

    def test_current_ensemble_does_not_depend_on_ml_family_weighting(self) -> None:
        base = aggregate(
            [
                strategy_signal("multi_timeframe_trend_alignment", Signal.BUY, confidence=0.7),
                strategy_signal("opening_range_breakout", Signal.BUY, confidence=0.7),
            ]
        )
        interface_decision = evaluate_ml_family_weight_suggestion(valid_suggestion())
        after_interface = aggregate(
            [
                strategy_signal("multi_timeframe_trend_alignment", Signal.BUY, confidence=0.7),
                strategy_signal("opening_range_breakout", Signal.BUY, confidence=0.7),
            ]
        )

        self.assertFalse(interface_decision.enabled)
        self.assertEqual(base.rawScore, after_interface.rawScore)
        self.assertEqual(base.finalScore, after_interface.finalScore)

    def test_equal_deterministic_family_weights_are_permanent_fallback(self) -> None:
        insufficient = evaluate_ml_family_weight_suggestion(
            valid_suggestion(sample_size=10),
            MLFamilyWeightingConfig(mode=OperatingMode.ACTIVE, minimumSampleRequirement=500),
        )
        missing_regime = evaluate_ml_family_weight_suggestion(
            valid_suggestion(regime_validation_passed=False),
            MLFamilyWeightingConfig(mode=OperatingMode.ACTIVE, requireRegimeSpecificValidation=True),
        )

        self.assertFalse(insufficient.enabled)
        self.assertEqual(insufficient.appliedWeights, deterministic_equal_family_weights())
        self.assertIn("family_weighting.insufficient_sample_size", insufficient.reasonCodes)
        self.assertFalse(missing_regime.enabled)
        self.assertEqual(missing_regime.appliedWeights, deterministic_equal_family_weights())
        self.assertIn("family_weighting.regime_validation_missing", missing_regime.reasonCodes)

    def test_no_model_can_assign_negative_or_unbounded_family_weight(self) -> None:
        config = MLFamilyWeightingConfig(mode=OperatingMode.ACTIVE, lowerBound=0.5, upperBound=1.5)
        negative = evaluate_ml_family_weight_suggestion(valid_suggestion(multiplier=-0.1), config)
        unbounded = evaluate_ml_family_weight_suggestion(valid_suggestion(multiplier=99.0), config)

        self.assertFalse(negative.enabled)
        self.assertIn("family_weighting.multiplier_out_of_bounds", negative.reasonCodes)
        self.assertEqual(negative.appliedWeights, deterministic_equal_family_weights())
        self.assertFalse(unbounded.enabled)
        self.assertIn("family_weighting.multiplier_out_of_bounds", unbounded.reasonCodes)

    def test_validated_active_suggestion_is_bounded_and_normalized_for_future_experiment(self) -> None:
        decision = evaluate_ml_family_weight_suggestion(
            MLFamilyWeightSuggestion(
                modelId="family-weight-test",
                modelVersion="v1",
                multipliers={
                    StrategyFamily.TREND: 1.5,
                    StrategyFamily.BREAKOUT: 1.0,
                    StrategyFamily.REVERSAL: 1.0,
                    StrategyFamily.MEAN_REVERSION: 1.0,
                    StrategyFamily.GAP_SESSION: 0.5,
                },
                sampleSize=1000,
                regimeValidationPassed=True,
                testedAgainstBaseline="family_aware_deterministic_equal_weights_v1",
            ),
            MLFamilyWeightingConfig(mode=OperatingMode.ACTIVE, minimumSampleRequirement=500),
        )

        self.assertTrue(decision.enabled)
        self.assertAlmostEqual(sum(decision.appliedWeights.values()) / len(decision.appliedWeights), 1.0, places=6)
        self.assertTrue(all(weight > 0 for weight in decision.appliedWeights.values()))
        self.assertEqual(decision.fallbackWeights, deterministic_equal_family_weights())

    def test_manual_family_weights_are_rejected_when_negative_or_unbounded(self) -> None:
        with self.assertRaises(ValidationError):
            FamilyAwareEnsembleConfig(familyWeights={StrategyFamily.TREND: -1.0})
        with self.assertRaises(ValidationError):
            FamilyAwareEnsembleConfig(familyWeights={StrategyFamily.TREND: 11.0})


def valid_suggestion(
    *,
    multiplier: float = 1.0,
    sample_size: int = 1000,
    regime_validation_passed: bool = True,
) -> MLFamilyWeightSuggestion:
    return MLFamilyWeightSuggestion(
        modelId="family-weight-test",
        modelVersion="v1",
        multipliers={family: multiplier for family in deterministic_equal_family_weights()},
        sampleSize=sample_size,
        regimeValidationPassed=regime_validation_passed,
        testedAgainstBaseline="family_aware_deterministic_equal_weights_v1",
    )


if __name__ == "__main__":
    unittest.main()
