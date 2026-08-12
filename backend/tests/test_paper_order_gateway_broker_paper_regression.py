from __future__ import annotations

import shutil
import unittest
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderGateway
from backend.app.execution import cost_model
from backend.app.execution.paper_order_gateway import deterministic_gateway_client_order_id
from backend.app.gates import GlobalGateResponse, GlobalOrderProposal, apply_global_gate_response


NOW = datetime(2026, 7, 14, 15, 30, tzinfo=UTC)
SESSION_DATE = date(2026, 7, 14)


class SharedPaperOrderGatewayBrokerPaperRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = Path("backend/.test_artifacts") / f"shared_broker_paper_{uuid.uuid4().hex}"
        shutil.rmtree(self.scratch, ignore_errors=True)
        self.previous_dirs = (
            cost_model.EXECUTION_COST_LEDGER_DIR,
            cost_model.EXECUTION_COST_CANDIDATE_DIR,
            cost_model.EXECUTION_COST_ACTIVE_DIR,
            cost_model.EXECUTION_COST_ACTIVE_HISTORY_DIR,
        )
        cost_model.EXECUTION_COST_LEDGER_DIR = self.scratch / "ledger"
        cost_model.EXECUTION_COST_CANDIDATE_DIR = self.scratch / "artifacts" / "candidates"
        cost_model.EXECUTION_COST_ACTIVE_DIR = self.scratch / "artifacts" / "active"
        cost_model.EXECUTION_COST_ACTIVE_HISTORY_DIR = self.scratch / "artifacts" / "active_history"

    def tearDown(self) -> None:
        (
            cost_model.EXECUTION_COST_LEDGER_DIR,
            cost_model.EXECUTION_COST_CANDIDATE_DIR,
            cost_model.EXECUTION_COST_ACTIVE_DIR,
            cost_model.EXECUTION_COST_ACTIVE_HISTORY_DIR,
        ) = self.previous_dirs
        shutil.rmtree(self.scratch, ignore_errors=True)

    def test_broker_paper_path_for_voting_ensemble_still_uses_shared_broker(self) -> None:
        broker = RecordingPaperBroker()
        store = MemoryStore()
        gateway = PaperOrderGateway(broker, store, execution_mode="BROKER_PAPER")
        proposal = global_proposal(
            algorithm_id="voting_ensemble",
            capital_partition_id="voting_ensemble.paper.default",
            order_intent_id="ve-broker-paper-regression",
        )

        result = gateway.submit(
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_passed=True,
            mode="automatic",
            evaluated_at=NOW,
        )

        expected_client_order_id = deterministic_gateway_client_order_id(proposal)
        self.assertTrue(result.submitted)
        self.assertFalse(result.duplicate)
        self.assertEqual(result.executionMode, "BROKER_PAPER")
        self.assertEqual(result.algorithmId, "voting_ensemble")
        self.assertEqual(result.clientOrderId, expected_client_order_id)
        self.assertEqual(result.status, "ACCEPTED")
        self.assertEqual(result.reasonCodes, ("paper_gateway.submitted",))

        self.assertTrue(broker.verified)
        self.assertEqual(broker.submit_count, 1)
        self.assertEqual(broker.refresh_order_ids, [expected_client_order_id])
        self.assertIsNotNone(broker.last_intent)
        assert broker.last_intent is not None
        self.assertEqual(broker.last_intent.executionMode, "BROKER_PAPER")
        self.assertEqual(broker.last_intent.algorithmId, "voting_ensemble")
        self.assertTrue(broker.last_intent.paperAccountVerified)

        intent = store.snapshots[f"paper_order_gateway.intent.{proposal.orderIntentId}"]
        self.assertEqual(intent["executionMode"], "BROKER_PAPER")
        self.assertEqual(intent["algorithmId"], "voting_ensemble")
        self.assertEqual(intent["status"], "ACCEPTED")
        self.assertTrue(intent["paperAccountVerified"])
        self.assertIn(f"paper_order_gateway.client_order.{expected_client_order_id}", store.snapshots)
        self.assertIn(f"paper_order_gateway.global_risk.{proposal.orderIntentId}", store.snapshots)
        self.assertIn(f"paper_order_gateway.result.{proposal.orderIntentId}", store.snapshots)
        self.assertFalse(any(key.startswith("weighted_voting.") for key in store.snapshots))


    def test_broker_fill_missing_ownership_is_enriched_from_submitted_intent(self) -> None:
        broker = OwnershipOmittingFillBroker()
        store = MemoryStore()
        gateway = PaperOrderGateway(broker, store, execution_mode="BROKER_PAPER")
        proposal = global_proposal(
            algorithm_id="regime",
            capital_partition_id="regime.paper-account.paper",
            order_intent_id="regime-fill-ownership-normalized",
        )

        result = gateway.submit(
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_passed=True,
            mode="automatic",
            evaluated_at=NOW,
        )

        self.assertTrue(result.submitted)
        self.assertIsNotNone(result.fill)
        assert result.fill is not None
        self.assertEqual(result.fill.algorithmId, "regime")
        self.assertEqual(result.fill.capitalPartitionId, "regime.paper-account.paper")
        self.assertEqual(result.fill.accountId, "paper-account")
        self.assertIsNotNone(result.protectiveOrder)
        assert result.protectiveOrder is not None
        self.assertEqual(result.protectiveOrder.capitalPartitionId, "regime.paper-account.paper")
        self.assertEqual(result.protectiveOrder.accountId, "paper-account")

