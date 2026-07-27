from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime.execution_gateway import (
    REGIME_EXECUTION_OUTBOX_STATUSES,
    RegimePaperGatewayStore,
    cancel_expired_regime_outbox_orders,
    process_regime_execution_outbox_once,
    submit_regime_outbox_record,
)
from backend.app.algorithms.regime.position_manager import RegimePositionManager
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisor, RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderGateway


NOW = datetime(2026, 7, 23, 15, 30, tzinfo=UTC)
TEST_TMP_ROOT = Path(__file__).resolve().parents[2] / ".pytest_regime_step7"


def test_step7_outbox_status_contract_and_idempotency_key_are_durable() -> None:
    required = {
        "pending",
        "risk_reserved",
        "submitting",
        "submitted",
        "acknowledged",
        "partially_filled",
        "filled",
        "cancel_requested",
        "cancelled",
        "rejected",
        "expired",
        "reconciliation_required",
    }
    repository, identity, _ = _repository()

    _insert_intent(repository, identity)
    outbox = repository.read_execution_outbox_record(identity, "regime-intent-1")

    assert required.issubset(set(REGIME_EXECUTION_OUTBOX_STATUSES))
    assert outbox["idempotencyKey"].startswith("regime-execution-idempotency-")


def test_step7_acceptance_records_risk_reserved_submit_fill_and_restart_position_restore() -> None:
    repository, identity, path = _repository()
    _insert_intent(repository, identity)
    broker = FakeRegimePaperBroker(fill_status="FILLED", filled_quantity=7)
    gateway = _gateway(repository, identity, broker)

    result = process_regime_execution_outbox_once(repository=repository, identity=identity, paper_gateway=gateway, evaluated_at=NOW)
    statuses = [record["processingStatus"] for record in repository.read_owned_records("regime_execution_outbox", identity)]
    restored_positions = RegimePositionManager(RegimeRepository(f"sqlite:///{path}")).restore_open_positions(identity)

    assert result is not None
    assert result.status == "filled"
    assert result.submitted is True
    assert broker.submit_count == 1
    assert "submitting" in statuses
    assert "risk_reserved" in statuses
    assert "submitted" in statuses
    assert "filled" in statuses
    assert repository.table_counts()["regime_orders"] == 1
    assert repository.table_counts()["regime_fills"] == 1
    assert restored_positions[0]["positionId"].startswith("regime-position-SPY-regime-intent-1")
    assert restored_positions[0]["filledQuantity"] == 7


def test_step7_rejection_releases_global_risk_reservation_without_fill() -> None:
    repository, identity, _ = _repository()
    _insert_intent(repository, identity)
    broker = FakeRegimePaperBroker(ack_status="REJECTED", fill_status=None)
    gateway = _gateway(repository, identity, broker)

    result = process_regime_execution_outbox_once(repository=repository, identity=identity, paper_gateway=gateway, evaluated_at=NOW)

    assert result.status == "rejected"
    assert broker.submit_count == 1
    assert gateway.global_risk_manager.reservations.all()[0].status == "released"
    assert repository.table_counts()["regime_orders"] == 1
    assert repository.table_counts()["regime_fills"] == 0


def test_step7_partial_fill_restart_does_not_create_second_economic_order() -> None:
    repository, identity, path = _repository()
    _insert_intent(repository, identity, quantity=10)
    broker = FakeRegimePaperBroker(fill_status="PARTIALLY_FILLED", filled_quantity=4)
    gateway = _gateway(repository, identity, broker)
    first = process_regime_execution_outbox_once(repository=repository, identity=identity, paper_gateway=gateway, evaluated_at=NOW)
    restarted_repository = RegimeRepository(f"sqlite:///{path}")
    restarted_broker = FakeRegimePaperBroker(fill_status="PARTIALLY_FILLED", filled_quantity=4)
    restarted_gateway = _gateway(restarted_repository, identity, restarted_broker)
    latest = restarted_repository.read_execution_outbox_record(identity, "regime-intent-1")

    duplicate = submit_regime_outbox_record(repository=restarted_repository, identity=identity, paper_gateway=restarted_gateway, outbox_record=latest, evaluated_at=NOW + timedelta(seconds=5))

    assert first.status == "partially_filled"
    assert duplicate.duplicate is True
    assert restarted_broker.submit_count == 0
    assert restarted_repository.table_counts()["regime_positions"] == 1
    assert restarted_repository.latest_open_regime_positions(identity)[0]["filledQuantity"] == 4


def test_step7_delayed_ack_and_duplicate_fill_observation_are_idempotent() -> None:
    repository, identity, _ = _repository()
    _insert_intent(repository, identity)
    broker = FakeRegimePaperBroker(fill_status=None, ack_delay_seconds=3)
    gateway = _gateway(repository, identity, broker)
    ack = process_regime_execution_outbox_once(repository=repository, identity=identity, paper_gateway=gateway, evaluated_at=NOW)
    manager = RegimePositionManager(repository)
    fill = _fill_observation(identity, fill_id="same-fill")

    first = manager.apply_fill_observation(identity, fill)
    duplicate = manager.apply_fill_observation(identity, fill)

    assert ack.status == "acknowledged"
    assert repository.read_execution_outbox_record(identity, "regime-intent-1")["latency"]["submitToAckMs"] == 3000
    assert first["updated"] is True
    assert duplicate["duplicate"] is True
    assert repository.table_counts()["regime_positions"] == 1
    assert repository.table_counts()["regime_trades"] == 1


