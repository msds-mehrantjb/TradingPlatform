from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import patch

from backend.app.algorithms.voting_ensemble.exit_policy import VOTING_ENSEMBLE_DEFAULT_MAX_HOLDING_MINUTES
from backend.app.algorithms.voting_ensemble.paper_execution import (
    VotingEnsembleLocalPaperExecutionEngine,
    VotingEnsemblePaperExecutionRepository,
)
from backend.app.domain.models import Signal
from backend.tests.test_voting_ensemble_automatic_paper_execution import NOW, local_engine_intent, seed_local_quote

NO_FEES = {"VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0", "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0"}


def engine_with_repository() -> tuple[VotingEnsembleLocalPaperExecutionEngine, VotingEnsemblePaperExecutionRepository]:
    repository = VotingEnsemblePaperExecutionRepository()
    return VotingEnsembleLocalPaperExecutionEngine(repository), repository


def order(repository: VotingEnsemblePaperExecutionRepository, client_order_id: str) -> dict:
    return [item for item in repository.inventory_snapshot()["localOrders"] if item["clientOrderId"] == client_order_id][0]


def open_short(engine: VotingEnsembleLocalPaperExecutionEngine, repository: VotingEnsemblePaperExecutionRepository, *, client_order_id: str = "short-entry", quantity: int = 3, **intent_overrides) -> None:
    # A SELL limit at 100 fills against a bid of 100; the ledger then holds -quantity.
    seed_local_quote(repository, bid=100.0, ask=100.05, bid_size=quantity)
    intent = local_engine_intent(client_order_id=client_order_id, quantity=quantity, side=Signal.SELL, limit_price=100.0, **intent_overrides)
    ack = engine.submit_order(intent)
    assert ack.status == "OPEN", ack.rejectedReason
    fill = engine.refresh_order(client_order_id)
    assert fill is not None and fill.filledQuantity == quantity


def open_long(engine: VotingEnsembleLocalPaperExecutionEngine, repository: VotingEnsemblePaperExecutionRepository, *, client_order_id: str = "long-entry", quantity: int = 3, **intent_overrides) -> None:
    seed_local_quote(repository, bid=100.0, ask=100.0, ask_size=quantity)
    intent = local_engine_intent(client_order_id=client_order_id, quantity=quantity, limit_price=100.0, **intent_overrides)
    ack = engine.submit_order(intent)
    assert ack.status == "OPEN", ack.rejectedReason
    fill = engine.refresh_order(client_order_id)
    assert fill is not None and fill.filledQuantity == quantity


