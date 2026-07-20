from __future__ import annotations

import unittest

from backend.app.algorithms.meta_strategy import (
    META_STRATEGY_POSITION_SIZING_VERSION,
    MetaStrategyBaselineSettings,
    MetaStrategyEffectiveSettings,
    MetaStrategySizingConfig,
    MetaStrategySizingContext,
    calculate_meta_strategy_position_size,
)


EXPECTED_CAP_IDS = {
    "risk_based_quantity",
    "position_cap_quantity",
    "buying_power_quantity",
    "liquidity_quantity",
    "maximum_share_quantity",
    "remaining_algorithm_risk_quantity",
    "global_risk_quantity_cap",
}


def baseline_settings(**overrides: object) -> MetaStrategyBaselineSettings:
    values = {
        "risk_percentage": 0.10,
        "position_cap": 0.50,
        "stop_multiplier": 1.0,
        "target_multiplier": 2.0,
        "maximum_holding_minutes": 30,
    }
    values.update(overrides)
    return MetaStrategyBaselineSettings(**values)


def effective_settings(baseline: MetaStrategyBaselineSettings | None = None, **overrides: object) -> MetaStrategyEffectiveSettings:
    base = baseline or baseline_settings()
    values = {
        "baseline_configuration_version": base.configuration_version,
        "baseline_settings_hash": base.settings_hash,
        "entry_threshold": base.entry_threshold,
        "model_probability_threshold": base.model_probability_threshold,
        "risk_percentage": base.risk_percentage,
        "position_cap": base.position_cap,
        "stop_multiplier": base.stop_multiplier,
        "target_multiplier": base.target_multiplier,
        "maximum_holding_minutes": base.maximum_holding_minutes,
        "spread_limit_bps": base.spread_limit_bps,
        "liquidity_requirement": base.liquidity_requirement,
        "trade_count_limit": base.trade_count_limit,
        "allow_long": base.allow_long,
        "allow_short": base.allow_short,
    }
    values.update(overrides)
    return MetaStrategyEffectiveSettings(**values)


def base_context(**overrides: object) -> MetaStrategySizingContext:
    baseline = overrides.pop("baseline_settings", baseline_settings())
    effective = overrides.pop("effective_settings", effective_settings(baseline))
    values = {
        "side": "BUY",
        "candidate_accepted": True,
        "local_gates_passed": True,
        "baseline_settings": baseline,
        "effective_settings": effective,
        "model_risk_multiplier": 1.0,
        "account_equity": 100_000.0,
        "available_buying_power": 100_000.0,
        "entry_price": 100.0,
        "stop_distance": 1.0,
        "market_liquidity": 10_000.0,
        "remaining_algorithm_risk": 100_000.0,
        "global_available_risk": 100_000.0,
        "global_quantity_cap": 100_000,
    }
    values.update(overrides)
    return MetaStrategySizingContext(**values)


