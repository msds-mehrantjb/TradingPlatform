from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime.execution_gateway import (
    RegimePaperGatewayStore,
    cancel_expired_regime_outbox_orders,
    process_regime_execution_outbox_once,
    submit_regime_outbox_record,
)
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderGateway
from backend.app.domain.models import Signal


NOW = datetime(2026, 7, 23, 15, 30, tzinfo=UTC)
TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / ".pytest_regime_execution"


def test_accepted_order_reserves_submits_reconciles_into_regime_inventory() -> None:
    repository, identity = _repository()
    _insert_intent(repository, identity)
    broker = FakeRegimePaperBroker(fill_status="FILLED", filled_quantity=7)
    gateway = _gateway(repository, identity, broker)

    result = process_regime_execution_outbox_once(repository=repository, identity=identity, paper_gateway=gateway, evaluated_at=NOW)

    assert result is not None
    assert result.status == "filled"
    assert result.submitted is True
    assert broker.submit_count == 1
    assert broker.last_intent.persistedBeforeSubmission is True
    outbox = repository.read_execution_outbox_record(identity, "regime-intent-1")
    assert outbox["processingStatus"] == "filled"
    assert outbox["brokerClientOrderId"] == broker.last_intent.clientOrderId
    assert outbox["orderReplacementPolicy"] == "cancel_stale_unfilled_orders_replace_requires_new_intent"
    assert outbox["stateMachine"]["version"] == "regime_execution_outbox_state_machine_v2"
    assert outbox["reservationId"]
    assert outbox["immutableProposal"]["decisionId"] == "regime-decision-1"
    assert outbox["immutableProposal"]["stopPrice"] == 99.0
    assert outbox["immutableProposal"]["targetPrice"] == 102.0
    assert outbox["globalApplication"]["globallyAllowedQuantity"] <= outbox["globalApplication"]["proposedQuantity"]
    assert "global_gate.stop_formula_not_modified" in outbox["globalApplication"]["immutableChecks"]
    assert outbox["latency"]["decisionToRiskMs"] >= 0
    assert outbox["latency"]["riskToSubmitMs"] >= 0
    assert outbox["latency"]["submitToAckMs"] >= 0
    assert repository.table_counts()["regime_orders"] == 1
    assert repository.table_counts()["regime_fills"] == 1
    assert repository.table_counts()["regime_positions"] == 1
    assert repository.table_counts()["regime_reconciliation_events"] == 1
    shared_fill = gateway.store.read_snapshot(f"paper_order_gateway.fill.{result.gateway_result['clientOrderId']}")
    assert shared_fill["algorithmId"] == "regime"
    assert shared_fill["orderIntentId"] == "regime-intent-1"


def test_rejected_order_releases_reservation_and_remains_regime_attributed() -> None:
    repository, identity = _repository()
    _insert_intent(repository, identity)
    broker = FakeRegimePaperBroker(ack_status="REJECTED", fill_status=None)
    gateway = _gateway(repository, identity, broker)

    result = process_regime_execution_outbox_once(repository=repository, identity=identity, paper_gateway=gateway, evaluated_at=NOW)

    assert result.status == "rejected"
    assert result.submitted is False
    assert broker.submit_count == 1
    outbox = repository.read_execution_outbox_record(identity, "regime-intent-1")
    assert outbox["processingStatus"] == "rejected"
    assert outbox["globalRiskDecision"]["reservationId"]
    reservations = gateway.global_risk_manager.reservations.all()
    assert reservations[0].status == "released"
    assert repository.table_counts()["regime_orders"] == 1
    assert repository.table_counts()["regime_fills"] == 0


def test_partial_fill_records_partial_status_and_owned_position() -> None:
    repository, identity = _repository()
    _insert_intent(repository, identity, quantity=10)
    broker = FakeRegimePaperBroker(fill_status="PARTIALLY_FILLED", filled_quantity=4)
    gateway = _gateway(repository, identity, broker)

    result = process_regime_execution_outbox_once(repository=repository, identity=identity, paper_gateway=gateway, evaluated_at=NOW)

    assert result.status == "partially_filled"
    outbox = repository.read_execution_outbox_record(identity, "regime-intent-1")
    assert outbox["processingStatus"] == "partially_filled"
    positions = repository.read_owned_records("regime_positions", identity)
    assert positions[0]["quantity"] == 4


def test_delayed_ack_records_submit_to_ack_latency() -> None:
    repository, identity = _repository()
    _insert_intent(repository, identity)
    broker = FakeRegimePaperBroker(fill_status=None, ack_delay_seconds=3)
    gateway = _gateway(repository, identity, broker)

    result = process_regime_execution_outbox_once(repository=repository, identity=identity, paper_gateway=gateway, evaluated_at=NOW)

    assert result.status == "acknowledged"
    outbox = repository.read_execution_outbox_record(identity, "regime-intent-1")
    assert outbox["latency"]["submitToAckMs"] == 3000


