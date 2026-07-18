from __future__ import annotations

import unittest
from dataclasses import replace

from backend.app.algorithms.wca.contracts import WcaBaselineSettings, WcaEffectiveSettings, WcaSide
from backend.app.algorithms.wca.sizing import (
    WCA_SIZING_INPUT_IDS,
    WCA_SIZING_INPUT_INVENTORY,
    WcaManualSizingOverride,
    WcaSizingContext,
    size_wca_order,
    tighten_protective_stop,
)


class WcaStep11SizingTests(unittest.TestCase):
    def test_wca_sizing_input_inventory_is_exact(self) -> None:
        expected = (
            "wca_signal_strength",
            "wca_confidence_and_edge",
            "wca_risk_allocation",
            "stop_distance",
            "available_buying_power",
            "position_cap_limit",
            "liquidity_participation",
            "maximum_shares",
            "remaining_wca_risk_budget",
            "global_gate_quantity_cap",
        )

        self.assertEqual(tuple(row.input_id for row in WCA_SIZING_INPUT_INVENTORY), expected)
        self.assertEqual(WCA_SIZING_INPUT_IDS, set(expected))
        self.assertTrue(all(row.source and row.responsibility for row in WCA_SIZING_INPUT_INVENTORY))

    def test_sizing_reports_each_limiting_cap(self) -> None:
        cases = (
            ("risk_based", replace(_context(), approved_risk_budget=6.0), _settings()),
            ("order_allocation", _context(), _settings(order_allocation_percent=0.5)),
            ("maximum_position", _context(), _settings(max_position_percent=0.5)),
            ("buying_power", replace(_context(), available_buying_power=450), _settings()),
            ("liquidity_participation", replace(_context(), average_one_minute_volume=4), _settings(max_participation_percent=100)),
            ("maximum_shares", _context(), _settings(max_allowed_shares=4)),
            ("global_gate", replace(_context(), global_gate_quantity_cap=4), _settings()),
        )

        for expected_cap, context, settings in cases:
            with self.subTest(expected_cap=expected_cap):
                result = size_wca_order(context, settings)

                self.assertEqual(result.sizing.limiting_factor, expected_cap)
                self.assertGreaterEqual(result.sizing.final_quantity, 0)
                self.assertIn(f"wca.sizing.cap.{expected_cap}", result.sizing.reason_codes)

    def test_invalid_inputs_fail_safely_with_zero_quantity(self) -> None:
        cases = (
            replace(_context(), price=0),
            replace(_context(), atr=0),
            replace(_context(), bid=100, ask=100),
            replace(_context(), available_buying_power=0),
        )

        for context in cases:
            with self.subTest(context=context):
                result = size_wca_order(context, _settings())

                self.assertEqual(result.sizing.final_quantity, 0)
                self.assertIsNone(result.proposed_order)
                self.assertTrue(result.sizing.blocked_reason)

    def test_buy_and_sell_orders_have_stops_and_targets_on_correct_side(self) -> None:
        buy = size_wca_order(_context(side=WcaSide.BUY), _settings())
        sell = size_wca_order(_context(side=WcaSide.SELL), _settings())

        self.assertIsNotNone(buy.proposed_order)
        self.assertLess(buy.sizing.stop_price, buy.sizing.entry_price)
        self.assertGreater(buy.sizing.target_price, buy.sizing.entry_price)
        self.assertIsNotNone(sell.proposed_order)
        self.assertGreater(sell.sizing.stop_price, sell.sizing.entry_price)
        self.assertLess(sell.sizing.target_price, sell.sizing.entry_price)

    def test_stop_risk_dollars_do_not_exceed_approved_budget(self) -> None:
        result = size_wca_order(replace(_context(), approved_risk_budget=50), _settings())

        self.assertLessEqual(result.sizing.stop_risk_dollars, 50)
        self.assertEqual(result.sizing.limiting_factor, "risk_based")

    def test_confidence_and_edge_reduce_wca_risk_scalar(self) -> None:
        strong = size_wca_order(replace(_context(), confidence_size_multiplier=0.80, edge_size_multiplier=0.80), _settings()).sizing
        weak_edge = size_wca_order(replace(_context(), confidence_size_multiplier=0.80, edge_size_multiplier=0.20), _settings()).sizing

        self.assertLess(weak_edge.risk_dollars, strong.risk_dollars)
        self.assertAlmostEqual(weak_edge.risk_dollars, strong.risk_dollars * 0.25, places=6)

    def test_quantity_calculation_is_deterministic(self) -> None:
        first = size_wca_order(_context(), _settings()).sizing
        second = size_wca_order(_context(), _settings()).sizing

        self.assertEqual(first.deterministic_json(), second.deterministic_json())

    def test_existing_same_side_position_is_not_increased_by_favorable_profile(self) -> None:
        context = replace(
            _context(),
            current_position_quantity=5,
            current_position_side=WcaSide.BUY,
            dynamic_profile_multiplier=1.0,
        )

        result = size_wca_order(context, _settings(final_risk_percent=1.0))

        self.assertEqual(result.sizing.final_quantity, 0)
        self.assertIsNone(result.proposed_order)
        self.assertEqual(result.sizing.limiting_factor, "position_increase_blocked")

    def test_sizing_only_produces_a_wca_order_proposal(self) -> None:
        result = size_wca_order(_context(), _settings())

        self.assertIsNotNone(result.proposed_order)
        self.assertEqual(result.proposed_order.status, "PROPOSED")
        self.assertEqual(result.proposed_order.quantity, result.sizing.final_quantity)
        self.assertIn("wca_sizing_v2_step11", result.proposed_order.reason_codes)

    def test_protective_stop_can_tighten_but_not_widen(self) -> None:
        self.assertEqual(tighten_protective_stop(current_stop_price=98, proposed_stop_price=99, side=WcaSide.BUY), 99)
        self.assertEqual(tighten_protective_stop(current_stop_price=98, proposed_stop_price=97, side=WcaSide.BUY), 98)
        self.assertEqual(tighten_protective_stop(current_stop_price=102, proposed_stop_price=101, side=WcaSide.SELL), 101)
        self.assertEqual(tighten_protective_stop(current_stop_price=102, proposed_stop_price=103, side=WcaSide.SELL), 102)

    def test_manual_overrides_are_revalidated_after_application(self) -> None:
        valid = size_wca_order(_context(), _settings(), manual_override=WcaManualSizingOverride(quantity=3))
        too_large = size_wca_order(_context(), _settings(), manual_override=WcaManualSizingOverride(quantity=99_999))
        wrong_stop_side = size_wca_order(_context(), _settings(), manual_override=WcaManualSizingOverride(stop_price=105))
        changed_limit = size_wca_order(_context(), _settings(), manual_override=WcaManualSizingOverride(limit_price=101))

        self.assertEqual(valid.sizing.final_quantity, 3)
        self.assertIsNotNone(valid.proposed_order)
        self.assertEqual(too_large.sizing.final_quantity, 0)
        self.assertIsNone(too_large.proposed_order)
        self.assertEqual(wrong_stop_side.sizing.final_quantity, 0)
        self.assertIsNone(wrong_stop_side.proposed_order)
        self.assertEqual(changed_limit.sizing.final_quantity, 0)
        self.assertIsNone(changed_limit.proposed_order)


