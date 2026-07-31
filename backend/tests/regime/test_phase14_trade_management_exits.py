from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.algorithms.regime.execution_gateway import RegimePaperGatewayStore, process_regime_execution_outbox_once
from backend.app.algorithms.regime.position_manager import RegimePositionManager
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.trade_management import manage_regime_positions_for_completed_bar
from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderGateway


NOW = datetime(2026, 7, 23, 15, 30, tzinfo=UTC)
TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / ".pytest_phase14_trade_management"


def test_partial_fill_is_immediately_protected_with_regime_attribution() -> None:
    repository, identity = _repository()
    _insert_entry_intent(repository, identity, quantity=10)
    broker = Phase14Broker(fill_status="PARTIALLY_FILLED", filled_quantity=4)

    result = process_regime_execution_outbox_once(repository=repository, identity=identity, paper_gateway=_gateway(repository, identity, broker), evaluated_at=NOW)

    position = repository.latest_open_regime_positions(identity)[0]
    assert result.status == "partially_filled"
    assert position["entryState"] == "partially_filled"
    assert position["partialFillProtectionState"] == "protected"
    assert position["protectiveOrderQuantity"] == 4
    assert position["filledQuantityProtected"] is True
    assert position["orderIntentId"] == "regime-intent-phase14"


def test_trailing_stop_tightens_only_when_profile_enabled() -> None:
    repository, identity = _repository()
    manager = RegimePositionManager(repository)
    position = {**manager.apply_fill_observation(identity, _fill(identity, filled_quantity=5))["position"], "targetPrice": 110.0}

    held = manager.evaluate_position(
        identity,
        position,
        candle=_candle(high=102.0, low=100.5, close=101.8),
        settings_snapshot={"settingsVersion": "settings-v1", "trailingExitsEnabled": False},
        confirmed_regime="strong_uptrend",
    )
    trailed = manager.evaluate_position(
        identity,
        held["position"],
        candle=_candle(high=103.0, low=101.5, close=102.5),
        settings_snapshot={"settingsVersion": "settings-v1", "trailingExitsEnabled": True, "exit_policy": {"trailingStopDistance": 1.0}},
        confirmed_regime="strong_uptrend",
    )

    assert held["position"]["stopPrice"] == 99.0
    assert trailed["position"]["stopPrice"] == 102.0
    assert "regime.position.trailing_stop_tightened" in trailed["reasonCodes"]


def test_trade_management_rejects_shared_account_position_without_fill_attribution() -> None:
    repository, identity = _repository()
    repository.record_position_state(
        identity,
        {
            **identity,
            "positionId": "shared-account-position",
            "tradeId": "shared-trade",
            "side": "Long",
            "quantity": 5,
            "filledQuantity": 5,
            "positionStatus": "open",
            "averageFillPrice": 100.0,
        },
    )

    result = manage_regime_positions_for_completed_bar(
        repository=repository,
        identity=identity,
        candle=_candle(low=99.8, high=100.4, close=100.1),
        settings_snapshot={"settingsVersion": "settings-v1"},
        confirmed_regime="strong_uptrend",
        evaluated_at=NOW,
    )

    assert result["blockedPositions"] == 1
    assert result["exitIntentsCreated"] == 0
    assert "regime.trade_management.shared_account_position_rejected" in result["reasonCodes"]


def test_exit_fill_reduces_owned_position_and_never_opens_reverse() -> None:
    repository, identity = _repository()
    manager = RegimePositionManager(repository)
    opened = manager.apply_fill_observation(identity, _fill(identity, filled_quantity=5))["position"]

    closed = manager.apply_fill_observation(
        identity,
        {
            **_fill(identity, fill_id="exit-fill-1", filled_quantity=5, side="Sell"),
            "orderIntentId": "regime-exit-phase14",
            "positionEffect": "exit_long",
            "positionId": opened["positionId"],
            "tradeId": opened["tradeId"],
            "averageFillPrice": 101.0,
        },
    )["position"]

    assert closed["positionStatus"] == "closed"
    assert closed["quantity"] == 0
    assert repository.latest_open_regime_positions(identity) == []
    with pytest.raises(ValueError):
        manager.apply_fill_observation(identity, {**_fill(identity, fill_id="orphan-exit", side="Sell"), "orderIntentId": "orphan-exit", "positionEffect": "exit_long"})


