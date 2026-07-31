from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app


def test_phase16_evaluate_rejects_direct_market_data_and_authoritative_state() -> None:
    client = TestClient(app)

    direct_data = client.post("/api/regime/evaluate", json={"marketData": {"symbol": "SPY", "candles": []}})
    caller_state = client.post("/api/regime/evaluate", json={"account": {"buyingPower": 100_000}})

    assert direct_data.status_code == 400, direct_data.text
    assert "regime.api.evaluate_direct_market_data_rejected" in direct_data.json()["detail"]["reasonCodes"]
    assert caller_state.status_code == 400, caller_state.text
    assert "regime.api.frontend_authoritative_payload_rejected" in caller_state.json()["detail"]["reasonCodes"]


def test_phase16_evaluate_diagnostic_shadow_is_read_only_repository_loaded() -> None:
    response = TestClient(app).post(
        "/api/regime/evaluate",
        json={"requestType": "diagnostic_shadow", "algorithmInstanceId": "regime-default", "accountId": "default", "runtimeMode": "shadow", "symbol": "SPY"},
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["requestType"] == "diagnostic_shadow"
    assert body["apiHandlersExecuteAuthoritativeTradingLogic"] is False
    assert "jobId" not in body
    assert "regime.api.diagnostic_shadow_repository_loaded_state" in body["reasonCodes"]


def test_phase16_backtest_run_returns_job_id_and_status_result_endpoints() -> None:
    response = TestClient(app).post("/api/regime/backtests/run", json={"symbol": "SPY", "candles": []})

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["algorithmId"] == "regime"
    assert body["jobKind"] == "backtest"
    assert body["apiHandlersExecuteHeavyWorkInline"] is False
    assert body["jobId"] in body["statusEndpoint"]
    assert body["jobId"] in body["resultEndpoint"]


def test_phase16_read_only_status_endpoints_are_available() -> None:
    client = TestClient(app)
    paths = (
        "/api/regime/runtime/health",
        "/api/regime/runtime/last-processed-bar",
        "/api/regime/settings/active-version",
        "/api/regime/strategies/inventory",
        "/api/regime/runtime/current-regime",
        "/api/regime/inventory/current",
        "/api/regime/orders/open",
        "/api/regime/reconciliation/status",
        "/api/regime/rollout/stage",
        "/api/regime/decisions/recent",
        "/api/regime/blockers/recent",
        "/api/regime/backtests/jobs",
    )

    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, f"{path}: {response.text}"
        assert response.json()["algorithmId"] == "regime"


def test_phase16_settings_activation_requires_explicit_audit_metadata() -> None:
    client = TestClient(app)
    missing = client.post("/api/regime/settings/versions/activate", json={"actor": "settings-admin"})
    accepted = client.post(
        "/api/regime/settings/versions/activate",
        json={"actor": "settings-admin", "settingsVersion": "regime-settings-v1", "reason": "phase16 explicit activation audit"},
    )
    rollback_missing = client.post("/api/regime/settings/versions/rollback", json={"actor": "settings-admin", "targetSettingsVersion": "regime-settings-v1"})

    assert missing.status_code == 400, missing.text
    assert "regime.api.settings_activation_requires_explicit_audit_metadata" in missing.json()["detail"]["reasonCodes"]
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["jobKind"] == "settings_activation"
    assert rollback_missing.status_code == 400, rollback_missing.text


def test_phase16_frontend_evaluate_helper_rejects_direct_market_data() -> None:
    frontend = Path("frontend/src/features/regime/api.ts").read_text(encoding="utf-8")

    assert "DIRECT_EVALUATION_DATA_KEYS" in frontend
    assert '"marketData"' in frontend
    assert "trusted finalized-bar reference" in frontend
