from __future__ import annotations

import unittest
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.weighted_voting.aggregation import aggregate_weighted_signals
from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.models import (
    WeightedDataQualityStatus,
    WeightedSide,
    WeightedStrategyFamily,
    WeightedStrategyOutcome,
    WeightedVotingSignal,
    WeightedWeightStateStatus,
)
from backend.app.algorithms.weighted_voting.weight_engine import (
    apply_weight_controls,
    create_unseeded_equal_weight_state,
    update_performance_weight_state,
)


TS = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)


class WeightedVotingWeightEngineTest(unittest.TestCase):
    def test_strategy_and_family_caps_hold_after_normalization(self) -> None:
        signals = strategy_signals(
            {
                "S4": 0.42,
                "S7": 0.32,
                "S5": 0.08,
                "S6": 0.08,
                "S1": 0.03,
                "S8": 0.03,
                "S2": 0.02,
                "S3": 0.02,
            }
        )

        result = apply_weight_controls(signals, historical_outcomes=correlated_mean_reversion_outcomes())
        final_weights = {signal.strategy_id: signal.final_weight for signal in result.signals}
        family_totals = family_weights(result.signals)

        self.assertAlmostEqual(sum(final_weights.values()), 1.0, delta=0.0000001)
        self.assertTrue(all(weight >= 0 for weight in final_weights.values()))
        self.assertTrue(all(weight <= 0.25 + 0.0000001 for weight in final_weights.values()))
        self.assertTrue(all(weight <= 0.40 + 0.0000001 for weight in family_totals.values()))
        self.assertLessEqual(family_totals[WeightedStrategyFamily.MEAN_REVERSION.value], 0.40 + 0.0000001)

    def test_disabled_and_unavailable_strategies_have_zero_weight(self) -> None:
        signals = strategy_signals({strategy_id: 0.125 for strategy_id in FAMILY_BY_STRATEGY})
        signals = update_signal(signals, "S1", {"eligible": False})
        signals = update_signal(
            signals,
            "S6",
            {
                "data_ready": False,
                "data_quality_status": WeightedDataQualityStatus.UNAVAILABLE,
                "p_buy": 0.0,
                "p_sell": 0.0,
                "p_hold": 1.0,
                "signal": WeightedSide.HOLD,
            },
        )

        result = apply_weight_controls(signals)
        final_weights = {signal.strategy_id: signal.final_weight for signal in result.signals}

        self.assertEqual(final_weights["S1"], 0.0)
        self.assertEqual(final_weights["S6"], 0.0)
        self.assertAlmostEqual(sum(final_weights.values()), 1.0, delta=0.0000001)

    def test_minimum_enabled_weight_and_data_quality_adjustments_are_visible(self) -> None:
        signals = strategy_signals({"S1": 0.91, "S2": 0.03, "S3": 0.02, "S4": 0.01, "S5": 0.01, "S6": 0.01, "S7": 0.005, "S8": 0.005})
        signals = update_signal(signals, "S6", {"data_quality_status": WeightedDataQualityStatus.PROXY})

        result = apply_weight_controls(signals)
        adjustments = {adjustment.strategy_id: adjustment for adjustment in result.adjustments}

        self.assertTrue(all(signal.final_weight >= 0.02 - 0.0000001 for signal in result.signals if signal.final_weight > 0))
        self.assertEqual(adjustments["S6"].data_quality_adjustment, 0.7)
        self.assertIn("weighted_voting.weight.data_quality", adjustments["S6"].reason_codes)
        self.assertLess(adjustments["S1"].family_cap_adjustment, 1.0)

    def test_correlation_penalties_use_weighted_owned_outcomes_only(self) -> None:
        signals = strategy_signals({strategy_id: 0.125 for strategy_id in FAMILY_BY_STRATEGY})
        result = apply_weight_controls(signals, historical_outcomes=correlated_mean_reversion_outcomes())
        adjustments = {adjustment.strategy_id: adjustment for adjustment in result.adjustments}

        self.assertLess(adjustments["S4"].correlation_penalty, 1.0)
        self.assertLess(adjustments["S7"].correlation_penalty, 1.0)
        self.assertEqual(adjustments["S1"].correlation_penalty, 1.0)

    def test_aggregation_decision_records_effective_weight_adjustments(self) -> None:
        signals = strategy_signals({strategy_id: 0.125 for strategy_id in FAMILY_BY_STRATEGY})
        decision = aggregate_weighted_signals(signals, decision_timestamp=TS, historical_outcomes=correlated_mean_reversion_outcomes())

        self.assertEqual(len(decision.weight_adjustments), 8)
        self.assertAlmostEqual(sum(adjustment.final_effective_weight for adjustment in decision.weight_adjustments), 1.0, delta=0.0000001)
        self.assertTrue(all(adjustment.original_frozen_weight >= 0 for adjustment in decision.weight_adjustments))
        self.assertTrue(any(adjustment.correlation_penalty < 1 for adjustment in decision.weight_adjustments))

    def test_initial_weight_state_is_unseeded_equal_weights(self) -> None:
        state = create_unseeded_equal_weight_state(timestamp=TS)

        self.assertEqual(state.state_status, WeightedWeightStateStatus.UNSEEDED_EQUAL_WEIGHTS.value)
        self.assertEqual(set(state.strategy_weights), set(FAMILY_BY_STRATEGY))
        self.assertTrue(all(weight == 0.125 for weight in state.strategy_weights.values()))
        self.assertAlmostEqual(sum(state.strategy_weights.values()), 1.0, delta=0.0000001)

    def test_same_session_update_preserves_frozen_active_weights(self) -> None:
        state = create_unseeded_equal_weight_state(timestamp=TS)
        seeded = update_performance_weight_state(
            state,
            performance_outcomes({"S1": [0.02] * 45, "S4": [-0.01] * 45}),
            update_timestamp=TS,
            session_date="2026-01-05",
        )

        intraday_attempt = update_performance_weight_state(
            seeded,
            performance_outcomes({"S1": [-0.03] * 45, "S4": [0.03] * 45}),
            update_timestamp=TS + timedelta(hours=2),
            session_date="2026-01-05",
        )

        self.assertEqual(intraday_attempt.strategy_weights, seeded.strategy_weights)
        self.assertEqual(intraday_attempt.active_session_date, "2026-01-05")

    def test_few_samples_remain_close_to_equal_weight(self) -> None:
        state = create_unseeded_equal_weight_state(timestamp=TS)
        updated = update_performance_weight_state(
            state,
            performance_outcomes({"S1": [0.03] * 5, "S2": [-0.01] * 5}),
            update_timestamp=TS,
            session_date="2026-01-06",
        )

        self.assertLessEqual(abs(updated.strategy_weights["S1"] - 0.125), 0.025 + 0.0000001)
        self.assertLess(updated.performance_metrics[0].sample_shrinkage, 1.0)
        self.assertAlmostEqual(sum(updated.strategy_weights.values()), 1.0, delta=0.0000001)

    def test_daily_weight_changes_respect_configured_limit(self) -> None:
        config = WeightedVotingConfig(maximum_daily_weight_change=0.02)
        state = create_unseeded_equal_weight_state(timestamp=TS)
        updated = update_performance_weight_state(
            state,
            performance_outcomes({"S1": [0.03] * 60, "S8": [0.025] * 60, "S4": [-0.02] * 60, "S7": [-0.02] * 60}),
            update_timestamp=TS,
            session_date="2026-01-07",
            config=config,
        )

        for strategy_id, weight in updated.strategy_weights.items():
            self.assertLessEqual(abs(weight - state.strategy_weights[strategy_id]), 0.02 + 0.0000001)
        self.assertAlmostEqual(sum(updated.strategy_weights.values()), 1.0, delta=0.0000001)

    def test_failed_update_preserves_last_valid_active_weights(self) -> None:
        state = create_unseeded_equal_weight_state(timestamp=TS)
        bad_config = WeightedVotingConfig(weight_smoothing_previous=0.80, weight_smoothing_candidate=0.30)

        failed = update_performance_weight_state(
            state,
            performance_outcomes({"S1": [0.03] * 60}),
            update_timestamp=TS,
            session_date="2026-01-08",
            config=bad_config,
        )

        self.assertEqual(failed.state_status, WeightedWeightStateStatus.VALIDATION_FAILED.value)
        self.assertEqual(failed.strategy_weights, state.strategy_weights)
        self.assertIn("weighted_voting.weights.validation_failed", failed.reason_codes)


