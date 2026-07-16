from __future__ import annotations

import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from backend.app.algorithms.weighted_voting.dynamic_settings import (
    DynamicSettingsResolver,
    default_dynamic_envelope,
    default_hard_limits,
    default_weighted_settings,
    migrate_legacy_weighted_settings,
    resolve_dynamic_settings_for_condition,
    resolve_effective_settings,
)
from backend.app.algorithms.weighted_voting.models import (
    WeightedDynamicEnvelope,
    WeightedEffectiveSettings,
    WeightedEventRiskLevel,
    WeightedHardLimits,
    WeightedLiquidityLevel,
    WeightedMarketCondition,
    WeightedMarketQuality,
    WeightedRangeCondition,
    WeightedSessionPhase,
    WeightedTrendDirection,
    WeightedVolatilityLevel,
)
from backend.app.algorithms.weighted_voting.persistence import load_effective_settings, persist_effective_settings


TS = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)


class WeightedVotingSettingsTest(unittest.TestCase):
    def test_defaults_remain_available_and_visible_in_effective_settings(self) -> None:
        defaults = default_weighted_settings(timestamp=TS)
        effective = resolve_effective_settings(default_settings=defaults, timestamp=TS)

        self.assertEqual(effective.default_settings, defaults)
        self.assertEqual(effective.base_risk_per_trade_percent, defaults.base_risk_per_trade_percent)
        self.assertEqual(effective.minimum_score, defaults.minimum_score)
        self.assertEqual(effective.target_r, defaults.target_r)
        self.assertIn("weighted_voting.settings.defaults_visible", effective.reason_codes)

    def test_dynamic_settings_are_clamped_by_envelope_and_hard_limits(self) -> None:
        defaults = default_weighted_settings(timestamp=TS)
        envelope = WeightedDynamicEnvelope(
            settings_timestamp=TS,
            enabled=True,
            base_risk_per_trade_percent_delta=10.0,
            maximum_shares_delta=10000,
            minimum_score_delta=0.5,
            pyramiding_may_enable=True,
        )
        limits = WeightedHardLimits(
            settings_timestamp=TS,
            maximum_base_risk_per_trade_percent=1.0,
            maximum_shares=1000,
            minimum_score_floor=0.5,
            minimum_score_ceiling=0.8,
            pyramiding_allowed=False,
        )

        effective = resolve_effective_settings(
            default_settings=defaults,
            dynamic_envelope=envelope,
            hard_limits=limits,
            dynamic_values={
                "base_risk_per_trade_percent": 5.0,
                "maximum_shares": 5000,
                "minimum_score": 0.95,
                "pyramiding_enabled": True,
            },
            timestamp=TS,
        )

        self.assertEqual(effective.base_risk_per_trade_percent, 1.0)
        self.assertEqual(effective.maximum_shares, 1000)
        self.assertEqual(effective.minimum_score, 0.8)
        self.assertFalse(effective.pyramiding_enabled)
        self.assertIn("weighted_voting.settings.base_risk_per_trade_percent.clamped", effective.reason_codes)
        self.assertIn("weighted_voting.settings.pyramiding.clamped", effective.reason_codes)

    def test_effective_settings_model_rejects_values_past_hard_limits(self) -> None:
        defaults = default_weighted_settings(timestamp=TS)
        envelope = default_dynamic_envelope(timestamp=TS)
        limits = WeightedHardLimits(settings_timestamp=TS, maximum_order_allocation_percent=5.0)

        with self.assertRaises(ValidationError):
            WeightedEffectiveSettings(
                settings_version="weighted_effective_settings_invalid",
                settings_timestamp=TS,
                default_settings=defaults,
                dynamic_envelope=envelope,
                hard_limits=limits,
                order_allocation_percent=10.0,
                configuration_version="weighted_config_test",
                configuration_hash="hash",
                explanation="Invalid effective settings.",
            )

    def test_existing_frontend_shaped_settings_can_be_migrated(self) -> None:
        migrated = migrate_legacy_weighted_settings(
            {
                "baseRiskPercent": 0.75,
                "orderAllocationPercent": 12.5,
                "dailyAllocationPercent": 35,
                "maxPositionPercent": 15,
                "maxAllowedShares": 250,
                "maxTradesPerDay": 7,
                "maxDailyLossPercent": 2.5,
                "maxParticipationPercent": 1.5,
                "minimumBuyScore": 0.62,
                "minimumSignalEdge": 0.14,
                "minimumActiveStrategies": 3,
                "minimumOneMinuteVolume": 75000,
                "atrStopMultiplier": 1.8,
                "minimumStopDistancePercent": 0.2,
                "takeProfitR": 2.4,
                "pyramidingEnabled": True,
            },
            timestamp=TS,
        )

        self.assertEqual(migrated.settings_version, "weighted_default_settings_migrated_v1")
        self.assertEqual(migrated.settings_timestamp, TS)
        self.assertEqual(migrated.base_risk_per_trade_percent, 0.75)
        self.assertEqual(migrated.maximum_shares, 250)
        self.assertEqual(migrated.minimum_liquidity_volume, 75000)
        self.assertTrue(migrated.pyramiding_enabled)

    def test_every_settings_layer_has_version_and_timestamp_and_can_persist_backend_authoritatively(self) -> None:
        defaults = default_weighted_settings(timestamp=TS)
        envelope = default_dynamic_envelope(timestamp=TS)
        limits = default_hard_limits(timestamp=TS)
        effective = resolve_effective_settings(default_settings=defaults, dynamic_envelope=envelope, hard_limits=limits, timestamp=TS)
        store = MemoryStore()

        persist_effective_settings(store, effective)
        loaded = load_effective_settings(store)

        for layer in (defaults, envelope, limits, effective):
            self.assertTrue(any(name.endswith("version") for name in type(layer).model_fields))
            self.assertEqual(layer.settings_timestamp, TS)
        self.assertEqual(loaded, effective)
        self.assertIn("weighted_voting.settings.effective", store.snapshots)

    def test_condition_resolver_is_reproducible_from_defaults_and_condition_inputs(self) -> None:
        defaults = default_weighted_settings(timestamp=TS)
        envelope = generous_envelope()
        limits = default_hard_limits(timestamp=TS)
        condition = clean_confirmed_condition()

        left = resolve_dynamic_settings_for_condition(
            default_settings=defaults,
            dynamic_envelope=envelope,
            hard_limits=limits,
            condition=condition,
            timestamp=TS,
        )
        right = DynamicSettingsResolver(default_settings=defaults, dynamic_envelope=envelope, hard_limits=limits).resolve(condition, timestamp=TS)

        self.assertEqual(left.deterministic_json(), right.deterministic_json())
        self.assertGreater(left.base_risk_per_trade_percent, defaults.base_risk_per_trade_percent)
        self.assertTrue(all(adjustment.reason_codes for adjustment in left.dynamic_adjustments))

    def test_condition_resolver_keeps_values_inside_envelope_hard_limits_and_global_allowances(self) -> None:
        defaults = default_weighted_settings(timestamp=TS)
        envelope = generous_envelope()
        limits = WeightedHardLimits(settings_timestamp=TS, maximum_base_risk_per_trade_percent=0.52, maximum_order_allocation_percent=25.0)

        effective = resolve_dynamic_settings_for_condition(
            default_settings=defaults,
            dynamic_envelope=envelope,
            hard_limits=limits,
            condition=clean_confirmed_condition(),
            global_allowances={"order_allocation_percent": 8.0},
            timestamp=TS,
        )

        self.assertEqual(effective.base_risk_per_trade_percent, 0.52)
        self.assertEqual(effective.order_allocation_percent, 8.0)
        self.assertLessEqual(effective.maximum_position_percent, limits.maximum_position_percent)
        self.assertIn("weighted_voting.dynamic_settings.condition_resolved", effective.reason_codes)
        order_adjustment = next(adjustment for adjustment in effective.dynamic_adjustments if adjustment.setting_name == "order_allocation_percent")
        self.assertIn("weighted_voting.dynamic_settings.order_allocation_percent.global_allowance_clamped", order_adjustment.reason_codes)

    def test_extreme_poor_or_blocked_conditions_zero_new_entry_risk_when_envelope_allows(self) -> None:
        defaults = default_weighted_settings(timestamp=TS)
        envelope = generous_envelope()

        effective = resolve_dynamic_settings_for_condition(
            default_settings=defaults,
            dynamic_envelope=envelope,
            hard_limits=default_hard_limits(timestamp=TS),
            condition=blocked_condition(),
            timestamp=TS,
        )

        self.assertEqual(effective.base_risk_per_trade_percent, 0.0)
        self.assertEqual(effective.order_allocation_percent, 0.0)
        self.assertEqual(effective.maximum_position_percent, 0.0)
        self.assertEqual(effective.maximum_participation_rate, 0.0)
        self.assertIn("weighted_voting.dynamic_settings.extreme_volatility_zero_new_entry_risk", effective.reason_codes)
        self.assertIn("weighted_voting.dynamic_settings.poor_liquidity_zero_new_entry_risk", effective.reason_codes)
        self.assertIn("weighted_voting.dynamic_settings.blocked_event_zero_new_entry_risk", effective.reason_codes)

    def test_risk_increase_requires_hysteresis_confirmation(self) -> None:
        defaults = default_weighted_settings(timestamp=TS)
        envelope = generous_envelope()
        unconfirmed = clean_condition(pending_confirmation_count=2, reason_codes=("weighted_voting.market_condition.hysteresis_hold",))
        confirmed = clean_confirmed_condition()

        held = resolve_dynamic_settings_for_condition(default_settings=defaults, dynamic_envelope=envelope, hard_limits=default_hard_limits(timestamp=TS), condition=unconfirmed, timestamp=TS)
        increased = resolve_dynamic_settings_for_condition(default_settings=defaults, dynamic_envelope=envelope, hard_limits=default_hard_limits(timestamp=TS), condition=confirmed, timestamp=TS)

        self.assertLessEqual(held.base_risk_per_trade_percent, defaults.base_risk_per_trade_percent)
        self.assertGreater(increased.base_risk_per_trade_percent, defaults.base_risk_per_trade_percent)
        self.assertIn("weighted_voting.dynamic_settings.hysteresis_blocks_exposure_increase", held.reason_codes)


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot


