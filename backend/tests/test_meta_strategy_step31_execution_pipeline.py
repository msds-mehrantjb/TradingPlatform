from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.app.algorithms.meta_strategy import (
    META_STRATEGY_EXECUTION_PIPELINE_STAGES,
    MetaStrategyExecutionPipelineConfig,
    MetaStrategyExecutionPipelineRequest,
    NoopMetaStrategyBrokerAdapter,
    pipeline_modes_using_authoritative_sequence,
    run_meta_strategy_execution_pipeline,
)
from backend.app.algorithms.meta_strategy.candidate_generator import CandidateComponentEvaluation, CandidateGenerationConfig
from backend.app.algorithms.meta_strategy.family_aggregation import FamilyAggregationConfig, aggregate_family_scores
from backend.app.algorithms.meta_strategy.settings import build_meta_strategy_settings
from backend.app.algorithms.meta_strategy.strategies.base import SnapshotEvaluationResult
from backend.app.algorithms.meta_strategy.strategy_registry import CONTEXT_STRATEGIES, DIRECTIONAL_STRATEGIES, REGIME_STRATEGIES, SAFETY_STRATEGIES
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

    def test_phase5_required_stages_persist_concrete_contract_outputs(self) -> None:
        with patch(
            "backend.app.algorithms.meta_strategy.execution_pipeline.evaluate_candidate_components",
            return_value=phase5_components(),
        ):
            result = run_meta_strategy_execution_pipeline(
                MetaStrategyExecutionPipelineRequest(mode="EVALUATION", snapshot_request=request_with()),
                global_risk_adapter=ReducingGlobalRisk(cap=0),
            )

        for stage in ("strategies", "context_and_regime", "safety", "family_aggregation"):
            with self.subTest(stage=stage):
                payload = result.stage_results[stage]
                self.assertEqual({"status", "eligible", "inputVersion", "outputVersion", "startedAt", "completedAt", "durationMs", "reasonCodes", "evidence"} <= set(payload), True)
                self.assertNotEqual(payload["status"], "captured_by_deterministic_candidate_stage")
                self.assertEqual(payload["durationMs"], 0)

        strategy = result.stage_results["strategies"]["evidence"]["strategyOutputs"][0]
        self.assertEqual({"strategyId", "strategyVersion", "familyId", "signal", "confidence", "eligible", "dataQuality", "evidence", "vetoes", "reasonCodes", "evaluatedAt"} <= set(strategy), True)
        self.assertFalse(result.stage_results["strategies"]["evidence"]["genericSessionDirectionFallbackUsed"])
        self.assertIn("qqqVsIwmRatio", result.stage_results["context_and_regime"]["evidence"]["contextOutputs"][1]["evidence"])
        self.assertIn("hardVetoes", result.stage_results["safety"]["evidence"])
        self.assertIn("familyScores", result.stage_results["family_aggregation"]["evidence"])

    def test_phase5_hard_stage_failure_prevents_order_and_downstream_success(self) -> None:
        with patch(
            "backend.app.algorithms.meta_strategy.execution_pipeline.evaluate_candidate_components",
            return_value=phase5_components(),
        ):
            result = run_meta_strategy_execution_pipeline(
                MetaStrategyExecutionPipelineRequest(mode="EVALUATION", snapshot_request=request_with(), event_blackout=True),
                global_risk_adapter=ReducingGlobalRisk(cap=99),
            )

        self.assertFalse(result.stage_results["safety"]["eligible"])
        self.assertFalse(result.inference.hardGatesPassed)
        self.assertIsNone(result.order_intent)
        self.assertEqual(result.stage_results["order_intent"]["status"], "NO_ORDER")
        self.assertIn("meta_strategy.pipeline.required_stage_blocked.safety", result.stage_results["order_intent"]["reasonCodes"])

    def test_phase5_duplicate_family_votes_are_bounded(self) -> None:
        with patch(
            "backend.app.algorithms.meta_strategy.execution_pipeline.evaluate_candidate_components",
            return_value=phase5_components(),
        ):
            result = run_meta_strategy_execution_pipeline(
                MetaStrategyExecutionPipelineRequest(mode="EVALUATION", snapshot_request=request_with()),
                global_risk_adapter=ReducingGlobalRisk(cap=0),
            )

        evidence = result.stage_results["family_aggregation"]["evidence"]
        self.assertEqual(evidence["activeStrategyCount"], 3)
        self.assertEqual(evidence["activeFamilyCount"], 2)
        self.assertEqual(set(evidence["familyScores"]), {"BREAKOUT", "TREND"})
        self.assertLessEqual(evidence["familyScores"]["TREND"]["buyScore"], 0.60)
        self.assertIn("multi_timeframe_trend_alignment", evidence["correlationPenalties"])

    def test_phase5_required_family_count_is_enforced(self) -> None:
        blocked = phase5_components(minimum_independent_families=3)
        with patch(
            "backend.app.algorithms.meta_strategy.execution_pipeline.evaluate_candidate_components",
            return_value=blocked,
        ):
            result = run_meta_strategy_execution_pipeline(
                MetaStrategyExecutionPipelineRequest(mode="EVALUATION", snapshot_request=request_with()),
                global_risk_adapter=ReducingGlobalRisk(cap=99),
            )

        self.assertFalse(result.stage_results["family_aggregation"]["eligible"])
        self.assertIn("meta_strategy.aggregation.minimum_independent_families", result.stage_results["family_aggregation"]["reasonCodes"])
        self.assertIsNone(result.order_intent)
        self.assertIn("meta_strategy.pipeline.required_stage_blocked.family_aggregation", result.stage_results["order_intent"]["reasonCodes"])

    def test_phase5_stage_results_are_deterministic_for_identical_input(self) -> None:
        components = phase5_components()
        settings = build_meta_strategy_settings(status="ACTIVE")
        config = MetaStrategyExecutionPipelineConfig(settings=settings, baseline_settings=settings.to_baseline_settings())
        with patch(
            "backend.app.algorithms.meta_strategy.execution_pipeline.evaluate_candidate_components",
            return_value=components,
        ):
            first = run_meta_strategy_execution_pipeline(MetaStrategyExecutionPipelineRequest(mode="EVALUATION", snapshot_request=request_with()), config=config)
            second = run_meta_strategy_execution_pipeline(MetaStrategyExecutionPipelineRequest(mode="EVALUATION", snapshot_request=request_with()), config=config)

        for stage in ("strategies", "context_and_regime", "safety", "family_aggregation"):
            self.assertEqual(first.stage_results[stage], second.stage_results[stage])

    def test_phase6_hard_gate_failure_is_passed_to_inference_as_false(self) -> None:
        with patch(
            "backend.app.algorithms.meta_strategy.execution_pipeline.evaluate_candidate_components",
            return_value=phase5_components(),
        ):
            result = run_meta_strategy_execution_pipeline(
                MetaStrategyExecutionPipelineRequest(mode="EVALUATION", snapshot_request=request_with(), event_blackout=True),
                config=phase6_config("FILTER", fallback_behavior="NO_TRADE"),
                global_risk_adapter=ReducingGlobalRisk(cap=99),
            )

        self.assertFalse(result.stage_results["safety"]["eligible"])
        self.assertFalse(result.stage_results["model_inference"]["hardGatesPassed"])
        self.assertFalse(result.inference.hardGatesPassed)
        self.assertFalse(result.inference.candidateAccepted)
        self.assertIsNone(result.order_intent)

    def test_phase6_model_cannot_restore_ineligible_candidate(self) -> None:
        schema_hash = phase6_feature_schema_hash()
        with patch(
            "backend.app.algorithms.meta_strategy.execution_pipeline.evaluate_candidate_components",
            return_value=phase5_components(minimum_independent_families=3),
        ):
            result = run_meta_strategy_execution_pipeline(
                MetaStrategyExecutionPipelineRequest(
                    mode="EVALUATION",
                    snapshot_request=request_with(),
                    model_artifact=phase6_artifact(schema_hash=schema_hash, probabilities={"BUY": 0.99, "SELL": 0.0, "HOLD": 0.01}, promoted=True),
                ),
                config=phase6_config("FILTER", fallback_behavior="NO_TRADE"),
                global_risk_adapter=ReducingGlobalRisk(cap=99),
            )

        self.assertFalse(result.stage_results["family_aggregation"]["eligible"])
        self.assertFalse(result.inference.candidateAccepted)
        self.assertEqual(result.inference.finalSignal, "HOLD")
        self.assertIsNone(result.order_intent)

    def test_phase6_schema_mismatch_blocks_model_application(self) -> None:
        with patch(
            "backend.app.algorithms.meta_strategy.execution_pipeline.evaluate_candidate_components",
            return_value=phase5_components(),
        ):
            result = run_meta_strategy_execution_pipeline(
                MetaStrategyExecutionPipelineRequest(
                    mode="EVALUATION",
                    snapshot_request=request_with(),
                    model_artifact=phase6_artifact(schema_hash="old-schema", probabilities={"BUY": 0.99, "SELL": 0.0, "HOLD": 0.01}, promoted=True),
                ),
                config=phase6_config("FILTER", fallback_behavior="NO_TRADE"),
                global_risk_adapter=ReducingGlobalRisk(cap=99),
            )

        self.assertFalse(result.stage_results["artifact_validation"]["compatible"])
        self.assertFalse(result.stage_results["artifact_validation"]["modelApplicationAllowed"])
        self.assertFalse(result.inference.appliedToOrder)
        self.assertEqual(result.inference.finalSignal, "HOLD")
        self.assertIn("meta_strategy.artifact_validation.feature_schema_mismatch", result.stage_results["artifact_validation"]["reasonCodes"])

    def test_phase6_unpromoted_artifact_stays_shadow_only(self) -> None:
        schema_hash = phase6_feature_schema_hash()
        with patch(
            "backend.app.algorithms.meta_strategy.execution_pipeline.evaluate_candidate_components",
            return_value=phase5_components(),
        ):
            result = run_meta_strategy_execution_pipeline(
                MetaStrategyExecutionPipelineRequest(
                    mode="EVALUATION",
                    snapshot_request=request_with(),
                    model_artifact=phase6_artifact(schema_hash=schema_hash, probabilities={"BUY": 0.99, "SELL": 0.0, "HOLD": 0.01}, promoted=False),
                ),
                config=phase6_config("FILTER", fallback_behavior="NO_TRADE"),
                global_risk_adapter=ReducingGlobalRisk(cap=0),
            )

        self.assertTrue(result.stage_results["artifact_validation"]["compatible"])
        self.assertFalse(result.stage_results["artifact_validation"]["promoted"])
        self.assertFalse(result.stage_results["artifact_validation"]["modelApplicationAllowed"])
        self.assertFalse(result.inference.appliedToOrder)
        self.assertIn("meta_strategy.artifact_validation.unpromoted_artifact_shadow_only", result.stage_results["artifact_validation"]["reasonCodes"])

    def test_phase6_missing_model_follows_fail_closed_policy(self) -> None:
        with patch(
            "backend.app.algorithms.meta_strategy.execution_pipeline.evaluate_candidate_components",
            return_value=phase5_components(),
        ):
            result = run_meta_strategy_execution_pipeline(
                MetaStrategyExecutionPipelineRequest(mode="EVALUATION", snapshot_request=request_with(), model_artifact=None),
                config=phase6_config("FILTER", fallback_behavior="NO_TRADE"),
                global_risk_adapter=ReducingGlobalRisk(cap=99),
            )

        self.assertEqual(result.stage_results["artifact_validation"]["status"], "FAIL_CLOSED")
        self.assertEqual(result.inference.finalSignal, "HOLD")
        self.assertFalse(result.inference.candidateAccepted)
        self.assertIn("meta_strategy.inference.model_unavailable", result.inference.reasonCodes)

    def test_phase6_deterministic_only_behavior_remains_available(self) -> None:
        with patch(
            "backend.app.algorithms.meta_strategy.execution_pipeline.evaluate_candidate_components",
            return_value=phase5_components(),
        ):
            result = run_meta_strategy_execution_pipeline(
                MetaStrategyExecutionPipelineRequest(mode="EVALUATION", snapshot_request=request_with(), model_artifact=None),
                config=phase6_config("DISABLED", fallback_behavior="NO_TRADE"),
                global_risk_adapter=ReducingGlobalRisk(cap=0),
            )

        self.assertEqual(result.stage_results["artifact_validation"]["status"], "DETERMINISTIC_ONLY")
        self.assertEqual(result.inference.finalSignal, "BUY")
        self.assertFalse(result.inference.appliedToOrder)
        self.assertTrue(result.inference.candidateAccepted)