FAMILY_BY_STRATEGY = {
    "S1": WeightedStrategyFamily.BREAKOUT,
    "S8": WeightedStrategyFamily.BREAKOUT,
    "S2": WeightedStrategyFamily.TREND,
    "S3": WeightedStrategyFamily.TREND,
    "S4": WeightedStrategyFamily.MEAN_REVERSION,
    "S7": WeightedStrategyFamily.MEAN_REVERSION,
    "S5": WeightedStrategyFamily.REVERSAL,
    "S6": WeightedStrategyFamily.REVERSAL,
}


def strategy_signals(weights: dict[str, float]) -> list[WeightedVotingSignal]:
    return [
        WeightedVotingSignal(
            strategy_id=strategy_id,
            strategy_name=f"{strategy_id} synthetic",
            strategy_version="weighted_strategy_test_v1",
            family=family,
            signal=WeightedSide.BUY,
            p_buy=0.6,
            p_sell=0.1,
            p_hold=0.3,
            directional_confidence=0.6,
            signal_strength=0.6,
            expected_raw_movement=0.001,
            expected_return=0.001,
            expected_return_after_costs=0.0008,
            strength=0.6,
            final_weight=weights.get(strategy_id, 0.0),
            eligible=True,
            data_ready=True,
            required_data_freshness_seconds=300,
            actual_data_freshness_seconds=0,
            data_quality_status=WeightedDataQualityStatus.FULL,
            data_timestamp=TS,
            explanation="Synthetic weighted signal.",
        )
        for strategy_id, family in FAMILY_BY_STRATEGY.items()
    ]