def test_stale_order_cancellation_updates_regime_outbox() -> None:
    repository, identity = _repository()
    _insert_intent(repository, identity)
    broker = FakeRegimePaperBroker(fill_status=None)
    gateway = _gateway(repository, identity, broker)
    first = process_regime_execution_outbox_once(repository=repository, identity=identity, paper_gateway=gateway, evaluated_at=NOW)

    cancellations = cancel_expired_regime_outbox_orders(repository=repository, identity=identity, paper_gateway=gateway, evaluated_at=NOW + timedelta(minutes=10))

    assert first.status == "acknowledged"
    assert broker.cancel_count == 1
    assert cancellations[0].status == "cancelled"
    outbox = repository.read_execution_outbox_record(identity, "regime-intent-1")
    assert outbox["processingStatus"] == "cancelled"
    statuses = [record["processingStatus"] for record in repository.read_owned_records("regime_execution_outbox", identity)]
    assert "cancel_pending" in statuses


def test_duplicate_gateway_response_does_not_resubmit_broker_order() -> None:
    repository, identity = _repository()
    _insert_intent(repository, identity)
    broker = FakeRegimePaperBroker(fill_status="FILLED")
    gateway = _gateway(repository, identity, broker)
    first = process_regime_execution_outbox_once(repository=repository, identity=identity, paper_gateway=gateway, evaluated_at=NOW)
    latest = repository.read_execution_outbox_record(identity, "regime-intent-1")

    duplicate = submit_regime_outbox_record(repository=repository, identity=identity, paper_gateway=gateway, outbox_record=latest, evaluated_at=NOW)

    assert first.status == "filled"
    assert duplicate.duplicate is True
    assert broker.submit_count == 1
    assert repository.table_counts()["regime_orders"] == 1
    assert repository.table_counts()["regime_fills"] == 1


def test_connection_interruption_marks_reconciliation_required_without_fill() -> None:
    repository, identity = _repository()
    _insert_intent(repository, identity)
    broker = FakeRegimePaperBroker(raise_on_submit=True)
    gateway = _gateway(repository, identity, broker)

    result = process_regime_execution_outbox_once(repository=repository, identity=identity, paper_gateway=gateway, evaluated_at=NOW)

    assert result.status == "reconciliation_required"
    outbox = repository.read_execution_outbox_record(identity, "regime-intent-1")
    assert outbox["processingStatus"] == "reconciliation_required"
    assert "regime.execution.paper_gateway_connection_interrupted" in outbox["reasonCodes"]
    assert repository.table_counts()["regime_fills"] == 0


def test_live_mode_is_hard_rejected_before_shared_gateway() -> None:
    repository, identity = _repository(runtime_mode="paper")
    _insert_intent(repository, identity)
    broker = FakeRegimePaperBroker()
    gateway = _gateway(repository, identity, broker)
    latest = repository.read_execution_outbox_record(identity, "regime-intent-1")

    result = submit_regime_outbox_record(repository=repository, identity={**identity, "runtimeMode": "live"}, paper_gateway=gateway, outbox_record={**latest, "runtimeMode": "live"}, evaluated_at=NOW)

    assert result.status == "rejected"
    assert broker.submit_count == 0
    assert "regime.execution.live_mode_rejected" in result.reason_codes


def test_market_entry_order_is_rejected_before_broker_submission() -> None:
    repository, identity = _repository()
    _insert_intent(repository, identity, execution={"orderTimeToLiveSeconds": 300, "orderType": "market"})
    broker = FakeRegimePaperBroker()
    gateway = _gateway(repository, identity, broker)

    result = process_regime_execution_outbox_once(repository=repository, identity=identity, paper_gateway=gateway, evaluated_at=NOW)

    assert result.status == "rejected"
    assert broker.submit_count == 0
    assert "regime.execution.market_entry_order_rejected" in result.reason_codes


def test_expired_entry_is_not_submitted_and_moves_to_expired() -> None:
    repository, identity = _repository()
    _insert_intent(repository, identity, created_at=NOW - timedelta(minutes=10), risk_expires_at=NOW - timedelta(minutes=5))
    broker = FakeRegimePaperBroker()
    gateway = _gateway(repository, identity, broker)

    result = process_regime_execution_outbox_once(repository=repository, identity=identity, paper_gateway=gateway, evaluated_at=NOW)

    assert result.status == "expired"
    assert broker.submit_count == 0
    assert "regime.execution.entry_intent_expired_before_submission" in result.reason_codes


