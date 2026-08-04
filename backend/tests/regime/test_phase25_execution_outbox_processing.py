from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime.execution_gateway import RegimePaperGatewayStore
import backend.app.algorithms.regime.runtime_supervisor as regime_runtime_supervisor
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.rollout import (
    LIMITED_PAPER_PROMOTION_EVIDENCE,
    REGIME_OPERATIONAL_ROLLOUT_EVIDENCE_SOURCE,
    activate_operational_rollout_stage,
)
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisor, RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.execution import PaperGatewayBrokerAck, PaperOrderGateway


ROOT = Path(__file__).resolve().parents[3]
TEST_TMP_ROOT = ROOT / "backend" / ".pytest_regime_execution_outbox_phase25"
NOW = datetime(2026, 7, 23, 15, 30, tzinfo=UTC)


def test_phase25_simulated_rollout_processes_outbox_without_real_paper_gateway() -> None:
    repository, identity = _repository()
    _record_stage_evidence(repository, identity)
    _activate_stage(repository, identity, "simulated_execution")
    _insert_intent(repository, identity)
    supervisor = RegimeRuntimeSupervisor(service=RegimeApplicationService(repository), config=_config(), paper_gateway=None)
    _mark_ready(supervisor)

    result = supervisor.process_execution_outbox_once()

    assert result["processed"] is True
    assert result["status"] == "filled"
    assert repository.table_counts()["regime_orders"] == 1
    assert repository.table_counts()["regime_fills"] == 1


def test_phase25_real_paper_rollout_processes_configured_paper_identity_not_default_shadow(monkeypatch) -> None:
    repository, identity = _repository()
    _record_stage_evidence(repository, identity)
    _activate_stage(repository, identity, "simulated_execution")
    _activate_stage(repository, identity, "limited_paper")
    _insert_intent(repository, identity)
    broker = _FakePaperBroker()
    gateway = PaperOrderGateway(broker, RegimePaperGatewayStore(repository, identity))
    supervisor = RegimeRuntimeSupervisor(
        service=RegimeApplicationService(repository),
        config=_config(),
        paper_gateway=gateway,
        account_snapshot_provider=lambda identity: _fresh_account(identity),
    )
    _mark_ready(supervisor)
    monkeypatch.setattr(regime_runtime_supervisor, "exchange_session", lambda _: _RegularSession())
    repository.write_runtime_snapshot(
        identity,
        "automatic_paper_control",
        {
            "requestedAutomaticPaperTradingEnabled": True,
            "automaticPaperTradingEnabled": True,
            "automaticPaperSubmissionEnabled": True,
            "paperButtonRequested": True,
            "paperButtonEffective": True,
            "rolloutStage": "limited_paper",
        },
    )

    result = supervisor.process_execution_outbox_once()

    assert result["processed"] is True
    assert result["submitted"] is True
    assert result["status"] == "acknowledged"
    assert broker.submit_count == 1
    latest = repository.read_execution_outbox_record(identity, "phase25-intent-1")
    assert latest is not None
    assert latest["brokerClientOrderId"]
    assert latest["algorithmInstanceId"] == "regime-paper-default"


def test_phase25_outbox_claim_is_exclusive_for_configured_paper_identity() -> None:
    repository, identity = _repository()
    _insert_intent(repository, identity)

    first = repository.claim_next_execution_outbox_record(identity, owner_id="worker-a", lease_seconds=30, now=NOW.isoformat().replace("+00:00", "Z"))
    second = repository.claim_next_execution_outbox_record(identity, owner_id="worker-b", lease_seconds=30, now=NOW.isoformat().replace("+00:00", "Z"))

    assert first is not None
    assert first["processingStatus"].startswith("processing:")
    assert first["claimedBy"] == "worker-a"
    assert second is None


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


def _config() -> RegimeRuntimeSupervisorConfig:
    return RegimeRuntimeSupervisorConfig(
        default_algorithm_instance_id="regime-paper-default",
        default_account_id="paper-account-123",
        default_runtime_mode="paper",
        symbol="SPY",
    )


