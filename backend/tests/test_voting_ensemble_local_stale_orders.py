"""Unfilled local paper entry orders expire.

In LOCAL_PAPER mode nothing ever cancelled a working order. The position-order manager
ran only local maintenance, and the gateway's stale-order sweep, which runs only in
broker mode, did not match the OPEN status the local broker acknowledges with. A limit
buy that never became executable was still live the next session, ready to fill a
day-old signal with a day-old stop and target.
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import patch

from backend.app.algorithms.voting_ensemble.paper_execution import (
    VotingEnsemblePaperExecutionQueue,
    VotingEnsemblePaperExecutionRepository,
    VotingEnsemblePaperExecutionRuntime,
)
from backend.tests.test_voting_ensemble_automatic_paper_execution import NOW, BuyDecisionService, seed_local_quote

ENV = {
    "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
    "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
    "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
}


class LocalStaleOrderExpiryTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.dict("os.environ", ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repository = VotingEnsemblePaperExecutionRepository()
        self.runtime = VotingEnsemblePaperExecutionRuntime(
            repository=self.repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            auto_start=False,
        )
        self.repository.local_account_snapshot(observed_at=NOW)

    def submit_buy(self, *, ask: float) -> dict:
        # The plan is a limit buy at 100.
        seed_local_quote(self.repository, bid=ask - 0.05, ask=ask, ask_size=100)
        enqueued = self.runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-stale",
            idempotency_key="idem-stale",
            source_job_id="job-stale",
            source_command_id="event-stale",
            evaluated_at=NOW,
        )
        self.assertTrue(enqueued["enqueued"], enqueued)
        self.runtime.process_once(evaluated_at=NOW)
        return self.entry_order()

    def entry_order(self) -> dict:
        orders = [order for order in self.runtime.inventory_snapshot()["orders"] if not order.get("protectiveKind")]
        self.assertEqual(len(orders), 1)
        return orders[0]

    def intent_status(self) -> str:
        intents = self.runtime.inventory_snapshot()["orderIntents"]
        self.assertEqual(len(intents), 1)
        return intents[0]["status"]

    def test_an_unexecutable_limit_buy_is_cancelled_once_it_is_stale(self) -> None:
        self.assertEqual(self.submit_buy(ask=100.25)["status"], "OPEN")

        fresh = self.runtime.run_local_position_order_maintenance(evaluated_at=NOW + timedelta(seconds=30))
        self.assertEqual(fresh["staleOrdersCanceled"], 0)
        self.assertEqual(self.entry_order()["status"], "OPEN")

        stale = self.runtime.run_local_position_order_maintenance(evaluated_at=NOW + timedelta(minutes=2))
        self.assertEqual(stale["staleOrdersCanceled"], 1)
        self.assertEqual(self.entry_order()["status"], "CANCELED")
        self.assertEqual(self.intent_status(), "CANCELED")

    def test_a_cancelled_order_cannot_fill_when_the_price_comes_back(self) -> None:
        self.submit_buy(ask=100.25)
        self.runtime.run_local_position_order_maintenance(evaluated_at=NOW + timedelta(days=1))

        seed_local_quote(self.repository, bid=99.9, ask=99.95, ask_size=100, observed_at=NOW + timedelta(days=1))
        self.runtime.run_local_position_order_maintenance(evaluated_at=NOW + timedelta(days=1, seconds=5))
        inventory = self.runtime.inventory_snapshot()

        self.assertEqual(self.entry_order()["status"], "CANCELED")
        self.assertEqual(inventory["positions"], [])
        self.assertEqual(inventory["fills"], [])

    def test_a_filled_entry_is_left_alone(self) -> None:
        self.assertEqual(self.submit_buy(ask=100.0)["status"], "FILLED")

        result = self.runtime.run_local_position_order_maintenance(evaluated_at=NOW + timedelta(minutes=2))

        self.assertEqual(result["staleOrdersCanceled"], 0)
        self.assertEqual(self.entry_order()["status"], "FILLED")
        self.assertEqual(len(self.runtime.inventory_snapshot()["positions"]), 1)


if __name__ == "__main__":
    unittest.main()
