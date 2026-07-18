from __future__ import annotations

import unittest
from datetime import datetime, timezone

from backend.app.algorithms.weighted_voting.aggregation import aggregate_weighted_signals
from backend.app.algorithms.weighted_voting.decision_gates import WeightedGateEvaluationMode, WeightedVotingGatePipelineResult
from backend.app.algorithms.weighted_voting.dynamic_settings import default_dynamic_envelope, default_hard_limits, default_weighted_settings, resolve_effective_settings
from backend.app.algorithms.weighted_voting.models import (
    WeightedCandle,
    WeightedDataQualityStatus,
    WeightedGateResult,
    WeightedGateStatus,
    WeightedMarketSnapshot,
    WeightedSide,
    WeightedStrategyFamily,
    WeightedVotingSignal,
)
from backend.app.algorithms.weighted_voting.position_sizing import (
    WeightedVotingSizingContext,
    calculate_weighted_voting_position_size,
)
from backend.app.algorithms.weighted_voting.risk_budget import WeightedVotingRiskBudget


TS = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)


class WeightedVotingPositionSizingTest(unittest.TestCase):
    def test_every_sizing_cap_is_visible_and_limiting_cap_is_identified(self) -> None:
        result = calculate_weighted_voting_position_size(base_context(global_max_shares=3))

        self.assertEqual({cap.cap_id for cap in result.caps}, {"risk", "order_allocation", "maximum_position", "available_buying_power", "liquidity_participation", "maximum_shares", "global_gates"})
        self.assertEqual(result.limiting_cap, "global_gates")
        self.assertEqual(result.limiting_factor, "global_maximum_quantity")
        self.assertEqual(result.quantity, 3)
        self.assertEqual(result.approved_local_quantity, result.requested_quantity)
        self.assertIn("weighted_voting.sizing.cap.global_gates", result.reason_codes)

    def test_result_reports_requested_quantities_and_named_minimum_caps(self) -> None:
        result = calculate_weighted_voting_position_size(base_context(global_max_shares=100000))

        expected_requested = min(
            result.risk_based_quantity,
            result.capital_partition_quantity,
            result.buying_power_quantity,
            result.liquidity_quantity,
            result.volume_participation_quantity,
            result.algorithm_maximum_quantity,
        )
        expected_final = min(expected_requested, result.global_maximum_quantity)

        self.assertEqual(result.requested_quantity, expected_requested)
        self.assertEqual(result.approved_local_quantity, expected_requested)
        self.assertEqual(result.quantity, expected_final)
        self.assertEqual(result.risk_dollars, result.effective_risk_dollars)
        self.assertGreater(result.stop_distance, 0)
        self.assertGreater(result.size_multiplier, 0)
        self.assertLessEqual(result.size_multiplier, 1)
        self.assertIn(f"weighted_voting.sizing.limiting_factor.{result.limiting_factor}", result.reason_codes)

    def test_weighted_voting_risk_budget_and_capital_partition_limit_quantity(self) -> None:
        budget = WeightedVotingRiskBudget(
            account_equity=100000.0,
            risk_percent=2.0,
            data_timestamp=TS,
            pending_trade_risk=600.0,
            capital_partition_percent=0.5,
        )

        result = calculate_weighted_voting_position_size(
            base_context(
                risk_budget=budget,
                remaining_weighted_daily_risk=5000.0,
                remaining_weighted_capital_partition=5000.0,
                global_available_risk=5000.0,
                global_max_shares=100000,
            )
        )

        self.assertEqual(result.capital_partition_quantity, 4)
        self.assertEqual(result.requested_quantity, 4)
        self.assertEqual(result.limiting_factor, "capital_partition_quantity")

    def test_global_risk_cap_can_only_reduce_final_quantity(self) -> None:
        high_global = calculate_weighted_voting_position_size(base_context(global_available_risk=5000.0, global_max_shares=100000))
        low_global = calculate_weighted_voting_position_size(base_context(global_available_risk=2.0, global_max_shares=100000))

        self.assertGreater(high_global.global_maximum_quantity, low_global.global_maximum_quantity)
        self.assertLess(low_global.quantity, high_global.quantity)
        self.assertEqual(low_global.limiting_factor, "global_maximum_quantity")

    def test_spread_comes_from_actual_quote_and_slippage_is_separate(self) -> None:
        result = calculate_weighted_voting_position_size(base_context(slippage_per_share=0.07))

        self.assertEqual(result.actual_bid, 100.0)
        self.assertEqual(result.actual_ask, 100.05)
        self.assertAlmostEqual(result.actual_spread, 0.05, delta=0.0000001)
        self.assertEqual(result.slippage_per_share, 0.07)
        self.assertAlmostEqual(result.spread_safety_buffer, 0.12, delta=0.0000001)

    def test_current_and_average_volume_are_stored_separately_and_average_can_limit_participation(self) -> None:
        result = calculate_weighted_voting_position_size(
            base_context(current_one_minute_volume=100000, average_one_minute_volume=1000, use_average_volume_for_participation=True)
        )

        self.assertEqual(result.current_one_minute_volume, 100000)
        self.assertEqual(result.average_one_minute_volume, 1000)
        self.assertEqual(next(cap.quantity for cap in result.caps if cap.cap_id == "liquidity_participation"), 10)

    def test_wider_stops_produce_fewer_shares_and_do_not_increase_dollar_risk(self) -> None:
        high_cap_settings = effective_settings(order_allocation_percent=100.0, maximum_position_percent=100.0, maximum_shares=100000)
        tight = calculate_weighted_voting_position_size(
            base_context(effective_settings=high_cap_settings, structural_invalidation_price=97.50, atr=0.1, average_one_minute_volume=1000000, remaining_weighted_capital_partition=100000.0)
        )
        wide = calculate_weighted_voting_position_size(
            base_context(effective_settings=high_cap_settings, structural_invalidation_price=95.00, atr=0.1, average_one_minute_volume=1000000, remaining_weighted_capital_partition=100000.0)
        )

        self.assertGreater(wide.stop_distance, tight.stop_distance)
        self.assertLess(wide.quantity, tight.quantity)
        self.assertLessEqual(wide.quantity * wide.stop_distance, wide.effective_risk_dollars + 0.0000001)

    def test_failed_decisions_and_failed_local_gates_produce_zero_quantity(self) -> None:
        failed_decision_result = calculate_weighted_voting_position_size(base_context(decision=hold_decision()))
        failed_gate_result = calculate_weighted_voting_position_size(base_context(local_gate_result=failed_gate_pipeline()))

        self.assertEqual(failed_decision_result.quantity, 0)
        self.assertIn("weighted_voting.sizing.failed_decision", failed_decision_result.reason_codes)
        self.assertEqual(failed_gate_result.quantity, 0)
        self.assertIn("weighted_voting.sizing.local_gate_failed", failed_gate_result.reason_codes)

    def test_no_positive_size_below_minimum_score_or_edge(self) -> None:
        settings = effective_settings(minimum_score=0.9, minimum_edge=0.5)
        result = calculate_weighted_voting_position_size(base_context(effective_settings=settings))

        self.assertEqual(result.quantity, 0)
        self.assertIn("weighted_voting.sizing.minimum_score_not_met", result.reason_codes)

    def test_long_and_short_use_actual_quote_consistently(self) -> None:
        long_result = calculate_weighted_voting_position_size(base_context(decision=directional_decision(WeightedSide.BUY)))
        short_result = calculate_weighted_voting_position_size(base_context(decision=directional_decision(WeightedSide.SELL), structural_invalidation_price=100.50))

        self.assertGreater(long_result.quantity, 0)
        self.assertGreater(short_result.quantity, 0)
        self.assertEqual(long_result.actual_ask, 100.05)
        self.assertEqual(short_result.actual_bid, 100.0)


