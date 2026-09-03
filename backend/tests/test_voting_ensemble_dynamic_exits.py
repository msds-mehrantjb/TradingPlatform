from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import patch

from backend.app.algorithms.voting_ensemble.exit_policy import VotingEnsembleExecutionSimulator
from backend.app.algorithms.voting_ensemble.paper_execution import (
    VotingEnsembleLocalPaperExecutionEngine,
    VotingEnsemblePaperExecutionRepository,
)
from backend.app.algorithms.voting_ensemble.risk_budget import resolve_voting_ensemble_risk_budget
from backend.app.algorithms.voting_ensemble.service import _exit_geometry, _remaining_daily_loss_budget
from backend.app.algorithms.voting_ensemble.snapshot import build_backtest_snapshot
from backend.app.algorithms.voting_ensemble.trading_settings.resolver import resolve_one_minute_trading_settings
from backend.app.domain.feature_engine import MarketCandle
from backend.app.domain.models import AccountRiskState, OrderPlan, Signal
from backend.app.execution.simulation import trailed_stop
from backend.tests.test_voting_ensemble_automatic_paper_execution import NOW, local_engine_intent, seed_local_quote
from backend.tests.test_voting_ensemble_snapshot import START, candles, snapshot_payload

NO_FEES = {"VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0", "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0"}
SETTINGS = resolve_one_minute_trading_settings(None)


def snapshot(**market_context_overrides):
    payload = snapshot_payload(candles(60))
    payload["market_context"].update(market_context_overrides)
    return build_backtest_snapshot(payload)


class ExitGeometryTest(unittest.TestCase):
    """Stop from the session's ATR, target from the nearest structural level past 1 R."""

    def test_stop_is_an_atr_multiple_and_target_defaults_to_the_r_multiple(self) -> None:
        snap = snapshot()
        entry = snap.nbbo.ask

        geometry = _exit_geometry(snap, Signal.BUY, entry, SETTINGS)

        atr = snap.features.atr
        self.assertIsNotNone(atr)
        expected_stop = max(atr * SETTINGS.stopPolicy.atrMultiplier, SETTINGS.stopPolicy.minimumStopDistanceDollars)
        self.assertAlmostEqual(geometry["features"]["stopDistance"], expected_stop, places=6)
        self.assertEqual(geometry["features"]["stopSource"], "atr")
        self.assertAlmostEqual(geometry["stopPrice"], entry - expected_stop, places=6)
        # Nothing structural sits between 1 R and 1.5 R on this tape, so the R target holds.
        self.assertEqual(geometry["features"]["targetSource"], "r_multiple")
        self.assertAlmostEqual(geometry["features"]["targetRMultiple"], 1.5, places=6)
        self.assertAlmostEqual(geometry["stopPrice"], entry - geometry["features"]["stopDistance"], places=6)
        # The post-fill rule is expressed in the same unit of risk.
        self.assertEqual(geometry["features"]["breakevenTriggerR"], 1.0)
        self.assertAlmostEqual(geometry["features"]["trailingStopDistance"], expected_stop, places=6)

    def test_a_structural_level_inside_the_band_becomes_the_target(self) -> None:
        base = snapshot()
        entry = base.nbbo.ask
        stop_distance = _exit_geometry(base, Signal.BUY, entry, SETTINGS)["features"]["stopDistance"]
        # Put the opening-range high 1.25 R above the entry (net of the spread buffer):
        # beyond the 1 R minimum, short of the 1.5 R target.
        level = entry + stop_distance * 1.25 + base.nbbo.spreadDollars
        snap = snapshot(openingRange={"high": round(level, 4), "low": 99.6, "open": 100.0, "close": 100.7})

        geometry = _exit_geometry(snap, Signal.BUY, entry, SETTINGS)

        self.assertEqual(geometry["features"]["targetSource"], "opening_range_high")
        self.assertAlmostEqual(geometry["targetPrice"], level - base.nbbo.spreadDollars, places=4)
        self.assertLess(geometry["features"]["targetRMultiple"], 1.5)
        self.assertGreaterEqual(geometry["features"]["targetRMultiple"], 1.0)

    def test_a_level_closer_than_the_minimum_r_is_ignored(self) -> None:
        base = snapshot()
        entry = base.nbbo.ask
        stop_distance = _exit_geometry(base, Signal.BUY, entry, SETTINGS)["features"]["stopDistance"]
        snap = snapshot(openingRange={"high": round(entry + stop_distance * 0.5, 4), "low": 99.6, "open": 100.0, "close": 100.7})

        geometry = _exit_geometry(snap, Signal.BUY, entry, SETTINGS)

        self.assertEqual(geometry["features"]["targetSource"], "r_multiple")

    def test_short_geometry_mirrors_the_long(self) -> None:
        snap = snapshot()
        entry = snap.nbbo.bid

        geometry = _exit_geometry(snap, Signal.SELL, entry, SETTINGS)

        self.assertGreater(geometry["stopPrice"], entry)
        self.assertLess(geometry["targetPrice"], entry)
        self.assertAlmostEqual(geometry["stopPrice"] - entry, geometry["features"]["stopDistance"], places=6)

    def test_fixed_dollars_are_the_fallback_without_an_atr(self) -> None:
        snap = snapshot()
        without_atr = snap.model_copy(update={"features": snap.features.model_copy(update={"atr": None})})

        geometry = _exit_geometry(without_atr, Signal.BUY, snap.nbbo.ask, SETTINGS)

        self.assertEqual(geometry["features"]["stopSource"], "fixed_dollars")
        self.assertAlmostEqual(geometry["features"]["stopDistance"], SETTINGS.stopPolicy.fixedStopDistanceDollars, places=6)


