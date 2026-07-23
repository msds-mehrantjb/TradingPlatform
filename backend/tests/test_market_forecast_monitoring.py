from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from backend.app import main as app_main
from backend.app.main import app
from backend.app.market_forecast_monitoring import market_forecast_monitoring_alerts


def execution_record(index: int, *, latency_ms: float, cost_error: float, filled: bool, net_value: float = 0.02) -> dict:
    decision_at = datetime(2026, 7, 23, 13, 30, tzinfo=UTC) + timedelta(minutes=index)
    submitted_at = decision_at + timedelta(milliseconds=latency_ms)
    return {
        "observationId": f"exec-{index}",
        "symbol": "SPY",
        "sourceMode": "paper",
        "decisionTimestamp": decision_at.isoformat(),
        "orderSubmissionTimestamp": submitted_at.isoformat(),
        "decisionToSubmissionLatencyMs": latency_ms,
        "labels": {
            "filled": filled,
            "nonFill": not filled,
            "partialFill": False,
            "stopLimitMiss": False,
            "realizedCostError": cost_error,
            "incrementalRealizedNetValue": net_value,
        },
    }


def forecast_record(
    index: int,
    *,
    probability: float,
    correct: bool,
    regime: str = "trend",
    net_value: float = 0.03,
) -> dict:
    prediction_at = datetime(2026, 7, 23, 13, 30, tzinfo=UTC) + timedelta(minutes=index)
    return {
        "forecastInvocationId": f"forecast-{index}",
        "symbol": "SPY",
        "predictionTimestamp": prediction_at.isoformat(),
        "eventTimestamp": prediction_at.isoformat(),
        "decisionTimestamp": (prediction_at + timedelta(milliseconds=200)).isoformat(),
        "predictionProbabilities": {
            "probabilityBuySuccess": probability,
            "probabilitySellSuccess": 1 - probability,
            "probabilityTimeout": 0.05,
        },
        "prediction": {"decisionAction": "buy"},
        "marketRegime": {"label": regime},
        "actual": {
            "status": "resolved",
            "executedDecisionCorrect": correct,
            "tradeResult": "win" if correct else "loss",
            "incrementalRealizedNetValueAfterExecutionCosts": net_value,
            "realizedVersusEstimatedCostError": 0.002,
        },
    }


def test_monitoring_alerts_latency_cost_calibration_and_fill_drift() -> None:
    executions = [
        *(execution_record(index, latency_ms=250, cost_error=0.002, filled=True) for index in range(12)),
        *(execution_record(index, latency_ms=1800, cost_error=0.09, filled=index % 6 == 0) for index in range(12, 18)),
    ]
    forecasts = [
        *(forecast_record(index, probability=0.82, correct=True) for index in range(12)),
        *(forecast_record(index, probability=0.91, correct=False) for index in range(12, 18)),
    ]

    result = market_forecast_monitoring_alerts(
        "SPY",
        forecast_records=forecasts,
        execution_records=executions,
        config={"minSamples": 6},
    )

    alert_ids = {alert["id"] for alert in result["alerts"]}
    assert result["status"] == "ALERTS_PRESENT"
    assert "monitoring.latency_drift" in alert_ids
    assert "monitoring.cost_estimate_error" in alert_ids
    assert "monitoring.calibration_drift" in alert_ids
    assert "monitoring.fill_rate_deterioration" in alert_ids
    assert all(alert["activationPolicy"] == "read_only_alerts_do_not_authorize_orders" for alert in result["alerts"])


def test_monitoring_alerts_regime_specific_failure() -> None:
    forecasts = [
        *(forecast_record(index, probability=0.70, correct=True, regime="trend", net_value=0.04) for index in range(8)),
        *(forecast_record(index, probability=0.80, correct=False, regime="choppy", net_value=-0.04) for index in range(8, 14)),
    ]

    result = market_forecast_monitoring_alerts(
        "SPY",
        forecast_records=forecasts,
        execution_records=[],
        config={"minSamples": 6, "regimeMinSamples": 3},
    )

    regime_alerts = [alert for alert in result["alerts"] if alert["id"] == "monitoring.regime_specific_failure"]
    assert regime_alerts
    assert regime_alerts[0]["dimensions"]["regime"] == "choppy"
    assert regime_alerts[0]["dimensions"]["averageIncrementalNetValue"] < 0


def test_market_forecast_monitoring_alert_endpoint(monkeypatch) -> None:
    def fake_alerts(symbol: str, *, limit: int) -> dict:
        return {
            "status": "NO_ALERTS",
            "symbol": symbol,
            "monitoringVersion": "test",
            "activationPolicy": "read_only_alerts_do_not_authorize_orders",
            "alerts": [],
            "summary": {"limit": limit},
        }

    monkeypatch.setattr(app_main, "market_forecast_monitoring_alerts", fake_alerts)
    response = TestClient(app).get("/api/market-forecast/monitoring/alerts?symbol=SPY&limit=50")

    assert response.status_code == 200
    assert response.json()["symbol"] == "SPY"
    assert response.json()["summary"]["limit"] == 50