class RecordingPaperBroker:
    def __init__(self) -> None:
        self.verified = False
        self.submit_count = 0
        self.last_intent: Any | None = None
        self.refresh_order_ids: list[str] = []

    def verify_paper_account(self) -> bool:
        self.verified = True
        return True

    def submit_bracket_order(self, intent: Any) -> PaperGatewayBrokerAck:
        self.submit_count += 1
        self.last_intent = intent
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=f"broker-{intent.clientOrderId}",
            status="ACCEPTED",
            acceptedAt=NOW,
        )

    def refresh_order(self, client_order_id: str):
        self.refresh_order_ids.append(client_order_id)
        return None

    def cancel_order(self, client_order_id: str) -> bool:
        return True

    def refresh_positions(self) -> list[dict[str, Any]]:
        return []


class OwnershipOmittingFillBroker(RecordingPaperBroker):
    def refresh_order(self, client_order_id: str):
        self.refresh_order_ids.append(client_order_id)
        return PaperGatewayFill(
            clientOrderId=client_order_id,
            algorithmId="regime",
            orderIntentId="regime-fill-ownership-normalized",
            symbol="SPY",
            side=Signal.BUY,
            filledQuantity=1,
            averageFillPrice=100.0,
            status="FILLED",
            filledAt=NOW,
        )

class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot


def global_proposal(
    *,
    algorithm_id: str,
    capital_partition_id: str,
    order_intent_id: str,
    quantity: int = 1,
) -> GlobalOrderProposal:
    return GlobalOrderProposal(
        algorithmId=algorithm_id,
        capitalPartitionId=capital_partition_id,
        decisionId=f"{order_intent_id}.decision",
        orderIntentId=order_intent_id,
        intent="new_entry",
        symbol="SPY",
        side=Signal.BUY,
        quantity=quantity,
        triggerPrice=100.0,
        limitPrice=100.0,
        stopPrice=99.0,
        targetPrice=102.0,
        plannedRiskDollars=float(quantity),
        settingsSnapshot={"settings_version": "broker-paper-regression"},
        entryFormula={"kind": "limit"},
        stopFormula={"kind": "structural"},
        targetFormula={"kind": "r_multiple"},
        strategyStateHash="strategy-state",
        proposedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=f"{order_intent_id}.config",
    )


def global_application(proposal: GlobalOrderProposal):
    response = GlobalGateResponse(
        action="ALLOW",
        maximumAllowedQuantity=proposal.quantity,
        maximumAdditionalRiskDollars=proposal.plannedRiskDollars,
        rejectionReasons=(),
        evaluatedAt=NOW,
        configurationHash=f"{proposal.orderIntentId}.global",
    )
    return apply_global_gate_response(proposal, response)


if __name__ == "__main__":
    unittest.main()
