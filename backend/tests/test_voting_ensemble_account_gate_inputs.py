from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from backend.app.algorithms.voting_ensemble.finalized_bar_producer import (
    _account_snapshot_from_inventory,
    _consecutive_losses_from_inventory,
)
from backend.app.algorithms.voting_ensemble.gates import VotingEnsembleLocalGateEngine
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

    def test_account_snapshot_carries_the_streak_on_both_branches(self) -> None:
        event = SimpleNamespace(
            barEndTimestamp=datetime(2026, 9, 2, 15, 10, tzinfo=UTC),
            receivedAt=datetime(2026, 9, 2, 15, 10, 1, tzinfo=UTC),
        )
        three_losers = [closed_trade(f"{SESSION}T14:00:00+00:00", -1.0), closed_trade(f"{SESSION}T14:30:00+00:00", -2.0), closed_trade(f"{SESSION}T14:50:00+00:00", -3.0)]
        with_account = {
            "localPaperAccount": {"equity": 100000.0, "buyingPower": 99000.0, "openPositionNotional": 1000.0, "tradesToday": 3},
            "closedTrades": three_losers,
        }
        self.assertEqual(_account_snapshot_from_inventory(with_account, event)["consecutiveLosses"], 3)
        self.assertEqual(_account_snapshot_from_inventory({"closedTrades": three_losers}, event)["consecutiveLosses"], 3)
        self.assertEqual(_account_snapshot_from_inventory({}, event)["consecutiveLosses"], 0)


class ConsecutiveLossGateTest(unittest.TestCase):
    def test_limit_fires_at_three_as_configured(self) -> None:
        engine = VotingEnsembleLocalGateEngine()
        self.assertEqual(engine.config.maximumConsecutiveLosses, 3)
        two = engine.evaluate(base_gate_input(accountRiskState=account_state(), riskState={"consecutiveLosses": 2})).to_global_gate_decision()
        three = engine.evaluate(base_gate_input(accountRiskState=account_state(), riskState={"consecutiveLosses": 3})).to_global_gate_decision()

        self.assertNotIn("voting_ensemble.local_gate.consecutive_loss_limit", two.reasonCodes)
        self.assertFalse(three.eligible)
        self.assertIn("voting_ensemble.local_gate.consecutive_loss_limit", three.reasonCodes)

    def test_risk_state_reads_the_streak_from_the_account_snapshot(self) -> None:
        snapshot = build_live_paper_snapshot(snapshot_payload(candles(30)))
        after_losses = snapshot.model_copy(update={"accountRiskSnapshot": {**snapshot.accountRiskSnapshot, "consecutiveLosses": 2}})

        self.assertEqual(_risk_state(after_losses)["consecutiveLosses"], 2)
        self.assertEqual(_risk_state(snapshot)["consecutiveLosses"], 0)


if __name__ == "__main__":
    unittest.main()
