from __future__ import annotations

import dataclasses
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from backend.app import market_feed
from backend.app.config import get_settings
from backend.app.database import CandleStore
from backend.app.market_feed import (
    INSTRUMENTS,
    Instrument,
    UnavailableMarketDataProvider,
    active_instrument,
    build_providers,
    instrument,
    market_feed_status,
    require_tradeable,
    set_active_instrument,
)


def candle(provider: str, close: float) -> dict:
    return {
        "provider": provider,
        "feed": "iex",
        "symbol": "SPY",
        "timeframe": "1Min",
        "timestamp": "2026-09-01T13:31:00Z",
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1000,
        "trade_count": 1,
        "vwap": close,
    }


def temp_store() -> CandleStore:
    path = os.path.join(tempfile.mkdtemp(), "candles.db")
    return CandleStore(dataclasses.replace(get_settings(), database_url=f"sqlite:///{path}"))


class SharedStoreProviderIsolationTest(unittest.TestCase):
    """The store is the central point, so two sources must not blend into one series."""

    def test_two_providers_do_not_merge_into_one_series(self) -> None:
        store = temp_store()
        store.upsert_many([candle("alpaca", 100.0), candle("futures", 5000.0)])

        alpaca = store.range(symbol="SPY", timeframe="1Min", feed="iex", provider="alpaca")
        futures = store.range(symbol="SPY", timeframe="1Min", feed="iex", provider="futures")

        self.assertEqual([row["close"] for row in alpaca], [100.0])
        self.assertEqual([row["close"] for row in futures], [5000.0])

    def test_the_unfiltered_read_still_returns_every_source(self) -> None:
        """Existing callers keep their behaviour; the filter is opt-in."""
        store = temp_store()
        store.upsert_many([candle("alpaca", 100.0), candle("futures", 5000.0)])

        rows = store.range(symbol="SPY", timeframe="1Min", feed="iex")

        self.assertEqual(sorted(row["close"] for row in rows), [100.0, 5000.0])

    def test_latest_reads_honour_the_provider(self) -> None:
        store = temp_store()
        store.upsert_many([candle("alpaca", 100.0), candle("futures", 5000.0)])

        latest = store.latest(symbol="SPY", timeframe="1Min", feed="iex", limit=5, provider="futures")
        until = store.latest_until(
            symbol="SPY", timeframe="1Min", feed="iex", limit=5, end="2026-09-02T00:00:00Z", provider="alpaca"
        )

        self.assertEqual([row["close"] for row in latest], [5000.0])
        self.assertEqual([row["close"] for row in until], [100.0])


class InstrumentRegistryTest(unittest.TestCase):
    def test_the_registry_carries_real_contract_specifications(self) -> None:
        specs = {item.symbol: (item.point_value, item.tick_size) for item in INSTRUMENTS}

        self.assertEqual(specs["SPY"], (1.0, 0.01))
        self.assertEqual(specs["ES"], (50.0, 0.25))
        self.assertEqual(specs["MES"], (5.0, 0.25))
        self.assertEqual(specs["NQ"], (20.0, 0.25))
        self.assertEqual(specs["MNQ"], (2.0, 0.25))

    def test_futures_are_not_tradeable_until_the_platform_can_size_them(self) -> None:
        """The safety property: share-based sizing must never be applied to a contract.

        floor(dollars / price) on an ES contract returns a plausible number that is wrong by
        the contract's point value, so this has to be refused rather than attempted.
        """
        for item in INSTRUMENTS:
            with self.subTest(symbol=item.symbol):
                if item.asset_class == "future":
                    self.assertFalse(item.trade_ready)
                    self.assertIn("contract_sizing", item.missing_capabilities)
                    self.assertIn("extended_session", item.missing_capabilities)
                else:
                    self.assertTrue(item.trade_ready)

    def test_trading_an_unsupported_instrument_is_refused_by_name(self) -> None:
        with self.assertRaises(RuntimeError) as caught:
            require_tradeable(instrument("mes_future"))

        self.assertIn("contract_sizing", str(caught.exception))

    def test_trading_the_supported_instrument_is_allowed(self) -> None:
        self.assertEqual(require_tradeable(instrument("spy_equity")).symbol, "SPY")

    def test_an_unregistered_instrument_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            instrument("gold_future")

    def test_an_instrument_becomes_tradeable_only_when_its_capabilities_land(self) -> None:
        mes = instrument("mes_future")
        with mock.patch.object(
            market_feed, "SUPPORTED_CAPABILITIES", frozenset({"contract_sizing", "extended_session", "contract_rollover"})
        ):
            self.assertTrue(mes.trade_ready)
            self.assertEqual(mes.missing_capabilities, ())


