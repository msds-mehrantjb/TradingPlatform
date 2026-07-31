from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.algorithms.regime.configuration import flatten_regime_trading_settings
from backend.app.algorithms.regime.dynamic_profile import resolve_effective_regime_profile
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime_events import event_payload_has_forbidden_operational_state
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.algorithms.regime.settings_repository import RegimeSettingsRepository, regime_settings_repository_inventory


TEST_TMP_ROOT = Path(__file__).resolve().parents[2] / ".pytest_regime_phase2_settings"


def test_phase2_settings_repository_metadata_and_single_active_version() -> None:
    repository, identity = _repository()
    service = RegimeApplicationService(repository=repository)
    settings_repository = RegimeSettingsRepository(repository)

    created = service.handle_settings_command(
        {
            "commandType": "create_version",
            "actor": "settings-admin",
            "source": "phase2-test",
            "reason": "tighten-risk",
            "settings": {"identity": identity, "positionSizing": {"baseRiskPercent": 0.04}},
        }
    )
    snapshot = created["settingsSnapshot"]

    assert regime_settings_repository_inventory()["algorithmIdHardCoded"] is True
    assert created["activated"] is False
    assert snapshot["immutableVersionId"] == created["settingsVersion"]
    assert snapshot["contentHash"] == created["settingsHash"]
    assert snapshot["activationStatus"] == "inactive"
    assert snapshot["activationTimestamp"] is None
    assert snapshot["createdSource"] == "phase2-test"
    assert snapshot["baselineSettings"]["baseRiskPercent"] == 0.10
    assert snapshot["hardSafetyLimits"]["liveTradingEnabled"] is False
    assert snapshot["strategyLifecycleStates"]["moving_average_trend"]["lifecycle"] == "active"
    assert snapshot["regimeProfileMatrixVersion"] == snapshot["profileVersion"]
    assert snapshot["reasonForActivationOrRollback"] == "tighten-risk"

    activated = settings_repository.activate_settings_snapshot(
        {
            "actor": "settings-admin",
            "activationReason": "paper-rollout-tightened-risk",
            "settings": {"identity": identity, "positionSizing": {"baseRiskPercent": 0.04}},
        }
    )
    active_snapshot = service.active_settings(identity)["settingsSnapshot"]

    assert activated["settingsVersion"] == created["settingsVersion"]
    assert active_snapshot["activationStatus"] == "active"
    assert active_snapshot["activationTimestamp"]
    assert active_snapshot["reasonForActivationOrRollback"] == "paper-rollout-tightened-risk"
    assert service.active_settings(identity)["settingsVersion"] == activated["settingsVersion"]

    with pytest.raises(ValueError):
        settings_repository.active_settings_snapshot({**identity, "algorithmId": "weighted_voting"})


def test_phase2_decision_and_backtest_reject_authoritative_request_state() -> None:
    repository, identity = _repository()
    service = RegimeApplicationService(repository=repository)

    for forbidden in (
        "settings",
        "account",
        "position",
        "currentPosition",
        "inventorySnapshot",
        "globalRiskCapacityQuantity",
        "dailyPnl",
        "availableRisk",
        "buyingPower",
    ):
        with pytest.raises(ValueError, match="authoritative request fields"):
            service.evaluate({**identity, forbidden: {}})

    assert event_payload_has_forbidden_operational_state({"marketData": {"currentPosition": {"quantity": 1}}})
    assert event_payload_has_forbidden_operational_state({"marketData": {"globalRiskCapacityQuantity": 10}})


def test_phase2_effective_settings_overlay_order_and_caps() -> None:
    repository, identity = _repository()
    active = RegimeApplicationService(repository=repository).active_settings(identity)
    settings = flatten_regime_trading_settings(active["settingsSnapshot"])
    classification = type(
        "Classification",
        (),
        {"axes": type("Axes", (), {"volatility": "high", "liquidity": "thin", "session": "close", "event_risk": "elevated"})()},
    )()

    effective = resolve_effective_regime_profile(settings, "strong_uptrend", classification)

    assert effective["effectiveSettingsOrder"] == (
        "regime_immutable_baseline",
        "confirmed_regime_profile",
        "volatility_overlay",
        "liquidity_overlay",
        "session_overlay",
        "economic_event_overlay",
        "regime_local_risk_reduction",
        "shared_global_risk_reduction_or_rejection",
    )
    assert effective["baseRiskPercent"] <= settings["baseRiskPercent"]
    assert effective["maxPositionPercent"] <= settings["maxPositionPercent"]
    assert effective["maxParticipationPercent"] <= settings["maxParticipationPercent"]
    assert effective["maximumSlippageBps"] <= settings["maximumSlippageBps"]
    assert effective["maximumHoldingBars"] <= settings["maxHoldingBars"]
    assert effective["minimumNetExpectedEdge"] >= settings["minimumNetExpectedEdge"]
    assert "regime.overlay.volatility.high" in effective["overlayReasons"]
    assert "regime.overlay.liquidity.thin" in effective["overlayReasons"]


def _repository() -> tuple[RegimeRepository, dict[str, str]]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    identity = {
        "algorithmId": "regime",
        "algorithmInstanceId": f"phase2-settings-{uuid4().hex[:8]}",
        "accountId": "paper-account-phase2-settings",
        "runtimeMode": "paper",
        "symbol": "SPY",
    }
    path = TEST_TMP_ROOT / f"{uuid4().hex}.sqlite3"
    return RegimeRepository(f"sqlite:///{path}"), identity
