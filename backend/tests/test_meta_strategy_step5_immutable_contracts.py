from __future__ import annotations

import math
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from backend.app.algorithms.meta_strategy import (
    CandidateGeometry,
    ContextEvaluation,
    DeterministicCandidate,
    EffectiveMetaProfile,
    FamilyScore,
    MetaBacktestResult,
    MetaDecision,
    MetaFeatureSet,
    MetaLabel,
    MetaOrderIntent,
    MetaSizingResult,
    MetaStrategyMarketSnapshot,
    ModelArtifactManifest,
    ModelPrediction,
    PaperStabilityEvidence,
    PromotionEvidence,
    RegimeEvaluation,
    SafetyEvaluation,
    StrategyEvaluation,
    TradeManagementResult,
)


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)
COMMON = {
    "algorithm_id": "meta_strategy",
    "algorithm_version": "meta_strategy_algorithm_v1",
    "configuration_version": "meta_strategy_config_v1",
    "strategy_catalog_version": "meta_strategy_strategy_catalog_v1",
    "decision_id": "decision-1",
    "snapshot_id": "snapshot-1",
    "timestamp": NOW,
}


CONTRACT_BUILDERS = {
    MetaStrategyMarketSnapshot: lambda: {**COMMON, "symbol": "SPY", "last_price": 100.0, "volume": 10_000.0, "spread_bps": 1.2},
    StrategyEvaluation: lambda: {
        **COMMON,
        "strategy_id": "trend_following",
        "family": "trend",
        "signal": "BUY",
        "confidence": 0.7,
        "reliability": 0.8,
        "eligible": True,
    },
    ContextEvaluation: lambda: {**COMMON, "context_id": "breadth", "effect": "confirm_long", "confidence": 0.6, "data_ready": True},
    RegimeEvaluation: lambda: {**COMMON, "regime_id": "trend", "label": "strong_uptrend", "direction": 1, "volatility": "NORMAL", "confidence": 0.75},
    SafetyEvaluation: lambda: {**COMMON, "status": "PASS", "eligible": True, "risk_multiplier": 1.0},
    FamilyScore: lambda: {**COMMON, "family": "trend", "buy_score": 0.8, "sell_score": 0.1, "hold_score": 0.1, "confidence": 0.8, "reliability": 0.75},
    DeterministicCandidate: lambda: {
        **COMMON,
        "signal": "BUY",
        "confidence": 0.72,
        "eligible": True,
        "family_scores": (FamilyScore(**CONTRACT_BUILDERS[FamilyScore]()),),
    },
    CandidateGeometry: lambda: {
        **COMMON,
        "candidate_id": "candidate-1",
        "side": "BUY",
        "entry_price": 100.0,
        "stop_price": 99.0,
        "target_price": 102.0,
        "quantity": 10.0,
        "risk_reward": 2.0,
    },
    MetaFeatureSet: lambda: {
        **COMMON,
        "feature_schema_version": "meta_strategy_feature_schema_v1",
        "feature_schema_hash": "abc123",
        "feature_count": 2,
        "features": {"momentum": 0.4, "volatility": 0.2},
    },
    MetaLabel: lambda: {
        **COMMON,
        "label_specification_version": "meta_strategy_label_specification_v1",
        "label": "BUY",
        "outcome": "WIN",
        "return_r": 1.4,
        "barrier_minutes": 30,
    },
    ModelArtifactManifest: lambda: {
        **COMMON,
        "model_version": "meta_strategy_model_v1",
        "model_artifact_version": "meta_strategy_model_artifact_v1",
        "artifact_id": "artifact-1",
        "feature_schema_hash": "abc123",
        "label_specification_version": "meta_strategy_label_specification_v1",
        "trained_rows": 120,
        "metrics": {"accuracy": 0.62},
    },
    ModelPrediction: lambda: {
        **COMMON,
        "model_version": "meta_strategy_model_v1",
        "model_artifact_version": "meta_strategy_model_artifact_v1",
        "probabilities": {"BUY": 0.6, "SELL": 0.1, "HOLD": 0.3},
        "predicted_label": "BUY",
        "confidence": 0.6,
        "ood_score": 0.1,
    },
    MetaDecision: lambda: {**COMMON, "final_signal": "BUY", "status": "ACCEPTED", "confidence": 0.65, "risk_multiplier": 0.8},
    EffectiveMetaProfile: lambda: {
        **COMMON,
        "dynamic_profile_version": "meta_strategy_dynamic_profile_v1",
        "profile_id": "active-default",
        "operating_mode": "ACTIVE",
        "max_risk_multiplier": 0.8,
        "settings": {"min_probability": 0.55},
    },
    MetaSizingResult: lambda: {
        **COMMON,
        "position_sizing_version": "meta_strategy_position_sizing_v1",
        "symbol": "SPY",
        "quantity": 10.0,
        "notional": 1000.0,
        "risk_dollars": 25.0,
        "risk_multiplier": 0.8,
    },
    MetaOrderIntent: lambda: {
        **COMMON,
        "order_intent_id": "order-1",
        "symbol": "SPY",
        "side": "BUY",
        "quantity": 10.0,
        "order_type": "LIMIT",
        "limit_price": 100.0,
        "time_in_force": "DAY",
    },
    TradeManagementResult: lambda: {
        **COMMON,
        "exit_policy_version": "meta_strategy_exit_policy_v1",
        "position_id": "position-1",
        "action": "MOVE_STOP",
        "stop_price": 100.5,
        "target_price": 102.0,
        "realized_r": 0.4,
    },
    PromotionEvidence: lambda: {
        **COMMON,
        "candidate_model_version": "meta_strategy_model_v1",
        "promoted": False,
        "sample_size": 100,
        "net_expectancy": 0.12,
        "max_drawdown": 0.08,
        "metrics": {"profit_factor": 1.2},
    },
    PaperStabilityEvidence: lambda: {
        **COMMON,
        "stable": True,
        "paper_sessions": 10,
        "trade_count": 25,
        "rejection_rate": 0.2,
        "max_drawdown": 0.05,
        "metrics": {"slippage_bps": 1.5},
    },
    MetaBacktestResult: lambda: {
        **COMMON,
        "backtest_engine_version": "meta_strategy_backtest_engine_v1",
        "run_id": "backtest-1",
        "start_timestamp": NOW - timedelta(days=5),
        "end_timestamp": NOW,
        "trade_count": 40,
        "net_pnl": 1250.0,
        "max_drawdown": 250.0,
        "metrics": {"sharpe": 1.1},
    },
}