class DailyLossBudgetSizingTest(unittest.TestCase):
    """One more trade may not risk more than the day can still lose."""

    def account(self, *, realized: float = 0.0, open_risk_percent: float = 0.0) -> AccountRiskState:
        return AccountRiskState(
            accountId="test",
            equity=25_000.0,
            buyingPower=25_000.0,
            openPositionNotional=0.0,
            realizedPnlToday=realized,
            totalOpenRiskPercent=open_risk_percent,
            tradesToday=0,
            observedAt=NOW,
            sessionDate=NOW.date(),
        )

    def test_remaining_budget_nets_losses_and_open_risk_against_the_daily_limit(self) -> None:
        # 2% of 25,000 is 500. Down 300 with 100 of open risk leaves 100.
        self.assertEqual(_remaining_daily_loss_budget(self.account(realized=-300.0, open_risk_percent=0.4), SETTINGS), 100.0)
        # A winning day does not enlarge the budget.
        self.assertEqual(_remaining_daily_loss_budget(self.account(realized=+400.0), SETTINGS), 500.0)
        self.assertEqual(_remaining_daily_loss_budget(self.account(realized=-900.0), SETTINGS), 0.0)
        self.assertIsNone(_remaining_daily_loss_budget(None, SETTINGS))

    def config(self, **overrides) -> dict:
        return {
            "candidateSignal": "BUY",
            "gatesPassed": True,
            "netEdgePassed": True,
            "riskPerTradePercent": 0.5,
            "orderAllocationPercent": 100.0,
            "dailyAllocationPercent": 100.0,
            "maximumPositionPercent": 100.0,
            "profileMaximumShares": 100_000,
            "availableFillableQuantity": 100_000,
            "currentOneMinuteVolume": 10_000_000,
            "voteEdge": 0.7,
            "independentFamilySupport": 2,
            "minimumIndependentFamilySupport": 2,
            **overrides,
        }

    def test_the_budget_binds_the_risk_and_is_visible_in_the_reason_codes(self) -> None:
        # 0.5% of 25,000 is 125 of risk; at a $1 stop that is 125 shares.
        unbounded = resolve_voting_ensemble_risk_budget(self.config(), equity=25_000.0, entry_price=100.0, stop_distance=1.0)
        bounded = resolve_voting_ensemble_risk_budget(self.config(remainingDailyLossBudgetDollars=40.0), equity=25_000.0, entry_price=100.0, stop_distance=1.0)
        exhausted = resolve_voting_ensemble_risk_budget(self.config(remainingDailyLossBudgetDollars=0.0), equity=25_000.0, entry_price=100.0, stop_distance=1.0)

        self.assertEqual(unbounded.quantity, 125)
        self.assertEqual(bounded.quantity, 40)
        self.assertTrue(any(code.startswith("voting_ensemble.risk_budget.daily_loss_budget_bound:") for code in bounded.reason_codes))
        self.assertEqual(exhausted.quantity, 0)
        self.assertIn("voting_ensemble.risk_budget.daily_loss_budget_exhausted", exhausted.reason_codes)

    def test_a_larger_budget_than_the_risk_changes_nothing(self) -> None:
        budget = resolve_voting_ensemble_risk_budget(self.config(remainingDailyLossBudgetDollars=5_000.0), equity=25_000.0, entry_price=100.0, stop_distance=1.0)

        self.assertEqual(budget.quantity, 125)
        self.assertFalse(any("daily_loss_budget" in code for code in budget.reason_codes))


