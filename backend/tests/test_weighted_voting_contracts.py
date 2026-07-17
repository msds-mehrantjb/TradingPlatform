from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from backend.app.algorithms.weighted_voting.aggregation import aggregate_weighted_signals
from backend.app.algorithms.weighted_voting.config import WEIGHTED_VOTING_CONFIG_VERSION
from backend.app.algorithms.weighted_voting.identity import (
    WEIGHTED_VOTING_ACTIVE_WEIGHT_VERSION,
    WEIGHTED_VOTING_ALGORITHM_ID,
    WEIGHTED_VOTING_API_NAMESPACE,
    WEIGHTED_VOTING_API_VERSION,
    WEIGHTED_VOTING_CONFIGURATION_VERSION,
    WEIGHTED_VOTING_REASON_CODE_PREFIX,
    WEIGHTED_VOTING_SERVICE_VERSION,
    WEIGHTED_VOTING_STRATEGY_VERSION,
    is_weighted_voting_reason_code,
    weighted_voting_reason_code,
    weighted_voting_service_boundary,
)
from backend.app.algorithms.weighted_voting.models import (
    ALGORITHM_ID,
    WeightedArtifactManifest,
    WeightedBacktestFold,
    WeightedBacktestRun,
    WeightedBacktestStatus,
    WeightedDataQualityStatus,
    WeightedCandle,
    WeightedDecision,
    WeightedDefaultSettings,
    WeightedDynamicEnvelope,
    WeightedEffectiveSettings,
    WeightedGateResult,
    WeightedGateStatus,
    WeightedHardLimits,
    WeightedMarketCondition,
    WeightedMarketSnapshot,
    WeightedOrderProposal,
    WeightedOrderStatus,
    WeightedPositionState,
    WeightedRangeCondition,
    WeightedSide,
    WeightedStrategyFamily,
    WeightedStrategyOutcome,
    WeightedStrategySignal,
    WeightedStrategyStatistics,
    WeightedTradeRecord,
    WeightedTrendDirection,
    WeightedVoteScores,
    WeightedVolatilityLevel,
    WeightedWeightState,
)
from backend.app.algorithms.weighted_voting.service import WeightedVotingService


TS = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


