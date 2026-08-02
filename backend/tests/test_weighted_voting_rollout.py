from __future__ import annotations

import unittest
from datetime import datetime, timezone

from backend.app.algorithms.weighted_voting.rollout import (
    CONTROLLED_ROLLOUT_STAGES,
    ROLLOUT_AUDIT_PREFIX,
    ROLLOUT_EVIDENCE_PREFIX,
    ROLLOUT_STATE_KEY,
    ROLLOUT_VALIDATION_KEY,
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
    WeightedVotingControlledRolloutEvidence,
    automatic_submission_allowed,
    controlled_rollout_status,
    controlled_rollout_evidence_from_shadow_report,
    evaluate_controlled_rollout_promotion,
    evaluate_rollout_stage,
    evaluate_weighted_voting_rollout_control,
    load_persisted_rollout_validation,
    persist_rollout_validation_record,
    promote_controlled_rollout_stage,
    record_valid_rollout_state,
    rollback_controlled_rollout_stage,
    rollback_weighted_voting_rollout,
    rollout_feature_flags,
    rollout_status,
    small_allocation_guardrails,
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
        validated_flags = WeightedVotingRolloutFlags(v2_enabled=True, dynamic_reduction_enabled=True, dynamic_increase_enabled=False, auto_submit_enabled=True)
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
        flags = WeightedVotingRolloutFlags(v2_enabled=True, dynamic_reduction_enabled=True, dynamic_increase_enabled=False, auto_submit_enabled=True)
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
        flags = WeightedVotingRolloutFlags(v2_enabled=True, shadow_mode=True, dynamic_reduction_enabled=True, dynamic_increase_enabled=False, auto_submit_enabled=True)
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
            flags=WeightedVotingRolloutFlags(v2_enabled=True, dynamic_reduction_enabled=True, dynamic_increase_enabled=False, auto_submit_enabled=False),
            validation=validation,
        )
        automatic_enabled = evaluate_rollout_stage(
            "automatic_paper_submission",
            flags=WeightedVotingRolloutFlags(v2_enabled=True, dynamic_reduction_enabled=True, dynamic_increase_enabled=False, auto_submit_enabled=True),
            validation=validation,
        )

        self.assertIn("weighted_voting.rollout.dynamic_reduction_flag_disabled", reduction_blocked.reason_codes)
        self.assertIn("weighted_voting.rollout.dynamic_increase_flag_disabled", increase_blocked.reason_codes)
        self.assertIn("weighted_voting.rollout.auto_submit_flag_disabled", automatic_blocked.reason_codes)
        self.assertTrue(automatic_enabled.enabled)

    def test_live_trading_blocks_every_stage_even_when_flags_and_metrics_pass(self) -> None:
        flags = WeightedVotingRolloutFlags(v2_enabled=True, shadow_mode=True, dynamic_reduction_enabled=True, dynamic_increase_enabled=False, auto_submit_enabled=True)
        validation = fully_validated_rollout(live_trading_enabled=True)

        for stage in ROLLOUT_STAGES:
            with self.subTest(stage=stage):
                status = evaluate_rollout_stage(stage, flags=flags, validation=validation)
                self.assertFalse(status.enabled)
                self.assertIn("weighted_voting.rollout.live_trading_never_allowed", status.reason_codes)

    def test_rollout_status_reports_all_stages_and_auto_permission(self) -> None:
        status = rollout_status(
            flags=WeightedVotingRolloutFlags(v2_enabled=True, dynamic_reduction_enabled=True, dynamic_increase_enabled=False, auto_submit_enabled=True),
            validation=fully_validated_rollout(),
        )

        self.assertEqual(status["algorithm_id"], "weighted_voting")
        self.assertEqual(len(status["stages"]), len(ROLLOUT_STAGES))
        self.assertTrue(status["automatic_submission_allowed"])
        self.assertFalse(status["live_trading_allowed"])

    def test_automatic_submission_requires_persisted_backend_validation_and_env_flag(self) -> None:
        store = MemoryStore()
        flags = WeightedVotingRolloutFlags(v2_enabled=True, shadow_mode=False, dynamic_reduction_enabled=True, dynamic_increase_enabled=False, auto_submit_enabled=True)
        store.write_snapshot(
            ROLLOUT_STATE_KEY,
            {
                "algorithm_id": "weighted_voting",
                "rollout_version": "weighted_voting_rollout_v2",
                "stage": "automatic_paper_small_allocation",
                "status": "valid",
                "automatic_paper_submission_allowed": True,
            },
        )

        missing = automatic_submission_allowed(flags=flags, store=store)
        record = persist_rollout_validation_record(
            store,
            fully_validated_rollout(),
            source_authority="backend.weighted_voting.validation_worker",
            approved_by="ops-user",
            recorded_at=NOW,
        )
        allowed = automatic_submission_allowed(flags=flags, store=store)
        env_blocked = automatic_submission_allowed(flags=WeightedVotingRolloutFlags(v2_enabled=True, auto_submit_enabled=False), store=store)

        self.assertFalse(missing)
        self.assertTrue(allowed)
        self.assertFalse(env_blocked)
        self.assertEqual(store.snapshots[ROLLOUT_VALIDATION_KEY]["validation_record_id"], record["validation_record_id"])
        self.assertTrue(load_persisted_rollout_validation(store).persisted_operator_approval)

    def test_dynamic_increase_validation_is_not_required_for_auto_paper(self) -> None:
        validation = fully_validated_rollout()
        validation = WeightedVotingRolloutValidation(**{**validation.model_dump(), "dynamic_increase_validated": False})
        flags = WeightedVotingRolloutFlags(v2_enabled=True, shadow_mode=False, dynamic_reduction_enabled=True, dynamic_increase_enabled=False, auto_submit_enabled=True)

        status = evaluate_rollout_stage("automatic_paper_submission", flags=flags, validation=validation)

        self.assertTrue(status.enabled)

    def test_frontend_cannot_mark_rollout_validation_passed(self) -> None:
        store = MemoryStore()

        with self.assertRaises(ValueError):
            persist_rollout_validation_record(
                store,
                fully_validated_rollout(),
                source_authority="frontend.react.button",
                approved_by="ops-user",
                recorded_at=NOW,
            )

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

    def test_controlled_rollout_stages_are_explicit_and_default_auto_submit_is_disabled(self) -> None:
        store = MemoryStore()
        status = controlled_rollout_status(store)
        legacy_status = rollout_status()

        self.assertEqual(
            CONTROLLED_ROLLOUT_STAGES,
            (
                "disabled",
                "background_observation",
                "shadow_decisions",
                "manual_paper_submission",
                "automatic_paper_small_allocation",
                "automatic_paper_approved_allocation",
            ),
        )
        self.assertEqual(status["active_stage"], "background_observation")
        self.assertFalse(status["automatic_paper_submission_allowed"])
        self.assertFalse(automatic_submission_allowed(store=store))
        self.assertEqual(tuple(legacy_status["controlled_stages"]), CONTROLLED_ROLLOUT_STAGES)
        self.assertIn("weighted_voting.rollout.successful_build_not_approval", status["reason_codes"])

    def test_promotion_requires_immutable_evidence_and_persists_stage(self) -> None:
        store = MemoryStore()
        blocked, blockers = evaluate_controlled_rollout_promotion(
            current_stage="manual_paper_submission",
            target_stage="automatic_paper_small_allocation",
            evidence=WeightedVotingControlledRolloutEvidence(no_unresolved_isolation_failures=True, global_risk_fail_closed_tests_passing=True, shadow_opportunity_count=100),
        )
        self.assertFalse(blocked)
        self.assertIn("weighted_voting.rollout.inventory_unreconciled", blockers)
        promotion = promote_controlled_rollout_stage(
            store,
            target_stage="shadow_decisions",
            evidence=passing_evidence(shadow_opportunity_count=75),
            actor="ops-user",
            promoted_at=NOW,
        )

        self.assertTrue(promotion.promoted)
        self.assertEqual(store.snapshots[ROLLOUT_STATE_KEY]["stage"], "shadow_decisions")
        self.assertFalse(store.snapshots[ROLLOUT_STATE_KEY]["automatic_paper_submission_allowed"])
        self.assertTrue(any(key.startswith(ROLLOUT_EVIDENCE_PREFIX) for key in store.snapshots))
        self.assertTrue(any(key.startswith(ROLLOUT_AUDIT_PREFIX) for key in store.snapshots))

    def test_small_allocation_stage_caps_and_stops_after_reconciliation_discrepancy(self) -> None:
        guardrails = small_allocation_guardrails()
        store = MemoryStore()
        store.write_snapshot(
            ROLLOUT_STATE_KEY,
            {
                "algorithm_id": "weighted_voting",
                "rollout_version": "weighted_voting_rollout_v2",
                "stage": "manual_paper_submission",
                "status": "valid",
                "automatic_paper_submission_allowed": False,
            },
        )

        promotion = promote_controlled_rollout_stage(
            store,
            target_stage="automatic_paper_small_allocation",
            evidence=passing_evidence(shadow_opportunity_count=100, manual_paper_sample_count=25),
            actor="ops-user",
            promoted_at=NOW,
        )
        status = controlled_rollout_status(store)

        self.assertTrue(promotion.promoted)
        self.assertTrue(status["automatic_paper_submission_allowed"])
        self.assertEqual(status["small_allocation_guardrails"]["cap_quantity"], guardrails.cap_quantity)
        self.assertEqual(status["small_allocation_guardrails"]["cap_daily_trades"], 2)
        self.assertFalse(status["small_allocation_guardrails"]["pyramiding_enabled"])
        self.assertLessEqual(status["small_allocation_guardrails"]["maximum_spread_percent"], 0.0005)
        self.assertTrue(status["small_allocation_guardrails"]["stop_entries_after_reconciliation_discrepancy"])
        self.assertEqual(tuple(status["small_allocation_guardrails"]["approved_active_strategy_ids"]), ("S2", "S5", "S6", "S7"))

    def test_shadow_report_evidence_does_not_skip_to_small_automatic_allocation(self) -> None:
        report = shadow_report(decisions=12)
        evidence = controlled_rollout_evidence_from_shadow_report(report)

        promoted, blockers = evaluate_controlled_rollout_promotion(
            current_stage="manual_paper_submission",
            target_stage="automatic_paper_small_allocation",
            evidence=evidence,
        )

        self.assertFalse(promoted)
        self.assertEqual(evidence.shadow_opportunity_count, 12)
        self.assertEqual(evidence.manual_paper_sample_count, 0)
        self.assertFalse(evidence.automated_paper_readiness_detected)
        self.assertIn("weighted_voting.rollout.shadow_opportunity_minimum_not_met", blockers)
        self.assertIn("weighted_voting.rollout.automated_paper_readiness_not_detected", blockers)

    def test_shadow_report_with_automated_readiness_can_promote_small_allocation_without_manual_paper(self) -> None:
        store = MemoryStore()
        store.write_snapshot(
            ROLLOUT_STATE_KEY,
            {
                "algorithm_id": "weighted_voting",
                "rollout_version": "weighted_voting_rollout_v2",
                "stage": "shadow_decisions",
                "status": "valid",
                "automatic_paper_submission_allowed": False,
            },
        )
        evidence = controlled_rollout_evidence_from_shadow_report(
            shadow_report(decisions=55),
        )

        promotion = promote_controlled_rollout_stage(
            store,
            target_stage="automatic_paper_small_allocation",
            evidence=evidence,
            actor="ops-user",
            promoted_at=NOW,
        )

        self.assertTrue(promotion.promoted)
        self.assertEqual(evidence.manual_paper_sample_count, 0)
        self.assertTrue(evidence.automated_paper_readiness_detected)
        self.assertTrue(store.snapshots[ROLLOUT_STATE_KEY]["automatic_paper_submission_allowed"])
        self.assertEqual(store.snapshots[ROLLOUT_STATE_KEY]["small_allocation_guardrails"]["cap_quantity"], 10)

    def test_controlled_rollback_is_immediate_and_safe(self) -> None:
        store = MemoryStore()
        store.write_snapshot(
            ROLLOUT_STATE_KEY,
            {
                "algorithm_id": "weighted_voting",
                "rollout_version": "weighted_voting_rollout_v2",
                "stage": "automatic_paper_small_allocation",
                "status": "valid",
                "automatic_paper_submission_allowed": True,
                "reason_codes": ("weighted_voting.rollout.stage_persisted",),
            },
        )
        store.write_snapshot(
            ROLLBACK_STATE_KEY,
            {
                "algorithm_id": "weighted_voting",
                "rollout_version": "weighted_voting_rollout_v2",
                "stage": "manual_paper_submission",
                "status": "valid",
                "automatic_paper_submission_allowed": False,
                "reason_codes": ("weighted_voting.rollout.stage_persisted",),
            },
        )

        restored = rollback_controlled_rollout_stage(store, actor="ops-user", rolled_back_at=NOW)

        self.assertEqual(restored["stage"], "manual_paper_submission")
        self.assertFalse(restored["automatic_paper_submission_allowed"])
        self.assertEqual(store.snapshots[ROLLOUT_STATE_KEY]["stage"], "manual_paper_submission")
        self.assertTrue(any(key.startswith(f"{ROLLOUT_AUDIT_PREFIX}rollback.") for key in store.snapshots))


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
        paper_broker_e2e_validated=True,
        reconciliation_validated=True,
        restart_recovery_validated=True,
        persisted_operator_approval=True,
        validation_record_id="weighted_voting.rollout.validation.test",
        source_authority="backend.weighted_voting.test_validation_worker",
        approved_by="ops-user",
        recorded_at=NOW.isoformat(),
        live_trading_enabled=live_trading_enabled,
    )


