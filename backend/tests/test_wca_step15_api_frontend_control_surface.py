from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

import backend.app.algorithms.wca.api as wca_api
from backend.app.algorithms.wca.contracts import WcaCandle, WcaPaperExecutionRequest
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.algorithms.wca.service import WcaService
from backend.app.main import app


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "wca" / "golden_snapshots.json"


def test_wca_status_and_inventory_are_read_only_control_surface(monkeypatch) -> None:
    service = _service()
    monkeypatch.setattr(wca_api, "WCA_API_SERVICE", service)
    client = TestClient(app)

    status = client.get("/api/wca/status")
    inventory = client.get("/api/wca/inventory")
    virtual_inventory = client.get("/api/wca/inventory/virtual")

    assert status.status_code == 200, status.text
    body = status.json()
    assert body["apiProcessRole"] == "transport_and_presentation_only"
    assert body["runtimeProcessRequired"] is True
    assert body["apiHealth"]["doesNotRunRuntime"] is True
    assert body["paperOnly"] is True
    assert "runtimeHealth" in body
    assert "activeVersions" in body
    assert "observability" in body
    assert "brokerStatus" in body["observability"]
    assert "reconciliationStatus" in body["observability"]
    assert body["virtualInventory"]["separateFromOtherAlgorithms"] is True

    assert inventory.status_code == 200, inventory.text
    catalog = inventory.json()
    assert len(catalog["primary"]) == 11
    assert len(catalog["modifiers"]) == 11
    assert len(catalog["hardFilters"]) == 7

    assert virtual_inventory.status_code == 200, virtual_inventory.text
    assert virtual_inventory.json()["algorithmId"] == "wca"


def test_wca_configuration_update_creates_candidate_and_activation_command(monkeypatch) -> None:
    service = _service()
    monkeypatch.setattr(wca_api, "WCA_API_SERVICE", service)
    client = TestClient(app)

    before = client.get("/api/wca/configuration").json()["configurationVersion"]
    candidate = client.put("/api/wca/configuration", json={"creator": "step15-test"})
    after = client.get("/api/wca/configuration").json()["configurationVersion"]

    assert candidate.status_code == 200, candidate.text
    candidate_body = candidate.json()
    assert candidate_body["status"] == "CANDIDATE_SAVED"
    assert candidate_body["activationRequired"] is True
    assert "wca.api.configuration_does_not_activate_inline" in candidate_body["reasonCodes"]
    assert after == before

    activation = client.post(f"/api/wca/configuration/{candidate_body['configurationVersion']}/activate")

    assert activation.status_code == 202, activation.text
    assert activation.json()["commandType"] == "configuration_activation"


def test_wca_background_actions_return_accepted_command_or_job_ids(monkeypatch) -> None:
    service = _service()
    service.evaluate = _raise_if_inline  # type: ignore[method-assign]
    service.execute_paper = _raise_if_inline  # type: ignore[method-assign]
    monkeypatch.setattr(wca_api, "WCA_API_SERVICE", service)
    client = TestClient(app)

    shadow = client.post("/api/wca/evaluate", json=_shadow_payload())
    paper = client.post("/api/wca/paper/manual", json=WcaPaperExecutionRequest(candles=_candles(), runId="step15-paper").model_dump(mode="json", by_alias=True))
    pause = client.post("/api/wca/runtime/pause", json={"reason": "step15"})
    resume = client.post("/api/wca/runtime/resume", json={"reason": "step15"})
    reconciliation = client.post("/api/wca/reconciliation/request", json={"accountId": "paper", "symbol": "SPY"})
    emergency = client.post("/api/wca/risk/emergency-reduce", json={"accountId": "paper", "symbol": "SPY", "reason": "step15"})

    assert shadow.status_code == 202, shadow.text
    assert shadow.json()["job_type"] == "shadow_comparison"
    assert shadow.json()["queued"] is True

    for response, command_type in (
        (paper, "manual_paper_command"),
        (pause, "pause_new_entries"),
        (resume, "resume_new_entries"),
        (reconciliation, "broker_reconciliation"),
        (emergency, "emergency_risk_reduction"),
    ):
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["commandType"] == command_type
        assert body["accepted"] is True
        assert body["paperOnly"] is True
        assert "wca.api.background_control_surface" in body["reasonCodes"]
        assert "decision" not in body
        progress = client.get(f"/api/wca/commands/{body['commandId']}")
        assert progress.status_code == 200, progress.text
        assert progress.json()["command_type"] == command_type


def test_wca_frontend_source_is_presentation_and_transport_only() -> None:
    feature_sources = "".join(path.read_text(encoding="utf-8") for path in (ROOT / "frontend" / "src" / "features" / "wca").glob("*.ts"))
    main_source = (ROOT / "frontend" / "src" / "main.ts").read_text(encoding="utf-8")

    assert "localStorage.setItem" not in feature_sources
    assert "Frontend WCA replay disabled; enqueue backend WCA backtest/research job." in main_source
    assert "WCA short-cycle long entries with automatic sell exits" not in main_source
    assert "Backend WCA backtest queued as" in main_source
    assert "/api/wca/backtests" in main_source


def _service() -> WcaService:
    db_root = ROOT / "backend" / "tests" / "tmp"
    db_root.mkdir(parents=True, exist_ok=True)
    db_path = db_root / f"wca-step15-{uuid4().hex}.sqlite"
    return WcaService(repository=WcaSqliteRepository(f"sqlite:///{db_path.as_posix()}"))


def _candles(count: int = 60) -> tuple[WcaCandle, ...]:
    start = datetime(2026, 7, 20, 13, 30, tzinfo=timezone.utc)
    return tuple(
        WcaCandle(
            timestamp=start + timedelta(minutes=index),
            open=100 + index * 0.02,
            high=100.08 + index * 0.02,
            low=99.95 + index * 0.02,
            close=100.03 + index * 0.02,
            volume=300_000,
        )
        for index in range(count)
    )


def _shadow_payload() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["snapshots"][0]


def _raise_if_inline(*_args, **_kwargs):
    raise AssertionError("WCA API endpoints must enqueue background work instead of running inline")
