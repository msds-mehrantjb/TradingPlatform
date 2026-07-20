from __future__ import annotations

import types
import unittest

import backend.app.algorithms.meta_strategy.execution_pipeline as execution_pipeline
from backend.app.algorithms.meta_strategy import (
    META_STRATEGY_EXECUTION_PIPELINE_STAGES,
    MetaStrategyExecutionPipelineRequest,
    MetaStrategyOrderValidationContext,
    build_meta_strategy_market_snapshot,
    build_meta_strategy_order_intent,
    validate_meta_strategy_order,
)
from backend.tests.test_meta_strategy_step31_execution_pipeline import RecordingBroker, RecordingPersistence
from backend.tests.test_meta_strategy_step7_market_snapshot import request_with


class ApprovingGlobalRisk:
    def apply(self, order_intent, *, requested_quantity: int) -> dict:  # noqa: ANN001
        return {
            "status": "PASS",
            "requestedQuantity": requested_quantity,
            "approvedQuantity": 1,
            "reasonCodes": ("test.global_risk_approved_after_adjustment",),
        }


class MetaStrategyStep32OrderValidationTest(unittest.TestCase):
    def test_valid_order_passes_all_final_validation_checks(self) -> None:
        snapshot = build_meta_strategy_market_snapshot(request_with())
        intent = build_meta_strategy_order_intent(snapshot=snapshot, side="BUY", quantity=10, stop_price=99.0).intent

        result = validate_meta_strategy_order(valid_context(order_intent=intent, snapshot=snapshot))

        self.assertTrue(result.valid)
        self.assertEqual(result.failures, ())
        self.assertEqual(result.persisted_payload["failureCount"], 0)

    def test_validation_reports_every_required_failure_without_short_circuiting(self) -> None:
        snapshot = build_meta_strategy_market_snapshot(request_with()).model_copy(
            update={
                "quote": {"timestamp": "2026-01-05T15:00:00+00:00"},
                "spread_bps": 999.0,
                "liquidity": {"shareVolume": 1.0},
                "volume": 1.0,
            }
        )
        invalid_intent = types.SimpleNamespace(
            algorithm_id="weighted_voting",
            order_intent_id="duplicate-intent",
            symbol="SPY",
            side="SELL",
            quantity=20.0,
        )

        result = validate_meta_strategy_order(
            valid_context(
                order_intent=invalid_intent,
                snapshot=snapshot,
                model_action="REJECT",
                deterministic_direction="BUY",
                final_direction="BUY",
                sizing_quantity=5,
                global_approved_quantity=4,
                entry_price=-1.0,
                stop_price=99.0,
                target_price=98.0,
                reward_risk=0.0,
                available_buying_power=10.0,
                reserved_risk_dollars=1_000.0,
                maximum_reserved_risk_dollars=10.0,
                session_allowed=False,
                duplicate_intent_ids=("duplicate-intent",),
                existing_position_symbols=("SPY",),
            )
        )

        expected = {
            "meta_strategy.order_validation.invalid_algorithm",
            "meta_strategy.order_validation.direction_mismatch",
            "meta_strategy.order_validation.model_action_not_tradeable",
            "meta_strategy.order_validation.quantity_exceeds_adjusted_cap",
            "meta_strategy.order_validation.invalid_entry",
            "meta_strategy.order_validation.invalid_stop",
            "meta_strategy.order_validation.invalid_target",
            "meta_strategy.order_validation.invalid_reward_risk",
            "meta_strategy.order_validation.buying_power_insufficient",
            "meta_strategy.order_validation.risk_reservation_exceeded",
            "meta_strategy.order_validation.session_blocked",
            "meta_strategy.order_validation.quote_stale",
            "meta_strategy.order_validation.spread_too_wide",
            "meta_strategy.order_validation.liquidity_too_low",
            "meta_strategy.order_validation.duplicate_intent",
            "meta_strategy.order_validation.existing_position_conflict",
        }

        self.assertFalse(result.valid)
        self.assertTrue(expected.issubset(set(result.reason_codes)))
        self.assertEqual(result.persisted_payload["failureCount"], len(result.failures))

    def test_final_validation_runs_after_global_quantity_reduction(self) -> None:
        self.assertLess(
            META_STRATEGY_EXECUTION_PIPELINE_STAGES.index("global_risk"),
            META_STRATEGY_EXECUTION_PIPELINE_STAGES.index("final_validation"),
        )

    def test_invalid_order_does_not_reach_broker_and_failures_are_persisted(self) -> None:
        broker = RecordingBroker()
        persistence = RecordingPersistence()
        original_handler = execution_pipeline._STAGE_HANDLERS["order_intent"]

        def injected_order_intent(state):  # noqa: ANN001
            snapshot = state.snapshot
            result = build_meta_strategy_order_intent(snapshot=snapshot, side="BUY", quantity=1, stop_price=99.0)
            state.order_intent = result.intent
            state.stage_results["order_intent"] = {"status": "CREATED", "quantity": 1, "reasonCodes": result.reason_codes}

        execution_pipeline._STAGE_HANDLERS["order_intent"] = injected_order_intent
        try:
            result = execution_pipeline.run_meta_strategy_execution_pipeline(
                MetaStrategyExecutionPipelineRequest(
                    mode="PAPER",
                    snapshot_request=request_with(),
                    duplicate_order_intent_ids=("meta_strategy.order_intent.decision-1",),
                ),
                broker_adapter=broker,
                persistence_adapter=persistence,
                global_risk_adapter=ApprovingGlobalRisk(),
            )
        finally:
            execution_pipeline._STAGE_HANDLERS["order_intent"] = original_handler

        self.assertFalse(result.order_validation.valid)
        self.assertIsNone(result.order_intent)
        self.assertEqual(broker.calls[-1][0], None)
        self.assertIn("meta_strategy.pipeline.invalid_order_blocked_before_broker", result.reason_codes)
        persisted_validation = persistence.payloads[0]["stageResults"]["final_validation"]
        self.assertFalse(persisted_validation["valid"])
        self.assertGreater(persisted_validation["failureCount"], 0)
        self.assertIn("meta_strategy.order_validation.duplicate_intent", persisted_validation["reasonCodes"])


def valid_context(**overrides):
    snapshot = overrides.pop("snapshot", build_meta_strategy_market_snapshot(request_with()))
    intent = overrides.pop("order_intent", build_meta_strategy_order_intent(snapshot=snapshot, side="BUY", quantity=10, stop_price=99.0).intent)
    values = {
        "order_intent": intent,
        "snapshot": snapshot,
        "model_action": "ACCEPT",
        "deterministic_direction": "BUY",
        "final_direction": "BUY",
        "sizing_quantity": 10,
        "global_approved_quantity": 10,
        "entry_price": 101.5,
        "stop_price": 99.0,
        "target_price": 106.0,
        "reward_risk": 2.0,
        "available_buying_power": 10_000.0,
        "reserved_risk_dollars": 25.0,
        "maximum_reserved_risk_dollars": 1_000.0,
        "session_allowed": True,
        "max_quote_age_seconds": 60,
        "max_spread_bps": 15.0,
        "minimum_liquidity": 50_000.0,
        "duplicate_intent_ids": (),
        "existing_position_symbols": (),
    }
    values.update(overrides)
    return MetaStrategyOrderValidationContext(**values)


if __name__ == "__main__":
    unittest.main()