def _repository() -> tuple[RegimeRepository, dict[str, str]]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    repository = RegimeRepository(f"sqlite:///{TEST_TMP_ROOT / f'{uuid4().hex}.sqlite3'}")
    identity = {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-phase14",
        "accountId": "paper-account",
        "runtimeMode": "paper",
        "symbol": "SPY",
    }
    return repository, identity


def _gateway(repository: RegimeRepository, identity: dict[str, str], broker: "Phase14Broker") -> PaperOrderGateway:
    return PaperOrderGateway(broker, RegimePaperGatewayStore(repository, identity))


def _insert_entry_intent(repository: RegimeRepository, identity: dict[str, str], *, quantity: int) -> None:
    inserted = repository.insert_order_intent(
        {
            **identity,
            "decisionId": "regime-decision-phase14",
            "orderIntentId": "regime-intent-phase14",
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
            "settingsSnapshot": {"settingsVersion": "regime-settings-v1", "profileVersion": "regime-profile-v1", "execution": {"orderType": "limit"}},
            "createdAt": NOW.isoformat().replace("+00:00", "Z"),
        }
    )
    assert inserted["inserted"] is True
    risk = repository.record_local_risk_result(
        identity,
        {
            **identity,
            "localRiskResultId": "regime-local-risk-phase14",
            "decisionId": "regime-decision-phase14",
            "orderIntentId": "regime-intent-phase14",
            "settingsVersion": "regime-settings-v1",
            "passed": True,
            "requestedQuantity": quantity,
            "approvedQuantity": quantity,
            "estimatedGrossEdge": 10.0,
            "estimatedTransactionCost": 1.0,
            "estimatedNetEdge": 9.0,
            "blockers": [],
            "reductions": [],
            "evaluatedAt": NOW.isoformat().replace("+00:00", "Z"),
            "expiresAt": "2027-07-23T15:30:00Z",
        },
    )
    assert risk["recorded"] is True


def _fill(identity: dict[str, str], *, fill_id: str = "fill-phase14", filled_quantity: int = 5, side: str = "Buy") -> dict:
    return {
        **identity,
        "algorithmId": "regime",
        "decisionId": "regime-decision-phase14",
        "orderIntentId": "regime-intent-phase14",
        "fillId": fill_id,
        "brokerOrderId": "broker-phase14",
        "symbol": "SPY",
        "side": side,
        "filledQuantity": filled_quantity,
        "submittedQuantity": filled_quantity,
        "averageFillPrice": 100.0,
        "stopPrice": 99.0,
        "targetPrice": 102.0,
        "filledAt": NOW.isoformat().replace("+00:00", "Z"),
        "settingsVersion": "regime-settings-v1",
    }


def _candle(*, high: float = 100.5, low: float = 99.5, close: float = 100.0) -> dict:
    return {"timestamp": "2026-07-23T15:31:00Z", "open": 100.0, "high": high, "low": low, "close": close, "volume": 100_000}


class Phase14Broker:
    def __init__(self, *, fill_status: str | None = "FILLED", filled_quantity: int = 5) -> None:
        self.fill_status = fill_status
        self.filled_quantity = filled_quantity
        self.submit_count = 0

    def verify_paper_account(self) -> bool:
        return True

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        self.submit_count += 1
        return PaperGatewayBrokerAck(clientOrderId=intent.clientOrderId, brokerOrderId=f"broker-{intent.clientOrderId}", status="ACCEPTED", acceptedAt=NOW)

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        if self.fill_status is None:
            return None
        return PaperGatewayFill(
            clientOrderId=client_order_id,
            algorithmId="regime",
            orderIntentId="regime-intent-phase14",
            symbol="SPY",
            side=Signal.BUY,
            filledQuantity=self.filled_quantity,
            averageFillPrice=100.01,
            status=self.fill_status,
            filledAt=NOW,
        )

    def cancel_order(self, client_order_id: str) -> bool:
        return True

    def refresh_positions(self) -> list[dict]:
        return []
