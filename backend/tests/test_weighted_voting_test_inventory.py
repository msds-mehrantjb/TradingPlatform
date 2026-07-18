from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
TESTS = ROOT / "tests"

ARCHITECTURE_TEST_FILES = (
    "test_weighted_voting_package_architecture.py",
    "test_weighted_voting_algorithm_isolation.py",
    "test_weighted_voting_legacy_compatibility.py",
    "test_weighted_voting_frontend_client_only.py",
    "test_weighted_voting_ml_decoupling.py",
)

STRATEGY_SUITE_CLASSES = (
    "OpeningRangeBreakoutStrategySuite",
    "FirstPullbackAfterOpenStrategySuite",
    "VwapTrendContinuationStrategySuite",
    "VwapMeanReversionStrategySuite",
    "FailedBreakoutReversalStrategySuite",
    "LiquiditySweepReversalStrategySuite",
    "BollingerAtrReversionStrategySuite",
    "VolatilityBreakoutStrategySuite",
)

STRATEGY_CASE_METHODS = (
    "test_buy_case",
    "test_sell_case",
    "test_hold_case",
    "test_missing_data_case",
    "test_stale_data_case",
    "test_boundary_threshold_case",
    "test_no_lookahead_case",
)

WEIGHT_TEST_MARKERS = (
    "test_strategy_and_family_caps_hold_after_normalization",
    "test_minimum_enabled_weight_and_data_quality_adjustments_are_visible",
    "test_same_session_update_preserves_frozen_active_weights",
    "test_few_samples_remain_close_to_equal_weight",
    "test_daily_weight_changes_respect_configured_limit",
    "test_weight_history_and_rollback_are_weighted_voting_owned",
    "test_contracts_serialize_deterministically",
)

DECISION_TEST_MARKERS = (
    "winner_score",
    "second_best_score",
    "winner_edge",
    "effective_weight_coverage",
    "conflicting_weight",
    "hold",
)

RISK_EXECUTION_TEST_FILES = (
    "test_weighted_voting_decision_gates.py",
    "test_weighted_voting_risk_budget.py",
    "test_weighted_voting_position_sizing.py",
    "test_weighted_voting_paper_order_gateway.py",
    "test_weighted_voting_final_acceptance.py",
)

BACKTEST_TEST_MARKERS = (
    "test_no_lookahead_and_next_candle_entry_are_enforced",
    "test_reproducibility_hash_uses_configuration_and_data_manifests",
    "regulatory_costs",
    "test_stop_wins_when_stop_and_target_are_both_touched_in_one_candle",
    "test_no_test_data_enters_calibration_window",
    "weight_data_end",
)


class WeightedVotingTestInventoryTest(unittest.TestCase):
    def test_architecture_test_inventory_is_independent(self) -> None:
        for filename in ARCHITECTURE_TEST_FILES:
            with self.subTest(filename=filename):
                source = _source(filename)
                self.assertIn("weighted_voting", source)
        architecture = _source("test_weighted_voting_package_architecture.py")
        self.assertIn("EXPECTED_FILES", architecture)
        self.assertIn("backend.app.ml", architecture)
        self.assertIn("backend.app.strategies", architecture)
        self.assertIn("frontend", architecture)
        self.assertIn("imports sibling algorithm", architecture)

    def test_each_strategy_has_a_dedicated_suite_with_required_cases(self) -> None:
        source = _source("test_weighted_voting_strategy_modules.py")

        for suite in STRATEGY_SUITE_CLASSES:
            with self.subTest(suite=suite):
                self.assertIn(f"class {suite}", source)
        for method in STRATEGY_CASE_METHODS:
            with self.subTest(method=method):
                self.assertIn(f"def {method}", source)

    def test_weight_decision_risk_and_execution_coverage_is_declared(self) -> None:
        weight_source = _source("test_weighted_voting_weight_engine.py")
        contract_source = _source("test_weighted_voting_contracts.py")
        aggregation_source = _source("test_weighted_voting_aggregation.py")
        gate_source = _source("test_weighted_voting_decision_gates.py")
        execution_source = "\n".join(_source(filename) for filename in RISK_EXECUTION_TEST_FILES)

        for marker in WEIGHT_TEST_MARKERS:
            with self.subTest(marker=marker):
                self.assertIn(marker, weight_source + contract_source)
        for marker in DECISION_TEST_MARKERS:
            with self.subTest(marker=marker):
                self.assertIn(marker, aggregation_source + gate_source)
        for marker in (
            "minimum_active_weight_coverage",
            "globallyAllowedQuantity",
            "REJECT_NEW_ENTRY",
            "global_direction_not_reversed",
            "ownership",
            "duplicate",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, execution_source)

    def test_backtesting_inventory_has_required_regression_coverage(self) -> None:
        backtest_source = _source("test_weighted_voting_backtest_engine.py")
        walk_forward_source = _source("test_weighted_voting_walk_forward.py")
        data_validation_source = _source("test_weighted_voting_backtest_data_validation.py")
        combined = "\n".join((backtest_source, walk_forward_source, data_validation_source))

        for marker in BACKTEST_TEST_MARKERS:
            with self.subTest(marker=marker):
                self.assertIn(marker, combined)
        self.assertIn("missing_regular_session_bars", data_validation_source)
        self.assertIn("quotes.stale_or_missing", data_validation_source)


def _source(filename: str) -> str:
    path = TESTS / filename
    if not path.is_file():
        raise AssertionError(f"Missing Weighted Voting test file: {filename}")
    return path.read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
