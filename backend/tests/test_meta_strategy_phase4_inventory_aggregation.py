from __future__ import annotations

import unittest
from datetime import UTC, datetime

from backend.app.algorithms.meta_strategy import (
    ACTIVE_DIRECTIONAL_STRATEGIES,
    ALL_META_STRATEGY_STRATEGIES,
    CONTEXT_STRATEGIES,
    DIRECTIONAL_STRATEGIES,
    REGIME_STRATEGIES,
    SAFETY_STRATEGIES,
    SHADOW_DIRECTIONAL_STRATEGIES,
    MetaStrategyMarketSnapshot,
    StrategyDescriptor,
    generate_deterministic_candidate,
    meta_strategy_strategy_catalog,
    validate_meta_strategy_registry,
)
from backend.app.algorithms.meta_strategy.family_aggregation import (
    FamilyAggregationConfig,
    StrategyContribution,
    aggregate_family_scores,
)
from backend.app.algorithms.meta_strategy.settings import build_meta_strategy_settings


class MetaStrategyPhase4InventoryAggregationTest(unittest.TestCase):
    maxDiff = None

    def test_registry_has_complete_unique_descriptors(self) -> None:
        catalog = meta_strategy_strategy_catalog()
        validation = validate_meta_strategy_registry(catalog)
        descriptors = [StrategyDescriptor.from_registry_entry(entry) for entry in catalog]

        self.assertTrue(validation["valid"], validation)
        self.assertEqual(len({entry.strategy_id for entry in catalog}), len(catalog))
        self.assertEqual(len({descriptor.strategy_id for descriptor in descriptors}), len(descriptors))
        for descriptor in descriptors:
            with self.subTest(strategy=descriptor.strategy_id):
                self.assertTrue(descriptor.version)
                self.assertTrue(descriptor.family)
                self.assertTrue(descriptor.correlation_group)
                self.assertIn(descriptor.mode, {"ACTIVE", "SHADOW", "DISABLED"})
                self.assertGreater(len(descriptor.required_inputs), 0)
                self.assertGreater(len(descriptor.supported_sessions), 0)
                self.assertGreater(len(descriptor.supported_regimes), 0)
                self.assertTrue(descriptor.settings_type)
                self.assertTrue(descriptor.output_schema)

    def test_directional_inventory_separates_active_and_shadow_strategies(self) -> None:
        active_ids = tuple(entry.strategy_id for entry in ACTIVE_DIRECTIONAL_STRATEGIES)
        shadow_ids = tuple(entry.strategy_id for entry in SHADOW_DIRECTIONAL_STRATEGIES)

        self.assertEqual(
            active_ids,
            (
                "multi_timeframe_trend_alignment",
                "first_pullback_after_open",
                "opening_range_breakout",
                "vwap_trend_continuation",
                "volatility_breakout",
                "failed_breakout_reversal",
                "bollinger_atr_reversion",
                "vwap_mean_reversion",
            ),
        )
        self.assertEqual(
            shadow_ids,
            (
                "liquidity_sweep_reversal",
                "gap_continuation",
                "gap_fade",
                "economic_event_reaction",
            ),
        )
        self.assertNotIn("gap_continuation_gap_fade", {entry.strategy_id for entry in DIRECTIONAL_STRATEGIES})
        self.assertTrue(all(entry.mode == "ACTIVE" for entry in ACTIVE_DIRECTIONAL_STRATEGIES))
        self.assertTrue(all(entry.mode == "SHADOW" for entry in SHADOW_DIRECTIONAL_STRATEGIES))

    def test_context_regime_and_safety_inventory_are_not_duplicate_directional_voters(self) -> None:
        self.assertEqual({entry.role for entry in CONTEXT_STRATEGIES}, {"CONTEXT"})
        self.assertEqual({tuple(entry.supported_directions) for entry in CONTEXT_STRATEGIES}, {("HOLD",)})
        self.assertNotIn("ensemble_strategy_voting", {entry.strategy_id for entry in ALL_META_STRATEGY_STRATEGIES})
        self.assertEqual(tuple(entry.strategy_id for entry in REGIME_STRATEGIES), ("adx_atr_regime_classifier",))
        self.assertEqual({tuple(entry.supported_directions) for entry in REGIME_STRATEGIES}, {("HOLD",)})
        self.assertEqual(
            {entry.strategy_id for entry in SAFETY_STRATEGIES},
            {
                "cash_avoid_trading_filter",
                "stale_market_data_filter",
                "missing_critical_data_filter",
                "excessive_spread_filter",
                "insufficient_liquidity_filter",
                "extreme_volatility_filter",
                "economic_event_blackout_filter",
                "unsupported_session_filter",
                "operational_health_filter",
                "halt_luld_filter",
                "daily_loss_limit_filter",
                "trade_count_limit_filter",
                "duplicate_order_protection_filter",
                "existing_position_policy_filter",
                "local_risk_budget_filter",
            },
        )

    def test_shadow_strategies_are_evaluated_but_cannot_affect_orders(self) -> None:
        snapshot = snapshot_fixture()
        settings = build_meta_strategy_settings(
            directional_strategies={
                **{entry.strategy_id: {"enabled": False} for entry in ACTIVE_DIRECTIONAL_STRATEGIES},
                **{entry.strategy_id: {"enabled": True} for entry in SHADOW_DIRECTIONAL_STRATEGIES},
            },
            candidate_aggregation={
                "minimum_active_strategies": 1,
                "minimum_independent_families": 1,
                "maximum_abstention_rate": 1.0,
            },
        )
        result = generate_deterministic_candidate(snapshot, settings=settings)

        self.assertIn("gap_continuation", result.evidence["directionalOutputs"])
        self.assertIn("gap_fade", result.evidence["directionalOutputs"])
        self.assertEqual(result.evidence["directionalOutputs"]["gap_continuation"]["signal"], "BUY")
        self.assertEqual(result.direction, "HOLD")
        self.assertIn("meta_strategy.aggregation.no_active_directional_strategies", result.reason_codes)

    def test_correlation_groups_cap_related_directional_evidence(self) -> None:
        config = FamilyAggregationConfig(
            strategy_contribution_cap=1.0,
            family_contribution_cap=1.0,
            correlation_group_cap=0.40,
            minimum_active_strategies=1,
            minimum_independent_families=1,
            maximum_abstention_rate=1.0,
        )
        aggregation = aggregate_family_scores(
            (
                StrategyContribution("opening_range_breakout", "BREAKOUT", "BUY", 0.9, correlation_key="breakout"),
                StrategyContribution("volatility_breakout", "BREAKOUT", "BUY", 0.9, correlation_key="breakout"),
            ),
            config=config,
        )

        self.assertEqual(aggregation.signal, "BUY")
        self.assertLessEqual(aggregation.buy_score, 0.40)
        self.assertTrue(aggregation.family_scores[0].capped)

    def test_context_evidence_cannot_become_an_order_by_itself(self) -> None:
        aggregation = aggregate_family_scores(
            (
                StrategyContribution("relative_strength_qqq_iwm", "MARKET_CONTEXT", "BUY", 1.0),
                StrategyContribution("volume_confirmation_context", "MARKET_CONTEXT", "BUY", 1.0),
            ),
            config=FamilyAggregationConfig(minimum_active_strategies=1, minimum_independent_families=1, maximum_abstention_rate=1.0),
        )

        self.assertEqual(aggregation.signal, "HOLD")
        self.assertFalse(aggregation.eligible)
        self.assertIn("meta_strategy.aggregation.context_only_not_orderable", aggregation.reason_codes)


