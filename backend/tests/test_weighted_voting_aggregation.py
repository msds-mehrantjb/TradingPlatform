from __future__ import annotations

import unittest
from collections import defaultdict
from datetime import datetime, timezone

from backend.app.algorithms.weighted_voting.aggregation import aggregate_weighted_signals
from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.models import (
    WeightedDataQualityStatus,
    WeightedSide,
    WeightedStrategyFamily,
    WeightedVotingSignal,
)


TS = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)


class WeightedVotingAggregationTest(unittest.TestCase):
    def test_weighted_sell_cannot_be_reversed_to_buy_by_above_vwap_context(self) -> None:
        decision = aggregate_weighted_signals(
            strategy_signals(p_buy=0.10, p_sell=0.75, p_hold=0.15, side=WeightedSide.SELL, reason_code="weighted_voting.context.price_above_vwap"),
            decision_timestamp=TS,
        )

        self.assertEqual(decision.raw_winner, WeightedSide.SELL.value)
        self.assertEqual(decision.signal, WeightedSide.SELL.value)
        self.assertEqual(decision.proposed_side, WeightedSide.SELL.value)
        self.assertGreater(decision.vote_scores.normalized_sell_score, decision.vote_scores.normalized_buy_score)

    def test_weighted_buy_cannot_be_reversed_to_sell_by_below_vwap_context(self) -> None:
        decision = aggregate_weighted_signals(
            strategy_signals(p_buy=0.75, p_sell=0.10, p_hold=0.15, side=WeightedSide.BUY, reason_code="weighted_voting.context.price_below_vwap"),
            decision_timestamp=TS,
        )

        self.assertEqual(decision.raw_winner, WeightedSide.BUY.value)
        self.assertEqual(decision.signal, WeightedSide.BUY.value)
        self.assertEqual(decision.proposed_side, WeightedSide.BUY.value)
        self.assertGreater(decision.vote_scores.normalized_buy_score, decision.vote_scores.normalized_sell_score)

    def test_score_totals_winner_and_family_contributions_are_deterministic(self) -> None:
        decision = aggregate_weighted_signals(
            strategy_signals(p_buy=0.70, p_sell=0.20, p_hold=0.10, side=WeightedSide.BUY),
            decision_timestamp=TS,
        )

        self.assertAlmostEqual(decision.vote_scores.buy_score + decision.vote_scores.sell_score + decision.vote_scores.hold_score, 1.0, delta=0.0000001)
        self.assertAlmostEqual(
            decision.vote_scores.normalized_buy_score + decision.vote_scores.normalized_sell_score + decision.vote_scores.normalized_hold_score,
            1.0,
            delta=0.0000001,
        )
        self.assertEqual(decision.vote_scores.winning_side, WeightedSide.BUY.value)
        self.assertEqual(decision.vote_scores.winner_score, decision.vote_scores.normalized_buy_score)
        self.assertEqual(decision.vote_scores.second_best_score, decision.vote_scores.normalized_sell_score)
        self.assertEqual(decision.vote_scores.active_strategy_count, 8)
        self.assertEqual(decision.vote_scores.directional_strategy_count, 8)
        self.assertEqual(decision.vote_scores.total_active_weight, decision.vote_scores.active_weight)
        self.assertEqual(decision.vote_scores.total_directional_weight, 1.0)
        self.assertEqual(decision.vote_scores.final_provisional_signal, WeightedSide.BUY.value)
        self.assertGreater(decision.vote_scores.strategy_agreement, 0.0)
        self.assertGreater(decision.vote_scores.family_concentration, 0.0)
        self.assertEqual(decision.vote_scores.effective_weight_coverage, 1.0)
        self.assertAlmostEqual(sum(family["weight"] for family in decision.vote_scores.family_contributions.values()), 1.0, delta=0.0000001)
        self.assertEqual(decision.deterministic_json(), aggregate_weighted_signals(strategy_signals(p_buy=0.70, p_sell=0.20, p_hold=0.10, side=WeightedSide.BUY), decision_timestamp=TS).deterministic_json())

    def test_tie_returns_hold(self) -> None:
        decision = aggregate_weighted_signals(
            strategy_signals(p_buy=0.45, p_sell=0.45, p_hold=0.10, side=WeightedSide.BUY),
            decision_timestamp=TS,
        )

        self.assertEqual(decision.vote_scores.winning_side, WeightedSide.HOLD.value)
        self.assertEqual(decision.raw_winner, WeightedSide.HOLD.value)
        self.assertEqual(decision.signal, WeightedSide.HOLD.value)
        self.assertIn("weighted_voting.hold_or_tie_winner", decision.reason_codes)

    def test_insufficient_edge_returns_hold_without_reversing_weighted_winner(self) -> None:
        decision = aggregate_weighted_signals(
            strategy_signals(p_buy=0.51, p_sell=0.45, p_hold=0.04, side=WeightedSide.BUY),
            decision_timestamp=TS,
        )

        self.assertEqual(decision.raw_winner, WeightedSide.BUY.value)
        self.assertEqual(decision.signal, WeightedSide.HOLD.value)
        self.assertIn("weighted_voting.insufficient_winner_edge", decision.reason_codes)

    def test_insufficient_active_weight_returns_hold(self) -> None:
        decision = aggregate_weighted_signals(
            [unavailable_signal()],
            decision_timestamp=TS,
            config=WeightedVotingConfig(minimum_active_weight=0.5),
        )

        self.assertEqual(decision.signal, WeightedSide.HOLD.value)
        self.assertEqual(decision.vote_scores.active_weight, 0.0)
        self.assertIn("weighted_voting.insufficient_active_weight", decision.reason_codes)

    def test_aggregation_rejects_foreign_algorithm_inputs(self) -> None:
        contaminated_signal = strategy_signals(
            p_buy=0.70,
            p_sell=0.20,
            p_hold=0.10,
            side=WeightedSide.BUY,
            reason_code="voting_ensemble.family_score",
        )
        with self.assertRaises(ValueError):
            aggregate_weighted_signals(contaminated_signal, decision_timestamp=TS)

        foreign_algorithm_signal = strategy_signals(p_buy=0.70, p_sell=0.20, p_hold=0.10, side=WeightedSide.BUY)
        foreign_algorithm_signal[0] = foreign_algorithm_signal[0].model_copy(update={"algorithm_id": "wca"})
        with self.assertRaises(ValueError):
            aggregate_weighted_signals(foreign_algorithm_signal, decision_timestamp=TS)


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