def market_candle(minute: int, *, open: float, high: float, low: float, close: float) -> MarketCandle:
    return MarketCandle(timestamp=START + timedelta(minutes=minute), open=open, high=high, low=low, close=close, volume=100_000, symbol="SPY", timeframe="1Min")


def plan(*, side: Signal = Signal.BUY, trigger: float | None = 1.0, trail: float | None = 1.0, holding: int = 240) -> OrderPlan:
    long = side == Signal.BUY
    return OrderPlan(
        orderPlanId="plan-1",
        candidateId="candidate-1",
        symbol="SPY",
        side=side,
        orderType="LIMIT",
        quantity=10,
        entryPrice=100.0,
        stopPrice=99.0 if long else 101.0,
        targetPrice=110.0 if long else 90.0,
        limitPrice=100.0,
        maximumHoldingMinutes=holding,
        breakevenTriggerR=trigger,
        trailingStopDistance=trail,
        timeInForce="DAY",
        eligible=True,
        explanation="test",
        generatedAt=START,
        sessionDate=START.date(),
        configurationHash="test",
    )


class SimulatorTrailingStopTest(unittest.TestCase):
    """The simulator moves the stop to breakeven at +1 R and trails it, on candle closes."""

    def test_trailed_stop_rule(self) -> None:
        from backend.app.execution.simulation import SimulatedFill

        fill = SimulatedFill(status="FILLED", filledQuantity=10, requestedQuantity=10, averagePrice=100.0, filledAt=START, submittedAt=START, side=Signal.BUY, orderType="LIMIT", costs={})
        original = plan()
        # Below +1 R nothing moves.
        self.assertIsNone(trailed_stop(Signal.BUY, original, original, fill, 100.9))
        # At +1 R the stop goes to breakeven (trail of 1 lands on the entry too).
        self.assertEqual(trailed_stop(Signal.BUY, original, original, fill, 101.0), 100.0)
        # Further gains trail by the distance; never backwards.
        working = original.model_copy(update={"stopPrice": 101.5})
        self.assertEqual(trailed_stop(Signal.BUY, original, working, fill, 102.0), 101.5)
        self.assertEqual(trailed_stop(Signal.BUY, original, working, fill, 103.0), 102.0)
        # Short mirror.
        short = plan(side=Signal.SELL)
        self.assertEqual(trailed_stop(Signal.SELL, short, short, fill, 99.0), 100.0)
        self.assertEqual(trailed_stop(Signal.SELL, short, short.model_copy(update={"stopPrice": 98.5}), fill, 97.0), 98.0)

    def test_a_trailed_stop_exits_with_a_profit_the_static_stop_would_have_given_back(self) -> None:
        # Fill at 100 on the first candle, rally to 103, fall back below the trailed stop.
        candles_ = [
            market_candle(1, open=100.2, high=100.3, low=99.9, close=100.1),  # limit touched at 100
            market_candle(2, open=100.1, high=101.2, low=100.0, close=101.0),  # close +1 R → breakeven
            market_candle(3, open=101.0, high=103.1, low=100.9, close=103.0),  # close +3 R → stop trails to 102
            market_candle(4, open=102.9, high=103.0, low=101.5, close=101.6),  # low breaks 102 → stop hit
            market_candle(5, open=101.6, high=101.8, low=98.5, close=98.6),  # would have hit the static 99 stop
        ]
        trailed = VotingEnsembleExecutionSimulator().simulate(plan(), candles_, START)
        static = VotingEnsembleExecutionSimulator().simulate(plan(trigger=None, trail=None), candles_, START)

        self.assertEqual(trailed.exit.exitReason, "protective_stop")
        # The trailed stop sits one distance under the 103 close; the exit carries the
        # simulator's spread and slippage, hence the tolerance.
        self.assertAlmostEqual(trailed.exit.exitPrice, 102.0, delta=0.06)
        self.assertEqual(trailed.exit.exitAt, START + timedelta(minutes=4))
        self.assertIn("execution.stop_trailed", trailed.exit.reasonCodes)
        self.assertGreater(trailed.exit.pnl, 0)
        self.assertAlmostEqual(static.exit.exitPrice, 99.0, delta=0.06)
        self.assertLess(static.exit.pnl, 0)

    def test_the_stop_in_force_for_a_candle_is_the_one_before_it_printed(self) -> None:
        # A candle that both rallies past +1 R and dips below the entry is judged against
        # the original stop, not against a breakeven it earned only at its own close.
        candles_ = [
            market_candle(1, open=100.2, high=100.3, low=99.9, close=100.1),
            market_candle(2, open=100.1, high=101.5, low=99.5, close=101.4),
        ]
        execution = VotingEnsembleExecutionSimulator().simulate(plan(), candles_, START)

        # The series ends, so the simulator flattens at the close; what matters is that
        # the dip to 99.5 did not stop the trade out at a breakeven it had not yet earned.
        self.assertEqual(execution.exit.exitReason, "end_of_day")
        self.assertNotIn("execution.protective_stop_hit", execution.exit.reasonCodes)


