from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.app.algorithms.voting_ensemble.paper_execution import (
    VotingEnsembleLocalPaperExecutionEngine,
    VotingEnsemblePaperExecutionNamespaceError,
    VotingEnsemblePaperExecutionQueue,
    VotingEnsemblePaperExecutionRepository,
    VotingEnsemblePaperExecutionRuntime,
)
from backend.app.domain.models import Signal
from backend.tests.test_voting_ensemble_automatic_paper_execution import NOW, BuyDecisionService, local_engine_intent, seed_local_quote


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

    def test_insufficient_cash_rejects_new_entry(self) -> None:
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "1000"}):
            repository = VotingEnsemblePaperExecutionRepository()
            engine = VotingEnsembleLocalPaperExecutionEngine(repository)

            ack = engine.submit_order(local_engine_intent(client_order_id="cash-too-large", quantity=3, limit_price=500.0))

            self.assertEqual(ack.status, "REJECTED")
            self.assertEqual(ack.rejectedReason, "voting_ensemble.local_paper.insufficient_buying_power")

    def test_partial_fill_keeps_order_position_cash_and_average_cost_synchronized(self) -> None:
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_MAX_PARTICIPATION_PCT": "50", "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0", "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0"}):
            repository = VotingEnsemblePaperExecutionRepository()
            engine = VotingEnsembleLocalPaperExecutionEngine(repository)
            seed_local_quote(repository, bid=500.0, ask=500.0, ask_size=40)
            engine.submit_order(local_engine_intent(client_order_id="partial-sync", quantity=100, limit_price=500.0))

            fill = engine.refresh_order("partial-sync")
            inventory = repository.inventory_snapshot()
            order = [item for item in inventory["localOrders"] if item["clientOrderId"] == "partial-sync"][0]

            self.assertIsNotNone(fill)
            assert fill is not None
            self.assertEqual(fill.filledQuantity, 20)
            self.assertEqual(order["status"], "PARTIALLY_FILLED")
            self.assertEqual(order["filledQuantity"], 20)
            self.assertEqual(order["quantity"] - order["filledQuantity"], 80)
            self.assertEqual(inventory["localPositions"][0]["signedQuantity"], 20)
            self.assertEqual(inventory["localPositions"][0]["averageEntryPrice"], 500.0)
            self.assertEqual(inventory["localPaperAccount"]["cash"], 90000.0)

    def test_stop_loss_reduces_same_inventory_and_realizes_pnl(self) -> None:
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0", "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0"}):
            repository = VotingEnsemblePaperExecutionRepository()
            engine = VotingEnsembleLocalPaperExecutionEngine(repository)
            seed_local_quote(repository, bid=100.0, ask=100.0, ask_size=3)
            engine.submit_order(local_engine_intent(client_order_id="stop-entry", quantity=3, limit_price=100.0))
            engine.refresh_order("stop-entry")
            seed_local_quote(repository, bid=99.0, ask=99.05, bid_size=3)

            stop_fill = engine.refresh_order("stop-entry-stop")
            inventory = repository.inventory_snapshot()

            self.assertIsNotNone(stop_fill)
            assert stop_fill is not None
            self.assertEqual(stop_fill.filledQuantity, 3)
            self.assertEqual(inventory["localPositions"], [])
            self.assertEqual(inventory["localPaperAccount"]["realizedPnl"], -3.0)
            self.assertEqual(inventory["closedTrades"][0]["exitOrderId"], "stop-entry-stop")

    def test_profit_target_fills_and_cancels_sibling_stop(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        engine = VotingEnsembleLocalPaperExecutionEngine(repository)
        seed_local_quote(repository, bid=100.0, ask=100.0, ask_size=3)
        engine.submit_order(local_engine_intent(client_order_id="target-entry", quantity=3, limit_price=100.0))
        engine.refresh_order("target-entry")
        seed_local_quote(repository, bid=101.5, ask=101.55, bid_size=3)

        target_fill = engine.refresh_order("target-entry-target")
        inventory = repository.inventory_snapshot()
        stop = [order for order in inventory["localOrders"] if order["clientOrderId"] == "target-entry-stop"][0]

        self.assertIsNotNone(target_fill)
        self.assertEqual(inventory["localPositions"], [])
        self.assertEqual(stop["status"], "CANCELED")
        self.assertIn("voting_ensemble.local_paper_execution_engine.oco_sibling_canceled_after_exit_fill", stop["reasonCodes"])

    def test_local_mode_never_accepts_broker_trading_client_or_calls_broker_endpoints(self) -> None:
        class FailingBrokerClient:
            def refresh_account_snapshot(self):  # pragma: no cover - must not be called
                raise AssertionError("/account must not be called in LOCAL_PAPER")

            def refresh_positions(self):  # pragma: no cover - must not be called
                raise AssertionError("/positions must not be called in LOCAL_PAPER")

            def refresh_open_orders(self):  # pragma: no cover - must not be called
                raise AssertionError("/orders must not be called in LOCAL_PAPER")

        with self.assertRaises(VotingEnsemblePaperExecutionNamespaceError):
            VotingEnsemblePaperExecutionRuntime(repository=VotingEnsemblePaperExecutionRepository(), queue=VotingEnsemblePaperExecutionQueue(), broker_client=FailingBrokerClient(), execution_mode="LOCAL_PAPER", auto_start=False)

        with patch("backend.app.algorithms.voting_ensemble.paper_execution._default_paper_broker_client", side_effect=AssertionError("LOCAL_PAPER must not create a broker trading client")):
            repository = VotingEnsemblePaperExecutionRepository()
            seed_local_quote(repository)
            runtime = VotingEnsemblePaperExecutionRuntime(repository=repository, queue=VotingEnsemblePaperExecutionQueue(), auto_start=False)
            runtime.enqueue_from_decision(BuyDecisionService().evaluate({}), correlation_id="corr-no-broker", idempotency_key="idem-no-broker", source_job_id="job-no-broker", source_command_id="cmd-no-broker", evaluated_at=NOW)
            result = runtime.process_once(evaluated_at=NOW)

            self.assertIsNotNone(result)
            assert result is not None
            self.assertTrue(result["submitted"])
            self.assertIsNone(runtime.broker_client)

    def test_live_execution_mode_is_not_supported_for_voting_ensemble_paper_runtime(self) -> None:
        with self.assertRaisesRegex(
            VotingEnsemblePaperExecutionNamespaceError,
            "LOCAL_PAPER or BROKER_PAPER",
        ):
            VotingEnsemblePaperExecutionRuntime(
                repository=VotingEnsemblePaperExecutionRepository(),
                queue=VotingEnsemblePaperExecutionQueue(),
                execution_mode="LIVE",
                auto_start=False,
            )


if __name__ == "__main__":
    unittest.main()