def _insert_intent(repository: RegimeRepository, identity: dict[str, str]) -> None:
    created_at = datetime.now(UTC) - timedelta(seconds=10)
    payload = {
        **identity,
        "decisionId": "phase25-decision-1",
        "orderIntentId": "phase25-intent-1",
        "algorithmVersion": "regime_algorithm_v3_backend_authoritative",
        "settingsVersion": "phase25-settings",
        "profileVersion": "phase25-profile",
        "side": "Buy",
        "positionEffect": "enter_long",
        "quantity": 1,
        "entryPrice": 100.0,
        "limitPrice": 100.0,
        "stopPrice": 99.5,
        "targetPrice": 101.0,
        "riskDollars": 5.0,
        "completedBarFinalized": True,
        "completedBarTimestamp": datetime.now(UTC).replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z"),
        "marketDataValidation": {
            "passed": True,
            "complete": True,
            "current": True,
            "dataTimestamp": datetime.now(UTC).replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z"),
        },
        "globalRiskApproval": {"approved": True, "approvedQuantity": 1, "reservationId": "phase25-reservation-1"},
        "settingsSnapshot": {
            "settingsVersion": "phase25-settings",
            "profileVersion": "phase25-profile",
            "execution": {"orderTimeToLiveSeconds": 300, "orderType": "limit", "maximumCancelReplaceAttempts": 1},
        },
        "createdAt": created_at.isoformat().replace("+00:00", "Z"),
        "expiresAt": (created_at + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    }
    assert repository.insert_order_intent(payload)["inserted"] is True
    assert repository.record_local_risk_result(
        identity,
        {
            **identity,
            "localRiskResultId": "phase25-local-risk-1",
            "decisionId": "phase25-decision-1",
            "orderIntentId": "phase25-intent-1",
            "settingsVersion": "phase25-settings",
            "passed": True,
            "approvedQuantity": 1,
            "evaluatedAt": created_at.isoformat().replace("+00:00", "Z"),
            "expiresAt": (created_at + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        },
    )["recorded"] is True


def _mark_ready(supervisor: RegimeRuntimeSupervisor) -> None:
    supervisor.metrics.supervisor_started = True
    supervisor.metrics.recovery_succeeded = True
    supervisor.metrics.inventory_reconciled = True
    supervisor.metrics.broker_paper_mode_verified = True
    supervisor.metrics.broker_connectivity_ok = True
    supervisor.metrics.latest_reconciliation = {"reconciled": True}
    supervisor.metrics.entry_creation_paused_for_reconciliation = False
    supervisor.metrics.persistence_available = True
    supervisor.metrics.component_health["market_event_publisher"]["status"] = "healthy"
    supervisor.metrics.component_health["database"]["status"] = "healthy"
    supervisor.metrics.component_health["paper_broker"]["status"] = "healthy"
    supervisor.metrics.component_health["broker_connectivity"]["status"] = "healthy"


def _fresh_account(identity: dict[str, str]) -> dict:
    observed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
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
        "observedAt": observed_at,
    }


class _RegularSession:
    status = "midday"


def _record_stage_evidence(repository: RegimeRepository, identity: dict[str, str]) -> None:
    payload = {
        "backendEvidenceSource": REGIME_OPERATIONAL_ROLLOUT_EVIDENCE_SOURCE,
        "evidenceId": f"phase25-evidence-{uuid4().hex}",
        "recordedAt": NOW.isoformat().replace("+00:00", "Z"),
        "persistedEvidenceIds": LIMITED_PAPER_PROMOTION_EVIDENCE,
    }
    payload.update({requirement: True for requirement in LIMITED_PAPER_PROMOTION_EVIDENCE})
    assert repository.record_regime_rollout_promotion_evidence(identity, payload)["recorded"] is True


def _activate_stage(repository: RegimeRepository, identity: dict[str, str], stage: str) -> None:
    result = activate_operational_rollout_stage(
        _Store(repository, identity),
        stage,
        actor="phase25-test",
        reason=f"activate {stage}",
        evidence=repository.read_regime_rollout_promotion_evidence(identity),
        activated_at=NOW,
    )
    assert result["activated"] is True


class _Store:
    def __init__(self, repository: RegimeRepository, identity: dict[str, str]) -> None:
        self.repository = repository
        self.identity = identity

    def read_snapshot(self, key: str) -> dict:
        snapshot = self.repository.read_runtime_snapshot(self.identity, key)
        if snapshot is None:
            raise KeyError(key)
        return snapshot

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.repository.write_runtime_snapshot(self.identity, key, dict(snapshot))


class _FakePaperBroker:
    broker_kind = "regime_alpaca_paper"
    account_type = "paper"
    paper_only = True
    live_trading_enabled = False
    credentials_verified = True
    account_matches_configured_identity = True
    account_allowed_to_trade = True
    market_data_credentials_configured = True

    def __init__(self) -> None:
        self.submit_count = 0

    def verify_paper_account(self) -> bool:
        return True

    def paper_trading_configuration(self) -> dict:
        return {
            "verified": True,
            "paperOnly": True,
            "liveTradingEnabled": False,
            "accountType": "paper",
            "accountMatchesConfiguredIdentity": True,
            "accountAllowedToTrade": True,
            "credentialsVerified": True,
            "marketDataCredentialsConfigured": True,
            "reasonCodes": ["regime.alpaca_paper.account_verified"],
        }

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        self.submit_count += 1
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=f"broker-{intent.clientOrderId}",
            status="ACCEPTED",
            acceptedAt=NOW,
        )

    def refresh_order(self, client_order_id: str):
        return None

    def cancel_order(self, client_order_id: str) -> bool:
        return True

    def refresh_positions(self) -> list[dict]:
        return []