def phase6_config(mode: str, *, fallback_behavior: str) -> MetaStrategyExecutionPipelineConfig:
    settings = build_meta_strategy_settings(
        status="ACTIVE",
        ml_inference={"mode": mode, "fallback_behavior": fallback_behavior, "model_probability_threshold": 0.55},
    )
    return MetaStrategyExecutionPipelineConfig(settings=settings, baseline_settings=settings.to_baseline_settings(), submit_to_broker=False)


def phase6_feature_schema_hash() -> str:
    with patch(
        "backend.app.algorithms.meta_strategy.execution_pipeline.evaluate_candidate_components",
        return_value=phase5_components(),
    ):
        result = run_meta_strategy_execution_pipeline(
            MetaStrategyExecutionPipelineRequest(mode="EVALUATION", snapshot_request=request_with(), model_artifact=None),
            config=phase6_config("DISABLED", fallback_behavior="NO_TRADE"),
            global_risk_adapter=ReducingGlobalRisk(cap=0),
        )
    return result.features.schemaHash


def phase6_artifact(*, schema_hash: str, probabilities: dict[str, float], promoted: bool) -> dict:
    return {
        "artifactId": "phase6-artifact",
        "featureSchemaHash": schema_hash,
        "promoted": promoted,
        "championModel": "logistic_regression_champion",
        "models": {
            "logistic_regression_champion": {
                "available": True,
                "kind": "fixed_probability_test_model",
                "featureSchemaHash": schema_hash,
                "fixedProbabilities": probabilities,
                "modelHealthScore": 1.0,
                "calibration": {"method": "none", "approved": True},
            }
        },
    }


