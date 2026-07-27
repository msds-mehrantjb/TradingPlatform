from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend.app.algorithms.regime.rollout import (
    REGIME_AUTOMATIC_ORDER_SUBMISSION_ENABLED,
    REGIME_DYNAMIC_PROFILE_ENABLED,
    REGIME_GLOBAL_RISK_MANAGER_ENABLED,
    REGIME_ML_MODE,
    REGIME_PAPER_SUBMISSION_ENABLED,
    REGIME_ROLLBACK_STATE_KEY,
    REGIME_ROLLOUT_STAGES,
    REGIME_ROLLOUT_STATE_KEY,
    REGIME_SHORT_ENTRIES_ENABLED,
    REGIME_V2_ENABLED,
    RegimeRolloutEvidence,
    RegimeRolloutFlags,
    evaluate_regime_rollout_stage,
    limited_paper_orders_allowed,
    paper_submission_allowed,
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
        status = regime_rollout_status(flags=flags, evidence=RegimeRolloutEvidence())

        self.assertTrue(flags.v2_enabled)
        self.assertTrue(flags.dynamic_profile_enabled)
        self.assertEqual(flags.ml_mode, "shadow")
        self.assertTrue(flags.global_risk_manager_enabled)
        self.assertFalse(flags.short_entries_enabled)
        self.assertFalse(flags.paper_submission_enabled)
        self.assertFalse(flags.automatic_order_submission_enabled)
        self.assertFalse(status["limited_paper_orders_allowed"])
        self.assertFalse(status["paper_submission_allowed"])
        self.assertFalse(status["automatic_order_submission_allowed"])
        self.assertFalse(status["live_trading_allowed"])
        self.assertIn("regime.rollout.automatic_order_submission_disabled_by_default", status["reason_codes"])

    def test_feature_flags_parse_environment_independently(self) -> None:
        flags = regime_rollout_feature_flags(
            {
                REGIME_V2_ENABLED: "false",
                REGIME_DYNAMIC_PROFILE_ENABLED: "false",
                REGIME_ML_MODE: "confirm_only",
                REGIME_GLOBAL_RISK_MANAGER_ENABLED: "true",
                REGIME_SHORT_ENTRIES_ENABLED: "true",
                REGIME_PAPER_SUBMISSION_ENABLED: "true",
                REGIME_AUTOMATIC_ORDER_SUBMISSION_ENABLED: "true",
            }
        )

        self.assertFalse(flags.v2_enabled)
        self.assertFalse(flags.dynamic_profile_enabled)
        self.assertEqual(flags.ml_mode, "confirm_only")
        self.assertTrue(flags.global_risk_manager_enabled)
        self.assertTrue(flags.short_entries_enabled)
        self.assertTrue(flags.paper_submission_enabled)
        self.assertTrue(flags.automatic_order_submission_enabled)

    def test_stage_a_requires_real_offline_validation_evidence(self) -> None:
        blocked = evaluate_regime_rollout_stage("stage_a_offline_validation", evidence=RegimeRolloutEvidence())
        enabled = evaluate_regime_rollout_stage("stage_a_offline_validation", evidence=stage_a_evidence())

        self.assertFalse(blocked.enabled)
        self.assertIn("regime.rollout.evidence_missing:focused_tests_passed", blocked.reason_codes)
        self.assertIn("regime.rollout.evidence_missing:full_backend_tests_passed", blocked.reason_codes)
        self.assertTrue(enabled.enabled)

    def test_stage_b_requires_stage_a_and_shadow_runtime_evidence(self) -> None:
        blocked = evaluate_regime_rollout_stage("stage_b_shadow_runtime", evidence=stage_a_evidence())
        enabled = evaluate_regime_rollout_stage("stage_b_shadow_runtime", evidence=stage_b_evidence())

        self.assertFalse(blocked.enabled)
        self.assertIn("regime.rollout.evidence_missing:completed_bar_reliability_passed", blocked.reason_codes)
        self.assertIn("regime.rollout.evidence_missing:paper_backtest_replay_parity_passed", blocked.reason_codes)
        self.assertTrue(enabled.enabled)

    def test_stage_c_allows_intents_but_rejects_broker_orders(self) -> None:
        clean = evaluate_regime_rollout_stage("stage_c_paper_intent_validation", evidence=stage_c_evidence())
        dirty = evaluate_regime_rollout_stage(
            "stage_c_paper_intent_validation",
            evidence=stage_c_evidence(broker_orders_created_in_intent_validation=1),
        )

        self.assertTrue(clean.enabled)
        self.assertFalse(dirty.enabled)
        self.assertIn("regime.rollout.intent_validation_created_broker_orders", dirty.reason_codes)

    def test_stage_d_requires_explicit_paper_flag_and_limited_spy_controls(self) -> None:
        evidence = stage_d_evidence()
        blocked = evaluate_regime_rollout_stage("stage_d_limited_spy_paper_submission", evidence=evidence, flags=RegimeRolloutFlags())
        enabled = evaluate_regime_rollout_stage(
            "stage_d_limited_spy_paper_submission",
            evidence=evidence,
            flags=RegimeRolloutFlags(paper_submission_enabled=True),
        )

        self.assertFalse(blocked.enabled)
        self.assertIn("regime.rollout.paper_submission_flag_disabled", blocked.reason_codes)
        self.assertTrue(enabled.enabled)
        self.assertTrue(limited_paper_orders_allowed(flags=RegimeRolloutFlags(paper_submission_enabled=True), evidence=evidence))
        self.assertTrue(paper_submission_allowed(flags=RegimeRolloutFlags(paper_submission_enabled=True), evidence=evidence))

    def test_stage_e_requires_expanded_paper_quality_evidence(self) -> None:
        blocked = evaluate_regime_rollout_stage(
            "stage_e_expanded_paper_validation",
            evidence=stage_d_evidence(),
            flags=RegimeRolloutFlags(paper_submission_enabled=True),
        )
        enabled = evaluate_regime_rollout_stage(
            "stage_e_expanded_paper_validation",
            evidence=stage_e_evidence(),
            flags=RegimeRolloutFlags(paper_submission_enabled=True),
        )

        self.assertFalse(blocked.enabled)
        self.assertIn("regime.rollout.evidence_missing:fill_quality_passed", blocked.reason_codes)
        self.assertIn("regime.rollout.evidence_missing:no_duplicate_orders_passed", blocked.reason_codes)
        self.assertTrue(enabled.enabled)

    def test_live_trading_automatic_submission_shorts_and_non_shadow_ml_block_all_stages(self) -> None:
        status = regime_rollout_status(
            flags=RegimeRolloutFlags(
                ml_mode="confirm_only",
                short_entries_enabled=True,
                paper_submission_enabled=True,
                automatic_order_submission_enabled=True,
            ),
            evidence=stage_e_evidence(live_trading_enabled=True),
        )

        self.assertFalse(status["live_trading_allowed"])
        for stage in status["stages"]:
            self.assertFalse(stage["enabled"])
            self.assertIn("regime.rollout.live_trading_never_allowed", stage["reason_codes"])
            self.assertIn("regime.rollout.automatic_order_submission_not_permitted", stage["reason_codes"])
            self.assertIn("regime.rollout.ml_shadow_mode_required", stage["reason_codes"])
            self.assertIn("regime.rollout.short_entries_disabled_initially", stage["reason_codes"])

    def test_rollback_configuration_supports_selective_disable_and_restoration(self) -> None:
        rollback = rollback_configuration()

        self.assertFalse(rollback[REGIME_V2_ENABLED])
        self.assertFalse(rollback[REGIME_DYNAMIC_PROFILE_ENABLED])
        self.assertEqual(rollback[REGIME_ML_MODE], "off")
        self.assertTrue(rollback[REGIME_GLOBAL_RISK_MANAGER_ENABLED])
        self.assertFalse(rollback[REGIME_SHORT_ENTRIES_ENABLED])
        self.assertFalse(rollback[REGIME_PAPER_SUBMISSION_ENABLED])
        self.assertFalse(rollback[REGIME_AUTOMATIC_ORDER_SUBMISSION_ENABLED])
        self.assertEqual(rollback["regime_new_entries"], "disabled")
        self.assertEqual(rollback["protective_exits"], "preserved")
        self.assertFalse(rollback["live_orders"])
        self.assertFalse(rollback["paper_broker_submission"])

    def test_rollback_restores_previous_valid_state_without_deleting_records(self) -> None:
        store = MemoryStore()
        first = record_valid_regime_rollout_state(store, {"stage": "stage_b_shadow_runtime", "state_version": "valid-1"}, recorded_at=NOW)
        second = record_valid_regime_rollout_state(store, {"stage": "stage_d_limited_spy_paper_submission", "state_version": "valid-2"}, recorded_at=NOW)

        restored = rollback_regime_rollout(store, rolled_back_at=NOW)

        self.assertEqual(first["state_version"], "valid-1")
        self.assertEqual(second["state_version"], "valid-2")
        self.assertEqual(store.snapshots[REGIME_ROLLBACK_STATE_KEY]["state_version"], "valid-1")
        self.assertEqual(restored["state_version"], "valid-1")
        self.assertFalse(restored["historical_records_deleted"])
        self.assertEqual(store.snapshots[REGIME_ROLLOUT_STATE_KEY]["state_version"], "valid-1")

    def test_status_reports_complete_stage_sequence_and_application_config_flags(self) -> None:
        status = regime_rollout_status(flags=RegimeRolloutFlags(), evidence=RegimeRolloutEvidence())
        config_flags = ApplicationConfig().featureFlags

        self.assertEqual(status["algorithm_id"], "regime")
        self.assertEqual(status["deployment_sequence"], REGIME_ROLLOUT_STAGES)
        self.assertEqual(len(status["stages"]), len(REGIME_ROLLOUT_STAGES))
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
        self.assertFalse(body["feature_flags"][REGIME_PAPER_SUBMISSION_ENABLED])
        self.assertFalse(body["feature_flags"][REGIME_AUTOMATIC_ORDER_SUBMISSION_ENABLED])
        self.assertFalse(body["paper_submission_allowed"])
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


def stage_a_evidence(**overrides) -> RegimeRolloutEvidence:
    payload = {
        "focused_tests_passed": True,
        "full_backend_tests_passed": True,
        "realistic_backtesting_passed": True,
        "walk_forward_passed": True,
        "holdout_passed": True,
        "strategy_regime_occupancy_reasonable": True,
        "transaction_costs_included": True,
    }
    payload.update(overrides)
    _attach_persisted_ids(payload)
    return RegimeRolloutEvidence(**payload)


def stage_b_evidence(**overrides) -> RegimeRolloutEvidence:
    payload = {
        **stage_a_evidence().model_dump(),
        "completed_bar_reliability_passed": True,
        "decision_latency_passed": True,
        "stable_hysteresis_passed": True,
        "shadow_regime_occupancy_reasonable": True,
        "strategy_opportunity_frequency_reasonable": True,
        "blocker_frequency_reasonable": True,
        "restart_recovery_passed": True,
        "duplicate_prevention_passed": True,
        "paper_backtest_replay_parity_passed": True,
    }
    payload["persisted_evidence_ids"] = frozenset(payload["persisted_evidence_ids"])
    payload.update(overrides)
    _attach_persisted_ids(payload)
    return RegimeRolloutEvidence(**payload)


def stage_c_evidence(**overrides) -> RegimeRolloutEvidence:
    payload = {
        **stage_b_evidence().model_dump(),
        "quantity_validated": True,
        "stops_validated": True,
        "targets_validated": True,
        "transaction_cost_gate_validated": True,
        "global_risk_reservation_validated": True,
        "outbox_state_validated": True,
        "idempotency_validated": True,
    }
    payload["persisted_evidence_ids"] = frozenset(payload["persisted_evidence_ids"])
    payload.update(overrides)
    _attach_persisted_ids(payload)
    return RegimeRolloutEvidence(**payload)


def stage_d_evidence(**overrides) -> RegimeRolloutEvidence:
    payload = {
        **stage_c_evidence().model_dump(),
        "spy_only_validated": True,
        "single_instance_validated": True,
        "long_only_validated": True,
        "low_quantity_cap_validated": True,
        "no_pyramiding_validated": True,
        "limited_trades_per_day_validated": True,
        "strict_daily_loss_validated": True,
        "end_of_day_flatten_validated": True,
    }
    payload["persisted_evidence_ids"] = frozenset(payload["persisted_evidence_ids"])
    payload.update(overrides)
    _attach_persisted_ids(payload)
    return RegimeRolloutEvidence(**payload)


def stage_e_evidence(**overrides) -> RegimeRolloutEvidence:
    payload = {
        **stage_d_evidence().model_dump(),
        "fill_quality_passed": True,
        "reconciliation_passed": True,
        "expanded_restart_recovery_passed": True,
        "slippage_passed": True,
        "daily_loss_protection_passed": True,
        "position_isolation_passed": True,
        "no_duplicate_orders_passed": True,
    }
    payload["persisted_evidence_ids"] = frozenset(payload["persisted_evidence_ids"])
    payload.update(overrides)
    _attach_persisted_ids(payload)
    return RegimeRolloutEvidence(**payload)


def _attach_persisted_ids(payload: dict) -> None:
    existing = set(payload.get("persisted_evidence_ids") or ())
    existing.update(key for key, value in payload.items() if isinstance(value, bool) and value and key.endswith(("_passed", "_validated", "_included", "_reasonable")))
    payload["persisted_evidence_ids"] = frozenset(existing)


if __name__ == "__main__":
    unittest.main()
