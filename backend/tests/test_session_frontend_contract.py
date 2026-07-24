from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.algorithms.session import SessionBehavior, SessionPhase
from backend.app.main import app


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_MAIN = ROOT / "frontend" / "src" / "main.ts"
FRONTEND_CLIENT = ROOT / "frontend" / "src" / "api" / "client.ts"


def test_session_frontend_inventory_equals_backend_inventory_endpoint() -> None:
    inventory = TestClient(app).get("/api/session/inventory").json()
    client_source = FRONTEND_CLIENT.read_text(encoding="utf-8")

    assert 'session: "/api/session/current"' in client_source
    assert set(inventory["availablePhaseValues"]) == {item.value for item in SessionPhase}
    assert set(inventory["availableBehaviorValues"]) == {item.value for item in SessionBehavior}
    assert inventory["frontendAuthority"]["typescriptClassificationAllowed"] is False


def test_session_frontend_has_no_duplicate_classification_constants() -> None:
    source = FRONTEND_MAIN.read_text(encoding="utf-8")

    forbidden = ("SessionPhaseValues", "SessionBehaviorValues", "const sessionClassification", "opening_discovery:", "balanced_range:")
    for token in forbidden:
        assert token not in source


def test_session_frontend_renders_unknown_stale_without_healthy_defaults() -> None:
    source = FRONTEND_MAIN.read_text(encoding="utf-8")

    assert "renderAuthoritativeSessionLayer" in source
    assert "Unknown / stale evidence" in source
    assert "data-status=\"unknown\"" in source
    assert "Order affecting" in source
    assert "Readiness" in source
    assert "humanReasonCode" in source
