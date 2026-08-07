from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.app.algorithms.voting_ensemble.paper_execution import VotingEnsemblePaperExecutionRepository
from backend.app.domain.models import Signal
from backend.tests.test_voting_ensemble_automatic_paper_execution import NOW


class VotingEnsembleLocalPaperRestartTest(unittest.TestCase):
    def test_restart_recovers_local_account_position_and_applied_fill_ids(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_focused") / f"restart-{uuid4().hex}.json"
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000", "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0", "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0"}):
            repository = VotingEnsemblePaperExecutionRepository(store_path)
            repository.local_account_snapshot(observed_at=NOW)
            fill_kwargs = dict(client_order_id="restart-focused-buy", order_intent_id="intent-restart-focused-buy", symbol="SPY", side=Signal.BUY, requested_quantity=10, fill_price=100.0, filled_at=NOW)
            repository.inventory_ledger.apply_fill(**fill_kwargs)
            before = repository.inventory_snapshot()

            restarted = VotingEnsemblePaperExecutionRepository(store_path)
            restarted.inventory_ledger.apply_fill(**fill_kwargs)
            after = restarted.inventory_snapshot()

            self.assertEqual(after["localPaperAccount"]["cash"], before["localPaperAccount"]["cash"])
            self.assertEqual(after["localPaperAccount"]["realizedPnl"], before["localPaperAccount"]["realizedPnl"])
            self.assertEqual(after["localPositions"][0]["signedQuantity"], 10)
            self.assertEqual(after["localPositions"][0]["averageEntryPrice"], 100.0)
            self.assertEqual(len(after["localPaperAccount"]["appliedFillIds"]), 1)
        store_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
