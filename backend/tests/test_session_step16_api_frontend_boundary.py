from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import shutil
import uuid

from fastapi.testclient import TestClient

from backend.app import main as app_main
from backend.app.main import app
from backend.app.market_context import compute_market_context
from backend.app.algorithms.session import (
    DataQualityState,
    EventRiskState,
    LiquidityState,
    SessionBehavior,
    SessionClassification,
    SessionDecisionJsonlStore,
    SessionPhase,
    VolatilityState,
    build_session_decision_record,
    resolve_session_profile,
)
from backend.app.algorithms.session import api as session_api


NOW = datetime(2026, 7, 23, 14, 5, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]
FRONTEND_MAIN = ROOT / "frontend" / "src" / "main.ts"
FRONTEND_CLIENT = ROOT / "frontend" / "src" / "api" / "client.ts"


def test_session_step16_inventory_schema_and_enum_parity() -> None:
    response = TestClient(app).get("/api/session/inventory")

    assert response.status_code == 200
    payload = response.json()
    assert payload["subsystemVersion"] == "session_subsystem_v1"
    assert payload["classifierVersion"]
    assert payload["featureSchemaVersion"]
    assert set(payload["availablePhaseValues"]) == {item.value for item in SessionPhase}
    assert set(payload["availableBehaviorValues"]) == {item.value for item in SessionBehavior}
    assert payload["moduleStatus"]["classifier"] == "active"
    assert payload["moduleStatus"]["costGate"] == "shadow"
    assert payload["moduleStatus"]["orderSubmission"] == "disabled"
    assert payload["dataReadiness"]["unknownIsNotHealthy"] is True
    assert payload["orderAffectingStatus"]["enabled"] is False
    assert "nbbo_quote" in {feed["id"] for feed in payload["requiredInputFeeds"]}


def test_session_step16_current_and_history_use_authoritative_store(monkeypatch) -> None:
    scratch = _scratch_path()
    store = SessionDecisionJsonlStore(root=scratch / "session")
    record = build_session_decision_record(
        classification=_classification(),
        profile=resolve_session_profile(_classification()),
        output_mode="shadow",
        transition_state={"currentBehavior": "trend_up", "transitionReason": "SESSION_TRANSITION_INITIALIZED"},
        persisted_at=NOW,
    )
    assert store.write_record(record) == "persisted"
    monkeypatch.setattr(session_api, "SESSION_API_STORE", store)

    client = TestClient(app)
    current = client.get("/api/session/current?symbol=SPY&session_date=2026-07-23")
    history = client.get("/api/session/history?symbol=SPY&session_date=2026-07-23&limit=10")

    assert current.status_code == 200
    assert current.json()["status"] == "ready"
    assert current.json()["current"]["classificationId"] == "session-classification-step16"
    assert current.json()["current"]["display"]["phase"] == "Morning"
    assert history.status_code == 200
    assert history.json()["count"] == 1
    assert history.json()["records"][0]["featureSchemaVersion"]
    shutil.rmtree(scratch, ignore_errors=True)


def test_session_step16_current_unavailable_does_not_show_healthy_defaults(monkeypatch) -> None:
    scratch = _scratch_path()
    monkeypatch.setattr(session_api, "SESSION_API_STORE", SessionDecisionJsonlStore(root=scratch / "empty"))

    response = TestClient(app).get("/api/session/current?symbol=SPY")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "unavailable"
    assert payload["current"]["liquidityState"] == "unknown"
    assert payload["current"]["dataQualityState"] == "incomplete"
    assert payload["current"]["safetyBlocks"]["blockNewEntries"] is True
    assert payload["current"]["display"]["unknownOrStale"] is True
    shutil.rmtree(scratch, ignore_errors=True)


