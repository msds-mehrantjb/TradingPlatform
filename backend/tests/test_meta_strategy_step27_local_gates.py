from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.app.algorithms.meta_strategy.local_gates import (
    MetaStrategyLocalGateConfig,
    MetaStrategyLocalGateContext,
    evaluate_meta_strategy_local_gates,
)


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


EXPECTED_GATE_IDS = {
    "minimum_active_strategies",
    "minimum_independent_families",
    "minimum_deterministic_score",
    "minimum_deterministic_edge",
    "minimum_calibrated_success_probability",
    "maximum_uncertainty",
    "maximum_missingness",
    "maximum_ood_score",
    "minimum_model_health",
    "minimum_reward_risk_after_costs",
    "maximum_spread",
    "minimum_liquidity",
    "daily_loss_limit",
    "trade_count_limit",
    "cooldown",
    "event_blackout",
    "session_restriction",
    "paper_live_permission",
}


def base_context() -> MetaStrategyLocalGateContext:
    return MetaStrategyLocalGateContext(
        timestamp=NOW,
        proposed_quantity=12,
        active_strategy_count=3,
        independent_family_count=3,
        deterministic_score=0.70,
        deterministic_edge=0.12,
        calibrated_success_probability=0.64,
        uncertainty=0.25,
        missingness=0.05,
        ood_score=0.10,
        model_health_score=0.95,
        reward_risk_after_costs=1.60,
        spread_bps=4.0,
        liquidity=250_000.0,
        realized_daily_pnl=-100.0,
        daily_trade_count=1,
        last_trade_at=NOW - timedelta(minutes=15),
        event_blackout=False,
        session_phase="regular",
        execution_mode="PAPER",
        paper_trading_permission=True,
        live_trading_permission=False,
    )


class MetaStrategyStep27LocalGatesTest(unittest.TestCase):
    def test_all_local_gates_have_non_triggering_pass_case(self) -> None:
        result = evaluate_meta_strategy_local_gates(base_context())

        self.assertTrue(result.passed)
        self.assertEqual(result.proposed_quantity, 12)
        self.assertEqual(result.approved_quantity, 12)
        self.assertEqual({gate.gate_id for gate in result.gate_results}, EXPECTED_GATE_IDS)
        self.assertTrue(all(gate.passed for gate in result.gate_results))
        self.assertEqual(result.reason_codes, ())
        self.assertEqual(result.scope, "LOCAL_META_STRATEGY")
        self.assertFalse(result.global_gates_applied)

    def test_each_local_gate_trigger_forces_quantity_to_zero(self) -> None:
        cases = {
            "minimum_active_strategies": (
                replace(base_context(), active_strategy_count=1),
                "meta_strategy.local_gate.minimum_active_strategies_below_minimum",
            ),
            "minimum_independent_families": (
                replace(base_context(), independent_family_count=1),
                "meta_strategy.local_gate.minimum_independent_families_below_minimum",
            ),
            "minimum_deterministic_score": (
                replace(base_context(), deterministic_score=0.49),
                "meta_strategy.local_gate.minimum_deterministic_score_below_minimum",
            ),
            "minimum_deterministic_edge": (
                replace(base_context(), deterministic_edge=0.04),
                "meta_strategy.local_gate.minimum_deterministic_edge_below_minimum",
            ),
            "minimum_calibrated_success_probability": (
                replace(base_context(), calibrated_success_probability=0.51),
                "meta_strategy.local_gate.minimum_calibrated_success_probability_below_minimum",
            ),
            "maximum_uncertainty": (
                replace(base_context(), uncertainty=0.46),
                "meta_strategy.local_gate.maximum_uncertainty_above_maximum",
            ),
            "maximum_missingness": (
                replace(base_context(), missingness=0.26),
                "meta_strategy.local_gate.maximum_missingness_above_maximum",
            ),
            "maximum_ood_score": (
                replace(base_context(), ood_score=0.71),
                "meta_strategy.local_gate.maximum_ood_score_above_maximum",
            ),
            "minimum_model_health": (
                replace(base_context(), model_health_score=0.69),
                "meta_strategy.local_gate.minimum_model_health_below_minimum",
            ),
            "minimum_reward_risk_after_costs": (
                replace(base_context(), reward_risk_after_costs=0.99),
                "meta_strategy.local_gate.minimum_reward_risk_after_costs_below_minimum",
            ),
            "maximum_spread": (
                replace(base_context(), spread_bps=16.0),
                "meta_strategy.local_gate.maximum_spread_above_maximum",
            ),
            "minimum_liquidity": (
                replace(base_context(), liquidity=49_999.0),
                "meta_strategy.local_gate.minimum_liquidity_below_minimum",
            ),
            "daily_loss_limit": (
                replace(base_context(), realized_daily_pnl=-1_000.0),
                "meta_strategy.local_gate.daily_loss_limit_reached",
            ),
            "trade_count_limit": (
                replace(base_context(), daily_trade_count=5),
                "meta_strategy.local_gate.trade_count_limit_reached",
            ),
            "cooldown": (
                replace(base_context(), last_trade_at=NOW - timedelta(seconds=60)),
                "meta_strategy.local_gate.cooldown_active",
            ),
            "event_blackout": (
                replace(base_context(), event_blackout=True),
                "meta_strategy.local_gate.event_blackout_active",
            ),
            "session_restriction": (
                replace(base_context(), session_phase="after_hours"),
                "meta_strategy.local_gate.session_restricted",
            ),
            "paper_live_permission": (
                replace(base_context(), paper_trading_permission=False),
                "meta_strategy.local_gate.paper_live_permission_denied",
            ),
        }

        for gate_id, (context, reason_code) in cases.items():
            with self.subTest(gate_id=gate_id):
                result = evaluate_meta_strategy_local_gates(context)
                gate = next(item for item in result.gate_results if item.gate_id == gate_id)

                self.assertFalse(result.passed)
                self.assertFalse(gate.passed)
                self.assertEqual(result.approved_quantity, 0)
                self.assertIn(reason_code, result.reason_codes)
                self.assertFalse(result.global_gates_applied)

    def test_threshold_boundaries_pass_and_live_permission_is_explicit(self) -> None:
        boundary = replace(
            base_context(),
            active_strategy_count=2,
            independent_family_count=2,
            deterministic_score=0.50,
            deterministic_edge=0.05,
            calibrated_success_probability=0.52,
            uncertainty=0.45,
            missingness=0.25,
            ood_score=0.70,
            model_health_score=0.70,
            reward_risk_after_costs=1.00,
            spread_bps=15.0,
            liquidity=50_000.0,
            daily_trade_count=4,
            realized_daily_pnl=-999.99,
            last_trade_at=NOW - timedelta(seconds=300),
        )
        live = replace(boundary, execution_mode="LIVE", live_trading_permission=True)

        self.assertTrue(evaluate_meta_strategy_local_gates(boundary).passed)
        self.assertTrue(
            evaluate_meta_strategy_local_gates(
                live,
                config=MetaStrategyLocalGateConfig(live_trading_allowed=True),
            ).passed
        )
        self.assertFalse(evaluate_meta_strategy_local_gates(live).passed)

    def test_local_gates_do_not_import_or_apply_global_gates(self) -> None:
        source = Path("backend/app/algorithms/meta_strategy/local_gates.py").read_text(encoding="utf-8")
        result = evaluate_meta_strategy_local_gates(base_context())

        self.assertNotIn("backend.app.gates", source)
        self.assertNotIn("GlobalGate", source)
        self.assertFalse(result.global_gates_applied)


if __name__ == "__main__":
    unittest.main()