class WeightedVotingContractsTest(unittest.TestCase):
    def test_weighted_voting_identity_contract_is_complete_and_dedicated(self) -> None:
        boundary = weighted_voting_service_boundary()

        self.assertEqual(boundary.algorithm_id, "weighted_voting")
        self.assertEqual(WEIGHTED_VOTING_ALGORITHM_ID, "weighted_voting")
        self.assertEqual(ALGORITHM_ID, "weighted_voting")
        self.assertEqual(boundary.service_version, WEIGHTED_VOTING_SERVICE_VERSION)
        self.assertEqual(WeightedVotingService.version, WEIGHTED_VOTING_SERVICE_VERSION)
        self.assertEqual(boundary.api_namespace, "/api/weighted-voting")
        self.assertEqual(boundary.api_namespace, WEIGHTED_VOTING_API_NAMESPACE)
        self.assertEqual(boundary.api_version, WEIGHTED_VOTING_API_VERSION)
        self.assertEqual(boundary.configuration_version, WEIGHTED_VOTING_CONFIGURATION_VERSION)
        self.assertEqual(WEIGHTED_VOTING_CONFIG_VERSION, WEIGHTED_VOTING_CONFIGURATION_VERSION)
        self.assertEqual(boundary.strategy_version, WEIGHTED_VOTING_STRATEGY_VERSION)
        self.assertEqual(boundary.active_weight_version, WEIGHTED_VOTING_ACTIVE_WEIGHT_VERSION)
        self.assertIn("WeightedVotingEvaluateRequest", boundary.input_models)
        self.assertIn("WeightedVotingDecision", boundary.output_models)
        self.assertEqual(boundary.reason_code_namespace, WEIGHTED_VOTING_REASON_CODE_PREFIX)
        self.assertEqual(weighted_voting_reason_code("api.ready"), "weighted_voting.api.ready")
        self.assertTrue(is_weighted_voting_reason_code("weighted_voting.api.ready"))
        self.assertFalse(is_weighted_voting_reason_code("voting_ensemble.api.ready"))

    def test_invalid_strategy_probabilities_fail_validation(self) -> None:
        with self.assertRaises(ValidationError):
            strategy_signal(p_buy=0.7, p_sell=0.2, p_hold=0.2)

        with self.assertRaises(ValidationError):
            strategy_signal(p_buy=1.1, p_sell=0.0, p_hold=-0.1)

    def test_unavailable_strategy_signal_must_be_zero_directional_hold(self) -> None:
        with self.assertRaises(ValidationError):
            WeightedStrategySignal(
                strategy_id="S1",
                strategy_name="Opening Range Breakout",
                strategy_version="weighted_strategy_test_v1",
                family=WeightedStrategyFamily.BREAKOUT,
                signal=WeightedSide.BUY,
                p_buy=0.6,
                p_sell=0.1,
                p_hold=0.3,
                strength=0.6,
                final_weight=0.0,
                eligible=False,
                data_ready=False,
                data_quality_status=WeightedDataQualityStatus.UNAVAILABLE,
                data_timestamp=TS,
                explanation="Unavailable data cannot produce direction.",
            )

    def test_invalid_weight_state_fails_validation(self) -> None:
        with self.assertRaises(ValidationError):
            WeightedWeightState(
                strategy_weights={"S1": 0.8, "S2": 0.1},
                last_updated_at=TS,
                data_timestamp=TS,
                explanation="Invalid weights do not sum to one.",
            )

    def test_decision_and_order_include_required_metadata(self) -> None:
        decision_fields = set(WeightedDecision.model_fields)
        order_fields = set(WeightedOrderProposal.model_fields)
        required = {
            "algorithm_id",
            "decision_id",
            "configuration_version",
            "strategy_catalog_version",
            "weight_version",
            "data_timestamp",
            "data_manifest_hash",
            "settings_version",
            "proposed_side",
            "proposed_quantity",
            "reason_codes",
        }

        self.assertTrue(required.issubset(decision_fields), sorted(required - decision_fields))
        self.assertTrue(required.issubset(order_fields), sorted(required - order_fields))
        self.assertEqual(sample_decision().algorithm_id, ALGORITHM_ID)
        self.assertEqual(sample_order().algorithm_id, ALGORITHM_ID)

    def test_contracts_serialize_deterministically(self) -> None:
        left = sample_decision()
        right = sample_decision()

        self.assertEqual(left.deterministic_json(), right.deterministic_json())
        self.assertEqual(left.deterministic_hash(), right.deterministic_hash())
        self.assertEqual(left.deterministic_json(), json.dumps(left.model_dump(mode="json", exclude_none=True), sort_keys=True, separators=(",", ":")))

    def test_all_canonical_models_include_version_fields(self) -> None:
        canonical_models = [
            WeightedMarketSnapshot,
            WeightedStrategySignal,
            WeightedStrategyOutcome,
            WeightedStrategyStatistics,
            WeightedWeightState,
            WeightedMarketCondition,
            WeightedDefaultSettings,
            WeightedDynamicEnvelope,
            WeightedHardLimits,
            WeightedEffectiveSettings,
            WeightedVoteScores,
            WeightedGateResult,
            WeightedDecision,
            WeightedOrderProposal,
            WeightedPositionState,
            WeightedTradeRecord,
            WeightedBacktestRun,
            WeightedBacktestFold,
            WeightedArtifactManifest,
        ]

        for model in canonical_models:
            with self.subTest(model=model.__name__):
                self.assertTrue(any(field_name.endswith("version") for field_name in model.model_fields), model.model_fields)

    def test_aggregation_returns_canonical_decision_without_frontend_types(self) -> None:
        decision = aggregate_weighted_signals(
            [
                strategy_signal(strategy_id="S1", p_buy=0.7, p_sell=0.1, p_hold=0.2),
                strategy_signal(strategy_id="S2", p_buy=0.6, p_sell=0.1, p_hold=0.3),
            ],
            decision_timestamp=TS,
        )

        self.assertIsInstance(decision, WeightedDecision)
        self.assertEqual(decision.algorithm_id, ALGORITHM_ID)
        self.assertEqual(decision.raw_winner, WeightedSide.BUY.value)
        self.assertEqual(decision.signal, WeightedSide.BUY.value)
        self.assertEqual(decision.proposed_side, WeightedSide.BUY.value)
        self.assertEqual(decision.proposed_quantity, 0)
        self.assertEqual(decision.vote_scores.buy_score, 0.65)


