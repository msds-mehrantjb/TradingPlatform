from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.voting_ensemble.backtest import VotingEnsembleBacktestRunner
from backend.app.algorithms.voting_ensemble.backtest_config import VotingEnsembleBacktestConfig
from backend.app.algorithms.voting_ensemble.service import _active_instrument_tradeable, _operational_state
from backend.app.market_feed import instrument, instrument_for_symbol

START = datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc)


def series(minutes: int = 390, *, base: float = 500.0, scale: float = 1.0) -> list[dict]:
    rows, price = [], base
    for index in range(minutes):
        phase = index % 80
        step = (1.4 if phase < 30 else (-1.1 if phase < 60 else 0.6)) * scale
        price += step
        wick = max(abs(step) * 0.8, 0.12) * scale
        ts = START + timedelta(minutes=index)
        rows.append(
            {
                "timestamp": ts.isoformat().replace("+00:00", "Z"),
                "open": round(price - step, 4),
                "high": round(max(price, price - step) + wick, 4),
                "low": round(min(price, price - step) - wick, 4),
                "close": round(price, 4),
                "volume": 140000 + (index % 7) * 6000,
            }
        )
    return rows


def replay(symbol: str) -> dict:
    runner = VotingEnsembleBacktestRunner(
        config=VotingEnsembleBacktestConfig(warmupCandles=40, includeDecisionRecords=True)
    )
    return runner.run(
        symbol=symbol,
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


class InstrumentLookupTest(unittest.TestCase):
    def test_the_registry_is_searchable_by_symbol(self) -> None:
        self.assertEqual(instrument_for_symbol("SPY").instrument_id, "spy_equity")
        self.assertEqual(instrument_for_symbol("mes").instrument_id, "mes_future")

    def test_an_unregistered_symbol_carries_no_restriction(self) -> None:
        """A breadth component is not an instrument; absence must not read as a refusal."""
        self.assertIsNone(instrument_for_symbol("XLK"))

    def test_the_futures_contracts_are_still_not_sizeable(self) -> None:
        for instrument_id in ("mes_future", "mnq_future"):
            with self.subTest(instrument=instrument_id):
                selected = instrument(instrument_id)
                self.assertFalse(selected.trade_ready)
                self.assertIn("contract_sizing", selected.missing_capabilities)


class InstrumentGateTest(unittest.TestCase):
    """An instrument the platform cannot size must be refused before sizing, not after.

    The platform sizes in shares. An MES point is worth $5 and an MNQ point $2, so the share
    path does not fail loudly on a future -- it returns a plausible quantity that is wrong by
    the point value. `require_tradeable` was written for this and was never called from any
    production path, which left the refusal decorative.
    """

    def test_an_equity_still_trades(self) -> None:
        result = replay("SPY")

        self.assertGreater(result["totalTrades"], 0)
        self.assertIn(
            "voting_ensemble.local_gate.instrument_not_tradeable:passed",
            result["decisionRecords"][0]["reasonCodes"],
        )

    def test_a_future_is_refused_on_every_bar(self) -> None:
        for symbol in ("MES", "MNQ"):
            with self.subTest(symbol=symbol):
                result = replay(symbol)

                self.assertEqual(result["totalTrades"], 0)
                blocked = [
                    record
                    for record in result["decisionRecords"]
                    if "voting_ensemble.local_gate.instrument_not_tradeable" in (record.get("reasonCodes") or [])
                ]
                self.assertEqual(len(blocked), result["decisionCount"])


class InstrumentStateFallbackTest(unittest.TestCase):
    """A payload that omits the flag must not slip past the gate."""

    class _Snapshot:
        operationalHealthSnapshot: dict = {}
        sessionState: dict = {}
        feedHealthStatus = "ready"

    class _Regime:
        features: dict = {}

    def test_an_omitted_flag_falls_back_to_the_active_instrument(self) -> None:
        state = _operational_state(self._Snapshot(), None, self._Regime())

        self.assertIn("instrumentTradeable", state)
        self.assertEqual(state["instrumentTradeable"], _active_instrument_tradeable())

    def test_an_explicit_flag_wins(self) -> None:
        snapshot = self._Snapshot()
        snapshot.operationalHealthSnapshot = {"instrumentTradeable": False}

        state = _operational_state(snapshot, None, self._Regime())

        self.assertFalse(state["instrumentTradeable"])


if __name__ == "__main__":
    unittest.main()
