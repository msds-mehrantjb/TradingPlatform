from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend.app.algorithms.regime.rollout import (
    REGIME_DYNAMIC_PROFILE_ENABLED,
    REGIME_GLOBAL_RISK_MANAGER_ENABLED,
    REGIME_ML_MODE,
    REGIME_ROLLBACK_STATE_KEY,
    REGIME_ROLLOUT_PHASES,
    REGIME_ROLLOUT_STATE_KEY,
    REGIME_SHORT_ENTRIES_ENABLED,
    REGIME_V2_ENABLED,
    RegimeRolloutFlags,
    RegimeRolloutValidation,
    evaluate_regime_rollout_phase,
    limited_paper_orders_allowed,
    record_valid_regime_rollout_state,
    regime_rollout_feature_flags,
    regime_rollout_status,
    rollback_configuration,
    rollback_regime_rollout,
)
from backend.app.config import ApplicationConfig
from backend.app.main import app


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


class RegimePhase17RolloutTest(unittest.TestCase):
    def test_initial_deployment_flags_default_to_paper_only_shadow_posture(self) -> None:
        flags = regime_rollout_feature_flags({})
        status = regime_rollout_status(flags=flags, validation=RegimeRolloutValidation())

        self.assertTrue(flags.v2_enabled)
        self.assertTrue(flags.dynamic_profile_enabled)
        self.assertEqual(flags.ml_mode, "shadow")
        self.assertTrue(flags.global_risk_manager_enabled)
        self.assertFalse(flags.short_entries_enabled)
        self.assertFalse(status["limited_paper_orders_allowed"])
        self.assertFalse(status["live_trading_allowed"])
        self.assertIn("regime.rollout.paper_only", status["reason_codes"])

    def test_feature_flags_parse_environment_independently(self) -> None:
        flags = regime_rollout_feature_flags(
            {
                REGIME_V2_ENABLED: "false",
                REGIME_DYNAMIC_PROFILE_ENABLED: "false",
                REGIME_ML_MODE: "confirm_only",
                REGIME_GLOBAL_RISK_MANAGER_ENABLED: "true",
                REGIME_SHORT_ENTRIES_ENABLED: "true",
            }
        )

        self.assertFalse(flags.v2_enabled)
        self.assertFalse(flags.dynamic_profile_enabled)
        self.assertEqual(flags.ml_mode, "confirm_only")
        self.assertTrue(flags.global_risk_manager_enabled)
        self.assertTrue(flags.short_entries_enabled)

    def test_deployment_sequence_requires_prior_acceptance_before_limited_paper_orders(self) -> None:
        flags = RegimeRolloutFlags()
        historical = evaluate_regime_rollout_phase("historical_characterization", flags=flags, validation=RegimeRolloutValidation())
        backtest = evaluate_regime_rollout_phase(
            "dedicated_backtest",
            flags=flags,
            validation=RegimeRolloutValidation(historical_characterization_passed=True),
        )
        limited = evaluate_regime_rollout_phase(
            "limited_paper_orders",
            flags=flags,
            validation=RegimeRolloutValidation(
                historical_characterization_passed=True,
                dedicated_backtest_passed=True,
                untouched_oos_passed=True,
                paper_shadow_decisions_passed=True,
                old_new_decision_comparison_passed=False,
                tests_passed=True,
            ),
        )

        self.assertTrue(historical.enabled)
        self.assertTrue(backtest.enabled)
        self.assertFalse(limited.enabled)
        self.assertIn("regime.rollout.old_new_decision_comparison_not_validated", limited.reason_codes)

    def test_ml_shadow_stage_requires_shadow_mode_and_short_entries_stay_disabled_initially(self) -> None:
        validation = RegimeRolloutValidation(historical_characterization_passed=True, dedicated_backtest_passed=True)
        wrong_ml = evaluate_regime_rollout_phase(
            "ml_shadow",
            flags=RegimeRolloutFlags(ml_mode="confirm_only"),
            validation=validation,
        )
        shadow_with_shorts = evaluate_regime_rollout_phase(
            "paper_shadow_decisions",
            flags=RegimeRolloutFlags(short_entries_enabled=True),
            validation=RegimeRolloutValidation(
                historical_characterization_passed=True,
                dedicated_backtest_passed=True,
                untouched_oos_passed=True,
            ),
        )

        self.assertFalse(wrong_ml.enabled)
        self.assertIn("regime.rollout.ml_shadow_mode_required", wrong_ml.reason_codes)
        self.assertFalse(shadow_with_shorts.enabled)
        self.assertIn("regime.rollout.short_entries_disabled_initially", shadow_with_shorts.reason_codes)

    def test_limited_paper_orders_require_tests_and_global_risk_manager(self) -> None:
        blocked = evaluate_regime_rollout_phase(
            "limited_paper_orders",
            flags=RegimeRolloutFlags(global_risk_manager_enabled=False),
            validation=fully_validated_rollout(tests_passed=False, limited_paper_orders_approved=False),
        )
        enabled = evaluate_regime_rollout_phase(
            "limited_paper_orders",
            flags=RegimeRolloutFlags(),
            validation=fully_validated_rollout(limited_paper_orders_approved=True),
        )

        self.assertFalse(blocked.enabled)
        self.assertIn("regime.rollout.tests_not_passed", blocked.reason_codes)
        self.assertIn("regime.rollout.global_risk_manager_required", blocked.reason_codes)
        self.assertTrue(enabled.enabled)
        self.assertTrue(limited_paper_orders_allowed(flags=RegimeRolloutFlags(), validation=fully_validated_rollout(limited_paper_orders_approved=True)))

    def test_one_successful_backtest_never_enables_live_or_promotion_review(self) -> None:
        status = regime_rollout_status(
            flags=RegimeRolloutFlags(),
            validation=RegimeRolloutValidation(
                historical_characterization_passed=True,
                dedicated_backtest_passed=True,
                live_trading_enabled=True,
            ),
        )
        promotion = evaluate_regime_rollout_phase(
            "promotion_review",
            flags=RegimeRolloutFlags(),
            validation=RegimeRolloutValidation(historical_characterization_passed=True, dedicated_backtest_passed=True),
        )

        self.assertFalse(status["live_trading_allowed"])
        for phase in status["phases"]:
            self.assertFalse(phase["enabled"])
            self.assertIn("regime.rollout.live_trading_never_allowed", phase["reason_codes"])
        self.assertFalse(promotion.enabled)
        self.assertIn("regime.rollout.untouched_oos_not_validated", promotion.reason_codes)

    def test_rollback_configuration_supports_selective_disable_and_restoration(self) -> None:
        rollback = rollback_configuration()

        self.assertFalse(rollback[REGIME_V2_ENABLED])
        self.assertFalse(rollback[REGIME_DYNAMIC_PROFILE_ENABLED])
        self.assertEqual(rollback[REGIME_ML_MODE], "off")
        self.assertTrue(rollback[REGIME_GLOBAL_RISK_MANAGER_ENABLED])
        self.assertFalse(rollback[REGIME_SHORT_ENTRIES_ENABLED])
        self.assertEqual(rollback["regime_new_entries"], "disabled")
        self.assertEqual(rollback["protective_exits"], "preserved")
        self.assertTrue(rollback["restore_previous_settings"])
        self.assertTrue(rollback["restore_previous_model_artifact"])
        self.assertEqual(rollback["database_migration_rollback"], "safe_only")
        self.assertEqual(rollback["disable_dynamic_profiles_only"], {REGIME_DYNAMIC_PROFILE_ENABLED: False})
        self.assertEqual(rollback["disable_ml_only"], {REGIME_ML_MODE: "off"})
        self.assertFalse(rollback["delete_historical_records"])
        self.assertFalse(rollback["live_orders"])

    def test_rollback_restores_previous_valid_state_without_deleting_records(self) -> None:
        store = MemoryStore()
        first = record_valid_regime_rollout_state(store, {"phase": "paper_shadow_decisions", "state_version": "valid-1"}, recorded_at=NOW)
        second = record_valid_regime_rollout_state(store, {"phase": "limited_paper_orders", "state_version": "valid-2"}, recorded_at=NOW)

        restored = rollback_regime_rollout(store, rolled_back_at=NOW)

        self.assertEqual(first["state_version"], "valid-1")
        self.assertEqual(second["state_version"], "valid-2")
        self.assertEqual(store.snapshots[REGIME_ROLLBACK_STATE_KEY]["state_version"], "valid-1")
        self.assertEqual(restored["state_version"], "valid-1")
        self.assertFalse(restored["historical_records_deleted"])
        self.assertEqual(store.snapshots[REGIME_ROLLOUT_STATE_KEY]["state_version"], "valid-1")

    def test_status_reports_complete_sequence_and_application_config_flags(self) -> None:
        status = regime_rollout_status(flags=RegimeRolloutFlags(), validation=RegimeRolloutValidation())
        config_flags = ApplicationConfig().featureFlags

        self.assertEqual(status["algorithm_id"], "regime")
        self.assertEqual(status["deployment_sequence"], REGIME_ROLLOUT_PHASES)
        self.assertEqual(len(status["phases"]), len(REGIME_ROLLOUT_PHASES))
        self.assertTrue(config_flags.regimeV2Enabled)
        self.assertTrue(config_flags.regimeDynamicProfileEnabled)
        self.assertEqual(config_flags.regimeMlMode, "shadow")
        self.assertTrue(config_flags.regimeGlobalRiskManagerEnabled)
        self.assertFalse(config_flags.regimeShortEntriesEnabled)

    def test_regime_rollout_status_api_reports_paper_only_flags(self) -> None:
        response = TestClient(app).get("/api/regime/rollout/status")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["algorithm_id"], "regime")
        self.assertTrue(body["feature_flags"][REGIME_V2_ENABLED])
        self.assertEqual(body["feature_flags"][REGIME_ML_MODE], "shadow")
        self.assertFalse(body["feature_flags"][REGIME_SHORT_ENTRIES_ENABLED])
        self.assertFalse(body["live_trading_allowed"])


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot


def fully_validated_rollout(
    *,
    limited_paper_orders_approved: bool = True,
    tests_passed: bool = True,
    live_trading_enabled: bool = False,
) -> RegimeRolloutValidation:
    return RegimeRolloutValidation(
        historical_characterization_passed=True,
        dedicated_backtest_passed=True,
        untouched_oos_passed=True,
        ml_shadow_passed=True,
        paper_shadow_decisions_passed=True,
        old_new_decision_comparison_passed=True,
        limited_paper_orders_approved=limited_paper_orders_approved,
        global_gate_monitoring_passed=True,
        enough_multi_regime_trades_collected=True,
        performance_review_passed=True,
        tests_passed=tests_passed,
        live_trading_enabled=live_trading_enabled,
    )


if __name__ == "__main__":
    unittest.main()
