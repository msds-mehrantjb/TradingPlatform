from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import backend.app.algorithms.regime.runtime_publisher as runtime_publisher
import backend.app.algorithms.regime.runtime_events as runtime_events
import backend.app.algorithms.regime.runtime_supervisor as runtime_supervisor
from backend.app.algorithms.regime.paper_session_acceptance import (
    RegimePaperSessionHarnessConfig,
    run_regime_paper_session_acceptance,
    run_regime_paper_session_soak,
)


TEST_TMP_ROOT = Path(__file__).resolve().parents[2] / ".pytest_regime_phase35_paper_session"


def test_phase35_deterministic_paper_session_acceptance_harness(monkeypatch) -> None:
    monkeypatch.setattr(runtime_supervisor, "exchange_session", lambda _value: _RegularSession())
    monkeypatch.setattr(runtime_publisher, "exchange_session", lambda _value: _RegularSession())
    monkeypatch.setattr(runtime_events, "exchange_session", lambda _value: _RegularSession())
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)

    report = asyncio.run(
        run_regime_paper_session_acceptance(
            RegimePaperSessionHarnessConfig(
                repository_path=TEST_TMP_ROOT / f"{uuid4().hex}.sqlite3",
                account_id="paper-session-account-123",
                poll_interval_seconds=0.01,
            )
        )
    )
    payload = report.as_dict()

    assert payload["status"] == "passed"
    assert payload["backendStartedWithoutBrowser"] is True
    assert payload["restoredSuccessfully"] is True
    assert payload["paperInitiallyOff"] is True
    assert payload["automaticPublications"] >= 2
    assert payload["backgroundDecisions"] >= 2
    assert payload["submittedWhileOff"] == 0
    assert payload["paperOnEffective"] is True
    assert payload["readinessGatesHealthy"] is True
    assert payload["eligibleFixtureOrders"] == 1
    assert payload["gatewaySubmissions"] == 1
    assert payload["acknowledgedOrders"] == 1
    assert payload["filledQuantity"] == 5
    assert payload["regimeInventoryQuantity"] == 5
    assert payload["paperOffBlockedNextEntry"] is True
    assert payload["protectionContinued"] is True
    assert payload["reconciliationContinued"] is True
    assert payload["restartDuplicateOrders"] == 0
    assert payload["liveEndpointContacted"] is False
    assert payload["runtimeMode"] == "paper"
    assert payload["algorithmInstanceId"] == "regime-paper-default"
    assert payload["accountId"] == "paper-session-account-123"
    assert payload["symbol"] == "SPY"
    assert all("api.alpaca.markets" not in endpoint for endpoint in payload["endpointsContacted"])


def test_phase35_full_session_soak_mode_produces_readiness_report(monkeypatch) -> None:
    monkeypatch.setattr(runtime_supervisor, "exchange_session", lambda _value: _RegularSession())
    monkeypatch.setattr(runtime_publisher, "exchange_session", lambda _value: _RegularSession())
    monkeypatch.setattr(runtime_events, "exchange_session", lambda _value: _RegularSession())
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)

    report = asyncio.run(
        run_regime_paper_session_soak(
            RegimePaperSessionHarnessConfig(
                repository_path=TEST_TMP_ROOT / f"{uuid4().hex}.sqlite3",
                account_id="paper-session-soak-account-123",
                poll_interval_seconds=0.01,
                soak_minutes=3,
            )
        )
    ).as_dict()

    assert report["soakMode"] is True
    assert report["soakMinutesRequested"] == 3
    assert report["status"] == "passed"
    assert report["automaticPublications"] >= 3
    assert report["gatewaySubmissions"] == 1
    assert report["liveEndpointContacted"] is False


class _RegularSession:
    status = "midday"
    session_date = "2026-08-03"
    market_open_et = "2026-08-03T09:30:00-04:00"
    market_close_et = "2026-08-03T16:00:00-04:00"
    is_early_close = False
    minutes_from_open = 120
    evaluated_at = datetime(2026, 8, 3, 15, 0, tzinfo=UTC)
