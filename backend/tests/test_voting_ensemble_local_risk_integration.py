from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import patch

from backend.app.algorithms.voting_ensemble.paper_execution import (
    VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
    VotingEnsemblePaperExecutionQueue,
    VotingEnsemblePaperExecutionRepository,
    VotingEnsemblePaperExecutionRuntime,
)
from backend.tests.test_voting_ensemble_automatic_paper_execution import NOW, BuyDecisionService, seed_local_quote


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


if __name__ == "__main__":
    unittest.main()
