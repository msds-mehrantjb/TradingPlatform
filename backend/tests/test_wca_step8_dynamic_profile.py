from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.wca.contracts import (
    WcaAlgorithmRiskStatus,
    WcaBaselineSettings,
    WcaDataQualityStatus,
    WcaEvaluationStatus,
    WcaEventRiskStatus,
    WcaLiquidityStatus,
    WcaMarketStatus,
    WcaSessionStatus,
    WcaTrendStatus,
    WcaVolatilityStatus,
)
from backend.app.algorithms.wca.dynamic_profile import (
    WcaDynamicProfileConfig,
    protective_stop_distance_for_existing_position,
    resolve_dynamic_profile,
)


UTC = timezone.utc


class WcaStep8DynamicProfileTest(unittest.TestCase):
    def test_baseline_remains_unchanged_after_profile_calculation(self) -> None:
        baseline = baseline_settings()
        before = baseline.deterministic_json()

        profile = resolve_dynamic_profile(
            baseline=baseline,
            market_status=market_status(volatility=WcaVolatilityStatus.HIGH, algorithm_risk=WcaAlgorithmRiskStatus.DEFENSIVE),
            calculation_timestamp=timestamp(),
        )

        self.assertEqual(baseline.deterministic_json(), before)
        self.assertEqual(profile.baseline_settings_version, baseline.settings_version)
        self.assertEqual(profile.effective_settings.baseline_settings_version, baseline.settings_version)
        self.assertTrue(profile.active_overlays)
        self.assertIsNotNone(profile.effective_settings.market_status)
        self.assertEqual(profile.calculation_timestamp, timestamp())
        self.assertGreater(profile.expiration_timestamp, profile.calculation_timestamp)

    def test_disabling_dynamic_profile_restores_baseline_behavior(self) -> None:
        baseline = baseline_settings(pyramiding_enabled=True)

        profile = resolve_dynamic_profile(
            baseline=baseline,
            market_status=market_status(volatility=WcaVolatilityStatus.EXTREME, algorithm_risk=WcaAlgorithmRiskStatus.DAILY_STOP),
            calculation_timestamp=timestamp(),
            config=WcaDynamicProfileConfig(enabled=False),
        )
        effective = profile.effective_settings

        self.assertEqual(effective.active_overlays, ("baseline",))
        self.assertEqual(effective.final_risk_percent, baseline.base_risk_percent)
        self.assertEqual(effective.final_order_allocation_percent, baseline.order_allocation_percent)
        self.assertEqual(effective.final_daily_allocation_percent, baseline.daily_allocation_percent)
        self.assertEqual(effective.final_max_position_percent, baseline.max_position_percent)
        self.assertEqual(effective.final_max_daily_loss_percent, baseline.max_daily_loss_percent)
        self.assertEqual(effective.final_max_allowed_shares, baseline.max_allowed_shares)
        self.assertEqual(effective.final_minimum_score, baseline.minimum_score)
        self.assertEqual(effective.final_minimum_agreement, baseline.minimum_directional_agreement)
        self.assertEqual(effective.final_minimum_confidence, baseline.minimum_average_confidence)
        self.assertEqual(effective.final_entry_cutoff_minutes, baseline.entry_cutoff_minutes)
        self.assertEqual(effective.final_pyramiding_enabled, baseline.pyramiding_enabled)
        self.assertFalse(effective.entries_blocked)

    def test_dynamic_effective_settings_only_tighten_or_reduce(self) -> None:
        baseline = baseline_settings(max_allowed_shares=1000)

        profile = resolve_dynamic_profile(
            baseline=baseline,
            market_status=market_status(
                volatility=WcaVolatilityStatus.HIGH,
                liquidity=WcaLiquidityStatus.THIN,
                event_risk=WcaEventRiskStatus.ELEVATED,
                algorithm_risk=WcaAlgorithmRiskStatus.DEFENSIVE,
            ),
            calculation_timestamp=timestamp(),
            current_drawdown_percent=2.5,
        )
        effective = profile.effective_settings

        self.assertLessEqual(effective.final_risk_percent, baseline.base_risk_percent)
        self.assertLessEqual(effective.final_order_allocation_percent, baseline.order_allocation_percent)
        self.assertLessEqual(effective.final_daily_allocation_percent, baseline.daily_allocation_percent)
        self.assertLessEqual(effective.final_max_position_percent, baseline.max_position_percent)
        self.assertLessEqual(effective.final_max_allowed_shares, baseline.max_allowed_shares)
        self.assertLessEqual(effective.final_max_daily_loss_percent, baseline.max_daily_loss_percent)
        self.assertLessEqual(effective.final_max_daily_trades, baseline.max_daily_trades)
        self.assertGreaterEqual(effective.final_minimum_score, baseline.minimum_score)
        self.assertGreaterEqual(effective.final_minimum_agreement, baseline.minimum_directional_agreement)
        self.assertGreaterEqual(effective.final_minimum_confidence, baseline.minimum_average_confidence)
        self.assertGreaterEqual(effective.final_cooldown_seconds, baseline.cooldown_seconds)
        self.assertGreaterEqual(effective.final_assumed_slippage_per_share, baseline.assumed_slippage_per_share)
        self.assertLessEqual(effective.final_atr_stop_multiplier, baseline.atr_stop_multiplier)
        self.assertFalse(effective.final_pyramiding_enabled)

    def test_blocking_overlay_sets_zero_new_entry_risk(self) -> None:
        baseline = baseline_settings(max_allowed_shares=1000)

        profile = resolve_dynamic_profile(
            baseline=baseline,
            market_status=market_status(
                volatility=WcaVolatilityStatus.EXTREME,
                liquidity=WcaLiquidityStatus.UNSAFE,
                event_risk=WcaEventRiskStatus.BLOCKED,
                data_quality=WcaDataQualityStatus.INVALID,
                algorithm_risk=WcaAlgorithmRiskStatus.DAILY_STOP,
            ),
            calculation_timestamp=timestamp(),
        )
        effective = profile.effective_settings

        self.assertTrue(effective.entries_blocked)
        self.assertEqual(effective.final_risk_percent, 0)
        self.assertEqual(effective.final_order_allocation_percent, 0)
        self.assertEqual(effective.final_daily_allocation_percent, 0)
        self.assertEqual(effective.final_max_position_percent, 0)
        self.assertEqual(effective.final_max_daily_trades, 0)
        self.assertEqual(effective.final_max_allowed_shares, 0)

    def test_profile_switching_does_not_oscillate_on_every_candle(self) -> None:
        baseline = baseline_settings()
        config = WcaDynamicProfileConfig(minimum_profile_hold_seconds=300, profile_ttl_seconds=900)
        defensive = resolve_dynamic_profile(
            baseline=baseline,
            market_status=market_status(volatility=WcaVolatilityStatus.HIGH, algorithm_risk=WcaAlgorithmRiskStatus.DEFENSIVE),
            calculation_timestamp=timestamp(),
            config=config,
        )

        calmer = resolve_dynamic_profile(
            baseline=baseline,
            market_status=market_status(),
            calculation_timestamp=timestamp() + timedelta(seconds=60),
            previous_profile=defensive,
            config=config,
        )

        self.assertEqual(calmer.profile_id, defensive.profile_id)
        self.assertIn("wca.dynamic_profile.hold_previous", calmer.reason_codes)

        released = resolve_dynamic_profile(
            baseline=baseline,
            market_status=market_status(),
            calculation_timestamp=timestamp() + timedelta(seconds=360),
            previous_profile=defensive,
            config=config,
        )

        self.assertNotEqual(released.profile_id, defensive.profile_id)

    def test_existing_position_stop_is_never_widened_by_improved_conditions(self) -> None:
        self.assertEqual(
            protective_stop_distance_for_existing_position(current_stop_distance=1.25, proposed_stop_distance=1.80),
            1.25,
        )
        self.assertEqual(
            protective_stop_distance_for_existing_position(current_stop_distance=1.25, proposed_stop_distance=0.95),
            0.95,
        )