def base_context(**overrides) -> WeightedVotingSizingContext:
    values = {
        "decision": directional_decision(WeightedSide.BUY),
        "effective_settings": effective_settings(),
        "market_snapshot": market_snapshot(),
        "account_equity": 100000.0,
        "available_buying_power": 50000.0,
        "remaining_weighted_daily_risk": 1000.0,
        "remaining_weighted_capital_partition": 1000.0,
        "global_available_risk": 1000.0,
        "global_max_shares": 100000,
        "structural_invalidation_price": 99.50,
        "atr": 0.5,
        "slippage_per_share": 0.02,
        "current_one_minute_volume": 20000.0,
        "average_one_minute_volume": 20000.0,
        "use_average_volume_for_participation": True,
        "market_quality_multiplier": 1.0,
        "voting_quality_multiplier": 1.0,
        "volatility_multiplier": 1.0,
        "daily_performance_multiplier": 1.0,
        "drawdown_multiplier": 1.0,
    }
    values.update(overrides)
    return WeightedVotingSizingContext(**values)


def effective_settings(**overrides):
    defaults = default_weighted_settings(timestamp=TS)
    envelope = default_dynamic_envelope(timestamp=TS)
    limits = default_hard_limits(timestamp=TS)
    settings = resolve_effective_settings(default_settings=defaults, dynamic_envelope=envelope, hard_limits=limits, timestamp=TS)
    if overrides:
        settings = settings.model_copy(update=overrides)
    return settings