def update_signal(signals: list[WeightedVotingSignal], strategy_id: str, update: dict[str, object]) -> list[WeightedVotingSignal]:
    return [
        signal.model_copy(update=update) if signal.strategy_id == strategy_id else signal
        for signal in signals
    ]


def correlated_mean_reversion_outcomes() -> tuple[WeightedStrategyOutcome, ...]:
    returns = (0.01, -0.004, 0.008, -0.002, 0.011, -0.003)
    outcomes: list[WeightedStrategyOutcome] = []
    for index, value in enumerate(returns):
        outcomes.append(outcome("S4", index, value))
        outcomes.append(outcome("S7", index, value * 0.95))
        outcomes.append(outcome("S1", index, ((-1) ** index) * 0.002))
    return tuple(outcomes)


def performance_outcomes(returns_by_strategy: dict[str, list[float]]) -> tuple[WeightedStrategyOutcome, ...]:
    outcomes: list[WeightedStrategyOutcome] = []
    index = 0
    for strategy_id, returns in returns_by_strategy.items():
        for value in returns:
            outcomes.append(outcome(strategy_id, index, value))
            index += 1
    return tuple(outcomes)


def outcome(strategy_id: str, index: int, value: float) -> WeightedStrategyOutcome:
    return WeightedStrategyOutcome(
        strategy_id=strategy_id,
        side=WeightedSide.BUY,
        entry_timestamp=TS + timedelta(minutes=index),
        entry_price=100,
        outcome_return=value,
        explanation="Synthetic Weighted-owned completed outcome.",
    )


def family_weights(signals: tuple[WeightedVotingSignal, ...]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for signal in signals:
        totals[signal.family] += signal.final_weight
    return totals


if __name__ == "__main__":
    unittest.main()
