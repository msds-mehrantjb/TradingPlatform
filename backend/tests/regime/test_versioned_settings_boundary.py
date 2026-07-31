from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.algorithms.regime import api as regime_api
from backend.app.algorithms.regime.configuration import (
    REGIME_STRATEGY_IDS,
    flatten_regime_trading_settings,
    validate_regime_trading_settings_snapshot,
)
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.main import app


def identity(instance: str | None = None) -> dict[str, str]:
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": instance or f"settings-boundary-{uuid4().hex[:8]}",
        "accountId": "paper-account-settings-boundary",
        "runtimeMode": "paper",
        "symbol": "SPY",
    }


def temp_repository() -> RegimeRepository:
    root = Path(__file__).resolve().parents[1] / "tmp" / "regime_step3_boundary"
    root.mkdir(parents=True, exist_ok=True)
    return RegimeRepository(f"sqlite:///{root / f'{uuid4().hex}.sqlite'}")


def test_settings_snapshot_exposes_required_camelcase_sections_and_finite_caps() -> None:
    snapshot = validate_regime_trading_settings_snapshot({"identity": identity()}).as_dict()
    flat = flatten_regime_trading_settings(snapshot)

    for key in (
        "dataQuality",
        "strategyLifecycle",
        "strategySettings",
        "familyAggregation",
        "dynamicProfiles",
        "localRisk",
        "positionSizing",
        "entryPolicy",
        "exitPolicy",
        "paperExecution",
        "dailyLimits",
        "mlShadow",
    ):
        assert key in snapshot

    assert set(snapshot["strategySettings"]) == set(REGIME_STRATEGY_IDS)
    assert set(snapshot["strategyLifecycle"]) == set(REGIME_STRATEGY_IDS)
    assert snapshot["settingsHash"] == snapshot["configurationHash"]
    assert flat["baseRiskPercent"] == 0.10
    assert flat["maxPositionPercent"] == 10.0
    assert flat["dailyAllocationPercent"] == 20.0
    assert flat["maxAllowedShares"] > 0
    assert flat["maxOrderNotionalDollars"] > 0
    assert flat["maxPositionNotionalDollars"] > 0
    assert flat["orderTimeToLiveSeconds"] > 0
    assert flat["maximumSlippageBps"] >= 0


def test_camelcase_settings_are_validated_and_unknown_fields_fail_closed() -> None:
    valid = validate_regime_trading_settings_snapshot(
        {
            "identity": identity(),
            "positionSizing": {"baseRiskPercent": 0.04, "maxOrderNotionalDollars": 1_000},
            "paperExecution": {"orderTimeToLiveSeconds": 45},
            "strategySettings": {"moving_average_trend": {"lifecycle": "shadow"}},
        }
    ).as_dict()

    assert valid["positionSizing"]["baseRiskPercent"] == 0.04
    assert valid["paperExecution"]["orderTimeToLiveSeconds"] == 45
    assert valid["strategySettings"]["moving_average_trend"]["lifecycle"] == "shadow"

    with pytest.raises(ValueError):
        validate_regime_trading_settings_snapshot({"identity": identity(), "unsafeOperationalOverride": True})
    with pytest.raises(ValueError):
        validate_regime_trading_settings_snapshot({"identity": identity(), "paperExecution": {"liveBrokerEndpoint": "forbidden"}})


