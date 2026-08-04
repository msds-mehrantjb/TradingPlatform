from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime import stateful_core
from backend.app.algorithms.regime.account_snapshot import (
    REGIME_ACCOUNT_SNAPSHOT_SOURCE_AUTHORITY,
    build_regime_authoritative_account_snapshot_provider,
)
from backend.app.algorithms.regime.configuration import validate_regime_trading_settings_snapshot
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime_events import RegimeFinalisedBarEvent
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisor, RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.risk.manager import GlobalPortfolioRiskManager
from backend.tests.regime.test_phase22_automatic_paper_readiness import (
    IDENTITY,
    NOW,
    _buy_decision,
    _completed_bar_payload,
    _fresh_account,
)


TEST_TMP_ROOT = Path(__file__).resolve().parents[2] / ".pytest_regime_phase26_account_snapshots"


def test_phase26_authoritative_provider_combines_backend_account_and_global_risk_services() -> None:
    manager = GlobalPortfolioRiskManager()
    manager.reservations.reserve(
        decision_id="other-regime-decision",
        algorithm_id="regime",
        symbol="SPY",
        quantity=10,
        buying_power=1_000.0,
        risk_dollars=100.0,
    )

    def account_provider() -> dict:
        return {
            "sourceAuthority": "broker",
            "accountId": "paper-account-123",
            "runtimeMode": "paper",
            "equity": 50_000.0,
            "cash": 25_000.0,
            "buyingPower": 10_000.0,
            "lastPrice": 100.0,
            "positionsReconciled": True,
            "openOrdersReconciled": True,
            "accountTradingBlocked": False,
            "settingsSnapshot": {"caller": "not-authoritative"},
            "inventorySnapshot": {"quantity": 99},
            "positions": [{"symbol": "SPY"}],
            "orders": [{"symbol": "SPY"}],
            "fills": [{"symbol": "SPY"}],
            "runtimeState": {"paused": False},
        }

    provider = build_regime_authoritative_account_snapshot_provider(
        account_provider=account_provider,
        global_risk_manager=manager,
    )

    snapshot = provider(IDENTITY)

    assert snapshot["sourceAuthority"] == REGIME_ACCOUNT_SNAPSHOT_SOURCE_AUTHORITY
    assert snapshot["accountId"] == "paper-account-123"
    assert snapshot["runtimeMode"] == "paper"
    assert snapshot["availableBuyingPower"] == 9_000.0
    assert snapshot["globalRiskCapacityQuantity"] == 90
    assert snapshot["dailyAccountPnl"] == 0.0
    assert snapshot["positionsReconciled"] is True
    assert snapshot["openOrdersReconciled"] is True
    assert snapshot["accountTradingBlocked"] is False
    for caller_key in ("settingsSnapshot", "inventorySnapshot", "positions", "orders", "fills", "runtimeState"):
        assert caller_key not in snapshot


def test_phase26_supervisor_fails_closed_for_stale_wrong_account_snapshot() -> None:
    stale = (datetime.now(UTC) - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")

    def account_provider(identity: dict[str, str]) -> dict:
        return {
            "sourceAuthority": REGIME_ACCOUNT_SNAPSHOT_SOURCE_AUTHORITY,
            "accountId": "another-paper-account",
            "runtimeMode": "paper",
            "equity": 100_000.0,
            "cash": 100_000.0,
            "buyingPower": 100_000.0,
            "availableBuyingPower": 100_000.0,
            "globalRiskCapacityQuantity": 1_000,
            "dailyAccountPnl": 0.0,
            "positionsReconciled": True,
            "openOrdersReconciled": True,
            "accountTradingBlocked": False,
            "buyingPowerCurrent": True,
            "accountSnapshotFresh": True,
            "observedAt": stale,
        }

    supervisor = _supervisor(account_snapshot_provider=account_provider)
    event = RegimeFinalisedBarEvent.from_payload({**_completed_bar_payload(), **IDENTITY})
    snapshot = supervisor._load_shared_account_snapshot(event)

    assert snapshot["availableBuyingPower"] == 0.0
    assert snapshot["globalRiskCapacityQuantity"] == 0
    assert snapshot["accountTradingBlocked"] is True
    assert "regime.account_snapshot.account_id_mismatch" in snapshot["reasonCodes"]
    assert "regime.account_snapshot.observed_at_stale" in snapshot["reasonCodes"]
    assert "regime.account_snapshot.account_id_mismatch" in supervisor.metrics.entry_block_reason_codes


def test_phase26_stateful_core_zeroes_new_entry_when_account_snapshot_is_stale(monkeypatch) -> None:
    settings = validate_regime_trading_settings_snapshot({"identity": IDENTITY}).as_dict()
    monkeypatch.setattr(stateful_core, "calculate_regime_decision", lambda *args, **kwargs: _buy_decision(settings))
    account = {
        **_fresh_account(),
        "accountSnapshotFresh": False,
        "buyingPowerCurrent": False,
        "reasonCodes": ["regime.account_snapshot.provider_unavailable"],
    }

    result = stateful_core.process_completed_bar(
        snapshot=build_regime_market_snapshot(_completed_bar_payload()["marketData"]),
        settings_snapshot=settings,
        previous_state=None,
        inventory_snapshot={**IDENTITY, "quantity": 0, "openOrderQuantity": 0, "reservedCash": 0.0, "inventoryReconciled": True},
        account_snapshot=account,
    )

    assert result["orderProposal"] is None
    assert result["sizing"]["quantity"] == 0
    assert "regime.sizing.account_snapshot_stale" in result["sizing"]["blockers"]
    assert "regime.account_snapshot.stale" in result["sizing"]["blockers"]
    assert result["globalRiskApproval"] is None


def _supervisor(*, account_snapshot_provider) -> RegimeRuntimeSupervisor:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    repository = RegimeRepository(f"sqlite:///{TEST_TMP_ROOT / f'{uuid4().hex}.sqlite3'}")
    return RegimeRuntimeSupervisor(
        service=RegimeApplicationService(repository),
        config=RegimeRuntimeSupervisorConfig(
            default_algorithm_instance_id="regime-paper-default",
            default_account_id="paper-account-123",
            default_runtime_mode="paper",
            symbol="SPY",
            account_snapshot_max_age_seconds=30,
        ),
        account_snapshot_provider=account_snapshot_provider,
    )
