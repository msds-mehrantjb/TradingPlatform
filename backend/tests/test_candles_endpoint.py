from __future__ import annotations

import os
import asyncio
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import httpx
from fastapi.testclient import TestClient

_TEST_DB = Path(tempfile.gettempdir()) / "trading_candles_endpoint_tests.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"

from backend.app import main


@pytest.fixture(autouse=True)
def clear_ui_response_cache():
    main._UI_RESPONSE_CACHE.clear()
    yield
    main._UI_RESPONSE_CACHE.clear()


def cached_candle(timestamp: str = "2026-07-31T19:59:00Z") -> dict:
    return {
        "provider": "alpaca",
        "feed": "iex",
        "symbol": "SPY",
        "timeframe": "1Min",
        "timestamp": timestamp,
        "open": 100.0,
        "high": 100.1,
        "low": 99.9,
        "close": 100.05,
        "volume": 1000,
        "trade_count": 10,
        "vwap": 100.02,
    }


def test_candles_refresh_false_empty_cache_returns_without_alpaca_call_when_market_open() -> None:
    with (
        patch.object(main.store, "latest", return_value=[]),
        patch.object(main, "local_market_is_closed", return_value=False),
        patch.object(main.alpaca, "get_bars", new_callable=AsyncMock) as get_bars,
    ):
        response = TestClient(main.app).get("/api/candles?symbol=SPY&feed=iex&timeframe=1Min&limit=10&refresh=false")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "cache"
    assert payload["candles"] == []
    assert "No cached candles" in payload["warning"]
    get_bars.assert_not_called()


def test_latest_quote_uses_cached_candle_fallback_when_credentials_missing() -> None:
    with (
        patch.object(main, "settings", replace(main.settings, alpaca_key_id="", alpaca_secret_key="")),
        patch.object(main.store, "latest", return_value=[cached_candle()]),
        patch.object(main.alpaca, "get_latest_quote", new_callable=AsyncMock) as get_latest_quote,
    ):
        response = TestClient(main.app).get("/api/market-data/quotes/latest?symbol=SPY&feed=iex")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["quote"]["source"] == "cached_candle_quote_fallback"
    assert payload["quote"]["bid"] > 0
    assert payload["quote"]["ask"] > payload["quote"]["bid"]
    assert payload["quote"]["bidSize"] > 0
    assert payload["quote"]["askSize"] > 0
    assert "market_data.nbbo.cached_candle_quote_fallback_ready" in payload["reasonCodes"]
    get_latest_quote.assert_not_called()


def test_latest_quote_uses_cached_candle_fallback_when_alpaca_quote_fails() -> None:
    with (
        patch.object(main, "settings", replace(main.settings, alpaca_key_id="test-key", alpaca_secret_key="test-secret")),
        patch.object(main.store, "latest", return_value=[cached_candle()]),
        patch.object(main.alpaca, "get_latest_quote", new_callable=AsyncMock) as get_latest_quote,
    ):
        get_latest_quote.side_effect = httpx.ConnectError("quote upstream unavailable")
        response = TestClient(main.app).get("/api/market-data/quotes/latest?symbol=SPY&feed=iex")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["quote"]["source"] == "cached_candle_quote_fallback"
    assert payload["quote"]["bid"] > 0
    assert payload["quote"]["ask"] > payload["quote"]["bid"]
    assert "market_data.nbbo.alpaca_latest_quote_unavailable" in payload["reasonCodes"]
    assert "market_data.nbbo.cached_candle_quote_fallback_ready" in payload["reasonCodes"]


def test_candles_refresh_false_empty_cache_loads_latest_completed_session_when_market_closed() -> None:
    session_bars = [cached_candle("2026-07-31T19:59:00Z")]
    with (
        patch.object(main.store, "latest", return_value=[]),
        patch.object(main.store, "upsert_many") as upsert_many,
        patch.object(main, "local_market_is_closed", return_value=True),
        patch.object(main, "prepared_last_available_bars", return_value=(None, [])),
        patch.object(main, "previous_completed_market_session_date", return_value="2026-07-31"),
        patch.object(main.alpaca, "get_bars", new_callable=AsyncMock) as get_bars,
    ):
        get_bars.return_value = session_bars
        response = TestClient(main.app).get("/api/candles?symbol=SPY&feed=iex&timeframe=1Min&limit=10&refresh=false")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "alpaca"
    assert payload["sessionDate"] == "2026-07-31"
    assert payload["candles"] == session_bars
    assert "latest completed market session 2026-07-31" in payload["warning"]
    upsert_many.assert_called_once_with(session_bars)
    assert get_bars.await_args.kwargs["start"].startswith("2026-07-31")
    assert get_bars.await_args.kwargs["sort"] == "asc"


