from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.market_data_app import app


def test_lightweight_market_data_health() -> None:
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["service"] == "market-data"


def test_lightweight_market_data_candles_returns_display_data_without_heavy_runtime() -> None:
    client = TestClient(app)

    response = client.get("/api/candles?symbol=SPY&feed=iex&timeframe=1Min&limit=10&refresh=false")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] in {"cache", "demo"}
    assert len(payload["candles"]) == 10


def test_lightweight_market_data_quote_has_bid_ask_fallback() -> None:
    client = TestClient(app)

    response = client.get("/api/market-data/quotes/latest?symbol=SPY&feed=iex")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["quote"]["symbol"] == "SPY"
    assert payload["quote"]["bid"] > 0
    assert payload["quote"]["ask"] >= payload["quote"]["bid"]


def test_lightweight_strategy_fit_inventory_returns_active_catalog_without_heavy_runtime() -> None:
    client = TestClient(app)

    response = client.get("/api/v2/algorithms/strategy-fit/inventory")

    assert response.status_code == 200
    payload = response.json()
    assert payload["algorithmId"] == "strategy_fit"
    assert payload["sourceAuthority"] == "lightweight_market_data_service.voting_ensemble.strategy_catalog"
    assert payload["modules"]["directional"]
    assert payload["modules"]["directional"][0]["status"] == "active"


def test_lightweight_market_forecast_prediction_returns_advisory_contract() -> None:
    client = TestClient(app)

    response = client.get("/api/market-forecast/prediction?symbol=SPY&feed=iex&timeframe=1Min&limit=60&refresh=false")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "SPY"
    assert payload["status"] in {"ready", "INFERENCE_NOT_RUN", "MODEL_UNAVAILABLE", "insufficient_data"}
    assert payload["sourceAuthority"].startswith("lightweight_market_data_service.market_forecast_advisory")
    assert payload["forecastAppliedToOrder"] is False
    assert payload["multiHorizonForecast"]["positionManagementAuthority"] == "advisory_only"
    assert [row["horizonMinutes"] for row in payload["multiHorizonForecast"]["horizons"]] == [5, 10, 15]


def test_lightweight_news_summary_does_not_require_ollama() -> None:
    client = TestClient(app)

    response = client.get("/api/news-summary?symbol=SPY&limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "Lightweight rule summary"
    assert payload["ollamaHealth"]["status"] == "not_required"
    assert payload["warning"] == ""
    assert payload["summary"]["drivers"]
    assert "lightweight market-data service" in payload["summary"]["conclusion"]
