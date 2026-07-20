from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime

from backend.app.algorithms.meta_strategy import (
    META_STRATEGY_CONFIGURATION_VERSION,
    META_STRATEGY_DYNAMIC_PROFILE_VERSION,
    MetaStrategyBaselineSettings,
    MetaStrategyDynamicProfileConfig,
    MetaStrategyDynamicProfileContext,
    meta_strategy_baseline_settings,
    resolve_meta_strategy_dynamic_profile,
)


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def normal_context() -> MetaStrategyDynamicProfileContext:
    return MetaStrategyDynamicProfileContext(timestamp=NOW)


class MetaStrategyStep28DynamicProfileTest(unittest.TestCase):
    def test_baseline_configuration_is_immutable_and_effective_settings_do_not_overwrite_defaults(self) -> None:
        baseline = meta_strategy_baseline_settings()
        baseline_snapshot = baseline.as_dict()

        profile = resolve_meta_strategy_dynamic_profile(
            baseline,
            replace(normal_context(), volatility_level="HIGH", liquidity_level="POOR", spread_bps=20.0),
        )

        self.assertEqual(baseline.as_dict(), baseline_snapshot)
        self.assertEqual(baseline.risk_percentage, baseline_snapshot["riskPercentage"])
        self.assertNotEqual(profile.effective_settings.risk_percentage, baseline.risk_percentage)
        self.assertEqual(profile.effective_settings.baseline_configuration_version, baseline.configuration_version)
        self.assertEqual(profile.effective_settings.baseline_settings_hash, baseline.settings_hash)
        with self.assertRaises(FrozenInstanceError):
            baseline.risk_percentage = 0.25  # type: ignore[misc]

    def test_effective_profile_adjusts_every_supported_setting_within_bounds(self) -> None:
        baseline = MetaStrategyBaselineSettings(
            entry_threshold=0.55,
            model_probability_threshold=0.55,
            risk_percentage=0.01,
            position_cap=0.20,
            stop_multiplier=1.0,
            target_multiplier=2.0,
            maximum_holding_minutes=40,
            spread_limit_bps=20.0,
            liquidity_requirement=100_000.0,
            trade_count_limit=6,
            allow_long=True,
            allow_short=True,
        )

        profile = resolve_meta_strategy_dynamic_profile(
            baseline,
            replace(normal_context(), volatility_level="HIGH", liquidity_level="POOR", spread_bps=30.0, short_bias_allowed=False),
            config=MetaStrategyDynamicProfileConfig(maximum_risk_percentage=0.015, maximum_position_cap=0.25),
        )
        effective = profile.effective_settings

        self.assertGreater(effective.entry_threshold, baseline.entry_threshold)
        self.assertGreater(effective.model_probability_threshold, baseline.model_probability_threshold)
        self.assertGreater(effective.stop_multiplier, baseline.stop_multiplier)
        self.assertGreater(effective.target_multiplier, baseline.target_multiplier)
        self.assertLess(effective.risk_percentage, baseline.risk_percentage)
        self.assertLess(effective.position_cap, baseline.position_cap)
        self.assertLess(effective.maximum_holding_minutes, baseline.maximum_holding_minutes)
        self.assertLess(effective.spread_limit_bps, baseline.spread_limit_bps)
        self.assertGreater(effective.liquidity_requirement, baseline.liquidity_requirement)
        self.assertLess(effective.trade_count_limit, baseline.trade_count_limit)
        self.assertTrue(effective.allow_long)
        self.assertFalse(effective.allow_short)
        self.assertGreaterEqual(effective.risk_percentage, 0.0)
        self.assertLessEqual(effective.risk_percentage, baseline.risk_percentage)
        self.assertGreaterEqual(effective.position_cap, 0.0)
        self.assertLessEqual(effective.position_cap, baseline.position_cap)

    def test_risk_off_conditions_zero_entry_risk_and_disable_new_directions(self) -> None:
        risk_off_contexts = (
            replace(normal_context(), event_blackout=True),
            replace(normal_context(), session_allowed=False),
            replace(normal_context(), drawdown_risk_off=True),
            replace(normal_context(), volatility_level="EXTREME"),
            replace(normal_context(), model_health_score=0.10),
            replace(normal_context(), missingness=0.75),
            replace(normal_context(), ood_score=0.95),
        )

        for context in risk_off_contexts:
            with self.subTest(context=context):
                profile = resolve_meta_strategy_dynamic_profile(meta_strategy_baseline_settings(), context)
                effective = profile.effective_settings

                self.assertEqual(profile.profile_id, f"risk_off:{META_STRATEGY_DYNAMIC_PROFILE_VERSION}")
                self.assertEqual(effective.risk_percentage, 0.0)
                self.assertEqual(effective.position_cap, 0.0)
                self.assertEqual(effective.trade_count_limit, 0)
                self.assertFalse(effective.allow_long)
                self.assertFalse(effective.allow_short)
                self.assertIn("risk_off", profile.active_overlays)
                self.assertTrue(any(code.startswith("meta_strategy.dynamic_profile.risk_off") for code in profile.reason_codes))

    def test_profile_versions_reason_codes_and_effective_settings_are_persisted(self) -> None:
        baseline = meta_strategy_baseline_settings()
        profile = resolve_meta_strategy_dynamic_profile(
            baseline,
            replace(normal_context(), liquidity_level="GOOD"),
        )
        payload = profile.persisted_payload()

        self.assertEqual(payload["profileVersion"], META_STRATEGY_DYNAMIC_PROFILE_VERSION)
        self.assertEqual(payload["baselineConfigurationVersion"], META_STRATEGY_CONFIGURATION_VERSION)
        self.assertEqual(payload["baselineSettingsHash"], baseline.settings_hash)
        self.assertEqual(payload["effectiveSettings"]["baselineSettingsHash"], baseline.settings_hash)
        self.assertIn("meta_strategy.dynamic_profile.good_liquidity", payload["reasonCodes"])
        self.assertIn("liquidity_good", payload["activeOverlays"])
        self.assertEqual(payload["calculatedAt"], NOW.isoformat())

    def test_risk_adjustments_are_clamped_to_configured_bounds(self) -> None:
        baseline = MetaStrategyBaselineSettings(risk_percentage=0.05, position_cap=0.50, stop_multiplier=4.0, target_multiplier=5.0)
        profile = resolve_meta_strategy_dynamic_profile(
            baseline,
            replace(normal_context(), volatility_level="HIGH"),
            config=MetaStrategyDynamicProfileConfig(
                maximum_risk_percentage=0.01,
                maximum_position_cap=0.10,
                maximum_stop_multiplier=3.0,
                maximum_target_multiplier=4.0,
            ),
        )

        self.assertLessEqual(profile.effective_settings.risk_percentage, 0.01)
        self.assertLessEqual(profile.effective_settings.position_cap, 0.10)
        self.assertLessEqual(profile.effective_settings.stop_multiplier, 3.0)
        self.assertLessEqual(profile.effective_settings.target_multiplier, 4.0)


if __name__ == "__main__":
    unittest.main()
