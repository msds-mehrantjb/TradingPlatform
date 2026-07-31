from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path

from backend.app.algorithms.regime.configuration import validate_regime_settings
from backend.app.algorithms.regime.contracts import CANONICAL_MARKET_REGIMES, RegimeStrategyEvaluation
from backend.app.algorithms.regime.dynamic_profile import resolve_effective_regime_profile
from backend.app.algorithms.regime.family_aggregation import aggregate_family_scores
from backend.app.algorithms.regime.router import apply_confirmation_modules, evaluate_regime_role, route_regime_strategies
from backend.app.algorithms.regime.strategy_registry import REGIME_STRATEGY_DEFINITIONS, evaluate_strategy
from backend.tests.regime.fixtures.market_snapshots import classified_snapshot


ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "backend" / "tests" / "regime" / "coverage_manifest.json"

BEHAVIORAL_TEST_TOKENS = (
    "assert_directional_strategy_contract",
    "assert_non_directional_contract",
    "assert_safety_gate_contract",
    "evaluate_strategy",
    "execute_regime_pipeline",
    "aggregate_family_scores",
    "process_completed_bar",
)

SEMANTIC_EVIDENCE_KEYS = {
    "moving_average_trend": {"ema20Slope", "higherTimeframePermission", "extensionAtr"},
    "trend_pullback": {"pullbackDepthAtr", "confirmationCandle", "invalidationLevel"},
    "rsi_mean_reversion": {"rsi", "targetRoomAtr", "rangeCompatible"},
    "bollinger_band_mean_reversion": {"bands", "bandReentryFromAbove", "bandReentryFromBelow"},
    "opening_range_breakout": {"openingRange", "breakoutDistanceBps"},
    "intraday_breakout": {"reference", "compressionRatio", "netEdgeBps"},
    "macd_momentum": {"histogram", "freshCrossover", "normalizedMagnitude"},
    "market_structure": {"swingHighs", "swingLows", "breakOfStructure"},
    "gap_continuation_fade": {"gapBps", "previousRegularSessionClose", "sessionOpen"},
    "vwap_trend_continuation": {"vwapSlope", "heldOrReclaimedVwap", "interactionDistanceAtr"},
    "vwap_mean_reversion": {"distanceAtr", "netEdgeBps", "rangeCompatible"},
    "failed_breakout_reversal": {"references", "previousClose"},
    "liquidity_sweep_reversal": {"upperWickFraction", "lowerWickFraction", "relativeVolume"},
    "volatility_breakout": {"bodyDirection", "compressionRatio", "rangeExpansion"},
}


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _decision_output(strategy_id: str, family: str, signal: str = "Buy", confidence: float = 0.95) -> RegimeStrategyEvaluation:
    return RegimeStrategyEvaluation(
        strategy_id=strategy_id,
        name=strategy_id,
        family=family,
        role="directional",
        signal=signal,
        confidence=confidence,
        weight=1.0,
        eligible=True,
        reason="regime.test.behavioral_output",
        evidence={"strategyId": strategy_id},
    )