def phase5_components(*, minimum_independent_families: int = 2) -> CandidateComponentEvaluation:
    settings = build_meta_strategy_settings(status="ACTIVE")
    generation_config = CandidateGenerationConfig(
        aggregation=FamilyAggregationConfig(
            strategy_contribution_cap=0.35,
            family_contribution_cap=0.60,
            correlation_group_cap=0.40,
            minimum_active_strategies=2,
            minimum_independent_families=minimum_independent_families,
            maximum_abstention_rate=0.90,
        )
    )
    directional = tuple(phase5_directional_output(entry.strategy_id) for entry in DIRECTIONAL_STRATEGIES)
    context = tuple(phase5_context_output(entry.strategy_id) for entry in CONTEXT_STRATEGIES)
    regime = tuple(phase5_regime_output(entry.strategy_id) for entry in REGIME_STRATEGIES)
    safety = tuple(phase5_safety_output(entry.strategy_id) for entry in SAFETY_STRATEGIES)
    aggregation = aggregate_family_scores(directional, registry_entries=DIRECTIONAL_STRATEGIES, config=generation_config.aggregation)
    return CandidateComponentEvaluation(
        active_settings=settings,
        generation_config=generation_config,
        directional_outputs=directional,
        context_outputs=context,
        active_context_outputs=context,
        regime_outputs=regime,
        safety_outputs=safety,
        safety_blockers=(),
        aggregation=aggregation,
        safety_blocks=False,
    )


