from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import patch

from backend.app.algorithms.voting_ensemble.paper_execution import VotingEnsemblePaperExecutionRepository
from backend.app.domain.models import Signal
from backend.tests.test_voting_ensemble_automatic_paper_execution import NOW


class VotingEnsembleLocalPaperAccountingTest(unittest.TestCase):
    def test_initial_local_account_requires_no_alpaca_credentials(self) -> None:
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000"}, clear=True):
            repository = VotingEnsemblePaperExecutionRepository()

            account = repository.local_account_snapshot(observed_at=NOW)
            snapshot = repository.inventory_snapshot()

            self.assertEqual(account["initialCash"], 100000.0)
            self.assertEqual(account["cash"], 100000.0)
            self.assertEqual(account["realizedPnl"], 0.0)
            self.assertEqual(account["unrealizedPnl"], 0.0)
            self.assertEqual(snapshot["localPositions"], [])
            self.assertEqual(snapshot["brokerAccounts"], [])
            self.assertEqual(snapshot["brokerPositions"], [])

    def test_weighted_average_partial_reduction_and_closed_trade_accounting(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
            },
        ):
            repository = VotingEnsemblePaperExecutionRepository()
            ledger = repository.inventory_ledger
            repository.local_account_snapshot(observed_at=NOW)

            ledger.apply_fill(client_order_id="acct-buy-100", order_intent_id="intent-acct-buy-100", symbol="SPY", side=Signal.BUY, requested_quantity=100, fill_price=500.0, filled_at=NOW)
            ledger.apply_fill(client_order_id="acct-buy-50", order_intent_id="intent-acct-buy-50", symbol="SPY", side=Signal.BUY, requested_quantity=50, fill_price=510.0, filled_at=NOW + timedelta(minutes=1))
            after_add = repository.inventory_snapshot()
            self.assertEqual(after_add["localPositions"][0]["signedQuantity"], 150)
            self.assertAlmostEqual(after_add["localPositions"][0]["averageEntryPrice"], 503.333333, places=6)
            self.assertEqual(after_add["localPaperAccount"]["cash"], 24500.0)

            ledger.apply_fill(client_order_id="acct-sell-40", order_intent_id="intent-acct-sell-40", symbol="SPY", side=Signal.SELL, requested_quantity=40, fill_price=520.0, filled_at=NOW + timedelta(minutes=2))
            after_reduce = repository.inventory_snapshot()
            self.assertEqual(after_reduce["localPositions"][0]["signedQuantity"], 110)
            self.assertAlmostEqual(after_reduce["localPositions"][0]["averageEntryPrice"], 503.333333, places=6)
            self.assertAlmostEqual(after_reduce["localPaperAccount"]["realizedPnl"], round((520.0 - 503.3333333333333) * 40, 2), places=2)
            self.assertEqual(after_reduce["localPaperAccount"]["cash"], 45300.0)

            ledger.apply_fill(client_order_id="acct-sell-110", order_intent_id="intent-acct-sell-110", symbol="SPY", side=Signal.SELL, requested_quantity=110, fill_price=515.0, filled_at=NOW + timedelta(minutes=3))
            closed = repository.inventory_snapshot()
            self.assertEqual(closed["localPositions"], [])
            final_close = [trade for trade in closed["closedTrades"] if trade["exitOrderId"] == "acct-sell-110"][0]
            self.assertEqual(final_close["quantity"], 110)
            expected_total_realized = ((520.0 - 503.3333333333333) * 40) + ((515.0 - 503.3333333333333) * 110)
            self.assertAlmostEqual(closed["localPaperAccount"]["realizedPnl"], round(expected_total_realized, 2), places=2)
            self.assertEqual(closed["localPaperAccount"]["cash"], 101950.0)
            self.assertEqual(closed["localPaperAccount"]["tradesToday"], 4)

    def test_duplicate_fill_replay_changes_nothing(self) -> None:
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000", "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0", "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0"}):
            repository = VotingEnsemblePaperExecutionRepository()
            repository.local_account_snapshot(observed_at=NOW)
            fill_kwargs = dict(
                client_order_id="acct-duplicate-buy",
                applied_fill_id="same-fill-id",
                order_intent_id="intent-acct-duplicate-buy",
                symbol="SPY",
                side=Signal.BUY,
                requested_quantity=100,
                fill_price=500.0,
                filled_at=NOW,
            )

            repository.inventory_ledger.apply_fill(**fill_kwargs)
            before = repository.inventory_snapshot()
            repository.inventory_ledger.apply_fill(**fill_kwargs)
            after = repository.inventory_snapshot()

            self.assertEqual(after["localPaperAccount"]["cash"], before["localPaperAccount"]["cash"])
            self.assertEqual(after["localPaperAccount"]["realizedPnl"], before["localPaperAccount"]["realizedPnl"])
            self.assertEqual(after["localPositions"], before["localPositions"])
            self.assertEqual(after["localPaperAccount"]["tradesToday"], before["localPaperAccount"]["tradesToday"])


if __name__ == "__main__":
    unittest.main()
