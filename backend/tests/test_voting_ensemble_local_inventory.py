from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.app.algorithms.voting_ensemble.paper_execution import (
    VOTING_ENSEMBLE_ALGORITHM_ID,
    VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
    VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
    VotingEnsemblePaperExecutionRepository,
)
from backend.app.domain.models import Signal
from backend.tests.test_voting_ensemble_automatic_paper_execution import NOW, seed_local_quote


class VotingEnsembleLocalInventoryTest(unittest.TestCase):
    def test_inventory_snapshot_exposes_local_names_and_no_broker_authority(self) -> None:
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000"}):
            repository = VotingEnsemblePaperExecutionRepository()
            repository.local_account_snapshot(observed_at=NOW)
            repository.inventory_ledger.apply_fill(
                client_order_id="local-inventory-buy",
                order_intent_id="intent-local-inventory-buy",
                symbol="SPY",
                side=Signal.BUY,
                requested_quantity=4,
                fill_price=100.0,
                filled_at=NOW,
            )
            seed_local_quote(repository, bid=101.0, ask=101.05, observed_at=NOW)

            snapshot = repository.inventory_snapshot()

            self.assertEqual(snapshot["executionMode"], "LOCAL_PAPER")
            self.assertEqual(snapshot["sourceAuthority"], "voting_ensemble_local_paper_account")
            self.assertEqual(snapshot["localPaperAccount"]["accountId"], VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID)
            self.assertEqual(snapshot["localPositions"], snapshot["positions"])
            self.assertEqual(snapshot["localOrders"], snapshot["orders"])
            self.assertEqual(snapshot["recentFills"][0]["clientOrderId"], "local-inventory-buy")
            self.assertEqual(snapshot["brokerPositions"], [])
            self.assertEqual(snapshot["brokerAccounts"], [])
            for record in [snapshot["localPaperAccount"], *snapshot["localPositions"], *snapshot["recentFills"]]:
                self.assertEqual(record["algorithmId"], VOTING_ENSEMBLE_ALGORITHM_ID)
                self.assertEqual(record["capitalPartitionId"], VOTING_ENSEMBLE_CAPITAL_PARTITION_ID)


if __name__ == "__main__":
    unittest.main()