class RegimeBehavioralCiContractTest(unittest.TestCase):
    def test_registered_components_have_focused_behavioral_tests(self):
        manifest_entries = {
            component["component_id"]: component
            for component in _manifest()["components"]
            if component["component_type"] in {"directional_strategy", "confirmation_module", "context_module", "safety_gate"}
        }

        for definition in REGIME_STRATEGY_DEFINITIONS:
            with self.subTest(strategy=definition.strategy_id):
                component = manifest_entries[definition.strategy_id]
                test_path = ROOT / component["focused_test_path"]
                source = test_path.read_text(encoding="utf-8")

                self.assertTrue(test_path.exists(), component["focused_test_path"])
                self.assertIn(definition.strategy_id, source)
                self.assertTrue(any(token in source for token in BEHAVIORAL_TEST_TOKENS), component["focused_test_path"])
                self.assertNotIn("manifest", source.lower(), component["focused_test_path"])

    def test_each_named_directional_strategy_uses_distinct_semantic_evidence(self):
        market, raw = classified_snapshot("up")
        signatures: dict[str, frozenset[str]] = {}

        for definition in REGIME_STRATEGY_DEFINITIONS:
            if definition.role != "directional":
                continue
            with self.subTest(strategy=definition.strategy_id):
                output = evaluate_strategy(definition.strategy_id, market, raw)
                evidence_keys = set(output.evidence)
                required_keys = SEMANTIC_EVIDENCE_KEYS[definition.strategy_id]

                self.assertTrue(required_keys.issubset(evidence_keys), output.evidence)
                signatures[definition.strategy_id] = frozenset(evidence_keys)

        for left, left_signature in signatures.items():
            for right, right_signature in signatures.items():
                if left >= right:
                    continue
                self.assertNotEqual(left_signature, right_signature, f"{left} and {right} expose identical evidence")

    def test_role_modules_cannot_create_or_reverse_trade_direction(self):
        market, raw = classified_snapshot("up")
        for role in ("safety_gate", "regime_context", "confirmation"):
            with self.subTest(role=role):
                outputs = evaluate_regime_role(role, market, raw)
                self.assertTrue(outputs)
                self.assertEqual({output.signal for output in outputs}, {"Hold"})

        directional = (
            _decision_output("trend_a", "trend", "Buy", 0.60),
            _decision_output("trend_b", "trend", "Sell", 0.60),
            _decision_output("trend_hold", "trend", "Hold", 0.60),
        )
        high_confirmation = (
            RegimeStrategyEvaluation("volume_confirmation", "Volume", "confirmation", "confirmation", "Hold", 1.0, 0.0, True, "ok"),
        )
        adjusted = apply_confirmation_modules(directional, high_confirmation, settings={"maximumConfirmationAdjustment": 0.08})

        self.assertEqual(tuple(output.signal for output in adjusted), tuple(output.signal for output in directional))
        self.assertEqual(adjusted[2], directional[2])
        for before, after in zip(directional[:2], adjusted[:2], strict=True):
            self.assertLessEqual(abs(after.confidence - before.confidence), 0.08)

    def test_correlated_family_outputs_are_capped_not_multiplied(self):
        single_trend = aggregate_family_scores((_decision_output("trend_a", "trend"),), {"maximumContributionPerFamily": 0.25})
        duplicate_trend = aggregate_family_scores(
            (
                _decision_output("trend_a", "trend"),
                _decision_output("trend_b", "trend", confidence=0.90),
            ),
            {"maximumContributionPerFamily": 0.25},
        )
        independent_family = aggregate_family_scores(
            (
                _decision_output("trend_a", "trend"),
                _decision_output("breakout_a", "breakout"),
            ),
            {"maximumContributionPerFamily": 0.25},
        )

        self.assertEqual(single_trend["familyScores"]["trend"], 0.25)
        self.assertEqual(duplicate_trend["familyScores"]["trend"], 0.25)
        self.assertEqual(duplicate_trend["activeFamilyCount"], 1)
        self.assertIn("regime.family_aggregation.correlated_family_collapsed:trend", duplicate_trend["correlationCollisionReasonCodes"])
        self.assertEqual(independent_family["activeFamilyCount"], 2)
        self.assertEqual(independent_family["familyScores"]["trend"], 0.25)
        self.assertEqual(independent_family["familyScores"]["breakout"], 0.25)

    def test_canonical_regime_fixtures_drive_profile_and_routing_behavior(self):
        settings = validate_regime_settings({})
        market, raw = classified_snapshot("up")
        no_entry_regimes = {"event_risk", "liquidity_stress", "extreme_volatility_no_trade", "choppy_mixed", "unknown"}

        for regime in CANONICAL_MARKET_REGIMES:
            with self.subTest(regime=regime):
                effective = resolve_effective_regime_profile(settings, regime)
                routed = route_regime_strategies(market, replace(raw, raw_regime=regime), profile=effective, settings=settings)

                self.assertEqual(effective["profileId"], f"{regime}:regime_profile_matrix_v3_backend")
                self.assertIn("profileReasons", effective)
                self.assertIn("profileRouting", routed)
                if regime in no_entry_regimes:
                    self.assertTrue(effective["noNewEntries"])
                    self.assertFalse(routed["directionalOutputs"])
                else:
                    self.assertFalse(effective["noNewEntries"])
                    self.assertTrue(effective["allowedStrategyFamilies"])


if __name__ == "__main__":
    unittest.main()
