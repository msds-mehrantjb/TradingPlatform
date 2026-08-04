from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisor, RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.service import RegimeApplicationService


ROOT = Path(__file__).resolve().parents[3]
TEST_TMP_ROOT = ROOT / "backend" / ".pytest_regime_phase33_observability"


def test_phase33_runtime_status_exposes_operational_paper_readiness_fields() -> None:
    repository, identity = _repository()
    _seed_owned_operational_records(repository, identity)
    supervisor = RegimeRuntimeSupervisor(
        service=RegimeApplicationService(repository),
        config=RegimeRuntimeSupervisorConfig(
            default_algorithm_instance_id=identity["algorithmInstanceId"],
            default_account_id=identity["accountId"],
            default_runtime_mode="paper",
            symbol="SPY",
        ),
        account_snapshot_provider=lambda active_identity: _fresh_account(active_identity),
    )
    _mark_status_ready(supervisor)

    status = supervisor.status()

    for key in (
        "algorithmId",
        "algorithmInstanceId",
        "accountId",
        "runtimeMode",
        "symbol",
        "paperRequestedOn",
        "paperEffectiveOn",
        "rolloutStage",
        "marketOpen",
        "nextMarketOpen",
        "publisherStatus",
        "lastPublishedBar",
        "lastProcessedBar",
        "barLagSeconds",
        "decisionQueueDepth",
        "outboxQueueDepth",
        "brokerStatus",
        "accountSnapshotStatus",
        "inventoryReconciled",
        "ordersReconciled",
        "killSwitch",
        "activeSettingsVersion",
        "confirmedRegime",
        "latestDecision",
        "latestOrderIntent",
        "latestBrokerOrder",
        "currentRegimePosition",
        "dailyRegimePnl",
        "dailyRegimeTradeCount",
        "entryBlockReasonCodes",
        "workerHeartbeats",
    ):
        assert key in status
    assert status["algorithmId"] == "regime"
    assert status["algorithmInstanceId"] == "regime-paper-default"
    assert status["accountId"] == "paper-account-123"
    assert status["runtimeMode"] == "paper"
    assert status["symbol"] == "SPY"
    assert status["latestOrderIntent"]["algorithmId"] == "regime"
    assert status["latestBrokerOrder"]["algorithmId"] == "regime"
    assert status["currentRegimePosition"]["algorithmId"] == "regime"
    assert status["latestDecision"]["algorithmInstanceId"] == "regime-paper-default"
    assert status["brokerStatus"]["accountId"] == "paper-account-123"
    assert status["accountSnapshotStatus"]["runtimeMode"] == "paper"
    assert status["workerHeartbeats"]["symbol"] == "SPY"
    assert status["latestBrokerOrder"].get("raw") is None
    assert status["latestBrokerOrder"].get("apiSecret") is None
    assert "regime_decision_worker" in status["workerHeartbeats"]["workers"]


def _repository() -> tuple[RegimeRepository, dict[str, str]]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    repository = RegimeRepository(f"sqlite:///{TEST_TMP_ROOT / f'{uuid4().hex}.sqlite3'}")
    identity = {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-paper-default",
        "accountId": "paper-account-123",
        "runtimeMode": "paper",
        "symbol": "SPY",
    }
    return repository, identity


def _seed_owned_operational_records(repository: RegimeRepository, identity: dict[str, str]) -> None:
    now = datetime(2026, 7, 23, 15, 30, tzinfo=UTC).isoformat().replace("+00:00", "Z")
    intent = {
        **identity,
        "decisionId": "phase33-decision",
        "orderIntentId": "phase33-intent",
        "side": "Buy",
        "positionEffect": "enter_long",
        "quantity": 2,
        "entryPrice": 100.0,
        "completedBarFinalized": True,
        "marketDataValidation": {"passed": True, "complete": True, "current": True},
        "globalRiskApproval": {"approved": True, "approvedQuantity": 2, "reservationId": "phase33-reservation"},
        "settingsSnapshot": {"settingsVersion": "phase33-settings"},
        "createdAt": now,
    }
    assert repository.insert_order_intent(intent)["inserted"] is True
    repository.copy_broker_observation(
        {
            **identity,
            "type": "order",
            "decisionId": "phase33-decision",
            "orderIntentId": "phase33-intent",
            "clientOrderId": "regime-phase33-client",
            "brokerOrderId": "broker-phase33",
            "side": "Buy",
            "quantity": 2,
            "status": "accepted",
            "raw": {"brokerSecretNoise": "should-not-surface"},
            "timestamp": now,
        }
    )
    repository.record_position_state(
        identity,
        {
            **identity,
            "positionId": "phase33-position",
            "tradeId": "phase33-trade",
            "decisionId": "phase33-decision",
            "orderIntentId": "phase33-intent",
            "side": "Long",
            "positionStatus": "open",
            "filledQuantity": 2,
            "quantity": 2,
            "averageFillPrice": 100.0,
            "appliedFillIds": ["phase33-fill"],
            "authoritativeInventorySnapshot": {"quantity": 2},
        },
    )
    repository.write_runtime_snapshot(
        identity,
        "finalised_bar_ingestion",
        {
            **identity,
            "status": "healthy",
            "latestFinalizedCandle": now,
            "reasonCodes": ["regime.publisher.poll_succeeded"],
            "observedAt": now,
        },
    )


def _mark_status_ready(supervisor: RegimeRuntimeSupervisor) -> None:
    supervisor.metrics.supervisor_started = True
    supervisor.metrics.recovery_succeeded = True
    supervisor.metrics.inventory_reconciled = True
    supervisor.metrics.broker_paper_mode_verified = True
    supervisor.metrics.broker_connectivity_ok = True
    supervisor.metrics.persistence_available = True
    supervisor.metrics.active_settings_version = "phase33-settings"
    supervisor.metrics.current_rollout_stage = "limited_paper"
    supervisor.metrics.latest_reconciliation = {"reconciled": True}
    supervisor.metrics.last_finalized_bar = {"barCloseTimestamp": "2026-07-23T15:30:00Z", "symbol": "SPY", "runtimeMode": "paper"}
    supervisor.metrics.last_processed_bar = {"barCloseTimestamp": "2026-07-23T15:30:00Z", "symbol": "SPY", "runtimeMode": "paper"}
    supervisor.metrics.latest_decision = {
        "algorithmId": "regime",
        "decisionId": "phase33-decision",
        "decision": {"confirmed_state": {"confirmed_regime": "strong_uptrend"}},
    }
    supervisor.metrics.worker_status["regime_decision_worker"] = "running"
    for component in ("market_event_publisher", "database", "paper_broker", "broker_connectivity"):
        supervisor.metrics.component_health[component]["status"] = "healthy"


def _fresh_account(identity: dict[str, str]) -> dict[str, object]:
    return {
        "sourceAuthority": "backend_account_and_global_risk_services",
        "accountId": identity["accountId"],
        "runtimeMode": "paper",
        "equity": 100_000.0,
        "cash": 100_000.0,
        "buyingPower": 100_000.0,
        "availableBuyingPower": 100_000.0,
        "globalRiskCapacityQuantity": 100,
        "dailyAccountPnl": 0.0,
        "positionsReconciled": True,
        "openOrdersReconciled": True,
        "accountTradingBlocked": False,
        "buyingPowerCurrent": True,
        "accountSnapshotFresh": True,
        "observedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "reasonCodes": [],
    }