class MetaStrategyStep29PositionSizingTest(unittest.TestCase):
    def test_every_sizing_cap_is_visible_and_final_quantity_is_minimum_valid_cap(self) -> None:
        result = calculate_meta_strategy_position_size(base_context())

        self.assertEqual(result.position_sizing_version, META_STRATEGY_POSITION_SIZING_VERSION)
        self.assertEqual({cap.cap_id for cap in result.caps}, EXPECTED_CAP_IDS)
        expected = min(
            result.risk_based_quantity,
            result.position_cap_quantity,
            result.buying_power_quantity,
            result.liquidity_quantity,
            result.maximum_share_quantity,
            result.remaining_algorithm_risk_quantity,
            result.global_risk_quantity_cap,
        )

        self.assertEqual(result.quantity, expected)
        self.assertEqual(result.limiting_cap, "position_cap_quantity")
        self.assertEqual(result.quantity, 500)
        self.assertIn("meta_strategy.sizing.calculated", result.reason_codes)

    def test_each_cap_has_focused_boundary_case(self) -> None:
        cases = {
            "risk_based_quantity": (
                base_context(stop_distance=2_500.0, global_available_risk=100_000_000.0, remaining_algorithm_risk=100_000_000.0),
                4,
            ),
            "position_cap_quantity": (
                base_context(),
                500,
            ),
            "buying_power_quantity": (
                base_context(available_buying_power=300.0),
                3,
            ),
            "liquidity_quantity": (
                base_context(market_liquidity=30.0),
                3,
            ),
            "maximum_share_quantity": (
                base_context(),
                3,
                MetaStrategySizingConfig(maximum_share_quantity=3),
            ),
            "remaining_algorithm_risk_quantity": (
                base_context(remaining_algorithm_risk=3.0),
                3,
            ),
            "global_risk_quantity_cap": (
                base_context(global_available_risk=3.0, global_quantity_cap=100),
                3,
            ),
        }

        for cap_id, case in cases.items():
            context = case[0]
            expected_quantity = case[1]
            config = case[2] if len(case) > 2 else MetaStrategySizingConfig()
            with self.subTest(cap_id=cap_id):
                result = calculate_meta_strategy_position_size(context, config=config)

                self.assertEqual(result.limiting_cap, cap_id)
                self.assertEqual(result.quantity, expected_quantity)
                self.assertEqual(result.quantity, min(cap.quantity for cap in result.caps))
                self.assertIn(f"meta_strategy.sizing.cap.{cap_id}", result.reason_codes)

    def test_ml_risk_multiplier_cannot_increase_quantity(self) -> None:
        full = calculate_meta_strategy_position_size(base_context())
        reduced = calculate_meta_strategy_position_size(base_context(model_risk_multiplier=0.50))

        self.assertLessEqual(reduced.quantity, full.quantity)
        self.assertLessEqual(reduced.ml_adjusted_risk_dollars, reduced.dynamic_profile_risk_dollars)
        self.assertLessEqual(full.dynamic_profile_risk_dollars, full.base_risk_dollars)
        self.assertIn("meta_strategy.sizing.ml_cannot_increase_quantity", full.reason_codes)
        with self.assertRaisesRegex(ValueError, "model_risk_multiplier_out_of_bounds"):
            base_context(model_risk_multiplier=1.01)
        with self.assertRaisesRegex(ValueError, "model_risk_multiplier_out_of_bounds"):
            base_context(model_risk_multiplier=-0.01)

    def test_dynamic_profile_risk_and_risk_off_profiles_reduce_quantity(self) -> None:
        baseline = baseline_settings(risk_percentage=0.10)
        normal = calculate_meta_strategy_position_size(
            base_context(
                baseline_settings=baseline,
                effective_settings=effective_settings(baseline, risk_percentage=0.10, position_cap=2.0),
                available_buying_power=1_000_000.0,
                market_liquidity=1_000_000.0,
            )
        )
        defensive = calculate_meta_strategy_position_size(
            base_context(
                baseline_settings=baseline,
                effective_settings=effective_settings(baseline, risk_percentage=0.01, position_cap=2.0),
                available_buying_power=1_000_000.0,
                market_liquidity=1_000_000.0,
            )
        )
        risk_off = calculate_meta_strategy_position_size(
            base_context(
                baseline_settings=baseline,
                effective_settings=effective_settings(baseline, risk_percentage=0.0, position_cap=0.0, trade_count_limit=0, allow_long=False, allow_short=False),
            )
        )

        self.assertLess(defensive.quantity, normal.quantity)
        self.assertEqual(risk_off.quantity, 0)
        self.assertEqual(risk_off.dynamic_profile_risk_dollars, 0.0)

    def test_hold_rejected_candidates_and_failed_local_gates_receive_zero_quantity(self) -> None:
        cases = {
            "hold": base_context(side="HOLD"),
            "rejected": base_context(candidate_accepted=False),
            "local_gate_failed": base_context(local_gates_passed=False),
        }

        for name, context in cases.items():
            with self.subTest(name=name):
                result = calculate_meta_strategy_position_size(context)
                self.assertEqual(result.quantity, 0)
                self.assertEqual(result.ml_adjusted_risk_dollars, 0.0)

        self.assertIn("meta_strategy.sizing.candidate_rejected_or_hold", calculate_meta_strategy_position_size(cases["hold"]).reason_codes)
        self.assertIn("meta_strategy.sizing.local_gate_failed", calculate_meta_strategy_position_size(cases["local_gate_failed"]).reason_codes)

    def test_invalid_prices_and_stop_distances_fail_safely(self) -> None:
        cases = {
            "entry_price": (base_context(entry_price=0.0), "meta_strategy.sizing.invalid_entry_price"),
            "stop_distance": (base_context(stop_distance=0.0), "meta_strategy.sizing.invalid_stop_distance"),
            "account_equity": (base_context(account_equity=0.0), "meta_strategy.sizing.invalid_account_equity"),
            "buying_power": (base_context(available_buying_power=-1.0), "meta_strategy.sizing.invalid_buying_power"),
            "liquidity": (base_context(market_liquidity=-1.0), "meta_strategy.sizing.invalid_liquidity"),
            "remaining_algorithm_risk": (
                base_context(remaining_algorithm_risk=-1.0),
                "meta_strategy.sizing.invalid_remaining_algorithm_risk",
            ),
            "global_risk": (base_context(global_available_risk=-1.0), "meta_strategy.sizing.invalid_global_risk"),
        }

        for name, (context, reason_code) in cases.items():
            with self.subTest(name=name):
                result = calculate_meta_strategy_position_size(context)
                self.assertEqual(result.quantity, 0)
                self.assertEqual(result.limiting_cap, "invalid_market")
                self.assertIn(reason_code, result.reason_codes)


if __name__ == "__main__":
    unittest.main()