class ShortProtectionTest(unittest.TestCase):
    """A short fill gets the same protection a long fill gets, on the covering side.

    Before this the OCO was only created for BUY fills, so a short ran with no stop and no
    target until end of day, and end of day skipped shorts too.
    """

    def test_short_fill_creates_buy_side_stop_and_target(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            engine, repository = engine_with_repository()
            open_short(engine, repository)

            stop = order(repository, "short-entry-stop")
            target = order(repository, "short-entry-target")

        self.assertEqual(repository.inventory_snapshot()["localPositions"][0]["signedQuantity"], -3)
        self.assertEqual(stop["side"], "BUY")
        self.assertEqual(stop["orderType"], "STOP_LIMIT")
        self.assertEqual(stop["triggerPrice"], 101.0)
        self.assertEqual(stop["quantity"], 3)
        self.assertEqual(target["side"], "BUY")
        self.assertEqual(target["orderType"], "LIMIT")
        self.assertEqual(target["limitPrice"], 98.5)

    def test_short_stop_covers_when_ask_rises_and_cancels_the_target(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            engine, repository = engine_with_repository()
            open_short(engine, repository)
            seed_local_quote(repository, bid=100.95, ask=101.0, ask_size=3)

            stop_fill = engine.refresh_order("short-entry-stop")
            inventory = repository.inventory_snapshot()

        self.assertIsNotNone(stop_fill)
        assert stop_fill is not None
        self.assertEqual(stop_fill.side, Signal.BUY)
        self.assertEqual(stop_fill.filledQuantity, 3)
        self.assertEqual(inventory["localPositions"], [])
        self.assertEqual(inventory["localPaperAccount"]["realizedPnl"], -3.0)
        self.assertEqual(order(repository, "short-entry-target")["status"], "CANCELED")

    def test_short_target_covers_when_ask_falls_and_cancels_the_stop(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            engine, repository = engine_with_repository()
            open_short(engine, repository)
            seed_local_quote(repository, bid=98.4, ask=98.5, ask_size=3)

            target_fill = engine.refresh_order("short-entry-target")
            inventory = repository.inventory_snapshot()

        self.assertIsNotNone(target_fill)
        self.assertEqual(inventory["localPositions"], [])
        self.assertEqual(inventory["localPaperAccount"]["realizedPnl"], 4.5)
        self.assertEqual(order(repository, "short-entry-stop")["status"], "CANCELED")

    def test_end_of_day_flattening_covers_a_short(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            engine, repository = engine_with_repository()
            open_short(engine, repository)
            seed_local_quote(repository, bid=99.9, ask=100.0, ask_size=3)

            updates = engine.submit_end_of_day_liquidation(evaluated_at=NOW + timedelta(hours=6))
            inventory = repository.inventory_snapshot()

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["side"], "BUY")
        self.assertEqual(updates[0]["status"], "FILLED")
        self.assertEqual(inventory["localPositions"], [])

    def test_long_protection_is_unchanged(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            engine, repository = engine_with_repository()
            open_long(engine, repository)

            stop = order(repository, "long-entry-stop")
            target = order(repository, "long-entry-target")

        self.assertEqual((stop["side"], stop["triggerPrice"]), ("SELL", 99.0))
        self.assertEqual((target["side"], target["limitPrice"]), ("SELL", 101.5))


class MaximumHoldingTimeExitTest(unittest.TestCase):
    """The local engine closes a position once it has been open for its holding limit.

    This is the time stop the backtest simulator has always applied; the local engine never
    had one, so live and replay disagreed on more than half the recorded exits.
    """

    def test_no_exit_before_the_limit_and_a_flattening_exit_at_it(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            engine, repository = engine_with_repository()
            open_long(engine, repository, settings_snapshot={"maximumHoldingMinutes": 30})
            seed_local_quote(repository, bid=100.4, ask=100.5, bid_size=3)

            early = engine.submit_maximum_holding_exits(evaluated_at=NOW + timedelta(minutes=29))
            due = engine.submit_maximum_holding_exits(evaluated_at=NOW + timedelta(minutes=30))
            inventory = repository.inventory_snapshot()

        self.assertEqual(early, [])
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["exitReason"], "MAXIMUM_HOLDING_TIME")
        self.assertEqual(due[0]["side"], "SELL")
        self.assertEqual(due[0]["status"], "FILLED")
        self.assertEqual(due[0]["averageFillPrice"], 100.4)
        self.assertIn("voting_ensemble.local_paper.maximum_holding_time_exit_submitted", due[0]["reasonCodes"])
        self.assertEqual(inventory["localPositions"], [])
        self.assertEqual(inventory["localPaperAccount"]["realizedPnl"], 1.2)
        # The protective legs are cancelled once the position is gone.
        self.assertEqual(order(repository, "long-entry-stop")["status"], "CANCELED")
        self.assertEqual(order(repository, "long-entry-target")["status"], "CANCELED")

    def test_time_stop_covers_a_short_at_the_ask(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            engine, repository = engine_with_repository()
            open_short(engine, repository, settings_snapshot={"maximumHoldingMinutes": 30})
            seed_local_quote(repository, bid=99.6, ask=99.7, ask_size=3)

            due = engine.submit_maximum_holding_exits(evaluated_at=NOW + timedelta(minutes=30))
            inventory = repository.inventory_snapshot()

        self.assertEqual(due[0]["side"], "BUY")
        self.assertEqual(due[0]["status"], "FILLED")
        self.assertEqual(inventory["localPositions"], [])
        self.assertEqual(inventory["localPaperAccount"]["realizedPnl"], 0.9)

    def test_the_limit_comes_from_the_entry_order_with_the_algorithm_default_as_fallback(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            engine, repository = engine_with_repository()
            open_long(engine, repository, client_order_id="five-minute", settings_snapshot={"maximumHoldingMinutes": 5})
            seed_local_quote(repository, bid=100.0, ask=100.0, bid_size=3)

            self.assertEqual(order(repository, "five-minute")["maximumHoldingMinutes"], 5)
            self.assertEqual(engine.submit_maximum_holding_exits(evaluated_at=NOW + timedelta(minutes=4)), [])
            self.assertEqual(len(engine.submit_maximum_holding_exits(evaluated_at=NOW + timedelta(minutes=5))), 1)

            # An entry order that recorded no limit falls back to the algorithm default.
            engine2, repository2 = engine_with_repository()
            open_long(engine2, repository2, client_order_id="no-limit")
            seed_local_quote(repository2, bid=100.0, ask=100.0, bid_size=3)

            self.assertNotIn("maximumHoldingMinutes", order(repository2, "no-limit"))
            self.assertEqual(engine2.submit_maximum_holding_exits(evaluated_at=NOW + timedelta(minutes=VOTING_ENSEMBLE_DEFAULT_MAX_HOLDING_MINUTES - 1)), [])
            self.assertEqual(len(engine2.submit_maximum_holding_exits(evaluated_at=NOW + timedelta(minutes=VOTING_ENSEMBLE_DEFAULT_MAX_HOLDING_MINUTES))), 1)

    def test_a_second_position_starts_its_own_clock(self) -> None:
        """A flat record no longer carries the previous trade's openedAt forward."""
        with patch.dict("os.environ", NO_FEES):
            engine, repository = engine_with_repository()
            open_long(engine, repository, client_order_id="first", settings_snapshot={"maximumHoldingMinutes": 30})
            seed_local_quote(repository, bid=100.0, ask=100.0, bid_size=3)
            self.assertEqual(len(engine.submit_maximum_holding_exits(evaluated_at=NOW + timedelta(minutes=30))), 1)
            self.assertEqual(repository.inventory_snapshot()["localPositions"], [])

            reentry_at = NOW + timedelta(minutes=40)
            seed_local_quote(repository, bid=100.0, ask=100.0, ask_size=3, observed_at=reentry_at)
            intent = local_engine_intent(client_order_id="second", quantity=3, limit_price=100.0, settings_snapshot={"maximumHoldingMinutes": 30})
            intent.createdAt = reentry_at
            self.assertEqual(engine.submit_order(intent).status, "OPEN")
            self.assertIsNotNone(engine.refresh_order("second"))
            seed_local_quote(repository, bid=100.0, ask=100.0, bid_size=3, observed_at=reentry_at)

            position = repository.inventory_snapshot()["localPositions"][0]
            self.assertEqual(position["entryOrderId"], "second")
            self.assertEqual(engine.submit_maximum_holding_exits(evaluated_at=reentry_at + timedelta(minutes=29)), [])
            self.assertEqual(len(engine.submit_maximum_holding_exits(evaluated_at=reentry_at + timedelta(minutes=30))), 1)

    def test_an_open_holding_exit_is_not_duplicated(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            engine, repository = engine_with_repository()
            open_long(engine, repository, settings_snapshot={"maximumHoldingMinutes": 30})
            # No quoted size on the bid, so the exit is accepted but cannot fill yet.
            seed_local_quote(repository, bid=100.0, ask=100.0, bid_size=0)
            first = engine.submit_maximum_holding_exits(evaluated_at=NOW + timedelta(minutes=30))
            second = engine.submit_maximum_holding_exits(evaluated_at=NOW + timedelta(minutes=31))

        self.assertTrue(first[0]["submitted"])
        self.assertEqual(first[0]["status"], "OPEN")
        self.assertEqual(second[0]["submitted"], False)
        self.assertIn("voting_ensemble.local_paper.maximum_holding_exit_already_open", second[0]["reasonCodes"])


if __name__ == "__main__":
    unittest.main()
