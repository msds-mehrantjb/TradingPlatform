from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from backend.app.algorithms.meta_strategy import (
    DIRECTIONAL_STRATEGIES,
    MetaStrategyApplicationService,
    MetaStrategyDynamicOverlaySettings,
    MetaStrategyExecutionPipelineConfig,
    MetaStrategyJobRepository,
    MetaStrategySettingsStore,
    MetaStrategyStrategySettings,
    build_meta_strategy_conservative_paper_settings,
    build_meta_strategy_settings,
    instantiate_meta_strategy,
    resolve_meta_strategy_effective_settings,
    run_meta_strategy_execution_pipeline,
)
from backend.app.algorithms.meta_strategy.inference import MetaStrategyInferenceConfig
from backend.app.algorithms.meta_strategy.feature_schema import meta_strategy_feature_schema_hash
from backend.app.algorithms.meta_strategy.execution_pipeline import MetaStrategyExecutionPipelineRequest
from backend.tests.test_meta_strategy_step7_market_snapshot import request_with


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)
TEST_TMP_DIR = Path("backend") / ".pytest_meta_strategy_phase3"


class MetaStrategyPhase3SettingsTest(unittest.TestCase):
    maxDiff = None

    def test_baseline_creation_activation_promotion_and_rollback_are_persistent(self) -> None:
        db_path = _test_db_path("lifecycle")
        store = MetaStrategySettingsStore(db_path)
        baseline = build_meta_strategy_settings(settings_version="baseline-v1", created_at=NOW)
        draft = baseline.model_copy(update={"settings_version": "draft-v2"})

        created = store.create_baseline(baseline, actor="system")
        store.activate_settings(created.settings_version, actor="system")
        store.create_draft(draft, actor="ops")
        promoted = store.promote_draft("draft-v2", actor="ops", validation_evidence={"validated": True, "changeTicket": "MS-3"})
        rolled_back = store.rollback_to("baseline-v1", actor="ops", reason="regression")

        reopened = MetaStrategySettingsStore(db_path)
        self.assertEqual(promoted.promoted_settings_version, "draft-v2")
        self.assertEqual(rolled_back.restored_settings_version, "baseline-v1")
        self.assertEqual(reopened.get_active_settings().settings_version, "baseline-v1")
        self.assertEqual(len(reopened.promotion_history()), 1)
        self.assertEqual(len(reopened.rollback_history()), 1)

    def test_invalid_threshold_and_cap_rejection(self) -> None:
        with self.assertRaises(ValidationError):
            MetaStrategyStrategySettings(buy_threshold=1.50)

        with self.assertRaises(ValidationError):
            build_meta_strategy_settings(local_risk={"risk_percentage": -0.01})

        with self.assertRaises(ValidationError):
            build_meta_strategy_settings(candidate_aggregation={"minimum_independent_families": 1})

        validated_exception = build_meta_strategy_settings(
            candidate_aggregation={
                "minimum_independent_families": 1,
                "independent_family_exception": {"validated": True, "settingsVersion": "exception-v1", "evidenceId": "validated-one-family-profile"},
            }
        )
        self.assertEqual(validated_exception.candidate_aggregation.minimum_independent_families, 1)

    def test_dynamic_overlay_cannot_exceed_baseline_risk_caps(self) -> None:
        baseline = build_meta_strategy_settings(created_at=NOW)

        with self.assertRaisesRegex(ValueError, "risk"):
            resolve_meta_strategy_effective_settings(
                baseline,
                MetaStrategyDynamicOverlaySettings(risk_multiplier=1.20, reason="bad-risk"),
                calculated_at=NOW,
            )
        with self.assertRaisesRegex(ValueError, "spread"):
            resolve_meta_strategy_effective_settings(
                baseline,
                MetaStrategyDynamicOverlaySettings(spread_limit_bps=baseline.local_risk.spread_limit_bps + 1.0, reason="bad-spread"),
                calculated_at=NOW,
            )

        effective = resolve_meta_strategy_effective_settings(
            baseline,
            MetaStrategyDynamicOverlaySettings(
                risk_multiplier=0.50,
                position_size_multiplier=0.50,
                trade_count_limit=max(0, baseline.local_risk.trade_count_limit - 1),
                allowed_sessions=("OPENING", "MORNING"),
                evidence_threshold_increase=0.05,
                reason="defensive",
            ),
            calculated_at=NOW,
        )
        self.assertLessEqual(effective.local_risk.risk_percentage, baseline.local_risk.risk_percentage)
        self.assertLessEqual(effective.position_sizing.position_cap, baseline.position_sizing.position_cap)
        self.assertLessEqual(effective.local_risk.trade_count_limit, baseline.local_risk.trade_count_limit)
        self.assertEqual(effective.sessions.allowed_sessions, ("OPENING", "MORNING"))
        self.assertTrue(effective.dynamic_overlay_changes)
        self.assertTrue(all(change["reason"] == "defensive" for change in effective.dynamic_overlay_changes))

    def test_dedicated_settings_groups_and_conservative_paper_profile_are_versioned(self) -> None:
        store = MetaStrategySettingsStore(_test_db_path("paper-profile"))
        baseline = build_meta_strategy_settings(settings_version="baseline-before-paper", created_at=NOW)
        store.create_baseline(baseline, actor="system")
        store.activate_settings(baseline.settings_version, actor="system")

        profile = build_meta_strategy_conservative_paper_settings(
            settings_version="paper-conservative-v1",
            created_at=NOW,
            alpaca_paper_configured=True,
        )
        promotion = store.create_and_promote_paper_baseline(
            settings_version="paper-conservative-v2",
            actor="ops",
            alpaca_paper_configured=True,
        )
        active = store.get_active_settings()

        self.assertEqual(store.get_settings("baseline-before-paper").settings_version, "baseline-before-paper")
        self.assertEqual(promotion.promoted_settings_version, "paper-conservative-v2")
        self.assertEqual(active.settings_version, "paper-conservative-v2")
        self.assertEqual(profile.paper_execution.execution_mode, "PAPER")
        self.assertFalse(profile.paper_execution.synthetic_immediate_fills_allowed)
        self.assertFalse(profile.paper_execution.local_diagnostics_only)
        self.assertEqual(profile.ml_inference.mode, "DISABLED")
        self.assertEqual(profile.order_construction.order_type, "MARKETABLE_LIMIT")
        self.assertTrue(profile.position_management.one_position_per_symbol)
        self.assertTrue(profile.position_management.mandatory_end_of_day_handling)
        self.assertGreater(profile.position_management.no_new_entry_minutes_before_close, 0)
        self.assertLessEqual(profile.local_risk.risk_percentage, 0.001)
        self.assertLessEqual(profile.position_sizing.position_cap, 0.02)
        self.assertLessEqual(profile.position_sizing.maximum_share_quantity, 100)
        self.assertLessEqual(profile.local_risk.trade_count_limit, 3)
        self.assertLessEqual(profile.local_risk.maximum_daily_loss, 250.0)
        self.assertLessEqual(profile.local_risk.maximum_open_risk, 500.0)
        self.assertLessEqual(profile.local_risk.spread_limit_bps, 8.0)
        self.assertGreaterEqual(profile.local_risk.liquidity_requirement, 250_000.0)
        self.assertGreaterEqual(profile.local_risk.minimum_reward_to_risk, 1.5)
        self.assertTrue(profile.economic_event_rules.block_high_impact_events)
        self.assertTrue(profile.operational_limits.block_orders_when_unhealthy)

    def test_effective_settings_version_and_hash_pin_order_intent(self) -> None:
        settings = build_meta_strategy_conservative_paper_settings(
            settings_version="paper-pin-v1",
            created_at=NOW,
            ml_mode="DISABLED",
        )
        result = run_meta_strategy_execution_pipeline(
            MetaStrategyExecutionPipelineRequest(
                mode="PAPER",
                snapshot_request=request_with(),
                global_available_risk=10_000.0,
                global_quantity_cap=10,
                account_equity=100_000.0,
                available_buying_power=100_000.0,
                remaining_algorithm_risk=10_000.0,
                model_artifact={
                    "featureSchemaHash": meta_strategy_feature_schema_hash(),
                    "championModel": "none",
                    "models": {},
                },
            ),
            config=MetaStrategyExecutionPipelineConfig(
                settings=settings,
                baseline_settings=settings.to_baseline_settings(),
                inference_config=MetaStrategyInferenceConfig(mode="DISABLED", fallbackBehavior="NO_TRADE"),
                submit_to_broker=False,
            ),
        )

        self.assertEqual(result.snapshot.settings_version, settings.settings_version)
        self.assertEqual(result.settings_version, settings.settings_version)
        if result.order_intent is not None:
            self.assertEqual(result.order_intent.settings_version, settings.settings_version)
            self.assertEqual(result.order_intent.effective_settings_hash, settings.effective_settings_hash)
            self.assertEqual(result.order_intent.order_type, "LIMIT")

    def test_strategy_registry_injects_typed_settings(self) -> None:
        baseline = build_meta_strategy_settings(created_at=NOW)
        entry = next(item for item in DIRECTIONAL_STRATEGIES if item.strategy_id == "opening_range_breakout")
        strategy = instantiate_meta_strategy(entry, baseline)

        self.assertEqual(strategy.settings_version, baseline.settings_version)
        self.assertEqual(strategy.buy_threshold, baseline.directional_strategies["opening_range_breakout"].buy_threshold)
        self.assertEqual(strategy.minimum_warmup, baseline.directional_strategies["opening_range_breakout"].minimum_warmup)

    def test_settings_version_is_recorded_with_runtime_decisions(self) -> None:
        settings = build_meta_strategy_settings(settings_version="runtime-settings-v1", created_at=NOW)
        result = run_meta_strategy_execution_pipeline(
            MetaStrategyExecutionPipelineRequest(mode="PAPER", snapshot_request=request_with()),
            config_settings=settings,
        )

        self.assertEqual(result.snapshot.settings_version, "runtime-settings-v1")
        self.assertEqual(result.snapshot.effective_settings_hash, result.effective_settings_hash)
        self.assertEqual(result.stage_results["market_snapshot"]["settingsVersion"], "runtime-settings-v1")
        self.assertEqual(result.persistence_result["settingsVersion"], "runtime-settings-v1")

    def test_service_configuration_reads_and_writes_persisted_settings_without_request_overrides(self) -> None:
        store = MetaStrategySettingsStore(_test_db_path("service"))
        jobs = MetaStrategyJobRepository(f"sqlite:///{_test_db_path('service-jobs').resolve()}")
        service = MetaStrategyApplicationService(settings_store=store, job_repository=jobs)
        draft = build_meta_strategy_settings(settings_version="service-draft-v2", created_at=NOW)

        create_result = service.create_settings_draft({"settings": draft.model_dump(mode="json"), "actor": "ops"})
        promote_result = service.promote_settings_draft({"settingsVersion": "service-draft-v2", "actor": "ops", "validationEvidence": {"validated": True}})
        store.promote_draft("service-draft-v2", actor="settings-worker", validation_evidence={"validated": True})
        config_result = service.configuration()
        evaluation_result = service.paper_evaluate({"snapshotRequest": request_with().model_dump(mode="json"), "settingsVersion": "client-hidden-override"})

        self.assertEqual(create_result["status"], "OK")
        self.assertEqual(promote_result["status"], "OK")
        self.assertEqual(promote_result["payload"]["job"]["queueName"], "promotion")
        self.assertIn("meta_strategy.service.settings_promotion_job_queued", promote_result["reasonCodes"])
        self.assertEqual(config_result["payload"]["activeSettings"]["settingsVersion"], "service-draft-v2")
        self.assertEqual(evaluation_result["status"], "REJECTED")
        self.assertIn("settingsVersion", evaluation_result["payload"]["rejectedFields"])
        self.assertIn("meta_strategy.api.request_settings_override_rejected", evaluation_result["reasonCodes"])

    def test_meta_strategy_settings_do_not_modify_other_algorithm_state(self) -> None:
        store = MetaStrategySettingsStore(_test_db_path("isolation"))
        baseline = build_meta_strategy_settings(settings_version="isolated-v1", created_at=NOW)
        store.create_baseline(baseline, actor="system")

        with self.assertRaisesRegex(ValueError, "algorithm"):
            store.create_baseline(baseline.model_copy(update={"algorithm_id": "weighted_voting"}), actor="system")
        self.assertEqual(store.get_settings("isolated-v1").algorithm_id, "meta_strategy")


def _test_db_path(name: str) -> Path:
    TEST_TMP_DIR.mkdir(exist_ok=True)
    path = TEST_TMP_DIR / f"{name}.db"
    if path.exists():
        path.unlink()
    return path


if __name__ == "__main__":
    unittest.main()