def test_live_broker_configuration_is_rejected_before_submission() -> None:
    repository, identity = _repository()
    _insert_intent(repository, identity)
    broker = FakeRegimePaperBroker(base_url="https://api.alpaca.markets/v2", paper_only=False, live_trading_enabled=True, account_type="live")
    gateway = _gateway(repository, identity, broker)

    result = process_regime_execution_outbox_once(repository=repository, identity=identity, paper_gateway=gateway, evaluated_at=NOW)

    assert result.status == "rejected"
    assert broker.submit_count == 0
    assert "regime.execution.paper_broker.live_trading_enabled" in result.reason_codes
    assert "regime.execution.paper_broker.live_base_url_rejected" in result.reason_codes


def test_safe_gateway_failure_uses_bounded_retry_backoff() -> None:
    repository, identity = _repository()
    _insert_intent(repository, identity)
    broker = FakeRegimePaperBroker(raise_safe_before_submit=True)
    gateway = _gateway(repository, identity, broker)

    first = process_regime_execution_outbox_once(repository=repository, identity=identity, paper_gateway=gateway, evaluated_at=NOW)
    waiting = process_regime_execution_outbox_once(repository=repository, identity=identity, paper_gateway=gateway, evaluated_at=NOW + timedelta(seconds=1))

    outbox = repository.read_execution_outbox_record(identity, "regime-intent-1")
    assert first.status == "retry_scheduled"
    assert waiting.duplicate is True
    assert "regime.execution.retry_backoff_wait" in waiting.reason_codes
    assert outbox["retryCount"] == 1
    assert outbox["nextRetryAt"] > NOW.isoformat()
    assert broker.submit_count == 0


def test_decision_core_has_no_direct_strategy_to_broker_call() -> None:
    source = (Path(__file__).resolve().parents[1] / "app" / "algorithms" / "regime" / "stateful_core.py").read_text(encoding="utf-8")
    assert "PaperOrderGateway" not in source
    assert "submit_bracket_order" not in source


def _repository(*, runtime_mode: str = "paper") -> tuple[RegimeRepository, dict[str, str]]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = TEST_TMP_ROOT / f"{uuid4().hex}.sqlite3"
    repository = RegimeRepository(f"sqlite:///{path}")
    identity = {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-exec",
        "accountId": "paper-account",
        "runtimeMode": runtime_mode,
        "symbol": "SPY",
    }
    return repository, identity


def _gateway(repository: RegimeRepository, identity: dict[str, str], broker: "FakeRegimePaperBroker") -> PaperOrderGateway:
    return PaperOrderGateway(broker, RegimePaperGatewayStore(repository, identity))


def _insert_intent(
    repository: RegimeRepository,
    identity: dict[str, str],
    *,
    quantity: int = 7,
    execution: dict | None = None,
    created_at: datetime = NOW,
    risk_expires_at: datetime | None = None,
) -> None:
    result = repository.insert_order_intent(
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
                "execution": execution or {"orderTimeToLiveSeconds": 300, "orderType": "limit", "maximumCancelReplaceAttempts": 1},
            },
            "dataManifestHash": "manifest-1",
            "createdAt": created_at.isoformat().replace("+00:00", "Z"),
        }
    )
    assert result["inserted"] is True
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
            "evaluatedAt": created_at.isoformat().replace("+00:00", "Z"),
            "expiresAt": (risk_expires_at or NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        },
    )
    assert risk["recorded"] is True


class FakeRegimePaperBroker:
    def __init__(
        self,
        *,
        ack_status: str = "ACCEPTED",
        fill_status: str | None = "FILLED",
        filled_quantity: int = 7,
        ack_delay_seconds: int = 0,
        raise_on_submit: bool = False,
        raise_safe_before_submit: bool = False,
        base_url: str = "https://paper-api.alpaca.markets/v2",
        paper_only: bool = True,
        live_trading_enabled: bool = False,
        account_type: str = "paper",
    ) -> None:
        self.ack_status = ack_status
        self.fill_status = fill_status
        self.filled_quantity = filled_quantity
        self.ack_delay_seconds = ack_delay_seconds
        self.raise_on_submit = raise_on_submit
        self.raise_safe_before_submit = raise_safe_before_submit
        self.base_url = base_url
        self.paper_only = paper_only
        self.live_trading_enabled = live_trading_enabled
        self.account_type = account_type
        self.credentials_verified = True
        self.submit_count = 0
        self.cancel_count = 0
        self.last_intent = None

    def verify_paper_account(self) -> bool:
        if self.raise_safe_before_submit:
            exc = ConnectionError("paper broker pre-submit health check failed")
            exc.safe_to_retry = True
            raise exc
        return True

    def paper_trading_configuration(self) -> dict:
        return {
            "baseUrl": self.base_url,
            "paperOnly": self.paper_only,
            "liveTradingEnabled": self.live_trading_enabled,
            "accountType": self.account_type,
            "credentialsVerified": self.credentials_verified,
        }

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        self.submit_count += 1
        self.last_intent = intent
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
