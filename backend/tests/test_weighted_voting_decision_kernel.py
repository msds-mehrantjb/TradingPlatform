from __future__ import annotations

import ast
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.algorithms.weighted_voting.decision_kernel import WeightedVotingDecisionKernel, decision_kernel_status
from backend.app.algorithms.weighted_voting.dynamic_settings import DynamicSettingsResolver, default_weighted_settings, resolve_effective_settings
from backend.app.algorithms.weighted_voting.inventory import WeightedVotingInventorySnapshot
from backend.app.algorithms.weighted_voting.inventory import WeightedVotingPosition
from backend.app.algorithms.weighted_voting.market_snapshot import build_weighted_voting_market_snapshot
from backend.app.algorithms.weighted_voting.runtime_context import (
    WeightedVotingExecutionCostEstimate,
    WeightedVotingRuntimeContextBuilder,
    WeightedVotingStaticAccountPort,
    WeightedVotingStaticGlobalRiskPort,
    WeightedVotingStaticInventorySnapshotPort,
    WeightedVotingStaticMarketDataPort,
)
from backend.app.algorithms.weighted_voting.models import WeightedDataQualityStatus, WeightedSide, WeightedStrategyFamily, WeightedVotingSignal
from backend.app.algorithms.weighted_voting.weight_engine import create_unseeded_equal_weight_state
from backend.app.gates import GlobalGateResponse


SESSION_OPEN = datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc)
KERNEL_PATH = Path(__file__).parents[1] / "app" / "algorithms" / "weighted_voting" / "decision_kernel.py"
BACKTEST_PATH = Path(__file__).parents[1] / "app" / "algorithms" / "weighted_voting" / "backtest" / "engine.py"