def test_step7_cancellation_expiry_and_disconnection_are_durable() -> None:
    cancel_repository, cancel_identity, _ = _repository()
    _insert_intent(cancel_repository, cancel_identity)
    cancel_broker = FakeRegimePaperBroker(fill_status=None)
    cancel_gateway = _gateway(cancel_repository, cancel_identity, cancel_broker)
    first = process_regime_execution_outbox_once(repository=cancel_repository, identity=cancel_identity, paper_gateway=cancel_gateway, evaluated_at=NOW)
    cancellations = cancel_expired_regime_outbox_orders(repository=cancel_repository, identity=cancel_identity, paper_gateway=cancel_gateway, evaluated_at=NOW + timedelta(minutes=10))
    statuses = [record["processingStatus"] for record in cancel_repository.read_owned_records("regime_execution_outbox", cancel_identity)]

    disconnect_repository, disconnect_identity, _ = _repository()
    _insert_intent(disconnect_repository, disconnect_identity)
    disconnect_result = process_regime_execution_outbox_once(
        repository=disconnect_repository,
        identity=disconnect_identity,
        paper_gateway=_gateway(disconnect_repository, disconnect_identity, FakeRegimePaperBroker(raise_on_submit=True)),
        evaluated_at=NOW,
    )

    assert first.status == "acknowledged"
    assert cancellations[0].status == "cancelled"
    assert cancel_broker.cancel_count == 1
    assert "cancel_requested" in statuses
    assert "cancelled" in statuses
    assert disconnect_result.status == "reconciliation_required"
    assert disconnect_repository.table_counts()["regime_fills"] == 0


def test_step7_supervisor_blocks_new_entry_submission_until_recovery_and_reconciliation_are_healthy() -> None:
    repository, identity, _ = _repository(instance_id="regime-default", account_id="default")
    _insert_intent(repository, identity)
    broker = FakeRegimePaperBroker(fill_status="FILLED")
    supervisor = RegimeRuntimeSupervisor(
        service=RegimeApplicationService(repository),
        config=RegimeRuntimeSupervisorConfig(default_runtime_mode="paper", maintenance_interval_seconds=0.01),
        paper_gateway=_gateway(repository, identity, broker),
    )

    blocked = supervisor.process_execution_outbox_once()

    assert blocked["processed"] is False
    assert "regime.execution.recovery_or_reconciliation_unhealthy" in blocked["reasonCodes"]
    assert broker.submit_count == 0
    assert repository.read_execution_outbox_record(identity, "regime-intent-1")["processingStatus"] == "pending"


def test_step7_pause_keeps_existing_position_protected_and_end_of_day_flatten_available() -> None:
    repository, identity, _ = _repository()
    manager = RegimePositionManager(repository)
    position = manager.apply_fill_observation(identity, _fill_observation(identity))["position"]

    held = manager.evaluate_position(
        identity,
        position,
        candle=_candle(low=99.8, high=100.8, close=100.4),
        settings_snapshot=_settings(),
        confirmed_regime="strong_uptrend",
        entry_paused=True,
    )
    flattened = manager.evaluate_position(
        identity,
        held["position"],
        candle=_candle(timestamp="2026-07-23T19:55:00Z", low=100.0, high=100.6, close=100.2),
        settings_snapshot=_settings(),
        confirmed_regime="strong_uptrend",
        entry_paused=True,
    )

    assert held["action"] == "hold"
    assert held["position"]["entryPausedWhileProtected"] is True
    assert flattened["exitAction"]["reason"] == "end_of_day_flatten"


def test_step7_live_runtime_is_rejected_before_broker_submission() -> None:
    repository, identity, _ = _repository()
    _insert_intent(repository, identity)
    broker = FakeRegimePaperBroker(fill_status="FILLED")
    latest = repository.read_execution_outbox_record(identity, "regime-intent-1")

    result = submit_regime_outbox_record(
        repository=repository,
        identity={**identity, "runtimeMode": "live"},
        paper_gateway=_gateway(repository, identity, broker),
        outbox_record={**latest, "runtimeMode": "live"},
        evaluated_at=NOW,
    )

    assert result.status == "rejected"
    assert broker.submit_count == 0
    assert "regime.execution.live_mode_rejected" in result.reason_codes


def _repository(*, runtime_mode: str = "paper", instance_id: str = "regime-exec", account_id: str = "paper-account") -> tuple[RegimeRepository, dict[str, str], Path]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = TEST_TMP_ROOT / f"{uuid4().hex}.sqlite3"
    identity = {
        "algorithmId": "regime",
        "algorithmInstanceId": instance_id,
        "accountId": account_id,
        "runtimeMode": runtime_mode,
        "symbol": "SPY",
    }
    return RegimeRepository(f"sqlite:///{path}"), identity, path


