from __future__ import annotations

import os
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

_TEST_DB = Path(tempfile.gettempdir()) / "trading_candles_endpoint_tests.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"

from backend.app import main


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


def test_candles_refresh_false_empty_cache_loads_latest_completed_session_when_market_closed() -> None:
    session_bars = [cached_candle("2026-07-31T19:59:00Z")]
    with (
        patch.object(main.store, "latest", return_value=[]),
        patch.object(main.store, "upsert_many") as upsert_many,
        patch.object(main, "local_market_is_closed", return_value=True),
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


def test_candles_refresh_timeout_returns_demo_before_frontend_timeout() -> None:
    with (
        patch.object(main.store, "latest", return_value=[]),
        patch.object(main.store, "upsert_many") as upsert_many,
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
