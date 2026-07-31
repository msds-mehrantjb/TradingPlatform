from __future__ import annotations

import unittest

from backend.app.algorithms.regime.classifier import classify_market_regime
from backend.app.algorithms.regime.strategy_registry import REGIME_STRATEGY_DEFINITIONS, evaluate_strategy, regime_strategy_inventory
from backend.tests.regime.fixtures.market_snapshots import snapshot


PHASE7_ACTIVE_CANDIDATES = {
    "trend_pullback",
    "vwap_trend_continuation",
    "opening_range_breakout",
    "volatility_breakout",
    "vwap_mean_reversion",
    "bollinger_band_mean_reversion",
    "failed_breakout_reversal",
    "gap_continuation_fade",
}

PHASE7_NON_ACTIVE_DIRECTIONALS = {
    "moving_average_trend",
    "rsi_mean_reversion",
    "intraday_breakout",
    "macd_momentum",
    "market_structure",
    "liquidity_sweep_reversal",
}

REQUIRED_STRUCTURED_FIELDS = {
    "strategy_id",
    "strategy_version",
    "family",
    "role",
    "lifecycle_status",
    "signal",
    "confidence",
    "expected_gross_edge_bps",
    "entry_reference",
    "stop_reference",
    "target_reference",
    "valid_until",
    "setup_id",
    "reason_codes",
    "data_ready",
    "paperLongOnlyPositionEffect",
}


class Phase7StrategyContractTest(unittest.TestCase):
    def test_only_independent_paper_candidates_are_active_directionals(self):
        active = {
            definition.strategy_id
            for definition in REGIME_STRATEGY_DEFINITIONS
            if definition.role == "directional" and definition.lifecycle_status == "active"
        }
        self.assertEqual(active, PHASE7_ACTIVE_CANDIDATES)

        inventory = regime_strategy_inventory()["moduleInventory"]["directional"]
        statuses = {item["id"]: item["status"] for item in inventory}
        for strategy_id in PHASE7_ACTIVE_CANDIDATES:
            self.assertEqual(statuses[strategy_id], "active")
        for strategy_id in PHASE7_NON_ACTIVE_DIRECTIONALS:
            self.assertIn(statuses[strategy_id], {"shadow", "not_data_ready", "disabled"})

    def test_active_paper_candidates_emit_structured_strategy_results(self):
        context = {
            "previousRegularClose": 99.0,
            "marketStructureLevels": {"premarketHigh": 101.0, "premarketLow": 98.0},
            "estimatedTransactionCostBps": 1.5,
        }
        market = snapshot("up", count=120, context=context, hour=14)
        classification = classify_market_regime(market)

        for strategy_id in sorted(PHASE7_ACTIVE_CANDIDATES):
            with self.subTest(strategy=strategy_id):
                output = evaluate_strategy(strategy_id, market, classification)
                self.assertEqual(output.strategy_id, strategy_id)
                self.assertNotEqual(output.strategy_version, "unknown")
                self.assertEqual(output.lifecycle_status, "active")
                self.assertTrue(REQUIRED_STRUCTURED_FIELDS.issubset(output.evidence), output.evidence)
                self.assertEqual(output.evidence["strategy_id"], strategy_id)
                self.assertEqual(output.evidence["strategy_version"], output.strategy_version)
                self.assertEqual(output.evidence["lifecycle_status"], output.lifecycle_status)
                self.assertEqual(output.evidence["signal"], output.signal)
                self.assertEqual(tuple(output.evidence["reason_codes"]), output.reason_codes)
                self.assertIsInstance(output.expected_gross_edge_bps, float)
                if output.signal == "Sell":
                    self.assertEqual(output.evidence["paperLongOnlyPositionEffect"], "reduce_or_close_long_only")

    def test_shadow_directionals_cannot_emit_authoritative_signals(self):
        market = snapshot("up", count=120)
        classification = classify_market_regime(market)

        for strategy_id in sorted(PHASE7_NON_ACTIVE_DIRECTIONALS - {"liquidity_sweep_reversal"}):
            with self.subTest(strategy=strategy_id):
                output = evaluate_strategy(strategy_id, market, classification)
                self.assertEqual(output.lifecycle_status, "shadow")
                self.assertEqual(output.signal, "Hold")
                self.assertFalse(output.eligible)
                self.assertIn("shadowSignal", output.evidence)

    def test_liquidity_sweep_is_not_data_ready_with_candles_only(self):
        market = snapshot("up", count=120)
        classification = classify_market_regime(market)
        output = evaluate_strategy("liquidity_sweep_reversal", market, classification)

        self.assertEqual(output.lifecycle_status, "not_data_ready")
        self.assertFalse(output.data_ready)
        self.assertFalse(output.eligible)
        self.assertEqual(output.signal, "Hold")
        self.assertIn("regime.strategy.liquidity_sweep_reversal.microstructure_not_ready", output.reason_codes)


if __name__ == "__main__":
    unittest.main()
