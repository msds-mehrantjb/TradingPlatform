from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime.reconciliation import RECOVERABLE_OUTBOX_STATES, run_regime_broker_reconciliation
from backend.app.algorithms.regime.repository import RegimeRepository


NOW = datetime(2026, 7, 23, 15, 30, tzinfo=UTC)
TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / ".pytest_regime_phase15"


def test_phase15_restart_recovery_classifies_every_order_state() -> None:
    repository, identity = _repository()
    states = [
        "created",
        "risk_approved",
        "queued",
        "retry_scheduled",
        "submitting",
        "acknowledged",
        "partially_filled",
        "reconciliation_required",
        "filled",
        "cancelled",
        "rejected",
        "expired",
        "dead_letter",
    ]
    for index, state in enumerate(states, start=1):
        _insert_intent(repository, identity, suffix=str(index), status=state)

    recovered = repository.recover_unfinished_outbox_records(identity)
    reconciliation = run_regime_broker_reconciliation(repository=repository, identity=identity, broker=Phase15Broker(), evaluated_at=NOW, trigger="startup")
    recovered_intents = {item["orderIntentId"] for item in reconciliation["deterministicRecoveries"]}

    assert set(recovered["orderIntentIds"]) == {f"regime-intent-{index}" for index, state in enumerate(states, start=1) if state in RECOVERABLE_OUTBOX_STATES}
    assert recovered_intents == set(recovered["orderIntentIds"])
    assert reconciliation["riskReducingExitsAllowed"] is True


def test_phase15_ambiguous_submission_recovers_from_matching_broker_open_order() -> None:
    repository, identity = _repository()
    _insert_intent(repository, identity, status="submitting", broker_client_order_id="regime-client-1")
    broker = Phase15Broker(open_orders=[{"clientOrderId": "regime-client-1", "brokerOrderId": "broker-1", "status": "accepted", "algorithmId": "regime"}])

    reconciliation = run_regime_broker_reconciliation(repository=repository, identity=identity, broker=broker, evaluated_at=NOW, trigger="ambiguous_submission")

    outbox = repository.read_execution_outbox_record(identity, "regime-intent-1")
    assert reconciliation["reconciled"] is True
    assert outbox["processingStatus"] == "acknowledged"
    assert repository.table_counts()["regime_orders"] == 1
    assert any(item["recoveryAction"] == "broker_order_status_recovered" for item in reconciliation["deterministicRecoveries"])


def test_phase15_matching_broker_fill_recovers_inventory_without_duplicate_submission() -> None:
    repository, identity = _repository()
    _insert_intent(repository, identity, quantity=7, status="reconciliation_required", broker_client_order_id="regime-client-1")
    broker = Phase15Broker(
        fills=[
            {
                "clientOrderId": "regime-client-1",
                "brokerOrderId": "broker-1",
                "fillId": "broker-fill-1",
                "algorithmId": "regime",
                "side": "Buy",
                "quantity": 7,
                "price": 100.02,
                "timestamp": NOW.isoformat().replace("+00:00", "Z"),
            }
        ]
    )

    reconciliation = run_regime_broker_reconciliation(repository=repository, identity=identity, broker=broker, evaluated_at=NOW, trigger="broker_network_error")

    snapshot = repository.current_inventory_snapshot(identity)
    outbox = repository.read_execution_outbox_record(identity, "regime-intent-1")
    assert reconciliation["reconciled"] is True
    assert snapshot["quantity"] == 7
    assert outbox["processingStatus"] == "filled"
    assert repository.latest_open_regime_positions(identity)[0]["filledQuantity"] == 7


def test_phase15_unattributed_broker_position_blocks_entries_and_requires_manual_review() -> None:
    repository, identity = _repository()
    broker = Phase15Broker(positions=[{"symbol": "SPY", "quantity": 5, "averageEntryPrice": 100.0}])

    reconciliation = run_regime_broker_reconciliation(repository=repository, identity=identity, broker=broker, evaluated_at=NOW, trigger="periodic")

    assert reconciliation["reconciled"] is False
    assert reconciliation["blockNewEntries"] is True
    assert reconciliation["manualReviewRequired"] is True
    assert repository.current_inventory_snapshot(identity)["quantity"] == 0
    assert repository.table_counts()["regime_runtime_alerts"] == 1
    assert any("unattributed_broker_position" in item for item in reconciliation["discrepancies"])