def directional_decision(side: WeightedSide):
    return aggregate_weighted_signals(strategy_signals(side), decision_timestamp=TS)


def hold_decision():
    return aggregate_weighted_signals(strategy_signals(WeightedSide.HOLD, p_buy=0.1, p_sell=0.1, p_hold=0.8), decision_timestamp=TS)


def strategy_signals(side: WeightedSide, *, p_buy: float | None = None, p_sell: float | None = None, p_hold: float | None = None) -> list[WeightedVotingSignal]:
    if side == WeightedSide.BUY:
        probabilities = (0.82, 0.08, 0.10)
    elif side == WeightedSide.SELL:
        probabilities = (0.08, 0.82, 0.10)
    else:
        probabilities = (p_buy or 0.1, p_sell or 0.1, p_hold or 0.8)
    return [
        WeightedVotingSignal(
            strategy_id=strategy_id,
            strategy_name=f"{strategy_id} synthetic",
            strategy_version="weighted_strategy_test_v1",
            family=family,
            signal=side,
            p_buy=probabilities[0],
            p_sell=probabilities[1],
            p_hold=probabilities[2],
            directional_confidence=0.8,
            signal_strength=0.8,
            expected_raw_movement=0.002,
            expected_return=0.002,
            expected_return_after_costs=0.0015,
            strength=0.8,
            final_weight=0.125,
            eligible=True,
            data_ready=True,
            data_quality_status=WeightedDataQualityStatus.FULL,
            data_timestamp=TS,
            explanation="Synthetic sizing signal.",
        )
        for strategy_id, family in {
            "S1": WeightedStrategyFamily.BREAKOUT,
            "S8": WeightedStrategyFamily.BREAKOUT,
            "S2": WeightedStrategyFamily.TREND,
            "S3": WeightedStrategyFamily.TREND,
            "S4": WeightedStrategyFamily.MEAN_REVERSION,
            "S7": WeightedStrategyFamily.MEAN_REVERSION,
            "S5": WeightedStrategyFamily.REVERSAL,
            "S6": WeightedStrategyFamily.REVERSAL,
        }.items()
    ]


def market_snapshot() -> WeightedMarketSnapshot:
    return WeightedMarketSnapshot(
        symbol="SPY",
        data_timestamp=TS,
        one_minute_candles=(WeightedCandle(timestamp=TS, open=100.0, high=101.0, low=99.5, close=100.0, volume=20000),),
        bid=100.0,
        ask=100.05,
        explanation="Synthetic quote snapshot.",
    )


def failed_gate_pipeline() -> WeightedVotingGatePipelineResult:
    return WeightedVotingGatePipelineResult(
        permission_granted=False,
        mode=WeightedGateEvaluationMode.MANUAL,
        gate_results=(
            WeightedGateResult(
                gate_id="entry_quality",
                gate_name="Entry Quality",
                status=WeightedGateStatus.FAIL,
                blocks_order=True,
                data_timestamp=TS,
                reason_codes=("weighted_voting.gate.entry_quality_too_low",),
                explanation="Synthetic failed gate.",
            ),
        ),
        reason_codes=("weighted_voting.gate.entry_quality_too_low",),
        explanation="Synthetic failed gate pipeline.",
    )


if __name__ == "__main__":
    unittest.main()