def _context(side: WcaSide = WcaSide.BUY) -> WcaSizingContext:
    return WcaSizingContext(
        decision_id="decision-step11",
        order_intent_id="intent-step11",
        symbol="SPY",
        side=side,
        price=100,
        atr=1,
        bid=99.9,
        ask=100.1,
        account_equity=100_000,
        available_buying_power=100_000,
        average_one_minute_volume=100_000,
        confidence_size_multiplier=1.0,
        global_gate_quantity_cap=None,
        approved_risk_budget=None,
        fixed_stop_fallback=0.05,
        minimum_spread_multiple=2.0,
        minimum_reward_risk=2.0,
        estimated_cost_per_share=0.0,
    )


def _settings(
    *,
    order_allocation_percent: float = 100.0,
    max_position_percent: float = 100.0,
    max_participation_percent: float = 100.0,
    max_allowed_shares: int = 0,
    final_risk_percent: float = 1.0,
) -> WcaEffectiveSettings:
    baseline = WcaBaselineSettings(
        base_risk_percent=1.0,
        order_allocation_percent=order_allocation_percent,
        max_position_percent=max_position_percent,
        atr_stop_multiplier=1.0,
        minimum_stop_distance_percent=0.05,
        take_profit_r=2.0,
        assumed_slippage_per_share=0.0,
        max_participation_percent=max_participation_percent,
        max_allowed_shares=max_allowed_shares,
        hard_max_risk_percent=1.0,
        hard_max_order_allocation_percent=100.0,
        hard_max_position_percent=100.0,
        hard_max_allowed_shares=max_allowed_shares,
    )
    return WcaEffectiveSettings(
        baseline=baseline,
        baseline_settings_version=baseline.settings_version,
        final_risk_percent=final_risk_percent,
        final_order_allocation_percent=order_allocation_percent,
        final_max_position_percent=max_position_percent,
        final_max_allowed_shares=max_allowed_shares,
        final_max_participation_percent=max_participation_percent,
        final_atr_stop_multiplier=baseline.atr_stop_multiplier,
        final_minimum_stop_distance_percent=baseline.minimum_stop_distance_percent,
        final_take_profit_r=baseline.take_profit_r,
        final_assumed_slippage_per_share=baseline.assumed_slippage_per_share,
        reason_codes=("test.settings",),
    )


if __name__ == "__main__":
    unittest.main()