def candle(timestamp: datetime = TS) -> WeightedCandle:
    return WeightedCandle(
        timestamp=timestamp,
        open=100.0,
        high=101.0,
        low=99.5,
        close=100.5,
        volume=100000,
    )


def strategy_signal(
    *,
    strategy_id: str = "S1",
    p_buy: float = 0.6,
    p_sell: float = 0.1,
    p_hold: float = 0.3,
) -> WeightedStrategySignal:
    return WeightedStrategySignal(
        strategy_id=strategy_id,
        strategy_name="Opening Range Breakout",
        strategy_version="weighted_strategy_test_v1",
        family=WeightedStrategyFamily.BREAKOUT,
        signal=WeightedSide.BUY,
        p_buy=p_buy,
        p_sell=p_sell,
        p_hold=p_hold,
        directional_confidence=0.7,
        signal_strength=0.7,
        expected_raw_movement=0.001,
        expected_return=0.001,
        expected_return_after_costs=0.0008,
        strength=0.7,
        final_weight=0.5,
        eligible=True,
        data_ready=True,
        required_data_freshness_seconds=300,
        actual_data_freshness_seconds=0,
        data_quality_status=WeightedDataQualityStatus.FULL,
        data_timestamp=TS,
        reason_codes=(),
        explanation="Synthetic valid Weighted Voting strategy signal.",
    )


def vote_scores() -> WeightedVoteScores:
    return WeightedVoteScores(
        buy_score=0.65,
        sell_score=0.1,
        hold_score=0.25,
        max_score=0.65,
        margin=0.4,
        raw_winner=WeightedSide.BUY,
        data_timestamp=TS,
        explanation="Synthetic vote scores.",
    )


def gate_result() -> WeightedGateResult:
    return WeightedGateResult(
        gate_id="confidence",
        gate_name="Confidence",
        status=WeightedGateStatus.PASS,
        blocks_order=False,
        data_timestamp=TS,
        explanation="Synthetic passing gate.",
    )


def sample_decision() -> WeightedDecision:
    return WeightedDecision(
        decision_id="decision-1",
        configuration_version="weighted_config_v1",
        strategy_catalog_version="weighted_catalog_v1",
        weight_version="weighted_weights_v1",
        data_timestamp=TS,
        data_manifest_hash="manifest-hash",
        settings_version="weighted_settings_v1",
        proposed_side=WeightedSide.BUY,
        proposed_quantity=10,
        reason_codes=("weighted_voting.synthetic",),
        vote_scores=vote_scores(),
        gate_results=(gate_result(),),
        signal=WeightedSide.BUY,
        raw_winner=WeightedSide.BUY,
        eligible=True,
        data_ready=True,
        configuration_hash="config-hash",
        explanation="Synthetic canonical decision.",
    )


