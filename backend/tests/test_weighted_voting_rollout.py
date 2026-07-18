from __future__ import annotations

import unittest
from datetime import datetime, timezone

from backend.app.algorithms.weighted_voting.rollout import (
    ROLLOUT_STATE_KEY,
    ROLLBACK_STATE_KEY,
    WEIGHTED_VOTING_AUTO_SUBMIT_ENABLED,
    WEIGHTED_VOTING_DYNAMIC_INCREASE_ENABLED,
    WEIGHTED_VOTING_DYNAMIC_REDUCTION_ENABLED,
    WEIGHTED_VOTING_SHADOW_MODE,
    WEIGHTED_VOTING_V2_ENABLED,
    WEIGHTED_VOTING_ROLLOUT_STATES,
    ROLLOUT_STAGES,
    WeightedVotingRolloutFlags,
    WeightedVotingRolloutValidation,
    automatic_submission_allowed,
    evaluate_rollout_stage,
    evaluate_weighted_voting_rollout_control,
    record_valid_rollout_state,
    rollback_weighted_voting_rollout,
    rollout_feature_flags,
    rollout_status,
)


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


class WeightedVotingRolloutTest(unittest.TestCase):
    def test_default_rollout_activates_v2_shadow_without_auto_submit(self) -> None:
        flags = rollout_feature_flags({})
        status = rollout_status(flags=flags)

        self.assertTrue(flags.v2_enabled)
        self.assertTrue(flags.shadow_mode)
        self.assertFalse(flags.dynamic_reduction_enabled)
        self.assertFalse(flags.dynamic_increase_enabled)
        self.assertFalse(flags.auto_submit_enabled)
        self.assertTrue(status["stages"][0]["enabled"])
        self.assertFalse(status["automatic_submission_allowed"])
        self.assertFalse(status["live_trading_allowed"])
        self.assertEqual(tuple(status["allowed_states"]), WEIGHTED_VOTING_ROLLOUT_STATES)
        self.assertEqual(status["control"]["algorithm_id"], "weighted_voting")
        self.assertEqual(status["control"]["namespace"], "data/algorithms/weighted_voting/rollout/")

    def test_feature_flags_are_independent_and_default_auto_submit_is_disabled(self) -> None:
        flags = rollout_feature_flags(
            {
                WEIGHTED_VOTING_V2_ENABLED: "true",
                WEIGHTED_VOTING_SHADOW_MODE: "false",
                WEIGHTED_VOTING_DYNAMIC_REDUCTION_ENABLED: "true",
                WEIGHTED_VOTING_DYNAMIC_INCREASE_ENABLED: "false",
                WEIGHTED_VOTING_AUTO_SUBMIT_ENABLED: "false",
            }
        )

        self.assertTrue(flags.v2_enabled)
        self.assertFalse(flags.shadow_mode)
        self.assertTrue(flags.dynamic_reduction_enabled)
        self.assertFalse(flags.dynamic_increase_enabled)
        self.assertFalse(flags.auto_submit_enabled)
        self.assertFalse(automatic_submission_allowed(flags=flags, validation=fully_validated_rollout()))

    def test_rollout_control_declares_requested_lifecycle_states(self) -> None:
        self.assertEqual(
            WEIGHTED_VOTING_ROLLOUT_STATES,
            (
                "disabled",
                "backtest_only",
                "shadow",
                "paper_trading",
                "limited_paper",
                "production_ready",
                "paused",
                "emergency_disabled",
            ),
        )
        validated_flags = WeightedVotingRolloutFlags(v2_enabled=True, dynamic_reduction_enabled=True, dynamic_increase_enabled=True, auto_submit_enabled=True)
        validated = fully_validated_rollout()

        disabled = evaluate_weighted_voting_rollout_control("disabled", flags=validated_flags, validation=validated)
        backtest_only = evaluate_weighted_voting_rollout_control("backtest_only", flags=validated_flags, validation=validated)
        shadow = evaluate_weighted_voting_rollout_control("shadow", flags=validated_flags, validation=validated)
        limited = evaluate_weighted_voting_rollout_control("limited_paper", flags=validated_flags, validation=validated)
        paper = evaluate_weighted_voting_rollout_control("paper_trading", flags=validated_flags, validation=validated)
        production_ready = evaluate_weighted_voting_rollout_control("production_ready", flags=validated_flags, validation=validated)
        paused = evaluate_weighted_voting_rollout_control("paused", flags=validated_flags, validation=validated)
        emergency = evaluate_weighted_voting_rollout_control("emergency_disabled", flags=validated_flags, validation=validated)

        self.assertFalse(disabled.trading_allowed)
        self.assertFalse(backtest_only.trading_allowed)
        self.assertFalse(shadow.trading_allowed)
        self.assertTrue(limited.paper_trading_allowed)
        self.assertFalse(limited.automatic_submission_allowed)
        self.assertTrue(paper.automatic_submission_allowed)
        self.assertTrue(production_ready.production_ready)
        self.assertFalse(paused.trading_allowed)
        self.assertFalse(emergency.trading_allowed)

    def test_disabling_other_algorithm_does_not_disable_weighted_voting(self) -> None:
        flags = WeightedVotingRolloutFlags(v2_enabled=True, dynamic_reduction_enabled=True, dynamic_increase_enabled=True, auto_submit_enabled=True)
        validation = fully_validated_rollout()

        control = evaluate_weighted_voting_rollout_control(
            "paper_trading",
            disabled_algorithm_ids=("voting_ensemble", "wca", "regime"),
            flags=flags,
            validation=validation,
        )
        emergency = evaluate_weighted_voting_rollout_control(
            "paper_trading",
            account_wide_emergency_shutdown=True,
            disabled_algorithm_ids=("voting_ensemble",),
            flags=flags,
            validation=validation,
        )
        self_disabled = evaluate_weighted_voting_rollout_control(
            "paper_trading",
            disabled_algorithm_ids=("weighted_voting",),
            flags=flags,
            validation=validation,
        )

        self.assertEqual(control.effective_state, "paper_trading")
        self.assertTrue(control.trading_allowed)
        self.assertEqual(control.ignored_external_algorithm_disables, ("regime", "voting_ensemble", "wca"))
        self.assertIn("weighted_voting.rollout.external_algorithm_disable_ignored", control.reason_codes)
        self.assertEqual(emergency.effective_state, "emergency_disabled")
        self.assertFalse(emergency.trading_allowed)
        self.assertIn("weighted_voting.rollout.account_wide_emergency_shutdown", emergency.reason_codes)
        self.assertEqual(self_disabled.effective_state, "disabled")
        self.assertFalse(self_disabled.trading_allowed)

    def test_stages_require_prior_acceptance_metrics_before_enablement(self) -> None:
        flags = WeightedVotingRolloutFlags(v2_enabled=True, shadow_mode=True, dynamic_reduction_enabled=True, dynamic_increase_enabled=True, auto_submit_enabled=True)
        validation = WeightedVotingRolloutValidation(backend_shadow_passed=True)

        shadow = evaluate_rollout_stage("shadow_comparison", flags=flags, validation=validation)
        static = evaluate_rollout_stage("static_equal_weights", flags=flags, validation=validation)

        self.assertTrue(shadow.enabled)
        self.assertFalse(static.enabled)
        self.assertIn("weighted_voting.rollout.shadow_comparison_not_validated", static.reason_codes)

    def test_later_stage_flags_gate_dynamic_reductions_increases_and_automatic_submission(self) -> None:
        validation = fully_validated_rollout()

        reduction_blocked = evaluate_rollout_stage("dynamic_reduction", flags=WeightedVotingRolloutFlags(v2_enabled=True), validation=validation)
        increase_blocked = evaluate_rollout_stage(
            "dynamic_increase",
            flags=WeightedVotingRolloutFlags(v2_enabled=True, dynamic_reduction_enabled=True, dynamic_increase_enabled=False),
            validation=validation,
        )
        automatic_blocked = evaluate_rollout_stage(
            "automatic_paper_submission",
            flags=WeightedVotingRolloutFlags(v2_enabled=True, dynamic_reduction_enabled=True, dynamic_increase_enabled=True, auto_submit_enabled=False),
            validation=validation,
        )
        automatic_enabled = evaluate_rollout_stage(
            "automatic_paper_submission",
            flags=WeightedVotingRolloutFlags(v2_enabled=True, dynamic_reduction_enabled=True, dynamic_increase_enabled=True, auto_submit_enabled=True),
            validation=validation,
        )

        self.assertIn("weighted_voting.rollout.dynamic_reduction_flag_disabled", reduction_blocked.reason_codes)
        self.assertIn("weighted_voting.rollout.dynamic_increase_flag_disabled", increase_blocked.reason_codes)
        self.assertIn("weighted_voting.rollout.auto_submit_flag_disabled", automatic_blocked.reason_codes)
        self.assertTrue(automatic_enabled.enabled)

    def test_live_trading_blocks_every_stage_even_when_flags_and_metrics_pass(self) -> None:
        flags = WeightedVotingRolloutFlags(v2_enabled=True, shadow_mode=True, dynamic_reduction_enabled=True, dynamic_increase_enabled=True, auto_submit_enabled=True)
        validation = fully_validated_rollout(live_trading_enabled=True)

        for stage in ROLLOUT_STAGES:
            with self.subTest(stage=stage):
                status = evaluate_rollout_stage(stage, flags=flags, validation=validation)
                self.assertFalse(status.enabled)
                self.assertIn("weighted_voting.rollout.live_trading_never_allowed", status.reason_codes)

    def test_rollout_status_reports_all_stages_and_auto_permission(self) -> None:
        status = rollout_status(
            flags=WeightedVotingRolloutFlags(v2_enabled=True, dynamic_reduction_enabled=True, dynamic_increase_enabled=True, auto_submit_enabled=True),
            validation=fully_validated_rollout(),
        )

        self.assertEqual(status["algorithm_id"], "weighted_voting")
        self.assertEqual(len(status["stages"]), len(ROLLOUT_STAGES))
        self.assertTrue(status["automatic_submission_allowed"])
        self.assertFalse(status["live_trading_allowed"])

    def test_rollback_restores_previous_valid_rollout_state(self) -> None:
        store = MemoryStore()
        first = record_valid_rollout_state(store, {"stage": "manual_paper_submission", "state_version": "valid-1"}, recorded_at=NOW)
        second = record_valid_rollout_state(store, {"stage": "automatic_paper_submission", "state_version": "valid-2"}, recorded_at=NOW)

        restored = rollback_weighted_voting_rollout(store, rolled_back_at=NOW)

        self.assertEqual(first["state_version"], "valid-1")
        self.assertEqual(second["state_version"], "valid-2")
        self.assertEqual(store.snapshots[ROLLBACK_STATE_KEY]["state_version"], "valid-1")
        self.assertEqual(restored["state_version"], "valid-1")
        self.assertEqual(store.snapshots[ROLLOUT_STATE_KEY]["state_version"], "valid-1")
        self.assertIn("weighted_voting.rollout.rollback_restored_previous_valid_state", restored["reason_codes"])


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot


def fully_validated_rollout(*, live_trading_enabled: bool = False) -> WeightedVotingRolloutValidation:
    return WeightedVotingRolloutValidation(
        backend_shadow_passed=True,
        shadow_comparison_passed=True,
        static_equal_weights_passed=True,
        performance_weights_validated=True,
        dynamic_reduction_validated=True,
        dynamic_entry_exit_validated=True,
        dynamic_increase_validated=True,
        manual_paper_submission_validated=True,
        tests_passed=True,
        paper_validations_passed=True,
        live_trading_enabled=live_trading_enabled,
    )


if __name__ == "__main__":
    unittest.main()