class ActiveFeedTest(unittest.TestCase):
    def setUp(self) -> None:
        self._state = Path(tempfile.mkdtemp()) / "active_feed.json"
        patcher = mock.patch.object(market_feed, "ACTIVE_FEED_STATE_PATH", self._state)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_default_is_the_equity_instrument(self) -> None:
        self.assertEqual(active_instrument().symbol, "SPY")

    def test_switching_is_application_wide_and_survives_a_reload(self) -> None:
        set_active_instrument("mnq_future")

        self.assertEqual(active_instrument().symbol, "MNQ")
        self.assertEqual(market_feed_status()["scope"], "application_wide")

    def test_an_untradeable_instrument_can_be_selected_but_not_traded(self) -> None:
        """Collecting history is how trading support gets validated, so selection is allowed."""
        selected = set_active_instrument("es_future")

        self.assertFalse(selected.trade_ready)
        with self.assertRaises(RuntimeError):
            require_tradeable()

    def test_selection_can_be_refused_outright_when_the_caller_requires_tradeable(self) -> None:
        with self.assertRaises(ValueError):
            set_active_instrument("es_future", allow_untradeable=False)

    def test_an_unknown_instrument_cannot_be_selected(self) -> None:
        with self.assertRaises(KeyError):
            set_active_instrument("not_an_instrument")

    def test_a_corrupt_state_file_falls_back_to_the_default(self) -> None:
        self._state.parent.mkdir(parents=True, exist_ok=True)
        self._state.write_text("{ not json", encoding="utf-8")

        self.assertEqual(active_instrument().symbol, "SPY")


class ProviderTest(unittest.TestCase):
    def test_the_futures_provider_reports_unavailable_rather_than_empty(self) -> None:
        """Empty bars are indistinguishable from a quiet market and would read as real data."""
        providers = build_providers(alpaca_client=object())

        self.assertTrue(providers["alpaca"].available)
        self.assertFalse(providers["futures"].available)

    def test_asking_an_unconfigured_provider_for_bars_raises(self) -> None:
        provider = UnavailableMarketDataProvider("futures", "no vendor")

        with self.assertRaises(RuntimeError):
            import asyncio

            asyncio.run(
                provider.get_bars(symbol="ES", timeframe="1Min", feed="cme", limit=10, start=None, end=None, sort="asc")
            )


class ChartFollowsActiveFeedTest(unittest.TestCase):
    def setUp(self) -> None:
        self._state = Path(tempfile.mkdtemp()) / "active_feed.json"
        patcher = mock.patch.object(market_feed, "ACTIVE_FEED_STATE_PATH", self._state)
        patcher.start()
        self.addCleanup(patcher.stop)
        import backend.app.main as main_module

        self.client = TestClient(main_module.app)
        self.addCleanup(lambda: set_active_instrument("spy_equity"))

    def test_status_lists_every_instrument_and_why_each_is_or_is_not_tradeable(self) -> None:
        body = self.client.get("/api/market-feed").json()

        self.assertEqual(body["scope"], "application_wide")
        by_symbol = {item["symbol"]: item for item in body["instruments"]}
        self.assertTrue(by_symbol["SPY"]["tradeReady"])
        self.assertFalse(by_symbol["MES"]["tradeReady"])
        self.assertIn("contract_sizing", by_symbol["MES"]["missingCapabilities"])

    def test_switching_moves_the_app_and_reports_the_new_instrument(self) -> None:
        response = self.client.put("/api/market-feed/active", params={"instrumentId": "mnq_future"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["activeInstrument"]["symbol"], "MNQ")
        self.assertEqual(self.client.get("/api/market-feed").json()["activeInstrument"]["symbol"], "MNQ")

    def test_switching_to_an_unknown_instrument_is_a_404(self) -> None:
        self.assertEqual(
            self.client.put("/api/market-feed/active", params={"instrumentId": "nope"}).status_code, 404
        )


if __name__ == "__main__":
    unittest.main()