class WeightedVotingDecisionKernelTest(unittest.TestCase):
    def test_identical_context_produces_deterministic_ids_and_outputs(self) -> None:
        context = valid_context()

        first = WeightedVotingDecisionKernel.evaluate(context)
        second = WeightedVotingDecisionKernel.evaluate(context)

        self.assertEqual(first.deterministic_result_hash, second.deterministic_result_hash)
        self.assertEqual(first.decision.deterministic_json(), second.decision.deterministic_json())
        self.assertEqual(first.order_proposal.as_dict(), second.order_proposal.as_dict())
        self.assertEqual(first.observability_record, second.observability_record)
        self.assertEqual(first.decision.decision_id, f"weighted-voting-{context.finalised_one_minute_market_snapshot.data_timestamp.isoformat()}")

    def test_kernel_status_declares_side_effect_free_boundary(self) -> None:
        status = decision_kernel_status()

        self.assertTrue(status["sideEffectFree"])
        self.assertIn("write_persistence", status["forbiddenActions"])
        self.assertIn("create_default_global_risk_response", status["forbiddenActions"])
        self.assertIn("produce_immutable_observability_record", status["sequence"])

    def test_kernel_source_has_no_side_effect_forbidden_calls(self) -> None:
        source = KERNEL_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_names = {"write_snapshot", "submit_weighted_voting_paper_order", "requests", "httpx"}
        calls = {getattr(node.func, "attr", getattr(node.func, "id", "")) for node in ast.walk(tree) if isinstance(node, ast.Call)}

        self.assertTrue(forbidden_names.isdisjoint(calls))
        self.assertNotIn("datetime.now", source)
        self.assertNotIn("_now()", source)

    def test_daily_trade_limit_uses_inventory_trade_count(self) -> None:
        context = valid_context(daily_trade_count=999)

        result = WeightedVotingDecisionKernel.evaluate(context, signal_evaluator=buy_signal_evaluator)

        self.assertIn("weighted_voting.gate.trade_count_limit_reached", result.gate_result.reason_codes)
        self.assertEqual(result.sizing_result.quantity, 0)

    def test_existing_position_gate_uses_inventory_position(self) -> None:
        context = valid_context(open_position=True)

        result = WeightedVotingDecisionKernel.evaluate(context, signal_evaluator=buy_signal_evaluator)

        self.assertIsNotNone(context.current_position)
        self.assertIn("weighted_voting.gate.existing_position_blocks_entry", result.gate_result.reason_codes)
        self.assertIn("weighted_voting.gate.pyramiding_not_allowed", result.gate_result.reason_codes)

    def test_stale_account_snapshot_fails_closed(self) -> None:
        context = valid_context(account_observed_at=SESSION_OPEN)

        result = WeightedVotingDecisionKernel.evaluate(context, signal_evaluator=buy_signal_evaluator)

        self.assertEqual(result.decision.signal, "Hold")
        self.assertIn("weighted_voting.context.account_snapshot_stale", result.reason_codes)

    def test_expired_settings_fail_closed_without_runtime_resolver(self) -> None:
        payload = evaluate_payload()
        snapshot = build_weighted_voting_market_snapshot(payload)
        expired = resolve_effective_settings(
            timestamp=SESSION_OPEN,
            expiration_timestamp=SESSION_OPEN + timedelta(minutes=1),
        )
        context = valid_context(effective_settings=expired)

        result = WeightedVotingDecisionKernel.evaluate(context, signal_evaluator=buy_signal_evaluator)

        self.assertEqual(snapshot.data_timestamp, context.finalised_one_minute_market_snapshot.data_timestamp)
        self.assertEqual(result.decision.signal, "Hold")
        self.assertIn("weighted_voting.context.settings_expired", result.reason_codes)

    def test_runtime_resolver_recomputes_expired_settings_for_condition(self) -> None:
        expired = resolve_effective_settings(
            timestamp=SESSION_OPEN,
            expiration_timestamp=SESSION_OPEN + timedelta(minutes=1),
        )
        context = valid_context(effective_settings=expired)

        result = WeightedVotingDecisionKernel.evaluate(
            context,
            settings_resolver=DynamicSettingsResolver(
                default_settings=expired.default_settings,
                dynamic_envelope=expired.dynamic_envelope,
                hard_limits=expired.hard_limits,
            ),
            signal_evaluator=buy_signal_evaluator,
        )

        self.assertNotEqual(result.effective_settings.settings_version, expired.settings_version)
        self.assertIn("clean", result.effective_settings.settings_version)
        self.assertNotIn("weighted_voting.context.settings_expired", result.reason_codes)

    def test_backtest_routes_core_decision_flow_through_kernel(self) -> None:
        source = BACKTEST_PATH.read_text(encoding="utf-8")

        self.assertIn("WeightedVotingDecisionKernel.evaluate", source)
        self.assertNotIn("evaluate_local_decision_gates(", source)
        self.assertNotIn("calculate_weighted_voting_position_size(", source)
        self.assertNotIn("aggregate_weighted_signals(", source)

    def test_production_and_replay_contexts_match_decision_for_identical_events(self) -> None:
        production_context = valid_context(mode="production")
        replay_context = replace(production_context, mode="replay_fixture", manifest_hash="")

        production = WeightedVotingDecisionKernel.evaluate(production_context, signal_evaluator=buy_signal_evaluator)
        replay = WeightedVotingDecisionKernel.evaluate(replay_context, signal_evaluator=buy_signal_evaluator)

        self.assertEqual(production.decision.deterministic_json(), replay.decision.deterministic_json())
        self.assertEqual(production.gate_result.reason_codes, replay.gate_result.reason_codes)
        self.assertEqual(production.sizing_result.quantity, replay.sizing_result.quantity)

    def test_kernel_holds_when_expected_edge_does_not_survive_costs(self) -> None:
        context = valid_context(slippage_per_share=1.50, fee_per_share=1.50)

        result = WeightedVotingDecisionKernel.evaluate(context, signal_evaluator=buy_signal_evaluator)

        self.assertEqual(result.decision.signal, "Hold")
        self.assertEqual(result.order_proposal.side, "Hold")
        self.assertEqual(result.order_proposal.quantity, 0)
        self.assertIn("weighted_voting.decision_kernel.expected_edge_does_not_survive_costs", result.reason_codes)
        self.assertIn("weighted_voting.decision_kernel.local_gates_block_trade", result.reason_codes)

    def test_kernel_holds_when_global_gate_response_blocks_trade(self) -> None:
        timestamp = build_weighted_voting_market_snapshot(evaluate_payload()).data_timestamp
        context = valid_context(
            global_gate_response=GlobalGateResponse(
                action="REJECT_NEW_ENTRY",
                maximumAllowedQuantity=0,
                maximumAdditionalRiskDollars=0.0,
                rejectionReasons=("weighted_voting.test.global_gate_rejected",),
                evaluatedAt=timestamp,
                configurationHash="weighted-test-global-reject",
            )
        )

        result = WeightedVotingDecisionKernel.evaluate(context, signal_evaluator=buy_signal_evaluator)

        self.assertEqual(result.decision.signal, "Hold")
        self.assertEqual(result.order_proposal.side, "Hold")
        self.assertEqual(result.order_proposal.quantity, 0)
        self.assertIn("weighted_voting.decision_kernel.global_gate_response_blocks_trade", result.reason_codes)

    def test_kernel_holds_when_position_limit_blocks_trade(self) -> None:
        payload = evaluate_payload()
        timestamp = build_weighted_voting_market_snapshot(payload).data_timestamp
        settings = resolve_effective_settings(
            default_settings=default_weighted_settings(timestamp=timestamp).model_copy(update={"maximum_position_percent": 0.0}),
            timestamp=timestamp,
        )
        context = valid_context(effective_settings=settings)

        result = WeightedVotingDecisionKernel.evaluate(context, signal_evaluator=buy_signal_evaluator)

        self.assertEqual(result.decision.signal, "Hold")
        self.assertEqual(result.order_proposal.side, "Hold")
        self.assertEqual(result.order_proposal.quantity, 0)
        self.assertIn("weighted_voting.decision_kernel.position_limit_blocks_trade", result.reason_codes)


