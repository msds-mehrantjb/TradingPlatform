from __future__ import annotations

import unittest

from backend.app.algorithms.meta_strategy import (
    META_STRATEGY_EXECUTION_PIPELINE_STAGES,
    MetaStrategyExecutionPipelineConfig,
    MetaStrategyExecutionPipelineRequest,
    NoopMetaStrategyBrokerAdapter,
    pipeline_modes_using_authoritative_sequence,
    run_meta_strategy_execution_pipeline,
)
from backend.tests.test_meta_strategy_step7_market_snapshot import request_with


class RecordingBroker(NoopMetaStrategyBrokerAdapter):
    def __init__(self) -> None:
        self.calls: list[tuple[object, str]] = []

    def submit(self, order_intent, *, mode):  # noqa: ANN001
        self.calls.append((order_intent, mode))
        return super().submit(order_intent, mode=mode)


class RecordingPersistence:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def persist(self, payload: dict) -> dict:
        self.payloads.append(payload)
        return {"status": "PERSISTED", "recordId": payload["decisionId"], "reasonCodes": ("test.persisted",)}


class ReducingGlobalRisk:
    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.calls: list[tuple[object, int]] = []

    def apply(self, order_intent, *, requested_quantity: int) -> dict:  # noqa: ANN001
        self.calls.append((order_intent, requested_quantity))
        return {
            "status": "PASS" if order_intent is not None else "NO_ORDER",
            "requestedQuantity": requested_quantity,
            "approvedQuantity": min(self.cap, requested_quantity),
            "reasonCodes": ("test.global_risk_cap",),
        }


class MetaStrategyStep31ExecutionPipelineTest(unittest.TestCase):
    def test_pipeline_stage_order_matches_required_authoritative_sequence(self) -> None:
        expected = (
            "market_snapshot",
            "strategies",
            "context_and_regime",
            "safety",
            "family_aggregation",
            "deterministic_candidate",
            "candidate_geometry",
            "feature_builder",
            "artifact_validation",
            "model_inference",
            "ml_decision_policy",
            "local_gates",
            "dynamic_profile",
            "sizing",
            "order_intent",
            "global_risk",
            "final_validation",
            "broker_adapter",
            "persistence",
            "reconciliation",
        )

        self.assertEqual(META_STRATEGY_EXECUTION_PIPELINE_STAGES, expected)

    def test_every_mode_uses_the_same_pipeline_sequence(self) -> None:
        sequences = pipeline_modes_using_authoritative_sequence()

        self.assertEqual(
            set(sequences),
            {"EVALUATION", "SHADOW", "PAPER", "BACKTEST", "DAILY_REPLAY", "DIAGNOSTICS", "LIVE"},
        )
        self.assertTrue(all(sequence == META_STRATEGY_EXECUTION_PIPELINE_STAGES for sequence in sequences.values()))

    def test_evaluation_shadow_paper_backtest_replay_diagnostics_and_live_traverse_same_pipeline(self) -> None:
        for mode in ("EVALUATION", "SHADOW", "PAPER", "BACKTEST", "DAILY_REPLAY", "DIAGNOSTICS", "LIVE"):
            with self.subTest(mode=mode):
                broker = RecordingBroker()
                persistence = RecordingPersistence()
                global_risk = ReducingGlobalRisk(cap=0)
                result = run_meta_strategy_execution_pipeline(
                    MetaStrategyExecutionPipelineRequest(mode=mode, snapshot_request=request_with()),
                    broker_adapter=broker,
                    persistence_adapter=persistence,
                    global_risk_adapter=global_risk,
                )

                self.assertEqual(result.stage_sequence, META_STRATEGY_EXECUTION_PIPELINE_STAGES)
                self.assertEqual(tuple(result.stage_results), META_STRATEGY_EXECUTION_PIPELINE_STAGES)
                self.assertEqual(result.mode, mode)
                self.assertIsNotNone(result.snapshot)
                self.assertIsNotNone(result.deterministic_candidate)
                self.assertIsNotNone(result.geometry)
                self.assertIsNotNone(result.features)
                self.assertIsNotNone(result.inference)
                self.assertIsNotNone(result.local_gates)
                self.assertIsNotNone(result.dynamic_profile)
                self.assertIsNotNone(result.sizing)
                self.assertTrue(global_risk.calls)
                self.assertTrue(broker.calls)
                self.assertTrue(persistence.payloads)
                self.assertEqual(persistence.payloads[0]["stageSequence"], META_STRATEGY_EXECUTION_PIPELINE_STAGES)

    def test_live_trading_requires_separate_enablement(self) -> None:
        result = run_meta_strategy_execution_pipeline(
            MetaStrategyExecutionPipelineRequest(mode="LIVE", snapshot_request=request_with()),
            config=MetaStrategyExecutionPipelineConfig(live_trading_enabled=False),
        )

        self.assertIn("meta_strategy.pipeline.live_trading_not_enabled", result.reason_codes)
        self.assertEqual(result.broker_result["status"], "NO_ORDER")
        self.assertFalse(result.broker_result["submitted"])

    def test_global_risk_and_broker_stages_cannot_bypass_zero_sizing(self) -> None:
        result = run_meta_strategy_execution_pipeline(
            MetaStrategyExecutionPipelineRequest(mode="PAPER", snapshot_request=request_with()),
            global_risk_adapter=ReducingGlobalRisk(cap=999),
        )

        if result.sizing.quantity == 0:
            self.assertIsNone(result.order_intent)
            self.assertEqual(result.global_risk["approvedQuantity"], 0)
            self.assertEqual(result.broker_result["status"], "NO_ORDER")
            self.assertIsNone(result.reconciliation)


if __name__ == "__main__":
    unittest.main()