def baseline_settings(**overrides: object) -> WcaBaselineSettings:
    values = {
        "settings_version": "wca_baseline_test_v1",
        "minimum_score": 0.35,
        "minimum_directional_agreement": 0.50,
        "minimum_average_confidence": 0.45,
        "base_risk_percent": 1.0,
        "order_allocation_percent": 10.0,
        "daily_allocation_percent": 20.0,
        "max_position_percent": 15.0,
        "max_daily_loss_percent": 3.0,
        "max_daily_trades": 6,
        "max_allowed_shares": 0,
        "hard_max_risk_percent": 1.0,
        "hard_max_order_allocation_percent": 10.0,
        "hard_max_daily_allocation_percent": 20.0,
        "hard_max_position_percent": 15.0,
        "hard_max_daily_loss_percent": 3.0,
        "hard_max_allowed_shares": 0,
    }
    values.update(overrides)
    return WcaBaselineSettings(**values)


def market_status(
    *,
    trend: WcaTrendStatus = WcaTrendStatus.RANGE,
    volatility: WcaVolatilityStatus = WcaVolatilityStatus.NORMAL,
    liquidity: WcaLiquidityStatus = WcaLiquidityStatus.NORMAL,
    session: WcaSessionStatus = WcaSessionStatus.MIDDAY,
    event_risk: WcaEventRiskStatus = WcaEventRiskStatus.NORMAL,
    data_quality: WcaDataQualityStatus = WcaDataQualityStatus.HEALTHY,
    algorithm_risk: WcaAlgorithmRiskStatus = WcaAlgorithmRiskStatus.NORMAL,
) -> WcaMarketStatus:
    return WcaMarketStatus(
        status=WcaEvaluationStatus.ACTIVE,
        trend=trend,
        volatility=volatility,
        liquidity=liquidity,
        session=session,
        event_risk=event_risk,
        data_quality=data_quality,
        algorithm_risk=algorithm_risk,
        classification_confidence=0.9,
        input_timestamp=timestamp(),
        profile_expiration=timestamp() + timedelta(minutes=15),
        reason_codes=("wca.market_status.test",),
    )


def timestamp() -> datetime:
    return datetime(2026, 1, 6, 17, 0, tzinfo=UTC)


if __name__ == "__main__":
    unittest.main()