def valid_context(
    *,
    daily_trade_count: int = 0,
    open_position: bool = False,
    account_observed_at: datetime | None = None,
    effective_settings=None,
    mode: str = "test_fixture",
    global_available_risk: float = 1000.0,
    global_max_shares: int = 100000,
    global_gate_response=None,
    slippage_per_share: float = 0.01,
    fee_per_share: float = 0.01,
):
    payload = evaluate_payload()
    snapshot = build_weighted_voting_market_snapshot(payload)
    positions = (
        WeightedVotingPosition(
            algorithm_id="weighted_voting",
            position_id="kernel-test-position-1",
            symbol="SPY",
            side="Buy",
            quantity=10,
            average_entry_price=100.0,
            opened_at=snapshot.data_timestamp,
            decision_id="kernel-test-decision",
            order_intent_id="kernel-test-order-intent",
            client_order_id="kernel-test-client-order",
        ),
    ) if open_position else ()
    inventory = replace(
        WeightedVotingInventorySnapshot.empty(
            symbol="SPY",
            allocated_capital=100000.0,
            session_date=snapshot.data_timestamp.date(),
            created_at=snapshot.data_timestamp,
        ),
        open_positions=positions,
        daily_trade_count=daily_trade_count,
        remaining_daily_risk=1000.0,
        remaining_capital_partition=30000.0,
        updated_at=snapshot.data_timestamp,
    )
    return WeightedVotingRuntimeContextBuilder(
        market_data_port=WeightedVotingStaticMarketDataPort(snapshot),
        inventory_repository=WeightedVotingStaticInventorySnapshotPort(inventory),
        account_port=WeightedVotingStaticAccountPort(account_equity=100000.0, broker_buying_power=100000.0, observed_at=account_observed_at),
        global_risk_port=WeightedVotingStaticGlobalRiskPort(global_available_risk=global_available_risk, global_max_shares=global_max_shares, gate_response=global_gate_response),
        effective_settings=effective_settings or resolve_effective_settings(timestamp=snapshot.data_timestamp),
        active_weight_state=create_unseeded_equal_weight_state(timestamp=snapshot.data_timestamp, data_timestamp=snapshot.data_timestamp),
        observed_at=snapshot.data_timestamp,
        mode=mode,
        cost_estimate=WeightedVotingExecutionCostEstimate(
            slippage_per_share=slippage_per_share,
            fee_per_share=fee_per_share,
            observed_at=snapshot.data_timestamp,
            source_id="weighted_voting.test_fixture.cost_model",
            reason_codes=("weighted_voting.test_fixture.cost_model",),
        ),
    ).build()


def buy_signal_evaluator(snapshot, _config=None, _weights=None, _condition=None):
    family_by_strategy = {
        "S1": WeightedStrategyFamily.BREAKOUT,
        "S8": WeightedStrategyFamily.BREAKOUT,
        "S2": WeightedStrategyFamily.TREND,
        "S3": WeightedStrategyFamily.TREND,
        "S4": WeightedStrategyFamily.MEAN_REVERSION,
        "S7": WeightedStrategyFamily.MEAN_REVERSION,
        "S5": WeightedStrategyFamily.REVERSAL,
        "S6": WeightedStrategyFamily.REVERSAL,
    }
    return tuple(
        WeightedVotingSignal(
            strategy_id=strategy_id,
            strategy_name=f"{strategy_id} kernel safety signal",
            strategy_version="weighted_strategy_kernel_test_v1",
            family=family,
            signal=WeightedSide.BUY,
            p_buy=0.75,
            p_sell=0.05,
            p_hold=0.20,
            directional_confidence=0.75,
            signal_strength=0.75,
            expected_raw_movement=0.002,
            expected_return=0.002,
            expected_return_after_costs=0.0015,
            strength=0.75,
            final_weight=0.125,
            eligible=True,
            data_ready=True,
            required_data_freshness_seconds=300,
            actual_data_freshness_seconds=0,
            data_quality_status=WeightedDataQualityStatus.FULL,
            data_timestamp=snapshot.data_timestamp,
            explanation="Synthetic kernel safety signal.",
        )
        for strategy_id, family in family_by_strategy.items()
    )


def evaluate_payload() -> dict:
    rows = []
    for index in range(95):
        base = 100.0 + index * 0.03
        rows.append(
            {
                "timestamp": (SESSION_OPEN + timedelta(minutes=index)).isoformat(),
                "open": base,
                "high": base + 0.45,
                "low": base - 0.18,
                "close": base + 0.08,
                "volume": 200000 if index != 5 else 5000,
            }
        )
    return {
        "symbol": "SPY",
        "data_timestamp": rows[-1]["timestamp"],
        "candles": rows,
        "bid": rows[-1]["close"] - 0.01,
        "ask": rows[-1]["close"] + 0.01,
        "session_phase": "morning",
        "data_freshness_seconds": 0.0,
    }


if __name__ == "__main__":
    unittest.main()
