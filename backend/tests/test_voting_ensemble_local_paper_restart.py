from __future__ import annotations

import json
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.app.algorithms.voting_ensemble.paper_execution import (
    VOTING_ENSEMBLE_ALGORITHM_ID,
    VotingEnsembleLocalPaperExecutionEngine,
    VotingEnsemblePaperExecutionQueue,
    VotingEnsemblePaperExecutionRepository,
    VotingEnsemblePaperExecutionRuntime,
)
from backend.app.domain.models import Signal
from backend.tests.test_voting_ensemble_automatic_paper_execution import NOW, BuyDecisionService, local_engine_intent, order_plan, seed_local_quote


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

    def test_restart_recovers_open_position_cash_realized_pnl_and_open_order_identically(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_focused") / f"restart-full-{uuid4().hex}.json"
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with patch.dict(
            "os.environ",
            {
                "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
            },
        ):
            repository = VotingEnsemblePaperExecutionRepository(store_path)
            engine = VotingEnsembleLocalPaperExecutionEngine(repository)
            seed_local_quote(repository, bid=100.0, ask=100.0, ask_size=10)
            engine.submit_order(local_engine_intent(client_order_id="restart-full-entry", quantity=10, side=Signal.BUY, limit_price=100.0))
            engine.refresh_order("restart-full-entry")
            repository.inventory_ledger.apply_fill(
                client_order_id="restart-full-partial-exit",
                order_intent_id="intent-restart-full-partial-exit",
                symbol="SPY",
                side=Signal.SELL,
                requested_quantity=2,
                fill_price=101.0,
                filled_at=NOW + timedelta(seconds=1),
            )
            seed_local_quote(repository, bid=101.0, ask=101.05, ask_size=100, observed_at=NOW + timedelta(seconds=2))
            engine.submit_order(local_engine_intent(client_order_id="restart-full-open", quantity=1, side=Signal.BUY, limit_price=100.0))
            before = repository.inventory_snapshot()

            restarted_repository = VotingEnsemblePaperExecutionRepository(store_path)
            restarted_runtime = VotingEnsemblePaperExecutionRuntime(repository=restarted_repository, queue=VotingEnsemblePaperExecutionQueue(), auto_start=False)
            recovery = restarted_runtime.reconcile_broker_state(evaluated_at=NOW + timedelta(seconds=3))
            after = restarted_runtime.inventory_snapshot()
            open_order = [order for order in after["localOrders"] if order["clientOrderId"] == "restart-full-open"][0]

            self.assertIsNotNone(recovery)
            assert recovery is not None
            self.assertEqual(recovery["status"], "VALIDATED")
            self.assertTrue(recovery["brokerReconciliationSkipped"])
            self.assertEqual(after["localPaperAccount"]["cash"], before["localPaperAccount"]["cash"])
            self.assertEqual(after["localPaperAccount"]["realizedPnl"], before["localPaperAccount"]["realizedPnl"])
            self.assertEqual(after["localPositions"][0]["signedQuantity"], before["localPositions"][0]["signedQuantity"])
            self.assertEqual(after["localPositions"][0]["averageEntryPrice"], before["localPositions"][0]["averageEntryPrice"])
            self.assertEqual(after["localPositions"][0]["markPrice"], before["localPositions"][0]["markPrice"])
            self.assertEqual(open_order["status"], "OPEN")
            self.assertFalse(after["reconciliationBlocks"])
        store_path.unlink(missing_ok=True)

    def test_corrupt_persistence_blocks_new_entries_but_keeps_safe_exit_enqueuable(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_focused") / f"restart-corrupt-{uuid4().hex}.json"
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with patch.dict(
            "os.environ",
            {
                "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
            },
        ):
            repository = VotingEnsemblePaperExecutionRepository(store_path)
            repository.local_account_snapshot(observed_at=NOW)
            repository.inventory_ledger.apply_fill(
                client_order_id="corrupt-focused-entry",
                order_intent_id="intent-corrupt-focused-entry",
                symbol="SPY",
                side=Signal.BUY,
                requested_quantity=3,
                fill_price=100.0,
                filled_at=NOW,
            )
            payload = json.loads(store_path.read_text(encoding="utf-8"))
            account_key = "voting_ensemble.paper_execution.local_account.latest"
            payload["snapshots"][account_key]["cash"] = 12345.0
            payload["snapshots"][account_key]["cashBalance"] = 12345.0
            store_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            restarted_repository = VotingEnsemblePaperExecutionRepository(store_path)
            restarted_runtime = VotingEnsemblePaperExecutionRuntime(repository=restarted_repository, queue=VotingEnsemblePaperExecutionQueue(), auto_start=False)
            recovery = restarted_runtime.reconcile_broker_state(evaluated_at=NOW + timedelta(seconds=1))
            blocked_entry = restarted_runtime.enqueue_from_decision(
                BuyDecisionService().evaluate({}),
                correlation_id="corr-focused-corrupt-buy",
                idempotency_key="idem-focused-corrupt-buy",
                source_job_id="job-focused-corrupt-buy",
                source_command_id="cmd-focused-corrupt-buy",
                evaluated_at=NOW + timedelta(seconds=2),
            )
            safe_exit = restarted_runtime.enqueue_from_decision(
                {"algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID, "final_signal": "Sell", "safety_gate_failed": False, "order_plan": order_plan(side=Signal.SELL).model_dump(mode="json")},
                correlation_id="corr-focused-corrupt-sell",
                idempotency_key="idem-focused-corrupt-sell",
                source_job_id="job-focused-corrupt-sell",
                source_command_id="cmd-focused-corrupt-sell",
                evaluated_at=NOW + timedelta(seconds=2),
            )

            self.assertIsNotNone(recovery)
            assert recovery is not None
            self.assertEqual(recovery["status"], "LOCAL_CONSISTENCY_REQUIRED")
            self.assertEqual(recovery["recovery"]["status"], "RECOVERY_FAILED")
            self.assertIn("voting_ensemble.local_paper_recovery.cash_fill_invariant_failed", recovery["recovery"]["reasonCodes"])
            self.assertFalse(blocked_entry["enqueued"])
            self.assertIn("voting_ensemble.local_paper_recovery.cash_fill_invariant_failed", blocked_entry["reasonCodes"])
            self.assertTrue(safe_exit["enqueued"])
        store_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
