from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.algorithms.regime.execution_gateway import (
    RegimePaperGatewayStore,
    process_regime_execution_outbox_once,
    submit_regime_outbox_record,
)
from backend.app.algorithms.regime.position_manager import RegimePositionManager
from backend.app.algorithms.regime.reconciliation import run_regime_broker_reconciliation
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime_events import RegimeFinalisedBarEvent
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisor, RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.algorithms.regime.trade_management import manage_regime_positions_for_completed_bar
from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderGateway


NOW = datetime(2026, 7, 23, 15, 30, tzinfo=UTC)
TEST_TMP_ROOT = Path(__file__).resolve().parents[2] / ".pytest_regime_phase34_acceptance"


def test_phase34_required_inventory_isolation_scenario_blocks_entries_without_reassigning_ownership() -> None:
    repository, identity, _ = _repository()
    weighted_voting_position = {"algorithmId": "weighted_voting", "symbol": "SPY", "quantity": 60}

    RegimePositionManager(repository).apply_fill_observation(
        identity,
        _fill(identity, order_intent_id="regime-entry-25", fill_id="regime-fill-25", filled_quantity=25),
        settings_snapshot=_settings(),
    )

    regime_inventory = repository.current_inventory_snapshot(identity)
    assert regime_inventory["quantity"] == 25

    reconciliation = run_regime_broker_reconciliation(
        repository=repository,
        identity=identity,
        broker_positions=[{"symbol": "SPY", "quantity": 85, "averageFillPrice": 100.0, "source": "aggregate_account_position"}],
        broker_open_orders=[],
        broker_fills=[],
        evaluated_at=NOW,
        trigger="phase34_aggregate_position_check",
    )

    assert reconciliation["blockNewEntries"] is True
    assert any("unattributed_broker_position" in item for item in reconciliation["discrepancies"])
    assert repository.current_inventory_snapshot(identity)["quantity"] == 25
    assert weighted_voting_position == {"algorithmId": "weighted_voting", "symbol": "SPY", "quantity": 60}

    flattened = manage_regime_positions_for_completed_bar(
        repository=repository,
        identity=identity,
        candle=_candle(),
        settings_snapshot=_settings(),
        confirmed_regime="risk_off",
        global_emergency_flatten=True,
        evaluated_at=NOW,
    )

    assert flattened["exitIntentsCreated"] == 1
    assert flattened["exitIntents"][0]["quantity"] == 25
    assert flattened["exitIntents"][0]["ownedPositionQuantity"] == 25

    with pytest.raises(ValueError, match="cross-algorithm|rejects"):
        RegimePositionManager(repository).apply_fill_observation(
            identity,
            {**_fill(identity, order_intent_id="weighted-entry", fill_id="weighted-fill", filled_quantity=60), "algorithmId": "weighted_voting"},
            settings_snapshot=_settings(),
        )


def test_phase34_inventory_namespaces_are_independent_for_instance_account_mode_and_symbol() -> None:
    repository, identity, _ = _repository(instance_id="regime-paper-a", account_id="paper-a")
    variants = [
        (identity, 1),
        ({**identity, "algorithmInstanceId": "regime-paper-b"}, 2),
        ({**identity, "accountId": "paper-b"}, 3),
        ({**identity, "runtimeMode": "shadow"}, 4),
        ({**identity, "symbol": "QQQ"}, 5),
    ]

    for variant_identity, quantity in variants:
        RegimePositionManager(repository).apply_fill_observation(
            variant_identity,
            _fill(
                variant_identity,
                order_intent_id=f"intent-{variant_identity['algorithmInstanceId']}-{variant_identity['accountId']}-{variant_identity['runtimeMode']}-{variant_identity['symbol']}",
                fill_id=f"fill-{quantity}",
                filled_quantity=quantity,
            ),
            settings_snapshot=_settings(),
        )

    assert repository.current_inventory_snapshot(identity)["quantity"] == 1
    assert repository.current_inventory_snapshot({**identity, "algorithmInstanceId": "regime-paper-b"})["quantity"] == 2
    assert repository.current_inventory_snapshot({**identity, "accountId": "paper-b"})["quantity"] == 3
    assert repository.current_inventory_snapshot({**identity, "runtimeMode": "shadow"})["quantity"] == 4
    assert repository.current_inventory_snapshot({**identity, "symbol": "QQQ"})["quantity"] == 5