def passing_evidence(*, shadow_opportunity_count: int = 100, manual_paper_sample_count: int = 25) -> WeightedVotingControlledRolloutEvidence:
    return WeightedVotingControlledRolloutEvidence(
        no_unresolved_isolation_failures=True,
        inventory_reconciled=True,
        no_duplicate_order_incidents=True,
        worker_reliability_ok=True,
        decision_latency_ok=True,
        broker_latency_ok=True,
        data_freshness_stable=True,
        global_risk_fail_closed_tests_passing=True,
        restart_recovery_successful=True,
        shadow_opportunity_count=shadow_opportunity_count,
        manual_paper_sample_count=manual_paper_sample_count,
        transaction_cost_adjusted_paper_stability_ok=True,
        drawdown_within_limit=True,
        position_pnl_attribution_accurate=True,
        protective_order_reliability_ok=True,
        explicit_configuration_approval=True,
        automated_paper_readiness_detected=True,
        evidence_id="weighted_voting.rollout.evidence.test",
    )


def shadow_report(*, decisions: int) -> dict:
    return {
        "algorithmId": "weighted_voting",
        "runId": "weighted-voting-shadow-test",
        "liveOrdersSubmitted": False,
        "decisions": {"count": decisions},
        "acceptedProposals": {"count": 1},
        "latency": {"maxLatencyMs": 20.0},
        "simulatedFills": {"observedSlippagePerShare": 0.01},
        "pnl": {"netUnrealizedAfterFees": 10.0},
        "duplicatePrevention": {"duplicateEventPrevented": True},
        "reconciliationHealth": {
            "inventoryReconciled": True,
            "entriesPaused": False,
            "discrepancyCount": 0,
            "runtimeHealth": {
                "recoveryRequired": False,
            },
        },
        "restartRecovery": {
            "passed": True,
        },
        "protectiveOrderBehavior": {
            "passed": True,
        },
        "runtimeContexts": {
            "items": [
                {
                    "read_only_account_equity_available": True,
                    "read_only_broker_buying_power_available": True,
                    "global_risk_service_available": True,
                }
                for _ in range(decisions)
            ],
        },
        "globalGateApplications": {"count": decisions},
    }


if __name__ == "__main__":
    unittest.main()