class MetaStrategyStep5ImmutableContractsTest(unittest.TestCase):
    maxDiff = None

    def test_all_required_contracts_construct_with_valid_payloads(self) -> None:
        self.assertEqual(len(CONTRACT_BUILDERS), 20)
        for contract_type, builder in CONTRACT_BUILDERS.items():
            with self.subTest(contract=contract_type.__name__):
                instance = contract_type(**builder())
                self.assertEqual(instance.algorithm_id, "meta_strategy")
                self.assertEqual(instance.algorithm_version, "meta_strategy_algorithm_v1")
                self.assertEqual(instance.configuration_version, "meta_strategy_config_v1")
                self.assertEqual(instance.strategy_catalog_version, "meta_strategy_strategy_catalog_v1")
                self.assertEqual(instance.decision_id, "decision-1")
                self.assertEqual(instance.snapshot_id, "snapshot-1")
                self.assertEqual(instance.timestamp, NOW)

    def test_contracts_are_immutable(self) -> None:
        for contract_type, builder in CONTRACT_BUILDERS.items():
            with self.subTest(contract=contract_type.__name__):
                instance = contract_type(**builder())
                with self.assertRaises(ValidationError):
                    setattr(instance, "decision_id", "changed")

    def test_contracts_reject_missing_required_identity_version_and_timestamp_fields(self) -> None:
        required_fields = (
            "algorithm_id",
            "algorithm_version",
            "configuration_version",
            "strategy_catalog_version",
            "decision_id",
            "snapshot_id",
            "timestamp",
        )
        for contract_type, builder in CONTRACT_BUILDERS.items():
            for field_name in required_fields:
                payload = builder()
                payload.pop(field_name)
                with self.subTest(contract=contract_type.__name__, missing=field_name):
                    with self.assertRaises(ValidationError):
                        contract_type(**payload)

    def test_contracts_reject_cross_algorithm_identifiers(self) -> None:
        for contract_type, builder in CONTRACT_BUILDERS.items():
            payload = builder()
            payload["algorithm_id"] = "weighted_voting"
            with self.subTest(contract=contract_type.__name__):
                with self.assertRaises(ValidationError):
                    contract_type(**payload)

    def test_contracts_reject_nan_and_infinity_in_numeric_fields_and_nested_payloads(self) -> None:
        for bad_value in (math.nan, math.inf, -math.inf):
            payload = CONTRACT_BUILDERS[MetaFeatureSet]()
            payload["features"]["bad_value"] = bad_value
            with self.subTest(value=bad_value):
                with self.assertRaises(ValidationError):
                    MetaFeatureSet(**payload)

        for bad_value in (math.nan, math.inf, -math.inf):
            payload = CONTRACT_BUILDERS[ModelPrediction]()
            payload["probabilities"]["BUY"] = bad_value
            with self.subTest(probability=bad_value):
                with self.assertRaises(ValidationError):
                    ModelPrediction(**payload)

    def test_invalid_numeric_values_are_rejected(self) -> None:
        invalid_cases: tuple[tuple[type[Any], str, Any], ...] = (
            (MetaStrategyMarketSnapshot, "last_price", -1.0),
            (StrategyEvaluation, "confidence", 1.2),
            (ContextEvaluation, "confidence", -0.1),
            (RegimeEvaluation, "confidence", 2.0),
            (SafetyEvaluation, "risk_multiplier", 1.1),
            (FamilyScore, "buy_score", -0.01),
            (DeterministicCandidate, "confidence", 1.01),
            (CandidateGeometry, "quantity", -1.0),
            (MetaFeatureSet, "feature_count", -1),
            (MetaLabel, "barrier_minutes", -1),
            (ModelArtifactManifest, "trained_rows", -1),
            (ModelPrediction, "ood_score", -0.01),
            (MetaDecision, "risk_multiplier", 1.01),
            (EffectiveMetaProfile, "max_risk_multiplier", 1.01),
            (MetaSizingResult, "risk_dollars", -0.01),
            (MetaOrderIntent, "quantity", 0.0),
            (TradeManagementResult, "realized_r", 1001.0),
            (PromotionEvidence, "sample_size", -1),
            (PaperStabilityEvidence, "rejection_rate", 1.01),
            (MetaBacktestResult, "trade_count", -1),
        )
        for contract_type, field_name, bad_value in invalid_cases:
            payload = CONTRACT_BUILDERS[contract_type]()
            payload[field_name] = bad_value
            with self.subTest(contract=contract_type.__name__, field=field_name):
                with self.assertRaises(ValidationError):
                    contract_type(**payload)

    def test_backtest_result_rejects_inverted_time_window(self) -> None:
        payload = CONTRACT_BUILDERS[MetaBacktestResult]()
        payload["start_timestamp"] = NOW
        payload["end_timestamp"] = NOW - timedelta(minutes=1)

        with self.assertRaises(ValidationError):
            MetaBacktestResult(**payload)


if __name__ == "__main__":
    unittest.main()
