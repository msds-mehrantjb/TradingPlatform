from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import patch

from backend.app.algorithms.voting_ensemble.paper_execution import (
    VOTING_ENSEMBLE_ALGORITHM_ID,
    VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
    VotingEnsemblePaperExecutionQueue,
    VotingEnsemblePaperExecutionRepository,
    VotingEnsemblePaperExecutionRuntime,
)
from backend.app.domain.models import Signal
from backend.tests.test_voting_ensemble_automatic_paper_execution import NOW, BuyDecisionService, order_plan, seed_local_quote


class VotingEnsembleLocalRiskIntegrationTest(unittest.TestCase):
    def test_global_and_local_risk_use_voting_ensemble_local_buying_power(self) -> None:
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "50"}):
            repository = VotingEnsemblePaperExecutionRepository()
            repository.snapshots["global_risk.read_only_account.algorithm_b"] = {"algorithmId": "algorithm_b", "buyingPower": 1_000_000.0, "readOnly": True}
            seed_local_quote(repository)
            runtime = VotingEnsemblePaperExecutionRuntime(repository=repository, queue=VotingEnsemblePaperExecutionQueue(), auto_start=False)

            runtime.enqueue_from_decision(
                BuyDecisionService().evaluate({}),
                correlation_id="corr-focused-risk",
                idempotency_key="idem-focused-risk",
                source_job_id="job-focused-risk",
                source_command_id="cmd-focused-risk",
                evaluated_at=NOW,
            )
            result = runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))

            self.assertIsNotNone(result)
            assert result is not None
            self.assertFalse(result["submitted"])
            risk_account = repository.read_snapshot(f"paper_order_gateway.global_risk_account.{result['gatewayResult']['orderIntentId']}")
            self.assertEqual(risk_account["accountId"], VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID)
            self.assertEqual(risk_account["availableBuyingPower"], 50.0)
            self.assertEqual(runtime.inventory_snapshot()["localPositions"], [])

    def test_daily_loss_and_drawdown_block_new_entry_but_allow_existing_position_exit(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
            },
        ):
            repository = VotingEnsemblePaperExecutionRepository()
            repository.local_account_snapshot(observed_at=NOW)
            repository.inventory_ledger.apply_fill(
                client_order_id="risk-block-existing-long",
                order_intent_id="intent-risk-block-existing-long",
                symbol="SPY",
                side=Signal.BUY,
                requested_quantity=3,
                fill_price=100.0,
                filled_at=NOW,
            )
            seed_local_quote(repository, bid=101.0, ask=101.05, bid_size=3, observed_at=NOW + timedelta(seconds=1))
            repository.write_snapshot(
                "local_account.latest",
                {
                    **repository.local_account_snapshot(observed_at=NOW + timedelta(seconds=1)),
                    "cash": 0.0,
                    "cashBalance": 0.0,
                    "buyingPower": 0.0,
                    "usableEntryBuyingPower": 0.0,
                    "dailyNetPnl": -5000.0,
                    "dailyNetPnlAfterExitCosts": -5000.0,
                    "drawdownPercent": 10.0,
                    "drawdownFromIntradayHighPercent": 10.0,
                    "tradesToday": 99,
                },
            )
            runtime = VotingEnsemblePaperExecutionRuntime(
                repository=repository,
                queue=VotingEnsemblePaperExecutionQueue(),
                entry_permission_provider=lambda: {
                    "newEntriesAllowed": False,
                    "effectivePaperTradingEnabled": False,
                    "reasonCodes": ["paper.off", "daily.loss", "drawdown.limit"],
                },
                auto_start=False,
            )

            blocked_entry = runtime.enqueue_from_decision(
                BuyDecisionService().evaluate({}),
                correlation_id="corr-focused-risk-block-buy",
                idempotency_key="idem-focused-risk-block-buy",
                source_job_id="job-focused-risk-block-buy",
                source_command_id="cmd-focused-risk-block-buy",
                evaluated_at=NOW + timedelta(seconds=1),
            )
            allowed_exit = runtime.enqueue_from_decision(
                {"algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID, "final_signal": "Sell", "safety_gate_failed": False, "order_plan": order_plan(side=Signal.SELL).model_dump(mode="json")},
                correlation_id="corr-focused-risk-block-sell",
                idempotency_key="idem-focused-risk-block-sell",
                source_job_id="job-focused-risk-block-sell",
                source_command_id="cmd-focused-risk-block-sell",
                evaluated_at=NOW + timedelta(seconds=1),
            )
            result = runtime.process_once(evaluated_at=NOW + timedelta(seconds=2))
            inventory = runtime.inventory_snapshot()

            self.assertFalse(blocked_entry["enqueued"])
            self.assertIn("paper.off", blocked_entry["reasonCodes"])
            self.assertTrue(allowed_exit["enqueued"])
            self.assertIsNotNone(result)
            assert result is not None
            self.assertTrue(result["submitted"])
            self.assertEqual(inventory["localPositions"], [])
            gateway_account = repository.read_snapshot(f"paper_order_gateway.global_risk_account.{result['gatewayResult']['orderIntentId']}")
            self.assertEqual(gateway_account["accountId"], VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID)
            self.assertEqual(gateway_account["availableBuyingPower"], 0.0)


if __name__ == "__main__":
    unittest.main()
