from __future__ import annotations

import unittest

from backend.app.domain.feature_engine import FeatureQuality
from backend.app.domain.models import Direction, Signal
from backend.app.strategies.base import (
    StrategyEvaluationContext,
    hold_signal,
    strategy_signal,
    unavailable_signal,
    validate_no_direction_proxy_inputs,
)
from backend.app.strategies.registry import resolve_strategy

from test_point_in_time_feature_engine import request_with
from backend.app.domain.feature_engine import PointInTimeFeatureEngine


FEATURES = ("spy1mEma9", "spy1mEma20", "spy1mAtr14", "sessionVwap", "distanceFromVwapAtr")


class DirectionalStrategyContractTest(unittest.TestCase):
    def setUp(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_with())
        self.context = StrategyEvaluationContext(
            registryEntry=resolve_strategy("vwap_trend_continuation"),
            featureSnapshot=snapshot,
            configurationHash="contract-test",
        )

    def test_canonical_buy_signal_contract(self) -> None:
        signal = strategy_signal(
            self.context,
            signal=Signal.BUY,
            confidence=0.74,
            eligible=True,
            setupDetected=True,
            regimeFit=0.66,
            reliability=0.61,
            reasonCodes=["synthetic.buy"],
            explanation="Synthetic buy setup detected.",
            featureNames=FEATURES,
            structuralInvalidationPrice=99.5,
        )

        self.assertEqual(signal.signal, Signal.BUY.value)
        self.assertEqual(signal.direction, Direction.LONG)
        self.assertTrue(signal.eligible)
        self.assertTrue(signal.dataReady)
        self.assertEqual(signal.confidence, 0.74)
        self.assertEqual(signal.regimeFit, 0.66)
        self.assertEqual(signal.reliability, 0.61)
        self.assertEqual(signal.structuralInvalidationPrice, 99.5)
        self.assertIn("spy1mEma9", signal.features)
        self.assertIn("spy1mEma9", signal.inputTimestamps)

    def test_canonical_sell_signal_contract(self) -> None:
        signal = strategy_signal(
            self.context,
            signal=Signal.SELL,
            confidence=0.71,
            eligible=True,
            setupDetected=True,
            regimeFit=0.55,
            reliability=0.58,
            reasonCodes=["synthetic.sell"],
            explanation="Synthetic sell setup detected.",
            featureNames=FEATURES,
            structuralInvalidationPrice=104.5,
        )

        self.assertEqual(signal.signal, Signal.SELL.value)
        self.assertEqual(signal.direction, Direction.SHORT)
        self.assertTrue(signal.eligible)
        self.assertEqual(signal.structuralInvalidationPrice, 104.5)

    def test_canonical_hold_signal_contract(self) -> None:
        signal = hold_signal(
            self.context,
            confidence=0.22,
            setupDetected=False,
            regimeFit=0.44,
            reliability=0.5,
            reasonCodes=["synthetic.no_setup"],
            explanation="Synthetic setup is absent.",
            featureNames=FEATURES,
        )

        self.assertEqual(signal.signal, Signal.HOLD.value)
        self.assertEqual(signal.direction, Direction.FLAT)
        self.assertFalse(signal.eligible)
        self.assertTrue(signal.dataReady)
        self.assertFalse(signal.setupDetected)

    def test_missing_data_returns_hold_not_eligible(self) -> None:
        missing_snapshot = self.context.featureSnapshot.model_copy(
            update={
                "features": {
                    **self.context.featureSnapshot.features,
                    "spy1mEma9": self.context.featureSnapshot.features["spy1mEma9"].model_copy(
                        update={"quality": FeatureQuality.MISSING}
                    ),
                }
            }
        )
        context = StrategyEvaluationContext(
            registryEntry=self.context.registryEntry,
            featureSnapshot=missing_snapshot,
            configurationHash="contract-test",
        )

        signal = strategy_signal(
            context,
            signal=Signal.BUY,
            confidence=0.8,
            eligible=True,
            setupDetected=True,
            regimeFit=0.8,
            reliability=0.8,
            reasonCodes=["synthetic.buy"],
            explanation="Would be buy if data existed.",
            featureNames=FEATURES,
        )

        self.assertEqual(signal.signal, Signal.HOLD.value)
        self.assertEqual(signal.direction, Direction.FLAT)
        self.assertFalse(signal.eligible)
        self.assertFalse(signal.dataReady)
        self.assertIn("required_data_unavailable", signal.reasonCodes)

    def test_boundary_confidence_validation_is_enforced(self) -> None:
        with self.assertRaises(ValueError):
            strategy_signal(
                self.context,
                signal=Signal.BUY,
                confidence=1.01,
                eligible=True,
                setupDetected=True,
                regimeFit=0.5,
                reliability=0.5,
                reasonCodes=["synthetic.invalid"],
                explanation="Invalid confidence.",
                featureNames=FEATURES,
            )

    def test_unavailable_signal_uses_registry_inputs_and_no_proxy_fields(self) -> None:
        signal = unavailable_signal(self.context, requiredFeatureNames=("spy1mEma9", "missingFeature"))

        self.assertEqual(signal.requiredInputs, list(self.context.registryEntry.requiredInputs))
        self.assertFalse(signal.eligible)
        self.assertFalse(signal.dataReady)
        self.assertNotIn("session.directionBias", signal.features)
        self.assertNotIn("event.directionBias", signal.features)

    def test_direction_proxy_inputs_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "proxy direction inputs"):
            validate_no_direction_proxy_inputs(("session.directionBias", "spy1mEma9"))

        with self.assertRaisesRegex(ValueError, "proxy direction inputs"):
            strategy_signal(
                self.context,
                signal=Signal.BUY,
                confidence=0.7,
                eligible=True,
                setupDetected=True,
                regimeFit=0.5,
                reliability=0.5,
                reasonCodes=["synthetic.proxy"],
                explanation="Proxy should be rejected.",
                featureNames=("event.directionBias",),
            )


if __name__ == "__main__":
    unittest.main()