def test_session_step16_market_context_includes_authoritative_session_bridge() -> None:
    from backend.app.market_context import compute_market_context

    daily = [_candle(NOW - timedelta(days=80 - index), 400 + index * 0.2) for index in range(80)]
    intraday = [_candle(NOW + timedelta(minutes=index), 420 + index * 0.03) for index in range(15)]

    context = compute_market_context("SPY", daily, intraday)
    session = context["sessionAuthoritative"]

    assert session["classification"]["classifier_version"]
    assert session["classification"]["feature_schema_version"]
    assert session["display"]["phase"]
    assert session["display"]["behavior"]
    assert session["transitionState"]["transitionReason"] == "SESSION_TRANSITION_INITIALIZED"
    assert session["routePermissions"]["readOnly"] is True
    assert session["orderAffectingStatus"]["enabled"] is False


def test_session_step16_market_context_session_bridge_persists_shadow_record(monkeypatch) -> None:
    scratch = _scratch_path()
    store = SessionDecisionJsonlStore(root=scratch / "session")
    monkeypatch.setattr(app_main, "session_decision_store", store)
    daily = [_candle(NOW - timedelta(days=80 - index), 400 + index * 0.2) for index in range(80)]
    intraday = [_candle(NOW + timedelta(minutes=index), 420 + index * 0.03) for index in range(15)]

    app_main.persist_session_context_decision(compute_market_context("SPY", daily, intraday))

    records = store.read_records(symbol="SPY", session_date="2026-07-23")
    assert len(records) == 1
    assert records[0].outputMode == "shadow"
    assert records[0].transitionState["transitionReason"] == "SESSION_TRANSITION_INITIALIZED"
    assert records[0].strategyPermissions["readOnly"] is True
    shutil.rmtree(scratch, ignore_errors=True)


def test_session_step16_frontend_fetches_backend_session_and_does_not_define_classification_constants() -> None:
    main = FRONTEND_MAIN.read_text(encoding="utf-8")
    client = FRONTEND_CLIENT.read_text(encoding="utf-8")

    assert 'session: "/api/session/current"' in client
    assert "fetchSessionCurrent" in main
    assert "renderAuthoritativeSessionLayer" in main
    assert "sessionAuthoritative" in main
    assert "SessionPhaseValues" not in main
    assert "SessionBehaviorValues" not in main
    assert "const sessionClassification" not in main


def test_session_step16_frontend_displays_stale_unknown_and_reason_codes() -> None:
    source = FRONTEND_MAIN.read_text(encoding="utf-8")

    for required in (
        "Unknown / stale evidence",
        "humanReasonCode",
        "data-status=\"unknown\"",
        "Order affecting",
        "Transition",
        "Readiness",
    ):
        assert required in source


def _classification() -> SessionClassification:
    return SessionClassification(
        symbol="SPY",
        session_date="2026-07-23",
        exchange_timezone="America/New_York",
        market_event_time=NOW - timedelta(milliseconds=120),
        feature_snapshot_time=NOW - timedelta(milliseconds=40),
        decision_time=NOW,
        valid_until=NOW + timedelta(seconds=60),
        phase=SessionPhase.MORNING,
        behavior=SessionBehavior.TREND_UP,
        volatility_state=VolatilityState.NORMAL,
        liquidity_state=LiquidityState.HEALTHY,
        data_quality_state=DataQualityState.READY,
        event_risk_state=EventRiskState.CLEAR,
        direction_bias="long",
        phase_confidence=0.9,
        behavior_confidence=0.8,
        volatility_confidence=0.8,
        liquidity_confidence=0.9,
        data_quality_confidence=0.95,
        overall_confidence=0.8,
        safety_block_confidence=0.0,
        reason_codes=("SESSION_TEST_READY",),
        evidence={
            "classificationId": "session-classification-step16",
            "featureSnapshotId": "session-feature-step16",
            "baselineVersion": "baseline-step16",
        },
        allowed_strategy_families=("trend", "pullback", "vwap"),
        blocked_strategy_families=(),
        block_new_entries=False,
    )


def _candle(timestamp: datetime, close: float) -> dict[str, object]:
    return {
        "timestamp": timestamp.isoformat(),
        "open": close - 0.1,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": 1_000_000,
        "trade_count": 1_000,
        "vwap": close,
        "bid": close - 0.01,
        "ask": close + 0.01,
        "quoteAgeMs": 100,
    }


def _scratch_path() -> Path:
    path = Path("backend") / ".test_artifacts" / f"session_step16_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path