def strategy_signals(
    *,
    p_buy: float,
    p_sell: float,
    p_hold: float,
    side: WeightedSide,
    reason_code: str = "weighted_voting.synthetic",
) -> list[WeightedVotingSignal]:
    family_counts: dict[WeightedStrategyFamily, int] = defaultdict(int)
    return [
        WeightedVotingSignal(
            strategy_id=strategy_id,
            strategy_name=f"{strategy_id} synthetic",
            strategy_version="weighted_strategy_test_v1",
            family=family,
            signal=side,
            p_buy=p_buy,
            p_sell=p_sell,
            p_hold=p_hold,
            directional_confidence=0.7,
            signal_strength=0.7,
            expected_raw_movement=0.001,
            expected_return=0.001,
            expected_return_after_costs=0.0008,
            strength=0.7,
            final_weight=0.125,
            eligible=True,
            data_ready=True,
            required_data_freshness_seconds=300,
            actual_data_freshness_seconds=0,
            data_quality_status=WeightedDataQualityStatus.FULL,
            data_timestamp=TS,
            reason_codes=(reason_code,),
            explanation=f"Synthetic strategy signal {family_counts[family]}.",
        )
        for strategy_id, family in FAMILY_BY_STRATEGY.items()
    ]


def unavailable_signal() -> WeightedVotingSignal:
    return WeightedVotingSignal(
        strategy_id="S1",
        strategy_name="Opening Range Breakout",
        strategy_version="weighted_strategy_test_v1",
        family=WeightedStrategyFamily.BREAKOUT,
        signal=WeightedSide.HOLD,
        p_buy=0.0,
        p_sell=0.0,
        p_hold=1.0,
        strength=0.0,
        final_weight=1.0,
        eligible=False,
        data_ready=False,
        data_quality_status=WeightedDataQualityStatus.UNAVAILABLE,
        data_timestamp=TS,
        explanation="Unavailable signal.",
    )


if __name__ == "__main__":
    unittest.main()
