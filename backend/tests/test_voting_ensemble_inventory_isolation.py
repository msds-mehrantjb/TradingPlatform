from __future__ import annotations

import unittest
from datetime import timedelta

from backend.app.algorithms.voting_ensemble.paper_execution import (
    VOTING_ENSEMBLE_ALGORITHM_ID,
    VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
    VotingEnsemblePaperExecutionQueue,
    VotingEnsemblePaperExecutionRepository,
    VotingEnsemblePaperExecutionRuntime,
)
from backend.app.domain.models import Signal
from backend.tests.test_voting_ensemble_automatic_paper_execution import NOW, order_plan, seed_local_quote


class VotingEnsembleInventoryIsolationTest(unittest.TestCase):
    def test_foreign_same_symbol_inventory_is_read_only_and_never_mutated_by_ve_exit(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        repository.local_account_snapshot(observed_at=NOW)
        repository.inventory_ledger.apply_fill(client_order_id="ve-isolated-buy", order_intent_id="intent-ve-isolated-buy", symbol="SPY", side=Signal.BUY, requested_quantity=100, fill_price=100.0, filled_at=NOW)
        repository.snapshots["global_risk.read_only_position.algorithm_b.spy"] = {
            "algorithmId": "algorithm_b",
            "capitalPartitionId": "algorithm_b.paper.default",
            "symbol": "SPY",
            "side": "LONG",
            "quantity": 50,
            "marketValue": 5000.0,
            "readOnly": True,
            "sourceAuthority": "global_risk.read_only_aggregate",
        }
        seed_local_quote(repository, bid=101.0, ask=101.05, bid_size=200, observed_at=NOW + timedelta(seconds=1))
        runtime = VotingEnsemblePaperExecutionRuntime(repository=repository, queue=VotingEnsemblePaperExecutionQueue(), auto_start=False)
        sell_plan = order_plan(side=Signal.SELL).model_copy(update={"quantity": 150})

        runtime.enqueue_from_decision(
            {"algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID, "final_signal": "Sell", "safety_gate_failed": False, "order_plan": sell_plan.model_dump(mode="json")},
            correlation_id="corr-focused-isolation",
            idempotency_key="idem-focused-isolation",
            source_job_id="job-focused-isolation",
            source_command_id="cmd-focused-isolation",
            evaluated_at=NOW + timedelta(seconds=2),
        )
        result = runtime.process_once(evaluated_at=NOW + timedelta(seconds=3))
        inventory = runtime.inventory_snapshot()

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result["submitted"])
        self.assertEqual(inventory["localPositions"], [])
        self.assertEqual(inventory["closedTrades"][0]["quantity"], 100)
        self.assertFalse(any(position.get("algorithmId") == "algorithm_b" for position in inventory["localPositions"]))
        self.assertEqual(inventory["closedTrades"][0]["algorithmId"], VOTING_ENSEMBLE_ALGORITHM_ID)
        self.assertEqual(inventory["closedTrades"][0]["capitalPartitionId"], VOTING_ENSEMBLE_CAPITAL_PARTITION_ID)


if __name__ == "__main__":
    unittest.main()
