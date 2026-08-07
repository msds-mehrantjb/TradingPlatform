from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.app.algorithms.voting_ensemble.paper_execution import VotingEnsembleLocalPaperExecutionEngine, VotingEnsemblePaperExecutionRepository
from backend.app.domain.models import Signal
from backend.tests.test_voting_ensemble_automatic_paper_execution import NOW, local_engine_intent, seed_local_quote


class VotingEnsembleLocalPaperExecutionTest(unittest.TestCase):
    def test_local_engine_rejects_foreign_orders_and_simulates_owned_fill(self) -> None:
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000", "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0", "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0"}):
            repository = VotingEnsemblePaperExecutionRepository()
            engine = VotingEnsembleLocalPaperExecutionEngine(repository)
            seed_local_quote(repository, ask_size=5)

            foreign = engine.submit_order(local_engine_intent(client_order_id="foreign-local-order", quantity=1, algorithm_id="algorithm_b"))
            accepted = engine.submit_order(local_engine_intent(client_order_id="owned-local-order", quantity=5))
            fill = engine.refresh_order("owned-local-order")

            self.assertEqual(foreign.status, "REJECTED")
            self.assertEqual(foreign.rejectedReason, "voting_ensemble.local_paper.foreign_algorithm_rejected")
            self.assertEqual(accepted.status, "OPEN")
            self.assertIsNotNone(fill)
            assert fill is not None
            self.assertEqual(fill.filledQuantity, 5)
            self.assertEqual(repository.inventory_snapshot()["localPositions"][0]["signedQuantity"], 5)

    def test_exit_quantity_is_capped_to_voting_ensemble_owned_position(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        engine = VotingEnsembleLocalPaperExecutionEngine(repository)
        repository.local_account_snapshot(observed_at=NOW)
        repository.inventory_ledger.apply_fill(
            client_order_id="owned-entry",
            order_intent_id="intent-owned-entry",
            symbol="SPY",
            side=Signal.BUY,
            requested_quantity=3,
            fill_price=100.0,
            filled_at=NOW,
        )

        oversized = engine.submit_order(local_engine_intent(client_order_id="oversized-exit", quantity=10, side=Signal.SELL, limit_price=101.0))

        self.assertEqual(oversized.status, "REJECTED")
        self.assertEqual(oversized.rejectedReason, "voting_ensemble.local_paper.sell_quantity_exceeds_owned_inventory")


if __name__ == "__main__":
    unittest.main()
