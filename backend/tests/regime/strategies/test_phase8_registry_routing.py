from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from backend.app.algorithms.regime.contracts import RegimeStrategyEvaluation
from backend.app.algorithms.regime.family_aggregation import aggregate_family_scores
from backend.app.algorithms.regime.router import route_regime_strategies
from backend.app.algorithms.regime.strategy_registry import (
    REGIME_NO_TRADE_REGIMES,
    REGIME_STRATEGY_DEFINITIONS,
    REGIME_STRATEGY_METADATA,
    RegimeStrategyMetadata,
    regime_strategy_inventory,
    resolve_regime_strategy_alias,
    validate_regime_strategy_registry,
)
from backend.tests.regime.fixtures.classification_cases import classification
from backend.tests.regime.fixtures.market_snapshots import snapshot


class Phase8RegistryRoutingTest(unittest.TestCase):
    def test_inventory_reports_phase8_strategy_metadata(self):
        inventory = regime_strategy_inventory()
        directional = {item["id"]: item for item in inventory["moduleInventory"]["directional"]}
        trend = directional["trend_pullback"]

        self.assertEqual(trend["status"], "active")
        self.assertEqual(trend["lifecycle_status"], "active")
        self.assertEqual(trend["strategy_version"], "trend_pullback_v2")
        self.assertEqual(trend["role"], "directional")
        self.assertEqual(trend["family"], "trend")
        self.assertTrue(trend["data_requirements"])
        self.assertTrue(trend["compatible_regimes"])
        self.assertTrue(trend["activation_evidence"])
        self.assertTrue(trend["can_affect_orders"])
        self.assertIn("implementation_identity", trend)

        aliases = {item["id"]: item for item in inventory["aliasInventory"]}
        self.assertEqual(aliases["first_pullback_after_open"]["status"], "deprecated_alias")
        self.assertEqual(aliases["first_pullback_after_open"]["canonicalId"], "trend_pullback")
        self.assertFalse(aliases["first_pullback_after_open"]["canAffectOrders"])

    def test_startup_assertions_fail_closed_for_registry_corruption(self):
        no_active = tuple(
            replace(definition, lifecycle_status="shadow") if definition.role == "directional" else definition
            for definition in REGIME_STRATEGY_DEFINITIONS
        )
        with self.assertRaisesRegex(RuntimeError, "zero active directional"):
            validate_regime_strategy_registry(no_active)

        duplicate_id = (REGIME_STRATEGY_DEFINITIONS[0], REGIME_STRATEGY_DEFINITIONS[0], *REGIME_STRATEGY_DEFINITIONS[1:])
        with self.assertRaisesRegex(RuntimeError, "duplicate strategy IDs"):
            validate_regime_strategy_registry(duplicate_id)

        with self.assertRaisesRegex(RuntimeError, "alias cycle"):
            resolve_regime_strategy_alias("a", {"a": "b", "b": "a"})

    def test_startup_assertions_reject_missing_active_metadata_and_bad_routing(self):
        metadata = dict(REGIME_STRATEGY_METADATA)
        metadata["trend_pullback"] = RegimeStrategyMetadata(
            (),
            REGIME_STRATEGY_METADATA["trend_pullback"].compatible_regimes,
            REGIME_STRATEGY_METADATA["trend_pullback"].activation_evidence,
            True,
            REGIME_STRATEGY_METADATA["trend_pullback"].implementation_identity,
        )
        with patch("backend.app.algorithms.regime.strategy_registry.REGIME_STRATEGY_METADATA", metadata):
            with self.assertRaisesRegex(RuntimeError, "lack data requirements"):
                validate_regime_strategy_registry()

        metadata = dict(REGIME_STRATEGY_METADATA)
        metadata["trend_pullback"] = RegimeStrategyMetadata(
            REGIME_STRATEGY_METADATA["trend_pullback"].data_requirements,
            tuple(sorted((*REGIME_STRATEGY_METADATA["trend_pullback"].compatible_regimes, *REGIME_NO_TRADE_REGIMES))),
            REGIME_STRATEGY_METADATA["trend_pullback"].activation_evidence,
            True,
            REGIME_STRATEGY_METADATA["trend_pullback"].implementation_identity,
        )
        with patch("backend.app.algorithms.regime.strategy_registry.REGIME_STRATEGY_METADATA", metadata):
            with self.assertRaisesRegex(RuntimeError, "no-trade regimes"):
                validate_regime_strategy_registry()

        metadata = dict(REGIME_STRATEGY_METADATA)
        metadata["opening_range_breakout"] = RegimeStrategyMetadata(
            REGIME_STRATEGY_METADATA["opening_range_breakout"].data_requirements,
            REGIME_STRATEGY_METADATA["opening_range_breakout"].compatible_regimes,
            REGIME_STRATEGY_METADATA["opening_range_breakout"].activation_evidence,
            True,
            REGIME_STRATEGY_METADATA["trend_pullback"].implementation_identity,
        )
        with patch("backend.app.algorithms.regime.strategy_registry.REGIME_STRATEGY_METADATA", metadata):
            with self.assertRaisesRegex(RuntimeError, "share canonical implementation identity"):
                validate_regime_strategy_registry()

    def test_shadow_outputs_run_but_do_not_contribute_order_authority(self):
        routing = route_regime_strategies(
            snapshot("up", count=120),
            classification(raw_regime="strong_uptrend", confidence=0.9),
            {"minimumIndependentFamilies": 1},
        )

        outputs = {output.strategy_id: output for output in routing["directionalOutputs"]}
        self.assertIn("moving_average_trend", outputs)
        self.assertEqual(outputs["moving_average_trend"].lifecycle_status, "shadow")
        self.assertFalse(outputs["moving_average_trend"].eligible)
        self.assertNotIn("moving_average_trend", routing["selectedStrategyIds"])

    def test_routing_and_aggregation_return_hold_below_independent_threshold(self):
        routing = route_regime_strategies(
            snapshot("up", count=120),
            classification(raw_regime="strong_uptrend", confidence=0.9),
            {"minimumIndependentFamilies": 99},
        )
        self.assertEqual(routing["routeSignal"], "Hold")
        self.assertIn("regime.router.minimum_independent_strategies_not_met", routing["routeBlockers"])

        outputs = (
            RegimeStrategyEvaluation("trend_a", "trend_a", "trend", "directional", "Buy", 0.9, 1.0, True, "ok"),
            RegimeStrategyEvaluation("breakout_a", "breakout_a", "breakout", "directional", "Buy", 0.9, 1.0, True, "ok"),
        )
        aggregation = aggregate_family_scores(outputs, {"minimumIndependentFamilies": 3})
        self.assertEqual(aggregation["signal"], "Hold")
        self.assertIn("regime.family_aggregation.minimum_independent_strategies_not_met", aggregation["thresholdReasonCodes"])


if __name__ == "__main__":
    unittest.main()
