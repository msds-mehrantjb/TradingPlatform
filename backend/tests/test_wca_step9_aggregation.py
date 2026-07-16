from __future__ import annotations

import unittest

from backend.app.algorithms.wca.aggregation import WcaAggregationConfig, aggregate_wca
from backend.app.algorithms.wca.contracts import WcaEvaluationStatus, WcaGateStatus, WcaLocalGateResult, WcaSide, WcaStrategyEvaluation


class WcaStep9AggregationTest(unittest.TestCase):
    def test_inactive_strategies_do_not_dilute_active_confidence(self) -> None:
        result = aggregate_wca(
            (
                evaluation("C1", WcaSide.BUY, 0.80, 0.10),
                evaluation("C2", WcaSide.HOLD, 0.00, 0.90, status=WcaEvaluationStatus.NOT_APPLICABLE),
            ),
            config=WcaAggregationConfig(minimum_active_strategies=1, minimum_winner_edge=0.01, maximum_family_concentration=1.0),
        )

        self.assertEqual(result.active_strategy_count, 1)
        self.assertEqual(result.active_weight, 0.10)
        self.assertEqual(result.normalized_net_score, 0.80)
        self.assertEqual(result.pre_gate_decision, WcaSide.BUY.value)
        self.assertEqual(result.signal, WcaSide.BUY.value)
        self.assertEqual(len(result.exclusions), 1)
        self.assertIn("wca.aggregation.excluded.not_active", result.exclusions[0].reason_codes)

    def test_family_caps_are_applied_before_final_score(self) -> None:
        result = aggregate_wca(
            (
                evaluation("C1", WcaSide.BUY, 0.90, 0.30),
                evaluation("C2", WcaSide.BUY, 0.90, 0.30),
                evaluation("C3", WcaSide.BUY, 0.90, 0.30),
                evaluation("C9", WcaSide.SELL, 0.40, 0.10),
            ),
            config=WcaAggregationConfig(
                minimum_active_strategies=1,
                minimum_normalized_score=0.10,
                minimum_directional_agreement=0.40,
                minimum_average_confidence=0.30,
                minimum_winner_edge=0.01,
                maximum_family_concentration=0.50,
            ),
        )

        trend = next(row for row in result.family_contributions if row.family == "trend")
        self.assertLessEqual(trend.directional_weight / result.active_weight, 0.50 + 1e-8)
        self.assertAlmostEqual(result.buy_score, sum(row.score_contribution for row in result.strategy_contributions if row.signal == WcaSide.BUY.value), places=4)

    def test_ties_and_near_ties_produce_hold(self) -> None:
        result = aggregate_wca(
            (
                evaluation("C1", WcaSide.BUY, 0.70, 0.10),
                evaluation("C9", WcaSide.SELL, 0.69, 0.10),
            ),
            config=WcaAggregationConfig(minimum_active_strategies=1, minimum_normalized_score=0.01, minimum_winner_edge=0.05, maximum_family_concentration=1.0),
        )

        self.assertEqual(result.pre_gate_decision, WcaSide.HOLD.value)
        self.assertEqual(result.signal, WcaSide.HOLD.value)
        self.assertLess(result.winner_edge, 0.05)

    def test_invalid_data_cannot_create_directional_decision(self) -> None:
        result = aggregate_wca(
            (
                evaluation("C1", WcaSide.BUY, 0.95, 0.20, status=WcaEvaluationStatus.INVALID),
                evaluation("C2", WcaSide.BUY, 0.95, 0.20, data_quality_status=WcaEvaluationStatus.INVALID),
            ),
            config=WcaAggregationConfig(minimum_active_strategies=1, maximum_family_concentration=1.0),
        )

        self.assertEqual(result.active_strategy_count, 0)
        self.assertEqual(result.active_weight, 0)
        self.assertEqual(result.signal, WcaSide.HOLD.value)
        self.assertEqual(len(result.exclusions), 2)

    def test_failed_local_gate_turns_pre_gate_direction_to_post_gate_hold(self) -> None:
        result = aggregate_wca(
            (
                evaluation("C1", WcaSide.BUY, 0.90, 0.10),
                evaluation("C7", WcaSide.BUY, 0.85, 0.10),
                evaluation("C9", WcaSide.SELL, 0.20, 0.05),
            ),
            local_gates=(
                WcaLocalGateResult(
                    gate_id="expectancy",
                    status=WcaGateStatus.FAIL,
                    blocks_entry=True,
                    reason_codes=("wca.local_gate.expectancy.fail",),
                ),
            ),
            config=WcaAggregationConfig(minimum_active_strategies=1, minimum_winner_edge=0.01, maximum_family_concentration=1.0),
        )

        self.assertEqual(result.pre_gate_decision, WcaSide.BUY.value)
        self.assertEqual(result.post_local_gate_decision, WcaSide.HOLD.value)
        self.assertEqual(result.signal, WcaSide.HOLD.value)
        self.assertIn("wca.local_gate.expectancy.fail", result.reason_codes)

    def test_scores_are_reproducible_from_logged_contributions(self) -> None:
        result = aggregate_wca(
            (
                evaluation("C1", WcaSide.BUY, 0.80, 0.10),
                evaluation("C7", WcaSide.BUY, 0.70, 0.10),
                evaluation("C9", WcaSide.SELL, 0.60, 0.10),
                evaluation("C11", WcaSide.HOLD, 0.10, 0.10),
            ),
            config=WcaAggregationConfig(minimum_active_strategies=1, maximum_family_concentration=1.0),
        )

        buy_score = round(sum(row.score_contribution for row in result.strategy_contributions if row.signal == WcaSide.BUY.value), 4)
        sell_score = round(abs(sum(row.score_contribution for row in result.strategy_contributions if row.signal == WcaSide.SELL.value)), 4)
        active_weight = round(sum(row.adjusted_weight for row in result.strategy_contributions), 4)

        self.assertEqual(result.buy_score, buy_score)
        self.assertEqual(result.sell_score, sell_score)
        self.assertEqual(result.active_weight, active_weight)
        self.assertEqual(result.normalized_net_score, round((buy_score - sell_score) / active_weight, 4))
        self.assertEqual(result.exclusions[0].reason_codes[0], "wca.aggregation.excluded.deliberate_hold")


def evaluation(
    strategy_id: str,
    signal: WcaSide,
    confidence: float,
    weight: float,
    *,
    status: WcaEvaluationStatus = WcaEvaluationStatus.ACTIVE,
    data_quality_status: WcaEvaluationStatus = WcaEvaluationStatus.ACTIVE,
) -> WcaStrategyEvaluation:
    direction = 1 if signal == WcaSide.BUY else -1 if signal == WcaSide.SELL else 0
    return WcaStrategyEvaluation(
        strategy_id=strategy_id,
        strategy_version=f"wca_{strategy_id.lower()}_test_v1",
        name=strategy_id,
        status=status,
        signal=signal,
        confidence=confidence,
        raw_confidence=confidence,
        calibrated_confidence=confidence,
        direction=signal,
        applicability=status,
        evidence_strength=confidence if status == WcaEvaluationStatus.ACTIVE else 0,
        data_quality_status=data_quality_status,
        base_weight=weight,
        effective_weight=weight,
        contribution=round(direction * weight * confidence, 4),
        reason_codes=(f"wca.strategy.{strategy_id.lower()}",),
    )


if __name__ == "__main__":
    unittest.main()
