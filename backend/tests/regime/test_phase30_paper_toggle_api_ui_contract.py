from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.rollout import (
    LIMITED_PAPER_PROMOTION_EVIDENCE,
    REGIME_OPERATIONAL_ROLLOUT_EVIDENCE_SOURCE,
    activate_operational_rollout_stage,
)
from backend.app.algorithms.regime.runtime_supervisor import (
    RegimeRuntimeSupervisor,
    RegimeRuntimeSupervisorConfig,
    _paper_effective_activation_evaluation,
)
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.main import app


NOW = datetime(2026, 7, 23, 15, 30, tzinfo=UTC)
TEST_TMP_ROOT = Path(__file__).resolve().parents[2] / ".pytest_regime_phase30_paper_toggle"
IDENTITY = {
    "algorithmId": "regime",
    "algorithmInstanceId": "regime-paper-default",
    "accountId": "paper-account-123",
    "runtimeMode": "paper",
    "symbol": "SPY",
}


def test_phase30_runtime_status_exposes_requested_effective_and_named_blockers() -> None:
    repository = _repository()
    repository.write_runtime_snapshot(IDENTITY, "automatic_paper_control", {"requestedAutomaticPaperTradingEnabled": True})
    supervisor = _supervisor(repository)
    supervisor.metrics.supervisor_started = True
    supervisor.metrics.settings_available = False
    supervisor.metrics.queue_lag_block_active = True
    supervisor.metrics.kill_switch_active = True
    supervisor.metrics.component_health["database"]["status"] = "unhealthy"
    supervisor.metrics.component_health["market_event_publisher"]["status"] = "unhealthy"

    status = supervisor.status()

    assert status["paperRequestedOn"] is True
    assert status["paperEffectiveOn"] is False
    assert status["automaticPaperControl"]["paperRequestedOn"] is True
    assert status["automaticPaperControl"]["paperEffectiveOn"] is False
    assert {
        "rollout_not_promoted",
        "broker_unhealthy",
        "account_snapshot_stale",
        "market_data_stale",
        "inventory_not_reconciled",
        "open_orders_not_reconciled",
        "kill_switch_active",
        "database_unhealthy",
        "settings_unavailable",
        "publisher_unhealthy",
    }.issubset(set(status["paperEffectiveBlockers"]))


def test_phase30_effective_activation_requires_all_backend_gates() -> None:
    repository = _repository()
    _record_stage_evidence(repository)
    _activate_stage(repository, "simulated_execution")
    rollout = _activate_stage(repository, "limited_paper")
    supervisor = _supervisor(repository, account_snapshot_provider=lambda identity: _fresh_account(identity))
    supervisor.metrics.supervisor_started = True
    supervisor.metrics.recovery_succeeded = True
    supervisor.metrics.inventory_reconciled = True
    supervisor.metrics.broker_paper_mode_verified = True
    supervisor.metrics.broker_connectivity_ok = True
    supervisor.metrics.latest_reconciliation = {"reconciled": True}
    supervisor.metrics.persistence_available = True
    supervisor.metrics.settings_available = True
    for component in ("database", "settings_repository", "market_event_publisher", "paper_broker", "broker_connectivity"):
        supervisor.metrics.component_health[component]["status"] = "healthy"

    evaluation = _paper_effective_activation_evaluation(
        supervisor,
        IDENTITY,
        rollout_snapshot=rollout,
        control_snapshot={"requestedAutomaticPaperTradingEnabled": True, "liveTradingEnabled": False},
        requested=True,
        evaluated_at=NOW,
    )

    assert evaluation["blockers"] == ()
    assert evaluation["gateSnapshot"]["rolloutStageAllowsRealPaperExecution"] is True


def test_phase30_automatic_paper_api_rejects_frontend_runtime_state_writes() -> None:
    response = TestClient(app).post(
        "/api/regime/rollout/automatic-paper",
        json={
            "enabled": True,
            "actor": "frontend",
            "reason": "try to write state",
            "runtimeState": {"paperEffectiveOn": True},
        },
    )

    assert response.status_code == 400
    assert "regime.api.automatic_paper_authoritative_payload_rejected" in response.json()["detail"]["reasonCodes"]


def _repository() -> RegimeRepository:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    return RegimeRepository(f"sqlite:///{TEST_TMP_ROOT / f'{uuid4().hex}.sqlite3'}")


def _supervisor(repository: RegimeRepository, *, account_snapshot_provider=None) -> RegimeRuntimeSupervisor:
    return RegimeRuntimeSupervisor(
        service=RegimeApplicationService(repository),
        config=RegimeRuntimeSupervisorConfig(
            default_algorithm_instance_id=IDENTITY["algorithmInstanceId"],
            default_account_id=IDENTITY["accountId"],
            default_runtime_mode="paper",
            maintenance_interval_seconds=60,
            heartbeat_interval_seconds=60,
        ),
        account_snapshot_provider=account_snapshot_provider,
    )


def _fresh_account(identity: dict[str, str]) -> dict:
    return {
        "sourceAuthority": "backend_account_and_global_risk_services",
        "accountId": identity["accountId"],
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
        "observedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


def _record_stage_evidence(repository: RegimeRepository) -> None:
    payload = {
        "backendEvidenceSource": REGIME_OPERATIONAL_ROLLOUT_EVIDENCE_SOURCE,
        "evidenceId": f"phase30-evidence-{uuid4().hex}",
        "recordedAt": NOW.isoformat().replace("+00:00", "Z"),
        "persistedEvidenceIds": LIMITED_PAPER_PROMOTION_EVIDENCE,
        **{requirement: True for requirement in LIMITED_PAPER_PROMOTION_EVIDENCE},
    }
    assert repository.record_regime_rollout_promotion_evidence(IDENTITY, payload)["recorded"] is True


def _activate_stage(repository: RegimeRepository, stage: str) -> dict:
    result = activate_operational_rollout_stage(
        _Store(repository),
        stage,
        actor="phase30-test",
        reason=f"activate {stage}",
        evidence=repository.read_regime_rollout_promotion_evidence(IDENTITY),
        activated_at=NOW,
    )
    assert result["activated"] is True
    return dict(result)


class _Store:
    def __init__(self, repository: RegimeRepository) -> None:
        self.repository = repository

    def read_snapshot(self, key: str) -> dict:
        snapshot = self.repository.read_runtime_snapshot(IDENTITY, key)
        if snapshot is None:
            raise KeyError(key)
        return snapshot

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.repository.write_runtime_snapshot(IDENTITY, key, dict(snapshot))
