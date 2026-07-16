from __future__ import annotations

import unittest
from datetime import datetime, timezone

from backend.app.algorithms.weighted_voting.aggregation import aggregate_weighted_signals
from backend.app.algorithms.weighted_voting.decision_gates import (
    WeightedFiveMinuteAlignment,
    WeightedGateEvaluationMode,
    WeightedVotingLocalGateInputs,
    all_gates_pass,
    evaluate_local_decision_gates,
)
from backend.app.algorithms.weighted_voting.models import (
    WeightedCandle,
    WeightedDataQualityStatus,
    WeightedGateStatus,
    WeightedMarketSnapshot,
    WeightedPositionState,
    WeightedSide,
    WeightedStrategyFamily,
    WeightedVotingSignal,
)


TS = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)


class WeightedVotingDecisionGatesTest(unittest.TestCase):
    def test_manual_and_automatic_modes_produce_same_permission_result(self) -> None:
        manual = evaluate_local_decision_gates(valid_gate_inputs(mode=WeightedGateEvaluationMode.MANUAL))
        automatic = evaluate_local_decision_gates(valid_gate_inputs(mode=WeightedGateEvaluationMode.AUTOMATIC))

        self.assertTrue(manual.permission_granted)
        self.assertEqual(manual.permission_granted, automatic.permission_granted)
        self.assertEqual(gate_summary(manual.gate_results), gate_summary(automatic.gate_results))
        self.assertEqual(manual.reason_codes, automatic.reason_codes)

    def test_no_failed_mandatory_gate_is_ignored(self) -> None:
        inputs = valid_gate_inputs(
            five_minute_alignment=WeightedFiveMinuteAlignment.NEGATIVE,
            expected_value_after_costs=-0.01,
            entry_quality=0.2,
            session_allowed=False,
            weighted_daily_loss_percent=9.0,
            capital_available=0.0,
            current_position=WeightedPositionState(symbol="SPY", quantity=10, average_entry_price=100.0, data_timestamp=TS, explanation="Open position."),
        )

        result = evaluate_local_decision_gates(inputs)

        self.assertFalse(result.permission_granted)
        self.assertFalse(all_gates_pass(result.gate_results))
        self.assertIn("weighted_voting.gate.negative_five_minute_alignment", result.reason_codes)
        self.assertIn("weighted_voting.gate.nonpositive_expected_value_after_costs", result.reason_codes)
        self.assertIn("weighted_voting.gate.entry_quality_too_low", result.reason_codes)
        self.assertIn("weighted_voting.gate.session_window_closed", result.reason_codes)
        self.assertIn("weighted_voting.gate.daily_loss_limit_exceeded", result.reason_codes)
        self.assertIn("weighted_voting.gate.insufficient_capital", result.reason_codes)
        self.assertIn("weighted_voting.gate.pyramiding_not_allowed", result.reason_codes)

    def test_neutral_five_minute_alignment_is_informational_not_confirmed(self) -> None:
        result = evaluate_local_decision_gates(valid_gate_inputs(five_minute_alignment=WeightedFiveMinuteAlignment.NEUTRAL))
        gate = gate_by_id(result.gate_results, "five_minute_confirmation")

        self.assertEqual(gate.status, WeightedGateStatus.INFO.value)
        self.assertFalse(gate.blocks_order)
        self.assertNotEqual(gate.status, WeightedGateStatus.PASS.value)
        self.assertTrue(result.permission_granted)

    def test_unacceptable_strategy_data_quality_rejects_with_stable_reason_code(self) -> None:
        inputs = valid_gate_inputs(signals=tuple([strategy_signal("S1", WeightedStrategyFamily.BREAKOUT, data_ready=False)]))

        result = evaluate_local_decision_gates(inputs)

        self.assertFalse(result.permission_granted)
        self.assertIn("weighted_voting.gate.unacceptable_strategy_data_quality", result.reason_codes)

    def test_ml_meta_label_and_triple_barrier_gates_are_absent(self) -> None:
        result = evaluate_local_decision_gates(valid_gate_inputs())
        text = " ".join(
            [gate.gate_id for gate in result.gate_results]
            + [reason for gate in result.gate_results for reason in gate.reason_codes]
        ).lower()

        self.assertNotIn("ml", text)
        self.assertNotIn("meta", text)
        self.assertNotIn("triple", text)
        self.assertNotIn("barrier", text)