def generous_envelope() -> WeightedDynamicEnvelope:
    return WeightedDynamicEnvelope(
        settings_timestamp=TS,
        enabled=True,
        base_risk_per_trade_percent_delta=1.0,
        order_allocation_percent_delta=20.0,
        daily_allocation_percent_delta=30.0,
        maximum_position_percent_delta=20.0,
        maximum_shares_delta=1000000,
        maximum_trades_delta=10,
        maximum_daily_loss_percent_delta=3.0,
        maximum_participation_rate_delta=0.02,
        minimum_score_delta=0.2,
        minimum_edge_delta=0.1,
        minimum_active_strategies_delta=2,
        minimum_directional_strategies_delta=2,
        maximum_spread_percent_delta=0.001,
        minimum_liquidity_volume_delta=10000.0,
        atr_stop_multiplier_delta=1.0,
        minimum_stop_distance_percent_delta=0.001,
        target_r_delta=1.0,
        entry_buffer_percent_delta=0.001,
        break_even_trigger_r_delta=0.5,
        trailing_stop_atr_multiplier_delta=1.0,
        time_stop_minutes_delta=60,
        session_cutoff_minutes_delta=15,
    )


def clean_confirmed_condition() -> WeightedMarketCondition:
    return clean_condition(
        pending_confirmation_count=0,
        reason_codes=("weighted_voting.market_condition.hysteresis_confirmed",),
    )


