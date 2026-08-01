from __future__ import annotations

import unittest
from dataclasses import asdict, is_dataclass

from backend.app.algorithms.meta_strategy.execution_pipeline import (
    MetaStrategyExecutionPipelineConfig,
    MetaStrategyExecutionPipelineRequest,
    run_meta_strategy_execution_pipeline,
)
from backend.app.algorithms.meta_strategy.inference import MetaStrategyInferenceConfig
from backend.tests.test_meta_strategy_step7_market_snapshot import request_with


class MetaStrategyRequiredParityModesTest(unittest.TestCase):
    def test_backtest_replay_shadow_and_paper_match_before_execution_adapters(self) -> None:
        config = MetaStrategyExecutionPipelineConfig(
            submit_to_broker=False,
            inference_config=MetaStrategyInferenceConfig(mode="DISABLED", fallbackBehavior="NO_TRADE"),
        )
        request = request_with()
        model_artifact = {
            "artifactId": "shadow-disabled-parity",
            "modelVersion": "none",
            "featureSchemaVersion": "meta_strategy_feature_schema_v1",
            "compatible": False,
        }

        results = {
            mode: run_meta_strategy_execution_pipeline(
                MetaStrategyExecutionPipelineRequest(
                    mode=mode,
                    snapshot_request=request,
                    model_artifact=model_artifact,
                    account_equity=100_000.0,
                    available_buying_power=100_000.0,
                    remaining_algorithm_risk=1_000.0,
                    global_available_risk=1_000.0,
                    global_quantity_cap=10_000,
                ),
                config=config,
            )
            for mode in ("BACKTEST", "DAILY_REPLAY", "SHADOW", "PAPER")
        }

        comparable = {mode: decision_signature(result) for mode, result in results.items()}

        self.assertEqual(comparable["BACKTEST"], comparable["DAILY_REPLAY"])
        self.assertEqual(comparable["BACKTEST"], comparable["SHADOW"])
        self.assertEqual(comparable["BACKTEST"], comparable["PAPER"])


def decision_signature(result) -> dict:
    return {
        "stageSequence": result.stage_sequence,
        "snapshot": serializable(result.snapshot),
        "candidate": serializable(result.deterministic_candidate.deterministic_candidate),
        "geometry": serializable(result.geometry.geometry),
        "featuresHash": result.features.schemaHash,
        "inference": {
            "finalSignal": result.inference.finalSignal,
            "decisionAction": result.inference.decisionAction,
            "riskMultiplier": result.inference.recommendedRiskMultiplier,
        },
        "localGates": serializable(result.local_gates),
        "sizingQuantity": result.sizing.quantity,
        "orderIntent": serializable(result.order_intent) if result.order_intent else None,
        "finalValid": result.final_valid,
    }


def serializable(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): serializable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(serializable(item) for item in value)
    return value


if __name__ == "__main__":
    unittest.main()