def _gateway(repository: RegimeRepository, identity: dict[str, str], broker: "FakeRegimePaperBroker") -> PaperOrderGateway:
    return PaperOrderGateway(broker, RegimePaperGatewayStore(repository, identity))


def _insert_intent(repository: RegimeRepository, identity: dict[str, str], *, quantity: int = 7) -> None:
    inserted = repository.insert_order_intent(
        {
            **identity,
            "decisionId": "regime-decision-1",
            "orderIntentId": "regime-intent-1",
            "algorithmVersion": "regime_algorithm_v3_backend_authoritative",
            "settingsVersion": "regime-settings-v1",
            "profileVersion": "regime-profile-v1",
            "symbol": "SPY",
            "side": "Buy",
            "positionEffect": "enter_long",
            "quantity": quantity,
            "entryPrice": 100.0,
            "stopPrice": 99.0,
            "targetPrice": 102.0,
            "riskDollars": 100.0,
            "settingsSnapshot": {
                "settingsVersion": "regime-settings-v1",
                "profileVersion": "regime-profile-v1",
                "execution": {"orderTimeToLiveSeconds": 300, "orderType": "limit", "maximumCancelReplaceAttempts": 1},
            },
            "dataManifestHash": "manifest-1",
            "createdAt": NOW.isoformat().replace("+00:00", "Z"),
        }
    )
    assert inserted["inserted"] is True
    risk = repository.record_local_risk_result(
        identity,
        {
            **identity,
            "localRiskResultId": "regime-local-risk-1",
            "decisionId": "regime-decision-1",
            "orderIntentId": "regime-intent-1",
            "settingsVersion": "regime-settings-v1",
            "passed": True,
            "requestedQuantity": quantity,
            "approvedQuantity": quantity,
            "estimatedGrossEdge": 40.0,
            "estimatedTransactionCost": 5.0,
            "estimatedNetEdge": 35.0,
            "blockers": [],
            "reductions": [],
            "evaluatedAt": NOW.isoformat().replace("+00:00", "Z"),
            "expiresAt": (NOW + timedelta(days=365)).isoformat().replace("+00:00", "Z"),
        },
    )
    assert risk["recorded"] is True


def _fill_observation(identity: dict[str, str], *, fill_id: str = "fill-regime-intent-1") -> dict:
    return {
        **identity,
        "algorithmId": "regime",
        "decisionId": "regime-decision-1",
        "orderIntentId": "regime-intent-1",
        "fillId": fill_id,
        "brokerOrderId": "broker-regime-intent-1",
        "symbol": "SPY",
        "side": "Buy",
        "filledQuantity": 7,
        "submittedQuantity": 7,
        "averageFillPrice": 100.0,
        "stopPrice": 99.0,
        "targetPrice": 102.0,
        "filledAt": NOW.isoformat().replace("+00:00", "Z"),
        "settingsVersion": "regime-settings-v1",
    }


def _candle(*, timestamp: str = "2026-07-23T15:31:00Z", low: float = 99.5, high: float = 100.5, close: float = 100.0) -> dict:
    return {"timestamp": timestamp, "open": 100.0, "high": high, "low": low, "close": close, "volume": 100_000}


def _settings() -> dict:
    return {"settingsVersion": "regime-settings-v1", "maximumHoldingBars": 20, "flattenTimeEt": "15:55", "exit_policy": {"timeStopBars": 0}}


class FakeRegimePaperBroker:
    def __init__(
        self,
        *,
        ack_status: str = "ACCEPTED",
        fill_status: str | None = "FILLED",
        filled_quantity: int = 7,
        ack_delay_seconds: int = 0,
        raise_on_submit: bool = False,
    ) -> None:
        self.ack_status = ack_status
        self.fill_status = fill_status
        self.filled_quantity = filled_quantity
        self.ack_delay_seconds = ack_delay_seconds
        self.raise_on_submit = raise_on_submit
        self.submit_count = 0
        self.cancel_count = 0

    def verify_paper_account(self) -> bool:
        return True

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        self.submit_count += 1
        if self.raise_on_submit:
            raise ConnectionError("paper broker disconnected")
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=f"broker-{intent.clientOrderId}",
            status=self.ack_status,
            acceptedAt=NOW + timedelta(seconds=self.ack_delay_seconds) if self.ack_status != "REJECTED" else None,
            rejectedReason="paper rejected" if self.ack_status == "REJECTED" else None,
        )

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        if self.ack_status == "REJECTED" or self.fill_status is None:
            return None
        return PaperGatewayFill(
            clientOrderId=client_order_id,
            algorithmId="regime",
            orderIntentId="regime-intent-1",
            symbol="SPY",
            side=Signal.BUY,
            filledQuantity=self.filled_quantity,
            averageFillPrice=100.01,
            status=self.fill_status,
            filledAt=NOW,
        )

    def cancel_order(self, client_order_id: str) -> bool:
        self.cancel_count += 1
        return True

    def refresh_positions(self) -> list[dict]:
        return []