def test_candles_market_closed_refresh_true_prefers_existing_cache_without_alpaca_call() -> None:
    cached = [cached_candle()]
    with (
        patch.object(main.store, "latest", return_value=cached),
        patch.object(main, "local_market_is_closed", return_value=True),
        patch.object(main.alpaca, "get_bars", new_callable=AsyncMock) as get_bars,
    ):
        response = TestClient(main.app).get("/api/candles?symbol=SPY&feed=iex&timeframe=1Min&limit=10&refresh=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "cache"
    assert payload["candles"] == cached
    assert "Market is closed" in payload["warning"]
    get_bars.assert_not_called()


def test_candles_response_dedupes_equivalent_iso_timestamps() -> None:
    cached = [
        cached_candle("2026-08-04T18:07:00+00:00"),
        cached_candle("2026-08-04T18:07:00Z"),
        cached_candle("2026-08-04T18:08:00Z"),
    ]
    with (
        patch.object(main.store, "latest", return_value=cached),
        patch.object(main, "local_market_is_closed", return_value=True),
        patch.object(main.alpaca, "get_bars", new_callable=AsyncMock) as get_bars,
    ):
        response = TestClient(main.app).get("/api/candles?symbol=SPY&feed=iex&timeframe=1Min&limit=10&refresh=false")

    assert response.status_code == 200
    payload = response.json()
    assert [candle["timestamp"] for candle in payload["candles"]] == [
        "2026-08-04T18:07:00Z",
        "2026-08-04T18:08:00Z",
    ]
    get_bars.assert_not_called()


def test_candles_market_closed_uses_prepared_last_available_data_before_alpaca_call() -> None:
    prepared = [cached_candle("2026-07-30T19:59:00Z")]
    with (
        patch.object(main.store, "latest", return_value=[]),
        patch.object(main.store, "upsert_many") as upsert_many,
        patch.object(main, "local_market_is_closed", return_value=True),
        patch.object(main, "prepared_last_available_bars", return_value=("2026-07-30", prepared)),
        patch.object(main.alpaca, "get_bars", new_callable=AsyncMock) as get_bars,
    ):
        response = TestClient(main.app).get("/api/candles?symbol=SPY&feed=iex&timeframe=1Min&limit=10&refresh=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "prepared-backtest-data"
    assert payload["sessionDate"] == "2026-07-30"
    assert payload["candles"] == prepared
    assert "last available prepared market session 2026-07-30" in payload["warning"]
    upsert_many.assert_called_once_with(prepared)
    get_bars.assert_not_called()


def test_candles_refresh_timeout_returns_demo_before_frontend_timeout() -> None:
    with (
        patch.object(main.store, "latest", return_value=[]),
        patch.object(main.store, "upsert_many") as upsert_many,
        patch.object(main, "local_market_is_closed", return_value=False),
        patch.object(main.alpaca, "get_bars", new_callable=AsyncMock) as get_bars,
    ):
        get_bars.side_effect = TimeoutError("provider stalled")
        response = TestClient(main.app).get("/api/candles?symbol=SPY&feed=iex&timeframe=1Min&limit=10&refresh=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "demo"
    assert "timed out" in payload["warning"]
    assert len(payload["candles"]) == 10
    upsert_many.assert_called_once()


def test_candles_refresh_timeout_prefers_existing_cache() -> None:
    cached = [cached_candle()]
    with (
        patch.object(main.store, "latest", return_value=cached),
        patch.object(main.store, "upsert_many") as upsert_many,
        patch.object(main, "local_market_is_closed", return_value=False),
        patch.object(main.alpaca, "get_bars", new_callable=AsyncMock) as get_bars,
    ):
        get_bars.side_effect = TimeoutError("provider stalled")
        response = TestClient(main.app).get("/api/candles?symbol=SPY&feed=iex&timeframe=1Min&limit=10&refresh=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "cache"
    assert payload["candles"] == cached
    assert "timed out" in payload["warning"]
    upsert_many.assert_not_called()


def test_market_context_intraday_loader_uses_latest_completed_session_when_market_closed() -> None:
    session_bars = [cached_candle("2026-07-31T19:59:00Z")]
    with (
        patch.object(main.store, "latest", return_value=[]),
        patch.object(main.store, "upsert_many") as upsert_many,
        patch.object(main, "local_market_is_closed", return_value=True),
        patch.object(main, "prepared_last_available_bars", return_value=(None, [])),
        patch.object(main, "previous_completed_market_session_date", return_value="2026-07-31"),
        patch.object(main.alpaca, "get_bars", new_callable=AsyncMock) as get_bars,
    ):
        get_bars.return_value = session_bars
        result = asyncio.run(
            main._context_bars(
                symbol="SPY",
                feed="iex",
                timeframe="1Min",
                limit=10,
                refresh=False,
                as_of=None,
            )
        )

    assert result == session_bars
    upsert_many.assert_called_once_with(session_bars)
    assert get_bars.await_args.kwargs["start"].startswith("2026-07-31")


def test_market_context_loader_uses_prepared_last_available_when_market_closed() -> None:
    prepared = [cached_candle("2026-07-30T19:59:00Z")]
    with (
        patch.object(main.store, "latest", return_value=[]),
        patch.object(main.store, "upsert_many") as upsert_many,
        patch.object(main, "local_market_is_closed", return_value=True),
        patch.object(main, "prepared_last_available_bars", return_value=("2026-07-30", prepared)),
        patch.object(main.alpaca, "get_bars", new_callable=AsyncMock) as get_bars,
    ):
        result = asyncio.run(
            main._context_bars(
                symbol="SPY",
                feed="iex",
                timeframe="1Min",
                limit=10,
                refresh=False,
                as_of=None,
            )
        )

    assert result == prepared
    upsert_many.assert_called_once_with(prepared)
    get_bars.assert_not_called()
