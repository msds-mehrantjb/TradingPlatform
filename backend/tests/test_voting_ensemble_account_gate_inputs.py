from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from backend.app.algorithms.voting_ensemble.finalized_bar_producer import (
    _account_snapshot_from_inventory,
    _consecutive_losses_from_inventory,
    _open_positions_from_inventory,
)
from backend.app.algorithms.voting_ensemble.gates import VotingEnsembleLocalGateEngine, voting_ensemble_local_gate_config
from backend.app.algorithms.voting_ensemble.service import _risk_state
from backend.app.algorithms.voting_ensemble.snapshot import build_live_paper_snapshot
from backend.tests.test_voting_ensemble_local_gates import account_state, base_gate_input
from backend.tests.test_voting_ensemble_snapshot import candles, snapshot_payload


SESSION = "2026-09-02"


def closed_trade(closed_at: str, realized_pnl: float) -> dict:
    return {"closedAt": closed_at, "realizedPnl": realized_pnl, "symbol": "SPY"}


class ConsecutiveLossesFromInventoryTest(unittest.TestCase):
    """The consecutive-loss gate read an input nothing wrote; it was always zero."""

    def test_counts_todays_trailing_losers_and_a_winner_resets(self) -> None:
        inventory = {
            "closedTrades": [
                closed_trade(f"{SESSION}T14:40:00+00:00", -12.0),
                closed_trade(f"{SESSION}T14:10:00+00:00", 30.0),
                closed_trade(f"{SESSION}T13:50:00+00:00", -5.0),
                closed_trade(f"{SESSION}T13:30:00+00:00", -8.0),
                closed_trade(f"{SESSION}T15:05:00+00:00", -3.0),
            ]
        }
        # Sorted by close time the day reads L L W L L, so the run is two.
        self.assertEqual(_consecutive_losses_from_inventory(inventory, SESSION), 2)

    def test_yesterdays_losses_do_not_carry_over(self) -> None:
        inventory = {"closedTrades": [closed_trade("2026-09-01T15:30:00+00:00", -40.0), closed_trade("2026-09-01T15:50:00+00:00", -40.0)]}
        self.assertEqual(_consecutive_losses_from_inventory(inventory, SESSION), 0)

    def test_open_positions_are_counted_with_their_net_signed_quantity(self) -> None:
        inventory = {"localPositions": [{"signedQuantity": 10}, {"signedQuantity": 0}, {"signedQuantity": -4}]}
        self.assertEqual(_open_positions_from_inventory(inventory), (2, 6.0))
        self.assertEqual(_open_positions_from_inventory({}), (0, 0.0))

    def test_account_snapshot_carries_the_three_inputs(self) -> None:
        event = SimpleNamespace(
            barEndTimestamp=datetime(2026, 9, 2, 15, 10, tzinfo=UTC),
            receivedAt=datetime(2026, 9, 2, 15, 10, 1, tzinfo=UTC),
        )
        inventory = {
            "localPaperAccount": {"equity": 100000.0, "buyingPower": 99000.0, "openPositionNotional": 1000.0, "tradesToday": 3},
            "localPositions": [{"signedQuantity": 10}],
            "closedTrades": [closed_trade(f"{SESSION}T14:00:00+00:00", -1.0), closed_trade(f"{SESSION}T14:30:00+00:00", -2.0), closed_trade(f"{SESSION}T14:50:00+00:00", -3.0)],
        }

        account = _account_snapshot_from_inventory(inventory, event)

        self.assertEqual(account["consecutiveLosses"], 3)
        self.assertEqual(account["openPositionCount"], 1)
        self.assertEqual(account["netSignedQuantity"], 10.0)

        bare = _account_snapshot_from_inventory({"positions": [{"notional": 500.0, "signedQuantity": 5}]}, event)
        self.assertEqual(bare["consecutiveLosses"], 0)
        self.assertEqual(bare["openPositionCount"], 1)


class ConcurrentPositionGateTest(unittest.TestCase):
    def test_cap_blocks_a_second_position_but_lets_a_reversal_flatten(self) -> None:
        engine = VotingEnsembleLocalGateEngine(voting_ensemble_local_gate_config(maximum_concurrent_positions=1))

        blocked = engine.evaluate(
            base_gate_input(accountRiskState=account_state(), riskState={"consecutiveLosses": 0, "openPositionCount": 1, "candidateReducesOpenPosition": False})
        ).to_global_gate_decision()
        flattening = engine.evaluate(
            base_gate_input(accountRiskState=account_state(), riskState={"consecutiveLosses": 0, "openPositionCount": 1, "candidateReducesOpenPosition": True})
        ).to_global_gate_decision()
        flat = engine.evaluate(base_gate_input(accountRiskState=account_state(), riskState={"consecutiveLosses": 0, "openPositionCount": 0})).to_global_gate_decision()

        self.assertFalse(blocked.eligible)
        self.assertIn("voting_ensemble.local_gate.concurrent_position_limit", blocked.reasonCodes)
        self.assertNotIn("voting_ensemble.local_gate.concurrent_position_limit", flattening.reasonCodes)
        self.assertNotIn("voting_ensemble.local_gate.concurrent_position_limit", flat.reasonCodes)

    def test_zero_cap_leaves_the_count_ungated(self) -> None:
        engine = VotingEnsembleLocalGateEngine(voting_ensemble_local_gate_config(maximum_concurrent_positions=0))
        decision = engine.evaluate(base_gate_input(accountRiskState=account_state(), riskState={"consecutiveLosses": 0, "openPositionCount": 5})).to_global_gate_decision()
        self.assertNotIn("voting_ensemble.local_gate.concurrent_position_limit", decision.reasonCodes)

    def test_consecutive_loss_limit_fires_from_the_account_input(self) -> None:
        engine = VotingEnsembleLocalGateEngine()
        two = engine.evaluate(base_gate_input(accountRiskState=account_state(), riskState={"consecutiveLosses": 2})).to_global_gate_decision()
        three = engine.evaluate(base_gate_input(accountRiskState=account_state(), riskState={"consecutiveLosses": 3})).to_global_gate_decision()

        self.assertNotIn("voting_ensemble.local_gate.consecutive_loss_limit", two.reasonCodes)
        self.assertFalse(three.eligible)
        self.assertIn("voting_ensemble.local_gate.consecutive_loss_limit", three.reasonCodes)


class RiskStateFromAccountSnapshotTest(unittest.TestCase):
    def test_risk_state_reads_the_inputs_and_flags_a_reducing_candidate(self) -> None:
        snapshot = build_live_paper_snapshot(snapshot_payload(candles(30)))
        long_ten = snapshot.model_copy(update={"accountRiskSnapshot": {**snapshot.accountRiskSnapshot, "consecutiveLosses": 2, "openPositionCount": 1, "netSignedQuantity": 10.0}})

        selling = _risk_state(long_ten, SimpleNamespace(direction="short"))
        buying = _risk_state(long_ten, SimpleNamespace(direction="long"))
        no_candidate = _risk_state(long_ten)

        self.assertEqual(selling["consecutiveLosses"], 2)
        self.assertEqual(selling["openPositionCount"], 1)
        self.assertTrue(selling["candidateReducesOpenPosition"])
        self.assertFalse(buying["candidateReducesOpenPosition"])
        self.assertFalse(no_candidate["candidateReducesOpenPosition"])


if __name__ == "__main__":
    unittest.main()
