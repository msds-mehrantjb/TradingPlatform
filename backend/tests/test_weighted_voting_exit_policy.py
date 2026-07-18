from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.weighted_voting.aggregation import aggregate_weighted_signals
from backend.app.algorithms.weighted_voting.dynamic_settings import default_dynamic_envelope, default_hard_limits, default_weighted_settings, resolve_effective_settings
from backend.app.algorithms.weighted_voting.exit_policy import (
    WeightedExitAction,
    WeightedVotingExitInputs,
    exit_policy_status,
    evaluate_exit_lifecycle,
    open_exit_lifecycle,
)
from backend.app.algorithms.weighted_voting.models import (
    WeightedDataQualityStatus,
    WeightedExitReason,
    WeightedMarketQuality,
    WeightedSide,
    WeightedStrategyFamily,
    WeightedVotingSignal,
)


TS = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)


class WeightedVotingExitPolicyTest(unittest.TestCase):
    def test_every_position_starts_with_protective_stop_and_freezes_settings(self) -> None:
        settings = effective_settings(target_r=2.5)
        lifecycle = lifecycle_for(settings=settings)

        self.assertEqual(lifecycle.protective_stop, 99.0)
        self.assertEqual(lifecycle.structural_invalidation, 99.0)
        self.assertIsNone(lifecycle.atr_fallback_stop)
        self.assertEqual(lifecycle.original_effective_settings, settings)
        self.assertEqual(lifecycle.profit_target, 102.5)
        self.assertEqual(lifecycle.risk_reward_requirement, settings.target_r)
        self.assertEqual(lifecycle.original_quantity, lifecycle.remaining_quantity)

    def test_structural_invalidation_atr_fallback_and_risk_reward_are_owned_by_policy(self) -> None:
        lifecycle = open_exit_lifecycle(
            trade_id="trade-atr",
            symbol="SPY",
            side=WeightedSide.BUY,
            quantity=100,
            entry_price=100.0,
            entry_timestamp=TS,
            stop_price=99.0,
            structural_invalidation=98.75,
            atr_fallback_stop=99.2,
            minimum_risk_reward=2.0,
            effective_settings=effective_settings(target_r=2.5),
        )
        decision = evaluate_exit_lifecycle(
            WeightedVotingExitInputs(
                lifecycle=lifecycle,
                current_price=100.1,
                current_timestamp=TS + timedelta(minutes=1),
                current_condition_quality=WeightedMarketQuality.CLEAN,
                partial_profit_enabled=False,
            )
        )

        self.assertEqual(decision.structural_invalidation, 98.75)
        self.assertEqual(decision.atr_fallback_stop, 99.2)
        self.assertEqual(decision.risk_reward_requirement, 2.0)

        with self.assertRaises(ValueError):
            open_exit_lifecycle(
                trade_id="trade-bad-rr",
                symbol="SPY",
                side=WeightedSide.BUY,
                quantity=100,
                entry_price=100.0,
                entry_timestamp=TS,
                stop_price=99.0,
                minimum_risk_reward=2.0,
                effective_settings=effective_settings(target_r=1.5),
            )

    def test_no_stop_widening_or_risk_increase_is_possible(self) -> None:
        lifecycle = lifecycle_for(stop=99.0)
        decision = evaluate_exit_lifecycle(
            WeightedVotingExitInputs(
                lifecycle=lifecycle,
                current_price=100.2,
                current_timestamp=TS + timedelta(minutes=5),
                current_condition_quality=WeightedMarketQuality.CLEAN,
            )
        )

        self.assertGreaterEqual(decision.stop_price, lifecycle.protective_stop)
        self.assertLessEqual(decision.risk_per_share, lifecycle.original_risk_per_share)
        self.assertEqual(decision.updated_lifecycle.remaining_quantity, lifecycle.remaining_quantity)

    def test_end_of_session_positions_are_closed(self) -> None:
        decision = evaluate_exit_lifecycle(
            WeightedVotingExitInputs(
                lifecycle=lifecycle_for(),
                current_price=100.5,
                current_timestamp=TS + timedelta(hours=6),
                current_condition_quality=WeightedMarketQuality.CLEAN,
                end_of_session=True,
            )
        )

        self.assertEqual(decision.action, WeightedExitAction.EXIT.value)
        self.assertEqual(decision.exit_reason, WeightedExitReason.END_OF_DAY.value)
        self.assertEqual(decision.exit_quantity, 100)
        self.assertIn("weighted_voting.exit.end_of_session_liquidation", decision.reason_codes)

    def test_global_entry_blocks_do_not_block_protective_exits(self) -> None:
        decision = evaluate_exit_lifecycle(
            WeightedVotingExitInputs(
                lifecycle=lifecycle_for(),
                current_price=98.9,
                current_timestamp=TS + timedelta(minutes=2),
                current_condition_quality=WeightedMarketQuality.UNSTABLE,
                new_entries_blocked=True,
            )
        )

        self.assertEqual(decision.action, WeightedExitAction.EXIT.value)
        self.assertEqual(decision.exit_reason, WeightedExitReason.STOP_HIT.value)
        self.assertIn("weighted_voting.exit.protective_stop_hit", decision.reason_codes)

    def test_deterioration_exits_require_persistence_without_emergency(self) -> None:
        first = evaluate_exit_lifecycle(
            WeightedVotingExitInputs(
                lifecycle=lifecycle_for(),
                current_price=100.2,
                current_timestamp=TS + timedelta(minutes=3),
                current_condition_quality=WeightedMarketQuality.UNSTABLE,
                current_weighted_decision=weak_edge_decision(),
                deterioration_required_count=2,
            )
        )
        second = evaluate_exit_lifecycle(
            WeightedVotingExitInputs(
                lifecycle=first.updated_lifecycle,
                current_price=100.1,
                current_timestamp=TS + timedelta(minutes=4),
                current_condition_quality=WeightedMarketQuality.UNSTABLE,
                current_weighted_decision=weak_edge_decision(),
                deterioration_required_count=2,
            )
        )

        self.assertEqual(first.action, WeightedExitAction.HOLD.value)
        self.assertIn("weighted_voting.exit.deterioration_observed", first.reason_codes)
        self.assertEqual(second.action, WeightedExitAction.EXIT.value)
        self.assertIn("weighted_voting.exit.persistent_deterioration", second.reason_codes)

    def test_global_emergency_exit_does_not_wait_for_persistence(self) -> None:
        decision = evaluate_exit_lifecycle(
            WeightedVotingExitInputs(
                lifecycle=lifecycle_for(),
                current_price=100.2,
                current_timestamp=TS + timedelta(minutes=1),
                current_condition_quality=WeightedMarketQuality.CLEAN,
                global_emergency_exit=True,
            )
        )

        self.assertEqual(decision.action, WeightedExitAction.EXIT.value)
        self.assertEqual(decision.exit_reason, WeightedExitReason.RISK_GATE.value)
        self.assertTrue(decision.emergency_exit)
        self.assertTrue(decision.global_emergency_override)
        self.assertIn("weighted_voting.exit.global_emergency_exit", decision.reason_codes)

    def test_partial_profit_rule_reduces_but_does_not_close_position(self) -> None:
        decision = evaluate_exit_lifecycle(
            WeightedVotingExitInputs(
                lifecycle=lifecycle_for(),
                current_price=101.1,
                current_timestamp=TS + timedelta(minutes=2),
                current_condition_quality=WeightedMarketQuality.MIXED,
                partial_profit_fraction=0.5,
            )
        )

        self.assertEqual(decision.action, WeightedExitAction.PARTIAL_EXIT.value)
        self.assertEqual(decision.partial_exit_quantity, 50)
        self.assertEqual(decision.exit_quantity, 50)
        self.assertEqual(decision.updated_lifecycle.remaining_quantity, 50)
        self.assertTrue(decision.updated_lifecycle.partial_profit_taken)
        self.assertIn("weighted_voting.exit.partial_profit", decision.reason_codes)

    def test_signal_decay_and_opposing_weight_exit_after_persistence(self) -> None:
        first = evaluate_exit_lifecycle(
            WeightedVotingExitInputs(
                lifecycle=lifecycle_for(),
                current_price=100.2,
                current_timestamp=TS + timedelta(minutes=3),
                current_condition_quality=WeightedMarketQuality.CLEAN,
                signal_decay_exit=True,
                deterioration_required_count=2,
            )
        )
        second = evaluate_exit_lifecycle(
            WeightedVotingExitInputs(
                lifecycle=first.updated_lifecycle,
                current_price=100.2,
                current_timestamp=TS + timedelta(minutes=4),
                current_condition_quality=WeightedMarketQuality.CLEAN,
                opposing_weight_exit=True,
                deterioration_required_count=2,
            )
        )

        self.assertEqual(first.action, WeightedExitAction.HOLD.value)
        self.assertEqual(second.action, WeightedExitAction.EXIT.value)
        self.assertIn("weighted_voting.exit.persistent_deterioration", second.reason_codes)

    def test_weighted_voting_closes_only_its_own_allocation(self) -> None:
        decision = evaluate_exit_lifecycle(
            WeightedVotingExitInputs(
                lifecycle=lifecycle_for(weighted_allocation_id="weighted_voting"),
                current_price=98.5,
                current_timestamp=TS + timedelta(minutes=1),
                current_condition_quality=WeightedMarketQuality.UNSTABLE,
                global_emergency_exit=True,
                weighted_allocation_id="other_algorithm",
            )
        )

        self.assertEqual(decision.action, WeightedExitAction.HOLD.value)
        self.assertEqual(decision.exit_quantity, 0)

    def test_status_documents_global_emergency_as_reduce_or_close_only(self) -> None:
        status = exit_policy_status()

        self.assertEqual(status["ownership"], "weighted_voting_positions_only")
        self.assertEqual(status["globalEmergencyOverride"], "global controls may force reduction_or_closure_only")