def test_settings_command_lifecycle_keeps_invalid_activation_from_disturbing_active_version() -> None:
    repository = temp_repository()
    service = RegimeApplicationService(repository=repository)
    base_identity = identity()
    active = service.active_settings(base_identity)
    original_version = active["settingsVersion"]

    created = service.handle_settings_command(
        {
            "commandType": "create_version",
            "actor": "settings-admin",
            "settings": {"identity": base_identity, "positionSizing": {"baseRiskPercent": 0.04}},
        }
    )
    assert created["created"] is True
    assert created["activated"] is False
    assert service.active_settings(base_identity)["settingsVersion"] == original_version

    activated = service.handle_settings_command(
        {
            "commandType": "activate_version",
            "actor": "settings-admin",
            "settings": {"identity": base_identity, "positionSizing": {"baseRiskPercent": 0.04}},
        }
    )
    assert activated["settingsVersion"] == created["settingsVersion"]
    assert service.active_settings(base_identity)["flatSettings"]["baseRiskPercent"] == 0.04

    with pytest.raises(ValueError):
        service.handle_settings_command(
            {
                "commandType": "activate_version",
                "actor": "settings-admin",
                "settings": {"identity": base_identity, "positionSizing": {"baseRiskPercent": 0.99}},
            }
        )
    assert service.active_settings(base_identity)["settingsVersion"] == activated["settingsVersion"]

    rolled_back = service.handle_settings_command(
        {
            "commandType": "rollback_version",
            "actor": "settings-admin",
            **base_identity,
            "targetSettingsVersion": original_version,
        }
    )
    assert rolled_back["settingsVersion"] == original_version
    assert service.active_settings(base_identity)["settingsVersion"] == original_version

    with sqlite3.connect(repository.path) as conn:
        event_types = [
            row[0]
            for row in conn.execute(
                "SELECT json_extract(payload_json, '$.eventType') FROM regime_runtime_events WHERE algorithm_id = 'regime'"
            ).fetchall()
        ]
    assert "settings_version_created_audit" in event_types
    assert "settings_activation_audit" in event_types


def test_settings_command_routes_enqueue_background_jobs_without_inline_mutation() -> None:
    assert 'REGIME_JOB_MANAGER.enqueue("settings_activation"' in inspect.getsource(regime_api.create_regime_settings_version)
    assert "activate_settings" not in inspect.getsource(regime_api.activate_regime_settings_version)

    client = TestClient(app)
    base_identity = identity()
    for path in (
        "/api/regime/settings/versions/create",
        "/api/regime/settings/versions/validate",
        "/api/regime/settings/versions/activate",
        "/api/regime/settings/versions/rollback",
    ):
        payload: dict[str, object] = {"actor": "settings-admin", "identity": base_identity}
        if path.endswith("/activate"):
            payload["settingsVersion"] = "regime-settings-does-not-need-to-exist-for-queue"
            payload["reason"] = "route enqueue audit metadata"
        elif not path.endswith("/rollback"):
            payload["settings"] = {"identity": base_identity, "positionSizing": {"baseRiskPercent": 0.04}}
        else:
            payload["targetSettingsVersion"] = "regime-settings-does-not-need-to-exist-for-queue"
            payload["reason"] = "route rollback audit metadata"
        response = client.post(path, json=payload)
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["algorithmId"] == "regime"
        assert body["jobKind"] == "settings_activation"
        assert body["status"] == "queued"


def test_frontend_regime_requests_reject_settings_account_and_inventory_payloads() -> None:
    frontend = Path("frontend/src/features/regime/api.ts").read_text(encoding="utf-8")
    assert '"settings"' in frontend
    assert '"settingsSnapshot"' in frontend
    assert '"account"' in frontend
    assert '"inventorySnapshot"' in frontend
    assert '"currentPosition"' in frontend
    assert '"availableRisk"' in frontend
    assert '"buyingPower"' in frontend
    assert '"dailyPnl"' in frontend
    assert "createRegimeSettingsVersion" in frontend
    assert "validateRegimeSettingsVersion" in frontend
    assert "activateRegimeSettingsVersion" in frontend
    assert "rollbackRegimeSettingsVersion" in frontend

    main = Path("frontend/src/main.ts").read_text(encoding="utf-8")
    backtest_call = main[main.index("runRegimeBacktestOnBackend<RegimeBacktestResult>") : main.index("});", main.index("runRegimeBacktestOnBackend<RegimeBacktestResult>"))]
    assert "settings:" not in backtest_call
    assert "account:" not in backtest_call
    assert "availableBuyingPower" not in backtest_call
