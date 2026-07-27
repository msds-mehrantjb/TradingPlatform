from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from backend.app.algorithms.meta_strategy import (
    DIRECTIONAL_STRATEGIES,
    MetaStrategyApplicationService,
    MetaStrategyDynamicOverlaySettings,
    MetaStrategyJobRepository,
    MetaStrategySettingsStore,
    MetaStrategyStrategySettings,
    build_meta_strategy_settings,
    instantiate_meta_strategy,
    resolve_meta_strategy_effective_settings,
    run_meta_strategy_execution_pipeline,
)
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
