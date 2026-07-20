from __future__ import annotations

import unittest
from datetime import UTC, datetime

from backend.app.algorithms.meta_strategy import (
    DIRECTIONAL_STRATEGIES,
    FamilyAggregationConfig,
    StrategyContribution,
    aggregate_family_scores,
)
from backend.app.algorithms.meta_strategy.strategies.base import SnapshotEvaluationResult


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)
LOOSE_GATES = FamilyAggregationConfig(minimum_active_strategies=1, minimum_independent_families=1, maximum_abstention_rate=1.0)


class MetaStrategyStep13FamilyAggregationTest(unittest.TestCase):
    maxDiff = None

    def test_strategy_contribution_cap_is_enforced(self) -> None:
        result = aggregate_family_scores(
            (
                contribution("trend-a", "TREND", "BUY", confidence=1.0, correlation_key="trend-a"),
                contribution("breakout-a", "BREAKOUT", "SELL", confidence=0.1, correlation_key="breakout-a"),
            ),
            config=FamilyAggregationConfig(strategy_contribution_cap=0.2, family_contribution_cap=1.0, correlation_group_cap=1.0, minimum_active_strategies=1, minimum_independent_families=1, maximum_abstention_rate=1.0),
        )

        trend = family(result, "TREND")
        self.assertEqual(trend.buy_score, 0.2)
        self.assertEqual(result.signal, "BUY")

    def test_family_contribution_cap_prevents_duplicate_family_domination(self) -> None:
        result = aggregate_family_scores(
            (
                contribution("trend-a", "TREND", "BUY", confidence=1.0, correlation_key="trend-a"),
                contribution("trend-b", "TREND", "BUY", confidence=1.0, correlation_key="trend-b"),
                contribution("trend-c", "TREND", "BUY", confidence=1.0, correlation_key="trend-c"),
                contribution("breakout-a", "BREAKOUT", "SELL", confidence=1.0, correlation_key="breakout-a"),
            ),
            config=FamilyAggregationConfig(strategy_contribution_cap=0.35, family_contribution_cap=0.45, correlation_group_cap=1.0, minimum_active_strategies=1, minimum_independent_families=1, maximum_abstention_rate=1.0),
        )

        trend = family(result, "TREND")
        self.assertEqual(trend.buy_score, 0.45)
        self.assertTrue(trend.capped)
        self.assertLessEqual(trend.buy_score, 0.45)

    def test_alias_deduplication_keeps_one_influence(self) -> None:
        result = aggregate_family_scores(
            (
                contribution("failed_breakout_reversal", "REVERSAL", "BUY", confidence=0.6, canonical="failed_breakout_reversal"),
                contribution("Failed Breakout Strategy", "REVERSAL", "BUY", confidence=1.0, canonical="failed_breakout_reversal"),
                contribution("trend-a", "TREND", "BUY", confidence=0.5),
            ),
            config=LOOSE_GATES,
        )

        reversal = family(result, "REVERSAL")
        self.assertEqual(result.active_strategy_count, 2)
        self.assertEqual(reversal.active_strategy_count, 1)
        self.assertIn("meta_strategy.aggregation.alias_deduplicated", result.reason_codes)

    def test_correlation_controls_cap_related_strategies(self) -> None:
        result = aggregate_family_scores(
            (
                contribution("trend-a", "TREND", "BUY", confidence=1.0, correlation_key="ma_trend"),
                contribution("trend-b", "TREND", "BUY", confidence=1.0, correlation_key="ma_trend"),
                contribution("breakout-a", "BREAKOUT", "BUY", confidence=0.5, correlation_key="breakout-a"),
            ),
            config=FamilyAggregationConfig(strategy_contribution_cap=0.35, family_contribution_cap=1.0, correlation_group_cap=0.3, minimum_active_strategies=1, minimum_independent_families=1, maximum_abstention_rate=1.0),
        )

        self.assertEqual(family(result, "TREND").buy_score, 0.3)

    def test_minimum_active_strategy_gate(self) -> None:
        result = aggregate_family_scores(
            (
                contribution("trend-a", "TREND", "BUY", confidence=0.8),
                contribution("breakout-a", "BREAKOUT", "HOLD", confidence=0.0),
            ),
            config=FamilyAggregationConfig(minimum_active_strategies=2, minimum_independent_families=1, maximum_abstention_rate=1.0),
        )

        self.assertEqual(result.signal, "HOLD")
        self.assertIn("meta_strategy.aggregation.minimum_active_strategies", result.reason_codes)

    def test_minimum_independent_family_gate(self) -> None:
        result = aggregate_family_scores(
            (
                contribution("trend-a", "TREND", "BUY", confidence=0.8, correlation_key="trend-a"),
                contribution("trend-b", "TREND", "BUY", confidence=0.7, correlation_key="trend-b"),
            ),
            config=FamilyAggregationConfig(minimum_active_strategies=2, minimum_independent_families=2, maximum_abstention_rate=1.0),
        )

        self.assertEqual(result.signal, "HOLD")
        self.assertIn("meta_strategy.aggregation.minimum_independent_families", result.reason_codes)

    def test_buy_sell_tie_resolves_to_hold(self) -> None:
        result = aggregate_family_scores(
            (
                contribution("trend-a", "TREND", "BUY", confidence=0.6),
                contribution("breakout-a", "BREAKOUT", "SELL", confidence=0.6),
            ),
            config=LOOSE_GATES,
        )

        self.assertEqual(result.signal, "HOLD")
        self.assertFalse(result.eligible)
        self.assertIn("meta_strategy.aggregation.buy_sell_tie", result.reason_codes)

    def test_buy_sell_conflict_without_clear_edge_resolves_to_hold(self) -> None:
        result = aggregate_family_scores(
            (
                contribution("trend-a", "TREND", "BUY", confidence=0.61),
                contribution("breakout-a", "BREAKOUT", "SELL", confidence=0.6),
            ),
            config=FamilyAggregationConfig(
                strategy_contribution_cap=1.0,
                family_contribution_cap=1.0,
                correlation_group_cap=1.0,
                minimum_active_strategies=1,
                minimum_independent_families=1,
                maximum_abstention_rate=1.0,
                minimum_conflict_edge=0.05,
            ),
        )

        self.assertEqual(result.signal, "HOLD")
        self.assertIn("meta_strategy.aggregation.buy_sell_conflict", result.reason_codes)

    def test_abstention_gate_resolves_to_hold(self) -> None:
        result = aggregate_family_scores(
            (
                contribution("trend-a", "TREND", "BUY", confidence=0.8),
                contribution("breakout-a", "BREAKOUT", "BUY", confidence=0.7),
                contribution("rev-a", "REVERSAL", "HOLD", confidence=0.0),
                contribution("rev-b", "REVERSAL", "HOLD", confidence=0.0),
                contribution("mean-a", "MEAN_REVERSION", "HOLD", confidence=0.0),
            ),
            config=FamilyAggregationConfig(minimum_active_strategies=1, minimum_independent_families=1, maximum_abstention_rate=0.5),
        )

        self.assertEqual(result.signal, "HOLD")
        self.assertEqual(result.abstention_rate, 0.6)
        self.assertIn("meta_strategy.aggregation.maximum_abstention_rate", result.reason_codes)

    def test_hold_fallback_when_every_strategy_abstains(self) -> None:
        result = aggregate_family_scores((contribution("trend-a", "TREND", "HOLD", confidence=0.0),), config=LOOSE_GATES)

        self.assertEqual(result.signal, "HOLD")
        self.assertEqual(result.active_strategy_count, 0)
        self.assertIn("meta_strategy.aggregation.no_active_directional_strategies", result.reason_codes)

    def test_snapshot_evaluations_can_be_aggregated_and_emitted_as_candidate_contract(self) -> None:
        evaluations = (
            SnapshotEvaluationResult("multi_timeframe_trend_alignment", "BUY", 0.8, True, family="TREND", evidence={"correlationKey": "trend-a"}),
            SnapshotEvaluationResult("opening_range_breakout", "BUY", 0.7, True, family="BREAKOUT", evidence={"correlationKey": "breakout-a"}),
        )
        result = aggregate_family_scores(evaluations, registry_entries=DIRECTIONAL_STRATEGIES, config=LOOSE_GATES)
        candidate = result.to_deterministic_candidate(
            algorithm_version="meta_strategy_algorithm_v1",
            configuration_version="meta_strategy_config_v1",
            strategy_catalog_version="meta_strategy_strategy_catalog_v1",
            decision_id="decision-aggregation-1",
            snapshot_id="snapshot-aggregation-1",
            timestamp=NOW,
        )

        self.assertEqual(result.signal, "BUY")
        self.assertEqual(candidate.algorithm_id, "meta_strategy")
        self.assertEqual(candidate.signal, "BUY")
        self.assertTrue(candidate.eligible)
        self.assertEqual(len(candidate.family_scores), 2)


def contribution(
    strategy_id: str,
    family_name: str,
    signal: str,
    *,
    confidence: float,
    canonical: str | None = None,
    correlation_key: str | None = None,
) -> StrategyContribution:
    return StrategyContribution(
        strategy_id=strategy_id,
        family=family_name,
        signal=signal,
        confidence=confidence,
        canonical_influence_id=canonical,
        correlation_key=correlation_key or strategy_id,
    )


def family(result, family_name: str):
    return next(score for score in result.family_scores if score.family == family_name)


if __name__ == "__main__":
    unittest.main()
