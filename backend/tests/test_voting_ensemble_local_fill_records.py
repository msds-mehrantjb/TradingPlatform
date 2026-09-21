"""A fill's local accounting record survives the gateway writing its own view of it.

The local engine records a fill with the fields local recovery validates (schemaVersion,
appliedFillId, realized P&L); the shared gateway then writes its own dump under the same
key, and that dump has none of them. The loss was invisible until the next restart, when
recovery rejected the inventory and every new entry was blocked.
"""

from __future__ import annotations

import unittest

from backend.app.algorithms.voting_ensemble.paper_execution import (
    VOTING_ENSEMBLE_PAPER_GATEWAY_NAMESPACE,
    VotingEnsemblePaperExecutionRepository,
)

FILL_KEY = "paper_order_gateway.fill.ve-paper-test"
STORED_KEY = f"{VOTING_ENSEMBLE_PAPER_GATEWAY_NAMESPACE}.{FILL_KEY}"

LOCAL_RECORD = {
    "schemaVersion": "voting_ensemble_local_fill_v1",
    "appliedFillId": "applied-fill-hash",
    "clientOrderId": "ve-paper-test",
    "side": "BUY",
    "filledQuantity": 13,
    "averageFillPrice": 759.64,
    "realizedPnl": 0.0,
    "executionMode": "LOCAL_PAPER",
}

GATEWAY_DUMP = {
    "clientOrderId": "ve-paper-test",
    "side": "BUY",
    "filledQuantity": 13,
    "averageFillPrice": 759.64,
    "brokerOrderId": "local-ve-paper-test",
    "executionMode": "LOCAL_PAPER",
}


class LocalFillRecordTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = VotingEnsemblePaperExecutionRepository()

    def test_the_gateway_dump_keeps_the_local_accounting_fields(self) -> None:
        self.repository.write_snapshot(FILL_KEY, LOCAL_RECORD)
        self.repository.write_snapshot(FILL_KEY, GATEWAY_DUMP)

        stored = self.repository.snapshots[STORED_KEY]
        self.assertEqual(stored["schemaVersion"], "voting_ensemble_local_fill_v1")
        self.assertEqual(stored["appliedFillId"], "applied-fill-hash")
        # What the gateway does carry still wins.
        self.assertEqual(stored["brokerOrderId"], "local-ve-paper-test")
        self.assertEqual(stored["filledQuantity"], 13)

    def test_a_later_write_updates_the_fields_it_carries(self) -> None:
        self.repository.write_snapshot(FILL_KEY, LOCAL_RECORD)
        self.repository.write_snapshot(FILL_KEY, {**GATEWAY_DUMP, "filledQuantity": 20, "averageFillPrice": 760.0})

        stored = self.repository.snapshots[STORED_KEY]
        self.assertEqual(stored["filledQuantity"], 20)
        self.assertEqual(stored["averageFillPrice"], 760.0)

    def test_other_records_are_replaced_not_merged(self) -> None:
        self.repository.write_snapshot("local_order.ve-paper-test", {"status": "OPEN", "reasonCodes": ["opened"]})
        self.repository.write_snapshot("local_order.ve-paper-test", {"status": "CANCELED"})

        stored = self.repository.snapshots["voting_ensemble.paper_execution.local_order.ve-paper-test"]
        self.assertEqual(stored["status"], "CANCELED")
        self.assertNotIn("reasonCodes", stored)


if __name__ == "__main__":
    unittest.main()
