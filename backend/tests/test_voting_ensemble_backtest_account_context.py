from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.voting_ensemble.backtest import VotingEnsembleBacktestRunner
from backend.app.algorithms.voting_ensemble.backtest_config import VotingEnsembleBacktestConfig


START = datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc)  # 09:30 ET


def series(minutes: int = 390, *, base: float = 500.0, scale: float = 1.0) -> list[dict]:
    """A session with swings wide enough for more than one strategy family to have a view.

    The ensemble will not trade on a single family, so a smooth drift produces no trades no
    matter what context it is given. That is the algorithm working, not the harness failing.
    """
    rows, price = [], base
    for index in range(minutes):
        phase = index % 80
        step = (1.4 if phase < 30 else (-1.1 if phase < 60 else 0.6)) * scale
        price += step
        wick = max(abs(step) * 0.8, 0.12) * scale
        timestamp = START + timedelta(minutes=index)
        rows.append(
            {
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "open": round(price - step, 4),
                "high": round(max(price, price - step) + wick, 4),
                "low": round(min(price, price - step) - wick, 4),
                "close": round(price, 4),
                "volume": 140000 + (index % 7) * 6000,
            }
        )
    return rows


def run(**config) -> dict:
    runner = VotingEnsembleBacktestRunner(
        config=VotingEnsembleBacktestConfig(warmupCandles=40, includeDecisionRecords=True, **config)
    )
    return runner.run(
        symbol="SPY",
        spy_1m_candles=series(),
        qqq_candles=series(base=440.0, scale=1.2),
        iwm_candles=series(base=210.0, scale=0.8),
        breadth_components={
            "XLK": series(base=250.0, scale=1.1),
            "XLF": series(base=48.0, scale=0.9),
            "XLV": series(base=145.0, scale=0.7),
        },
        timeframe="1Min",
    )


def runner_with(**config) -> VotingEnsembleBacktestRunner:
    runner = object.__new__(VotingEnsembleBacktestRunner)
    runner.config = VotingEnsembleBacktestConfig(**config)
    return runner


def fill(entry_minute: int, exit_minute: int | None, net_pnl: float, *, notional: float = 5000.0, risk: float = 250.0) -> dict:
    return {
        "entryAt": START + timedelta(minutes=entry_minute),
        "exitAt": None if exit_minute is None else START + timedelta(minutes=exit_minute),
        "netPnl": net_pnl,
        "notional": notional,
        "risk": risk,
    }


class BacktestAccountContextTest(unittest.TestCase):
    """Replay has to be able to drive the real pipeline to a fill.

    Before the runner supplied operational and account context, every bar failed
    `local_gate.trading_disabled` and `local_gate.account_risk_state_missing`, so no
    candidate could reach an order plan. The only trade-producing replay test in the
    repository substituted a stub service, which measures the fill simulator rather than the
    algorithm, and the risk gates were never exercised in replay at all.
    """

    def test_the_real_pipeline_reaches_a_fill(self) -> None:
        result = run()

        self.assertGreater(result["totalTrades"], 0)
        self.assertGreater(len(result["trades"]), 0)

    def test_the_operational_context_is_load_bearing(self) -> None:
        """Turning trading off must stop every entry, which is what proves the wiring is real."""
        enabled = run()
        disabled = run(operationalHealth={"tradingEnabled": False})

        self.assertGreater(enabled["totalTrades"], 0)
        self.assertEqual(disabled["totalTrades"], 0)

        blocked = [
            record
            for record in disabled["decisionRecords"]
            if "voting_ensemble.local_gate.trading_disabled" in (record.get("reasonCodes") or [])
        ]
        self.assertEqual(len(blocked), disabled["decisionCount"])

    def test_realised_pnl_waits_for_the_exit_to_happen(self) -> None:
        """The simulator resolves a trade's whole life at entry; the account must not.

        Accruing PnL at entry would let the daily-loss and drawdown gates decide using money
        the account had not yet made when the gate ran — the risk gates reading evidence from
        their own future, which is the look-ahead replay exists to prevent.
        """
        runner = runner_with(startingCapital=100000.0)
        fills = [fill(10, 40, -900.0)]

        at_entry = runner._account_snapshot(timestamp=START + timedelta(minutes=20), fills=fills, intraday_high=100000.0)
        after_exit = runner._account_snapshot(timestamp=START + timedelta(minutes=45), fills=fills, intraday_high=100000.0)

        self.assertEqual(at_entry["realizedPnlToday"], 0.0)
        self.assertEqual(at_entry["equity"], 100000.0)
        self.assertEqual(after_exit["realizedPnlToday"], -900.0)
        self.assertEqual(after_exit["equity"], 99100.0)

    def test_an_open_position_shows_as_exposure_not_as_profit(self) -> None:
        runner = runner_with(startingCapital=100000.0)
        snapshot = runner._account_snapshot(
            timestamp=START + timedelta(minutes=20),
            fills=[fill(10, 40, -900.0, notional=25000.0, risk=1000.0)],
            intraday_high=100000.0,
        )

        self.assertEqual(snapshot["openPositionNotional"], 25000.0)
        self.assertEqual(snapshot["totalSpyNotionalPercent"], 25.0)
        self.assertEqual(snapshot["totalOpenRiskPercent"], 1.0)
        self.assertEqual(snapshot["realizedPnlToday"], 0.0)

    def test_a_trade_that_never_exits_is_never_realised(self) -> None:
        runner = runner_with(startingCapital=100000.0)
        snapshot = runner._account_snapshot(
            timestamp=START + timedelta(minutes=380),
            fills=[fill(10, None, 0.0)],
            intraday_high=100000.0,
        )

        self.assertEqual(snapshot["realizedPnlToday"], 0.0)
        self.assertEqual(snapshot["openPositionNotional"], 5000.0)

    def test_the_intraday_high_is_carried_not_recomputed_from_equity(self) -> None:
        """Drawdown is measured from the peak the account actually reached."""
        runner = runner_with(startingCapital=100000.0)
        snapshot = runner._account_snapshot(
            timestamp=START + timedelta(minutes=60),
            fills=[fill(10, 40, -900.0)],
            intraday_high=103000.0,
        )

        self.assertEqual(snapshot["intradayEquityHigh"], 103000.0)
        self.assertEqual(snapshot["equity"], 99100.0)

    def test_the_operational_default_leaves_the_session_gates_deriving_from_the_bars(self) -> None:
        """Market-open and entry-window must keep coming from the replayed session.

        Forcing them true would make replay unable to reproduce a live run that stopped
        trading at the entry-window boundary.
        """
        snapshot = runner_with()._operational_snapshot()

        self.assertTrue(snapshot["tradingEnabled"])
        self.assertTrue(snapshot["paperTradingMode"])
        self.assertNotIn("marketOpen", snapshot)
        self.assertNotIn("entryWindowOpen", snapshot)
        self.assertNotIn("validSession", snapshot)

    def test_a_configured_override_wins_over_the_default(self) -> None:
        snapshot = runner_with(operationalHealth={"tradingEnabled": False, "feedDegraded": True})._operational_snapshot()

        self.assertFalse(snapshot["tradingEnabled"])
        self.assertTrue(snapshot["feedDegraded"])
        self.assertTrue(snapshot["paperTradingMode"])


if __name__ == "__main__":
    unittest.main()