def phase5_directional_output(strategy_id: str) -> SnapshotEvaluationResult:
    active = {
        "multi_timeframe_trend_alignment": ("TREND", 0.80, "trend_alignment_measurement"),
        "vwap_trend_continuation": ("TREND", 0.70, "vwap_continuation_measurement"),
        "opening_range_breakout": ("BREAKOUT", 0.72, "opening_range_measurement"),
    }
    if strategy_id in active:
        family, confidence, measurement = active[strategy_id]
        return SnapshotEvaluationResult(
            strategy_id=strategy_id,
            signal="BUY",
            confidence=confidence,
            eligible=True,
            family=family,
            evidence={"measurement": measurement, "entryReference": 101.50, "suggestedStopReference": 100.95},
            required_input_status={"candles": True, "atr": True, "liquidity": True},
            reason_codes=(f"test.{strategy_id}.buy",),
        )
    entry = next(item for item in DIRECTIONAL_STRATEGIES if item.strategy_id == strategy_id)
    return SnapshotEvaluationResult(
        strategy_id=strategy_id,
        signal="HOLD",
        confidence=0.0,
        eligible=False,
        family=str(entry.family),
        evidence={"measurement": "inactive_fixture_measurement"},
        required_input_status={"candles": True},
        reason_codes=(f"test.{strategy_id}.hold",),
    )


def phase5_context_output(strategy_id: str) -> SnapshotEvaluationResult:
    evidence = {"familyWeightMultiplier": 1.0}
    if strategy_id == "relative_strength_qqq_iwm":
        evidence.update({"qqqVsIwmRatio": 1.02, "relativeStrengthEdge": 0.03})
    if strategy_id == "market_breadth_momentum":
        evidence.update({"breadthAverageReturn": 0.004, "advancingComponentRatio": 0.75})
    return SnapshotEvaluationResult(
        strategy_id=strategy_id,
        signal="HOLD",
        confidence=0.65,
        eligible=True,
        family="MARKET_CONTEXT",
        evidence=evidence,
        required_input_status={"context": True},
        reason_codes=(f"test.{strategy_id}.context",),
    )


def phase5_regime_output(strategy_id: str) -> SnapshotEvaluationResult:
    return SnapshotEvaluationResult(
        strategy_id=strategy_id,
        signal="HOLD",
        confidence=0.70,
        eligible=True,
        family="REGIME",
        evidence={
            "regimeLabel": "TREND_UP",
            "volatility": "NORMAL",
            "strategyFit": {"TREND": 1.0, "BREAKOUT": 1.0, "REVERSAL": 0.5, "MEAN_REVERSION": 0.5, "GAP_SESSION": 1.0},
        },
        required_input_status={"regime": True},
        reason_codes=(f"test.{strategy_id}.regime",),
    )


def phase5_safety_output(strategy_id: str) -> SnapshotEvaluationResult:
    return SnapshotEvaluationResult(
        strategy_id=strategy_id,
        signal="HOLD",
        confidence=0.0,
        eligible=True,
        family="SAFETY",
        evidence={"blocksNewEntries": False, "status": "PASS"},
        required_input_status={"safety": True},
        reason_codes=(f"test.{strategy_id}.pass",),
    )


if __name__ == "__main__":
    unittest.main()