def test_phase34_execution_path_is_duplicate_safe_and_updates_inventory_from_partial_and_final_fills() -> None:
    repository, identity, _ = _repository()
    _insert_intent(repository, identity, quantity=10)
    broker = AcceptancePaperBroker(fill_status="PARTIALLY_FILLED", filled_quantity=4)
    gateway = _gateway(repository, identity, broker)

    first = process_regime_execution_outbox_once(
        repository=repository,
        identity=identity,
        paper_gateway=gateway,
        evaluated_at=NOW,
    )

    assert first is not None
    assert first.status == "partially_filled"
    assert first.submitted is True
    assert broker.submit_count == 1
    assert repository.current_inventory_snapshot(identity)["quantity"] == 4

    latest = repository.read_execution_outbox_record(identity, "regime-intent-1")
    client_order_id = str(latest["brokerClientOrderId"])
    assert client_order_id.startswith("paper-")

    duplicate = submit_regime_outbox_record(
        repository=repository,
        identity=identity,
        paper_gateway=_gateway(repository, identity, AcceptancePaperBroker(fill_status="FILLED", filled_quantity=10)),
        outbox_record=latest,
        evaluated_at=NOW + timedelta(seconds=1),
    )

    assert duplicate.duplicate is True
    assert duplicate.submitted is False
    assert repository.current_inventory_snapshot(identity)["quantity"] == 4

    final_recovery = run_regime_broker_reconciliation(
        repository=repository,
        identity=identity,
        broker_open_orders=[],
        broker_fills=[
            {
                **identity,
                "algorithmId": "regime",
                "orderIntentId": "regime-intent-1",
                "clientOrderId": client_order_id,
                "brokerOrderId": f"broker-{client_order_id}",
                "fillId": "regime-fill-final-6",
                "symbol": "SPY",
                "side": "Buy",
                "filledQuantity": 6,
                "submittedQuantity": 10,
                "averageFillPrice": 100.02,
                "status": "FILLED",
                "filledAt": (NOW + timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
            }
        ],
        broker_positions=[
            {
                **identity,
                "algorithmId": "regime",
                "orderIntentId": "regime-intent-1",
                "clientOrderId": client_order_id,
                "positionId": "regime-position-SPY-regime-intent-1",
                "quantity": 10,
                "symbol": "SPY",
            }
        ],
        evaluated_at=NOW + timedelta(seconds=2),
        trigger="phase34_final_fill",
    )

    assert final_recovery["reconciled"] is True
    assert repository.current_inventory_snapshot(identity)["quantity"] == 10
    assert repository.read_execution_outbox_record(identity, "regime-intent-1")["processingStatus"] == "filled"
    observed_client_order_ids = {str(order.get("clientOrderId") or "") for order in repository.read_owned_records("regime_orders", identity)}
    assert observed_client_order_ids == {client_order_id}
    assert len(repository.read_owned_records("regime_trades", identity)) >= 1


def test_phase34_uncertain_submission_is_reconciled_before_retrying_broker_submit() -> None:
    repository, identity, _ = _repository()
    _insert_intent(repository, identity, quantity=5)
    first_broker = AcceptancePaperBroker(raise_on_submit=True)

    first = process_regime_execution_outbox_once(
        repository=repository,
        identity=identity,
        paper_gateway=_gateway(repository, identity, first_broker),
        evaluated_at=NOW,
    )

    assert first is not None
    assert first.status == "reconciliation_required"
    assert first_broker.submit_count == 1

    latest = repository.read_execution_outbox_record(identity, "regime-intent-1")
    client_order_id = str(latest["brokerClientOrderId"])
    recovered = run_regime_broker_reconciliation(
        repository=repository,
        identity=identity,
        broker_open_orders=[
            {
                **identity,
                "algorithmId": "regime",
                "orderIntentId": "regime-intent-1",
                "clientOrderId": client_order_id,
                "brokerOrderId": f"broker-{client_order_id}",
                "status": "accepted",
            }
        ],
        broker_fills=[],
        broker_positions=[],
        evaluated_at=NOW + timedelta(seconds=1),
        trigger="phase34_uncertain_submit",
    )

    assert recovered["reconciled"] is True
    assert repository.read_execution_outbox_record(identity, "regime-intent-1")["processingStatus"] == "acknowledged"

    retry_broker = AcceptancePaperBroker(fill_status="FILLED", filled_quantity=5)
    duplicate = submit_regime_outbox_record(
        repository=repository,
        identity=identity,
        paper_gateway=_gateway(repository, identity, retry_broker),
        outbox_record=repository.read_execution_outbox_record(identity, "regime-intent-1"),
        evaluated_at=NOW + timedelta(seconds=2),
    )

    assert duplicate.duplicate is True
    assert duplicate.submitted is False
    assert retry_broker.submit_count == 0


@pytest.mark.parametrize("crash_stage", ["event_received", "decision_completed", "decision_persisted"])
def test_phase34_restart_cutpoints_do_not_duplicate_completed_bar_decisions(crash_stage: str) -> None:
    async def scenario() -> None:
        repository, identity, _ = _repository(instance_id="regime-default", account_id="default", runtime_mode="shadow")
        event = RegimeFinalisedBarEvent.from_payload(_completed_bar_payload())
        crashing_service = CountingRegimeService(repository)
        crashing = RegimeRuntimeSupervisor(
            service=crashing_service,
            config=RegimeRuntimeSupervisorConfig(
                default_algorithm_instance_id=identity["algorithmInstanceId"],
                default_account_id=identity["accountId"],
                default_runtime_mode=identity["runtimeMode"],
                queue_maxsize=8,
                command_queue_maxsize=4,
                max_processing_lag_seconds=99_999_999,
                heartbeat_interval_seconds=60,
                maintenance_interval_seconds=60,
                crash_after_stage=crash_stage,
            ),
        )

        with pytest.raises(RuntimeError, match=f"simulated_crash_after_{crash_stage}"):
            await crashing.process_finalised_bar_event(event)

        restarted_service = CountingRegimeService(repository)
        restarted = RegimeRuntimeSupervisor(
            service=restarted_service,
            config=RegimeRuntimeSupervisorConfig(
                default_algorithm_instance_id=identity["algorithmInstanceId"],
                default_account_id=identity["accountId"],
                default_runtime_mode=identity["runtimeMode"],
                queue_maxsize=8,
                command_queue_maxsize=4,
                max_processing_lag_seconds=99_999_999,
                heartbeat_interval_seconds=60,
                maintenance_interval_seconds=60,
            ),
        )

        await restarted.process_finalised_bar_event(event)

        decisions = repository.read_owned_records("regime_decisions", event.identity)
        assert len(decisions) == 1
        if crash_stage == "event_received":
            assert restarted_service.evaluate_calls == 1
        else:
            assert restarted_service.evaluate_calls == 0

    asyncio.run(scenario())


class AcceptancePaperBroker:
    def __init__(
        self,
        *,
        ack_status: str = "ACCEPTED",
        fill_status: str | None = "FILLED",
        filled_quantity: int = 7,
        raise_on_submit: bool = False,
    ) -> None:
        self.ack_status = ack_status
        self.fill_status = fill_status
        self.filled_quantity = filled_quantity
        self.raise_on_submit = raise_on_submit
        self.submit_count = 0
        self.cancel_count = 0
        self.account_type = "paper"
        self.paper_only = True
        self.live_trading_enabled = False
        self.account_matches_configured_identity = True
        self.credentials_present = True
        self.market_data_credentials_present = True
        self.trading_blocked = False
        self.trading_url = "https://paper-api.alpaca.markets"

    def verify_paper_account(self) -> bool:
        return True

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        self.submit_count += 1
        if self.raise_on_submit:
            raise TimeoutError("broker accepted order but acknowledgement was lost")
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=f"broker-{intent.clientOrderId}",
            status=self.ack_status,
            acceptedAt=NOW if self.ack_status != "REJECTED" else None,
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


class CountingRegimeService(RegimeApplicationService):
    def __init__(self, repository: RegimeRepository) -> None:
        super().__init__(repository)
        self.evaluate_calls = 0

    def evaluate(self, payload: dict) -> dict:
        self.evaluate_calls += 1
        return super().evaluate(payload)


def _repository(
    *,
    runtime_mode: str = "paper",
    instance_id: str = "regime-paper-acceptance",
    account_id: str = "paper-account",
) -> tuple[RegimeRepository, dict[str, str], Path]:
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


def _gateway(repository: RegimeRepository, identity: dict[str, str], broker: AcceptancePaperBroker) -> PaperOrderGateway:
    return PaperOrderGateway(broker, RegimePaperGatewayStore(repository, identity))


def _insert_intent(repository: RegimeRepository, identity: dict[str, str], *, quantity: int) -> None:
    inserted = repository.insert_order_intent(
        {
            **identity,
            "decisionId": "regime-decision-1",
            "orderIntentId": "regime-intent-1",
            "algorithmVersion": "regime_algorithm_v3_backend_authoritative",
            "settingsVersion": "regime-settings-v1",
            "profileVersion": "regime-profile-v1",
            "side": "Buy",
            "positionEffect": "enter_long",
            "quantity": quantity,
            "entryPrice": 100.0,
            "stopPrice": 99.0,
            "targetPrice": 102.0,
            "riskDollars": 100.0,
            "settingsSnapshot": _settings(),
            "dataManifestHash": "manifest-1",
            "createdAt": NOW.isoformat().replace("+00:00", "Z"),
            "expiresAt": (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
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
            "expiresAt": (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        },
    )
    assert risk["recorded"] is True


def _fill(
    identity: dict[str, str],
    *,
    order_intent_id: str,
    fill_id: str,
    filled_quantity: int,
    side: str = "Buy",
) -> dict:
    return {
        **identity,
        "algorithmId": "regime",
        "decisionId": f"decision-{order_intent_id}",
        "orderIntentId": order_intent_id,
        "fillId": fill_id,
        "brokerOrderId": f"broker-{order_intent_id}",
        "clientOrderId": f"paper-{order_intent_id}",
        "symbol": identity["symbol"],
        "side": side,
        "filledQuantity": filled_quantity,
        "submittedQuantity": filled_quantity,
        "averageFillPrice": 100.0,
        "stopPrice": 99.0,
        "targetPrice": 102.0,
        "filledAt": NOW.isoformat().replace("+00:00", "Z"),
        "settingsVersion": "regime-settings-v1",
    }


def _settings() -> dict:
    return {
        "settingsVersion": "regime-settings-v1",
        "profileVersion": "regime-profile-v1",
        "execution": {"orderTimeToLiveSeconds": 300, "orderType": "limit", "maximumCancelReplaceAttempts": 1},
    }


def _candle() -> dict:
    return {"timestamp": "2026-07-23T15:31:00Z", "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 100_000}


def _completed_bar_payload() -> dict:
    candles = []
    price = 100.0
    for index in range(40):
        price += 0.03
        candles.append(
            {
                "timestamp": f"2026-07-23T14:{index:02d}:00Z",
                "open": round(price - 0.02, 4),
                "high": round(price + 0.08, 4),
                "low": round(price - 0.08, 4),
                "close": round(price, 4),
                "volume": 125_000,
            }
        )
    return {
        "algorithmInstanceId": "regime-default",
        "accountId": "default",
        "runtimeMode": "shadow",
        "symbol": "SPY",
        "completedBarTimestamp": candles[-1]["timestamp"],
        "publishedAt": NOW.isoformat().replace("+00:00", "Z"),
        "marketData": {
            "symbol": "SPY",
            "timeframe": "1Min",
            "primaryCandles": candles,
            "oneMinuteCandles": candles,
            "contextFeeds": {
                "quoteFreshness": {"status": "fresh", "ageMs": 1000, "bid": 100.0, "ask": 100.02, "spreadBps": 2.0, "expectedFillQuantity": 100},
                "scheduledEconomicEvent": {"state": "none", "minutesUntilEvent": 999},
            },
        },
    }