def lifecycle_for(*, settings=None, stop: float = 99.0, weighted_allocation_id: str = "weighted_voting"):
    return open_exit_lifecycle(
        trade_id="trade-1",
        symbol="SPY",
        side=WeightedSide.BUY,
        quantity=100,
        entry_price=100.0,
        entry_timestamp=TS,
        stop_price=stop,
        effective_settings=settings or effective_settings(),
        weighted_allocation_id=weighted_allocation_id,
    )


def effective_settings(**overrides):
    settings = resolve_effective_settings(
        default_settings=default_weighted_settings(timestamp=TS),
        dynamic_envelope=default_dynamic_envelope(timestamp=TS),
        hard_limits=default_hard_limits(timestamp=TS),
        timestamp=TS,
    )
    return settings.model_copy(update=overrides) if overrides else settings


def weak_edge_decision():
    return aggregate_weighted_signals(strategy_signals(), decision_timestamp=TS)


def strategy_signals() -> list[WeightedVotingSignal]:
    return [
        WeightedVotingSignal(
            strategy_id=f"S{index}",
            strategy_name=f"Synthetic {index}",
            strategy_version="weighted_strategy_test_v1",
            family=WeightedStrategyFamily.TREND,
            signal=WeightedSide.BUY,
            p_buy=0.45,
            p_sell=0.43,
            p_hold=0.12,
            directional_confidence=0.45,
            signal_strength=0.45,
            expected_raw_movement=0.0,
            expected_return=0.0,
            expected_return_after_costs=0.0,
            strength=0.45,
            final_weight=0.125,
            eligible=True,
            data_ready=True,
            data_quality_status=WeightedDataQualityStatus.FULL,
            data_timestamp=TS,
            explanation="Synthetic weak-edge signal.",
        )
        for index in range(8)
    ]


if __name__ == "__main__":
    unittest.main()