def valid_gate_inputs(**overrides) -> WeightedVotingLocalGateInputs:
    signals = overrides.pop("signals", tuple(strategy_signals()))
    decision = overrides.pop("decision", aggregate_weighted_signals(list(signals), decision_timestamp=TS))
    values = {
        "decision": decision,
        "signals": signals,
        "market_snapshot": market_snapshot(),
        "five_minute_alignment": WeightedFiveMinuteAlignment.POSITIVE,
        "expected_value_after_costs": 0.003,
        "spread_cost": 0.0001,
        "slippage_cost": 0.0001,
        "fee_cost": 0.00005,
        "atr_percent": 0.01,
        "entry_quality": 0.82,
        "session_allowed": True,
        "weighted_daily_loss_percent": 0.5,
        "weighted_daily_trade_count": 1,
        "capital_available": 25000.0,
        "current_position": None,
        "mode": WeightedGateEvaluationMode.MANUAL,
    }
    values.update(overrides)
    return WeightedVotingLocalGateInputs(**values)


def strategy_signals() -> list[WeightedVotingSignal]:
    return [
        strategy_signal("S1", WeightedStrategyFamily.BREAKOUT),
        strategy_signal("S8", WeightedStrategyFamily.BREAKOUT),
        strategy_signal("S2", WeightedStrategyFamily.TREND),
        strategy_signal("S3", WeightedStrategyFamily.TREND),
        strategy_signal("S4", WeightedStrategyFamily.MEAN_REVERSION),
        strategy_signal("S7", WeightedStrategyFamily.MEAN_REVERSION),
        strategy_signal("S5", WeightedStrategyFamily.REVERSAL),
        strategy_signal("S6", WeightedStrategyFamily.REVERSAL),
    ]


def strategy_signal(
    strategy_id: str,
    family: WeightedStrategyFamily,
    *,
    data_ready: bool = True,
) -> WeightedVotingSignal:
    return WeightedVotingSignal(
        strategy_id=strategy_id,
        strategy_name=f"{strategy_id} synthetic",
        strategy_version="weighted_strategy_test_v1",
        family=family,
        signal=WeightedSide.BUY if data_ready else WeightedSide.HOLD,
        p_buy=0.82 if data_ready else 0.0,
        p_sell=0.08 if data_ready else 0.0,
        p_hold=0.10 if data_ready else 1.0,
        directional_confidence=0.8 if data_ready else 0.0,
        signal_strength=0.8 if data_ready else 0.0,
        expected_raw_movement=0.002,
        expected_return=0.002,
        expected_return_after_costs=0.0015,
        strength=0.8 if data_ready else 0.0,
        final_weight=0.125,
        eligible=data_ready,
        data_ready=data_ready,
        required_data_freshness_seconds=300,
        actual_data_freshness_seconds=0 if data_ready else None,
        data_quality_status=WeightedDataQualityStatus.FULL if data_ready else WeightedDataQualityStatus.UNAVAILABLE,
        data_timestamp=TS,
        reason_codes=("weighted_voting.synthetic",),
        explanation="Synthetic gate signal.",
    )


def market_snapshot() -> WeightedMarketSnapshot:
    return WeightedMarketSnapshot(
        symbol="SPY",
        data_timestamp=TS,
        one_minute_candles=(
            WeightedCandle(timestamp=TS, open=100.0, high=101.0, low=99.5, close=100.5, volume=50000.0),
        ),
        bid=100.0,
        ask=100.02,
        explanation="Synthetic market snapshot for local gate checks.",
    )


def gate_summary(gates) -> tuple[tuple[str, str, bool, tuple[str, ...]], ...]:
    return tuple((gate.gate_id, gate.status, gate.blocks_order, gate.reason_codes) for gate in gates)


def gate_by_id(gates, gate_id: str):
    return next(gate for gate in gates if gate.gate_id == gate_id)


if __name__ == "__main__":
    unittest.main()
