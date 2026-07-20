from __future__ import annotations

import unittest

from backend.app.algorithms.meta_strategy import (
    MetaStrategyIdempotencyStore,
    NoopMetaStrategyBrokerAdapter,
    ReadOnlyMetaStrategyGlobalRiskAdapter,
    build_meta_strategy_market_snapshot,
    build_meta_strategy_order_intent,
    reconcile_meta_strategy_broker_fill,
    run_meta_strategy_execution_pipeline,
)
from backend.app.algorithms.meta_strategy.execution_pipeline import MetaStrategyExecutionPipelineRequest
from backend.tests.test_meta_strategy_step7_market_snapshot import DECISION_TIMESTAMP, request_with


def order_intent(quantity: int = 10):
    snapshot = build_meta_strategy_market_snapshot(request_with())
    result = build_meta_strategy_order_intent(
        snapshot=snapshot,
        side="BUY",
        quantity=quantity,
        stop_price=snapshot.last_price - 1.0,
    )
    assert result.intent is not None
    return result.intent


class TimeoutTransport:
    def __init__(self) -> None:
        self.calls = 0

    def submit(self, order_intent, *, idempotency_key: str, mode: str):  # noqa: ANN001
        self.calls += 1
        raise TimeoutError("broker did not acknowledge before deadline")


class MetaStrategyStep33GlobalRiskBrokerAdaptersTest(unittest.TestCase):
    def test_global_risk_can_reject_reduce_and_reserve_without_mutating_authoritative_decision(self) -> None:
        intent = order_intent(quantity=10)
        adapter = ReadOnlyMetaStrategyGlobalRiskAdapter(max_quantity=5, available_risk_dollars=100.0, stop_distance=2.0)

        reduced = adapter.apply(intent, requested_quantity=12)

        self.assertEqual(reduced["algorithmId"], "meta_strategy")
        self.assertEqual(reduced["approvedQuantity"], 5)
        self.assertEqual(reduced["reservedRiskDollars"], 10.0)
        self.assertLessEqual(reduced["approvedQuantity"], int(intent.quantity))
        self.assertFalse(reduced["candidateRewritten"])
        self.assertFalse(reduced["modelProbabilityChanged"])
        self.assertFalse(reduced["settingsChanged"])
        self.assertFalse(reduced["protectiveExitsRemoved"])

        rejected = ReadOnlyMetaStrategyGlobalRiskAdapter(reject=True).apply(intent, requested_quantity=10)

        self.assertEqual(rejected["status"], "REJECTED")
        self.assertEqual(rejected["approvedQuantity"], 0)
        self.assertEqual(rejected["algorithm_id"], "meta_strategy")

    def test_global_risk_cannot_increase_quantity(self) -> None:
        intent = order_intent(quantity=4)
        adapter = ReadOnlyMetaStrategyGlobalRiskAdapter(max_quantity=999, available_risk_dollars=999_999.0, stop_distance=1.0)

        result = adapter.apply(intent, requested_quantity=999)

        self.assertEqual(result["approvedQuantity"], 4)

    def test_duplicate_submissions_are_prevented(self) -> None:
        store = MetaStrategyIdempotencyStore()
        broker = NoopMetaStrategyBrokerAdapter(idempotency_store=store)
        intent = order_intent(quantity=7)

        first = broker.submit(intent, mode="PAPER")
        second = broker.submit(intent, mode="PAPER")

        self.assertTrue(first["submitted"])
        self.assertEqual(first["status"], "PAPER_ACCEPTED")
        self.assertFalse(second["submitted"])
        self.assertEqual(second["status"], "DUPLICATE_SUPPRESSED")
        self.assertEqual(second["algorithmId"], "meta_strategy")
        self.assertEqual(second["idempotencyRecord"]["algorithmId"], "meta_strategy")

    def test_broker_timeouts_do_not_create_duplicate_orders(self) -> None:
        transport = TimeoutTransport()
        broker = NoopMetaStrategyBrokerAdapter(transport=transport, live_enabled=True)
        intent = order_intent(quantity=3)

        first = broker.submit(intent, mode="LIVE")
        second = broker.submit(intent, mode="LIVE")

        self.assertEqual(first["status"], "TIMEOUT")
        self.assertFalse(first["submitted"])
        self.assertEqual(second["status"], "DUPLICATE_SUPPRESSED")
        self.assertEqual(transport.calls, 1)
        self.assertEqual(first["algorithmId"], "meta_strategy")

    def test_partial_fills_are_reconciled_with_algorithm_attribution(self) -> None:
        record = reconcile_meta_strategy_broker_fill(
            planned_quantity=100,
            filled_quantity=25,
            position_id="meta_strategy.position.partial",
            symbol="SPY",
            side="BUY",
            average_fill_price=101.5,
            filled_at=DECISION_TIMESTAMP,
            protective_stop=100.5,
            profit_target=103.5,
            maximum_holding_minutes=30,
        )

        self.assertEqual(record.algorithm_id, "meta_strategy")
        self.assertEqual(record.status, "PARTIAL")
        self.assertEqual(record.filled_quantity, 25)
        self.assertEqual(record.protective_order_quantity, 25)
        self.assertIn("meta_strategy.trade.partial_fill_tracked", record.reason_codes)

    def test_pipeline_shared_records_keep_meta_strategy_algorithm_id(self) -> None:
        result = run_meta_strategy_execution_pipeline(
            MetaStrategyExecutionPipelineRequest(mode="PAPER", snapshot_request=request_with())
        )

        self.assertEqual(result.global_risk["algorithmId"], "meta_strategy")
        self.assertEqual(result.broker_result["algorithmId"], "meta_strategy")
        if result.reconciliation is not None:
            self.assertEqual(result.reconciliation.algorithm_id, "meta_strategy")


if __name__ == "__main__":
    unittest.main()
