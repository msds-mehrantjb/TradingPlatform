from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import patch

from backend.app.algorithms.voting_ensemble.exit_policy import VotingEnsembleExecutionSimulator, voting_ensemble_execution_config
from backend.app.domain.models import Signal
from backend.app.execution.simulation import stop_gap_price
from backend.tests.test_voting_ensemble_dynamic_exits import market_candle, plan
from backend.tests.test_voting_ensemble_local_paper_exits import NO_FEES, engine_with_repository, open_long, open_short, order
from backend.tests.test_voting_ensemble_automatic_paper_execution import seed_local_quote
from backend.tests.test_voting_ensemble_snapshot import START


class GappedStopFillTest(unittest.TestCase):
    """A protective stop the quote gaps through fills at the quote, bounded by max slippage.

    The stop is a stop-limit whose limit equals its trigger, so after a gap the limit was
    never executable and the order sat open until the quote recovered, which on a fast
    move it may never do. The engine now fills a triggered protective stop at the far-side
    quote, no further than the configured maximum slippage past the stop; entry stop-limits
    keep strict limit semantics.
    """

    def test_a_normal_stop_fills_at_the_stop(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            engine, repository = engine_with_repository()
            open_long(engine, repository)  # stop 99.0
            seed_local_quote(repository, bid=99.0, ask=99.05, bid_size=3)

            fill = engine.refresh_order("long-entry-stop")

        self.assertIsNotNone(fill)
        self.assertEqual(fill.averageFillPrice, 99.0)
        self.assertEqual(repository.inventory_snapshot()["localPositions"], [])

    def test_a_long_stop_gapped_through_fills_at_the_bid(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            engine, repository = engine_with_repository()
            open_long(engine, repository)  # stop 99.0
            seed_local_quote(repository, bid=98.7, ask=98.75, bid_size=3)

            fill = engine.refresh_order("long-entry-stop")
            inventory = repository.inventory_snapshot()

        self.assertIsNotNone(fill)
        self.assertEqual(fill.averageFillPrice, 98.7)
        self.assertEqual(inventory["localPositions"], [])
        self.assertEqual(inventory["localPaperAccount"]["realizedPnl"], -3.9)
        execution = repository.read_snapshot("local_execution.long-entry-stop")
        self.assertEqual(execution["fillPolicy"], "protective_stop_gap_through_filled_at_quote_bounded_by_max_slippage")

    def test_a_gap_beyond_the_bound_fills_at_the_bound(self) -> None:
        with patch.dict("os.environ", {**NO_FEES, "VOTING_ENSEMBLE_LOCAL_PAPER_MAX_STOP_SLIPPAGE_DOLLARS": "0.25"}):
            engine, repository = engine_with_repository()
            open_long(engine, repository)  # stop 99.0
            seed_local_quote(repository, bid=97.0, ask=97.05, bid_size=3)

            fill = engine.refresh_order("long-entry-stop")

        self.assertIsNotNone(fill)
        self.assertEqual(fill.averageFillPrice, 98.75)

    def test_the_bound_can_come_from_the_entry_order_settings(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            engine, repository = engine_with_repository()
            open_long(engine, repository, settings_snapshot={"maxSlippagePerShare": 0.10})
            self.assertEqual(order(repository, "long-entry-stop")["maxStopSlippageDollars"], 0.10)
            seed_local_quote(repository, bid=97.0, ask=97.05, bid_size=3)

            fill = engine.refresh_order("long-entry-stop")

        self.assertEqual(fill.averageFillPrice, 98.9)

    def test_a_short_cover_gapped_through_fills_at_the_ask(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            engine, repository = engine_with_repository()
            open_short(engine, repository)  # stop 101.0 (BUY)
            seed_local_quote(repository, bid=101.35, ask=101.4, ask_size=3)

            fill = engine.refresh_order("short-entry-stop")
            inventory = repository.inventory_snapshot()

        self.assertIsNotNone(fill)
        self.assertEqual(fill.side, Signal.BUY)
        self.assertEqual(fill.averageFillPrice, 101.4)
        self.assertEqual(inventory["localPositions"], [])
        self.assertAlmostEqual(inventory["localPaperAccount"]["realizedPnl"], -4.2, places=6)

    def test_an_entry_stop_limit_keeps_strict_limit_semantics(self) -> None:
        from backend.tests.test_voting_ensemble_automatic_paper_execution import local_engine_intent

        with patch.dict("os.environ", NO_FEES):
            engine, repository = engine_with_repository()
            seed_local_quote(repository, bid=100.0, ask=100.0, ask_size=3)
            intent = local_engine_intent(client_order_id="stop-entry", quantity=3, order_type="STOP_LIMIT", limit_price=100.5, trigger_price=100.5)
            self.assertEqual(engine.submit_order(intent).status, "OPEN")
            # Gaps straight through the entry's limit: an entry is not chased.
            seed_local_quote(repository, bid=101.0, ask=101.05, ask_size=3)

            fill = engine.refresh_order("stop-entry")

        self.assertIsNone(fill)
        self.assertEqual(order(repository, "stop-entry")["status"], "OPEN")


class SimulatorGapBoundTest(unittest.TestCase):
    """The simulator applies the same bound, so replay and live agree on a gapped stop."""

    def test_gap_fill_is_bounded_by_the_voting_ensemble_config(self) -> None:
        config = voting_ensemble_execution_config()
        self.assertEqual(config.maximumStopSlippageDollars, 1.0)
        long_plan = plan()  # stop 99.0
        gapped = market_candle(2, open=97.0, high=97.5, low=96.5, close=97.0)
        self.assertEqual(stop_gap_price(Signal.BUY, long_plan, gapped, config), 98.0)
        # Without a bound the gap fills at the open.
        unbounded = config.model_copy(update={"maximumStopSlippageDollars": None})
        self.assertEqual(stop_gap_price(Signal.BUY, long_plan, gapped, unbounded), 97.0)
        short_plan = plan(side=Signal.SELL)  # stop 101.0
        gapped_up = market_candle(2, open=103.0, high=103.5, low=102.5, close=103.0)
        self.assertEqual(stop_gap_price(Signal.SELL, short_plan, gapped_up, config), 102.0)

    def test_a_gapped_long_stop_exits_at_the_bounded_price(self) -> None:
        candles_ = [
            market_candle(1, open=100.2, high=100.3, low=99.9, close=100.1),  # fill at 100
            market_candle(2, open=97.0, high=97.4, low=96.8, close=97.2),  # gaps through the 99 stop
        ]
        execution = VotingEnsembleExecutionSimulator().simulate(plan(trigger=None, trail=None), candles_, START)

        self.assertEqual(execution.exit.exitReason, "protective_stop")
        self.assertAlmostEqual(execution.exit.exitPrice, 98.0, delta=0.06)
        self.assertEqual(execution.exit.exitAt, START + timedelta(minutes=2))


if __name__ == "__main__":
    unittest.main()