class EngineTrailingStopTest(unittest.TestCase):
    """The local paper engine moves the protective stop by the same rule against the mark."""

    def open_long(self, engine, repository):
        seed_local_quote(repository, bid=100.0, ask=100.0, ask_size=3)
        intent = local_engine_intent(client_order_id="trail-long", quantity=3, limit_price=100.0, settings_snapshot={"breakevenTriggerR": 1.0, "trailingStopDistance": 1.0})
        intent.targetPrice = 110.0
        self.assertEqual(engine.submit_order(intent).status, "OPEN")
        self.assertIsNotNone(engine.refresh_order("trail-long"))

    def stop_order(self, repository, client_order_id: str) -> dict:
        return [item for item in repository.inventory_snapshot()["localOrders"] if item["clientOrderId"] == client_order_id][0]

    def test_long_stop_moves_to_breakeven_then_trails_and_fills_at_the_trailed_level(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            engine, repository = VotingEnsembleLocalPaperExecutionEngine(VotingEnsemblePaperExecutionRepository()), None
            repository = engine.repository
            self.open_long(engine, repository)
            self.assertEqual(self.stop_order(repository, "trail-long-stop")["triggerPrice"], 99.0)

            seed_local_quote(repository, bid=100.5, ask=100.55, bid_size=3)
            self.assertEqual(engine.trail_protective_stops(evaluated_at=NOW + timedelta(minutes=1)), [])

            seed_local_quote(repository, bid=101.0, ask=101.05, bid_size=3)
            moved = engine.trail_protective_stops(evaluated_at=NOW + timedelta(minutes=2))
            self.assertEqual((moved[0]["from"], moved[0]["to"]), (99.0, 100.0))
            self.assertIn("voting_ensemble.local_paper.stop_moved_to_breakeven", moved[0]["reasonCodes"])

            seed_local_quote(repository, bid=102.0, ask=102.05, bid_size=3)
            trailed = engine.trail_protective_stops(evaluated_at=NOW + timedelta(minutes=3))
            self.assertEqual(trailed[0]["to"], 101.0)
            self.assertIn("voting_ensemble.local_paper.stop_trailed", trailed[0]["reasonCodes"])

            # A pullback never moves the stop back down.
            seed_local_quote(repository, bid=101.4, ask=101.45, bid_size=3)
            self.assertEqual(engine.trail_protective_stops(evaluated_at=NOW + timedelta(minutes=4)), [])
            self.assertEqual(self.stop_order(repository, "trail-long-stop")["triggerPrice"], 101.0)

            seed_local_quote(repository, bid=101.0, ask=101.05, bid_size=3)
            fills = engine.evaluate_open_protective_orders()
            inventory = repository.inventory_snapshot()

        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].averageFillPrice, 101.0)
        self.assertEqual(inventory["localPositions"], [])
        self.assertEqual(inventory["localPaperAccount"]["realizedPnl"], 3.0)

    def test_short_stop_moves_to_breakeven_on_a_favourable_move(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            engine = VotingEnsembleLocalPaperExecutionEngine(VotingEnsemblePaperExecutionRepository())
            repository = engine.repository
            seed_local_quote(repository, bid=100.0, ask=100.05, bid_size=3)
            intent = local_engine_intent(client_order_id="trail-short", quantity=3, side=Signal.SELL, limit_price=100.0, settings_snapshot={"breakevenTriggerR": 1.0, "trailingStopDistance": 1.0})
            intent.targetPrice = 90.0
            self.assertEqual(engine.submit_order(intent).status, "OPEN")
            self.assertIsNotNone(engine.refresh_order("trail-short"))
            self.assertEqual(self.stop_order(repository, "trail-short-stop")["triggerPrice"], 101.0)

            seed_local_quote(repository, bid=98.95, ask=99.0, ask_size=3)
            moved = engine.trail_protective_stops(evaluated_at=NOW + timedelta(minutes=2))

        self.assertEqual((moved[0]["from"], moved[0]["to"]), (101.0, 100.0))
        self.assertEqual(self.stop_order(repository, "trail-short-stop")["side"], "BUY")


if __name__ == "__main__":
    unittest.main()