def sample_order() -> WeightedOrderProposal:
    return WeightedOrderProposal(
        decision_id="decision-1",
        order_id="order-1",
        configuration_version="weighted_config_v1",
        strategy_catalog_version="weighted_catalog_v1",
        weight_version="weighted_weights_v1",
        data_timestamp=TS,
        data_manifest_hash="manifest-hash",
        settings_version="weighted_settings_v1",
        proposed_side=WeightedSide.BUY,
        proposed_quantity=10,
        order_status=WeightedOrderStatus.PROPOSED,
        limit_price=101.0,
        stop_price=99.0,
        target_price=103.0,
        reason_codes=("weighted_voting.synthetic",),
        configuration_hash="config-hash",
        explanation="Synthetic canonical order.",
    )


def instantiate_all_contracts() -> list[object]:
    fold = WeightedBacktestFold(
        fold_id="fold-1",
        train_start=TS,
        train_end=TS + timedelta(days=1),
        test_start=TS + timedelta(days=2),
        test_end=TS + timedelta(days=3),
        data_manifest_hash="manifest-hash",
        explanation="Synthetic fold.",
    )
    defaults = WeightedDefaultSettings()
    dynamic = WeightedDynamicEnvelope()
    hard_limits = WeightedHardLimits()
    return [
        WeightedMarketSnapshot(symbol="SPY", data_timestamp=TS, one_minute_candles=(candle(),), explanation="Synthetic market snapshot."),
        strategy_signal(),
        WeightedStrategyOutcome(strategy_id="S1", side=WeightedSide.BUY, entry_timestamp=TS, entry_price=100.0, explanation="Synthetic outcome."),
        WeightedStrategyStatistics(strategy_id="S1", sample_size=10, trade_count=5, win_rate=0.6, data_timestamp=TS, explanation="Synthetic stats."),
        WeightedWeightState(strategy_weights={"S1": 0.5, "S2": 0.5}, last_updated_at=TS, data_timestamp=TS, explanation="Synthetic weights."),
        WeightedMarketCondition(
            trend_direction=WeightedTrendDirection.UP,
            volatility_level=WeightedVolatilityLevel.NORMAL,
            range_condition=WeightedRangeCondition.TRENDING,
            session_label="Morning",
            data_ready=True,
            data_timestamp=TS,
            explanation="Synthetic market condition.",
        ),
        defaults,
        dynamic,
        hard_limits,
        WeightedEffectiveSettings(
            settings_version="weighted_effective_settings_v1",
            default_settings=defaults,
            dynamic_envelope=dynamic,
            hard_limits=hard_limits,
            configuration_version="weighted_config_v1",
            configuration_hash="config-hash",
            explanation="Synthetic effective settings.",
        ),
        vote_scores(),
        gate_result(),
        sample_decision(),
        sample_order(),
        WeightedPositionState(symbol="SPY", quantity=10, average_entry_price=100.0, data_timestamp=TS, explanation="Synthetic position."),
        WeightedTradeRecord(
            trade_id="trade-1",
            decision_id="decision-1",
            order_id="order-1",
            symbol="SPY",
            side=WeightedSide.BUY,
            quantity=10,
            price=101.0,
            trade_timestamp=TS,
            explanation="Synthetic trade.",
        ),
        WeightedBacktestRun(
            run_id="run-1",
            status=WeightedBacktestStatus.COMPLETED,
            configuration_version="weighted_config_v1",
            strategy_catalog_version="weighted_catalog_v1",
            weight_version="weighted_weights_v1",
            settings_version="weighted_settings_v1",
            data_manifest_hash="manifest-hash",
            folds=(fold,),
            started_at=TS,
            completed_at=TS + timedelta(days=3),
            explanation="Synthetic backtest run.",
        ),
        fold,
        WeightedArtifactManifest(
            artifact_id="artifact-1",
            configuration_version="weighted_config_v1",
            strategy_catalog_version="weighted_catalog_v1",
            weight_version="weighted_weights_v1",
            settings_version="weighted_settings_v1",
            data_manifest_hash="manifest-hash",
            artifact_hash="artifact-hash",
            created_at=TS,
            explanation="Synthetic artifact manifest.",
        ),
    ]


if __name__ == "__main__":
    unittest.main()