def test_phase15_order_update_gap_blocks_new_entries_but_leaves_exits_allowed() -> None:
    repository, identity = _repository()
    _insert_intent(repository, identity, status="acknowledged", broker_client_order_id="regime-client-1")
    broker = Phase15Broker(open_orders=[], fills=[])

    reconciliation = run_regime_broker_reconciliation(repository=repository, identity=identity, broker=broker, evaluated_at=NOW, trigger="order_update_gap")

    assert reconciliation["reconciled"] is False
    assert reconciliation["blockNewEntries"] is True
    assert reconciliation["riskReducingExitsAllowed"] is True
    assert "regime.reconciliation.broker_order_update_gap:regime-intent-1" in reconciliation["discrepancies"]


def _repository() -> tuple[RegimeRepository, dict[str, str]]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = TEST_TMP_ROOT / f"{uuid4().hex}.sqlite3"
    repository = RegimeRepository(f"sqlite:///{path}")
    identity = {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-exec",
        "accountId": "paper-account",
        "runtimeMode": "paper",
        "symbol": "SPY",
    }
    return repository, identity


def _insert_intent(
    repository: RegimeRepository,
    identity: dict[str, str],
    *,
    suffix: str = "1",
    quantity: int = 7,
    status: str = "created",
    broker_client_order_id: str | None = None,
) -> None:
    order_intent_id = f"regime-intent-{suffix}"
    decision_id = f"regime-decision-{suffix}"
    intent = {
        **identity,
        "decisionId": decision_id,
        "orderIntentId": order_intent_id,
        "algorithmVersion": "regime_algorithm_v3_backend_authoritative",
        "settingsVersion": "regime-settings-v1",
        "profileVersion": "regime-profile-v1",
        "symbol": "SPY",
        "side": "Buy",
        "positionEffect": "enter_long",
        "quantity": quantity,
        "entryPrice": 100.0,
        "limitPrice": 100.0,
        "stopPrice": 99.0,
        "targetPrice": 102.0,
        "riskDollars": 100.0,
        "settingsSnapshot": {
            "settingsVersion": "regime-settings-v1",
            "profileVersion": "regime-profile-v1",
            "execution": {"orderTimeToLiveSeconds": 300, "orderType": "limit", "maximumCancelReplaceAttempts": 1},
        },
        "dataManifestHash": f"manifest-{suffix}",
        "dataTimestamp": (NOW + timedelta(seconds=int(suffix))).isoformat().replace("+00:00", "Z") if suffix.isdigit() else NOW.isoformat().replace("+00:00", "Z"),
        "createdAt": (NOW + timedelta(seconds=int(suffix))).isoformat().replace("+00:00", "Z") if suffix.isdigit() else NOW.isoformat().replace("+00:00", "Z"),
    }
    inserted = repository.insert_order_intent(intent)
    assert inserted["inserted"] is True
    if status != "created" or broker_client_order_id:
        repository.update_execution_outbox_status(
            identity,
            order_intent_id,
            status=status,
            payload={
                **intent,
                "brokerClientOrderId": broker_client_order_id,
                "reasonCodes": [f"test.status.{status}"],
            },
        )


class Phase15Broker:
    def __init__(
        self,
        *,
        open_orders: list[dict] | None = None,
        fills: list[dict] | None = None,
        positions: list[dict] | None = None,
    ) -> None:
        self._open_orders = list(open_orders or [])
        self._fills = list(fills or [])
        self._positions = list(positions or [])

    def refresh_open_orders(self) -> list[dict]:
        return list(self._open_orders)

    def refresh_fills(self) -> list[dict]:
        return list(self._fills)

    def refresh_positions(self) -> list[dict]:
        return list(self._positions)