def clean_condition(*, pending_confirmation_count: int, reason_codes: tuple[str, ...]) -> WeightedMarketCondition:
    return WeightedMarketCondition(
        trend_direction=WeightedTrendDirection.WEAK_UPTREND,
        volatility_level=WeightedVolatilityLevel.NORMAL,
        range_condition=WeightedRangeCondition.TRENDING,
        liquidity_level=WeightedLiquidityLevel.GOOD,
        session_phase=WeightedSessionPhase.MORNING,
        event_risk=WeightedEventRiskLevel.NONE,
        market_quality=WeightedMarketQuality.CLEAN,
        confidence=0.82,
        condition_inputs={"trend_slope": 0.002, "atr_percent": 0.006},
        pending_confirmation_count=pending_confirmation_count,
        data_ready=True,
        data_timestamp=TS,
        reason_codes=reason_codes,
        session_label="morning",
        explanation="Synthetic clean condition.",
    )


def blocked_condition() -> WeightedMarketCondition:
    return WeightedMarketCondition(
        trend_direction=WeightedTrendDirection.SIDEWAYS,
        volatility_level=WeightedVolatilityLevel.EXTREME,
        range_condition=WeightedRangeCondition.BREAKOUT,
        liquidity_level=WeightedLiquidityLevel.POOR,
        session_phase=WeightedSessionPhase.OPENING,
        event_risk=WeightedEventRiskLevel.BLOCKED,
        market_quality=WeightedMarketQuality.UNSTABLE,
        confidence=0.9,
        condition_inputs={"atr_percent": 0.04, "spread_percent": 0.004},
        data_ready=True,
        data_timestamp=TS,
        reason_codes=("weighted_voting.market_condition.immediate_deterioration",),
        session_label="opening",
        explanation="Synthetic blocked condition.",
    )


if __name__ == "__main__":
    unittest.main()
