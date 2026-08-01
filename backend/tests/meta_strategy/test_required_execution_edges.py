from __future__ import annotations

import unittest
from datetime import timedelta

from backend.tests.test_meta_strategy_phase9_paper_execution import NOW, RuntimeEnv


class MetaStrategyRequiredExecutionEdgesTest(unittest.TestCase):
    def test_multiple_fills_apply_once_and_accumulate_position_quantity(self) -> None:
        env = RuntimeEnv()
        env.create_outbox(quantity=10, reserved_risk=100.0)
        env.submission_worker().run_once(now=NOW)
        env.broker.events.append(env.broker.fill_event(quantity=3, status="PARTIALLY_FILLED", event_id="fill-1"))
        env.broker.events.append(env.broker.fill_event(quantity=7, status="FILLED", event_id="fill-2"))

        env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=10))

        snapshot = env.inventory.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        self.assertEqual(snapshot.open_positions[0].quantity, 10.0)
        self.assertEqual(len(env.inventory.inventory_records("fills", limit=10)), 2)
        self.assertEqual(env.jobs.outbox_for_order_intent("intent-1")["status"], "FILLED")

    def test_unknown_broker_state_is_visible_and_quarantines_inventory_mutation(self) -> None:
        env = RuntimeEnv()
        env.create_outbox(quantity=10, reserved_risk=100.0)
        env.submission_worker().run_once(now=NOW)
        env.broker.events.append(env.broker.status_event(status="UNKNOWN", event_id="unknown-state"))

        result = env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=10))

        outbox = env.jobs.outbox_for_order_intent("intent-1")
        self.assertEqual(result["status"], "OK")
        self.assertEqual(outbox["status"], "RECONCILIATION_REQUIRED")
        self.assertIn("UNKNOWN", str(outbox["payload"]))
        self.assertEqual(env.inventory.current_inventory_snapshot().open_positions, ())


if __name__ == "__main__":
    unittest.main()