def snapshot_fixture() -> MetaStrategyMarketSnapshot:
    timestamp = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)
    candles = tuple(
        {
            "timestamp": datetime(2026, 1, 5, 15, minute, tzinfo=UTC),
            "open": 100.0 + minute * 0.01,
            "high": 100.2 + minute * 0.01,
            "low": 99.9 + minute * 0.01,
            "close": 100.1 + minute * 0.01,
            "volume": 10_000,
        }
        for minute in range(45)
    )
    return MetaStrategyMarketSnapshot(
        algorithm_id="meta_strategy",
        algorithm_version="meta_strategy_algorithm_v1",
        configuration_version="meta_strategy_config_v1",
        strategy_catalog_version="meta_strategy_strategy_catalog_v1",
        decision_id="phase4-decision",
        snapshot_id="phase4-snapshot",
        timestamp=timestamp,
        symbol="SPY",
        last_price=101.0,
        bid_price=100.99,
        ask_price=101.01,
        spread_bps=1.98,
        volume=100_000,
        source_cutoff_timestamp=timestamp,
        point_in_time=True,
        candles={"1m": candles},
        vwap=100.5,
        moving_averages={"1m": {"ema20": 100.7, "ema50": 100.2}},
        atr={"1m": 1.0},
        adx={"1m": 30.0},
        rsi={"1m": 55.0},
        bollinger_bands={"1m": {"upper": 100.0, "middle": 99.0, "lower": 98.0}},
        relative_volume={"1m": 1.5},
        spread={"basisPoints": 1.98},
        liquidity={"level": "good", "score": 1.0},
        session_phase="OPENING",
        gap_state={"state": "gap_up", "gapPercent": 1.0},
        qqq_iwm_context={"spyVsQqq": 1.02, "spyVsIwm": 1.01},
        breadth={"averageReturn": 0.001, "componentCount": 2},
        economic_event_state={"state": "none", "active": False},
        features={
            "openingRangeHigh": 100.0,
            "openingRangeLow": 98.0,
            "bollingerWidthPercentile": 0.9,
            "gapTradeType": "continuation",
            "marketDailyPnl": -10.0,
            "dailyLossLimit": -1_000.0,
            "tradeCount": 1,
            "tradeCountLimit": 5,
            "duplicateOrderState": {"duplicate": False},
            "existingPositionState": {"policyAllowsEntry": True},
            "localRiskBudget": {"remainingRiskDollars": 500.0},
            "haltLuldState": {"halted": False},
            "operationalHealth": {"tradingAllowed": True},
            "criticalDataReady": True,
        },
    )


if __name__ == "__main__":
    unittest.main()
