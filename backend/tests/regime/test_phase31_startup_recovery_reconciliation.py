from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import backend.app.algorithms.regime.runtime_supervisor as runtime_supervisor
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisor, RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.service import RegimeApplicationService


ROOT = Path(__file__).resolve().parents[3]
TEST_TMP_ROOT = ROOT / "backend" / ".pytest_regime_phase31_startup_recovery"


def test_phase31_startup_recovery_restores_state_and_unblocks_only_after_all_checks_pass() -> None:
    repository, identity = _repository()
    repository.write_runtime_checkpoint(_checkpoint(identity))
    supervisor = _supervisor(repository, paper_gateway=_Gateway())

    asyncio.run(supervisor.run_recovery_once())

    recovery = supervisor.metrics.latest_recovery
    assert recovery["recoveryStatus"] == "completed"
    assert recovery["newEntriesPaused"] is False
    assert recovery["startupRecoveryChecks"]["settingsLoaded"] is True
    assert recovery["startupRecoveryChecks"]["hysteresisStateRestored"] is True
    assert recovery["startupRecoveryChecks"]["cooldownsRestored"] is True
    assert recovery["startupRecoveryChecks"]["dailyCountersRestored"] is True
    assert recovery["startupRecoveryChecks"]["brokerObservationsReconciled"] is True
    assert recovery["startupRecoveryChecks"]["globalRiskReservationsReconciled"] is True
    assert recovery["positionManagementRecovery"]["resumed"] is True
    assert supervisor.metrics.recovery_succeeded is True
    assert "regime.runtime.recovery_incomplete" not in supervisor.metrics.entry_block_reason_codes


def test_phase31_startup_recovery_blocks_entries_when_active_entry_reservation_is_missing() -> None:
    repository, identity = _repository()
    repository.write_runtime_checkpoint(_checkpoint(identity))
    _insert_outbox(repository, identity, order_intent_id="phase31-missing-reservation", status="queued", reservation_id=None)
    supervisor = _supervisor(repository, paper_gateway=_Gateway())

    asyncio.run(supervisor.run_recovery_once())

    recovery = supervisor.metrics.latest_recovery
    assert recovery["recoveryStatus"] == "blocked"
    assert recovery["newEntriesPaused"] is True
    assert recovery["startupRecoveryChecks"]["globalRiskReservationsReconciled"] is False
    assert "regime.runtime.recovery.global_risk_reservations_not_reconciled" in recovery["reasonCodes"]
    assert "regime.execution.risk_reservations_inconsistent" in supervisor.metrics.entry_block_reason_codes


def test_phase31_startup_recovery_releases_or_commits_terminal_reservations_without_resubmitting(
    monkeypatch,
) -> None:
    repository, identity = _repository()
    repository.write_runtime_checkpoint(_checkpoint(identity))
    _insert_outbox(repository, identity, order_intent_id="phase31-rejected", status="rejected", reservation_id="reservation-release-1")
    _insert_outbox(repository, identity, order_intent_id="phase31-filled", status="filled", reservation_id="reservation-commit-1")
    released: list[str] = []
    committed: list[tuple[str, str]] = []
    monkeypatch.setattr(runtime_supervisor, "release_regime_global_risk_reservation", lambda reservation_id: released.append(str(reservation_id)) or True)
    monkeypatch.setattr(
        runtime_supervisor,
        "commit_regime_global_risk_reservation",
        lambda reservation_id, *, broker_order_id=None: committed.append((str(reservation_id), str(broker_order_id or ""))) or True,
    )
    supervisor = _supervisor(repository, paper_gateway=_Gateway())

    asyncio.run(supervisor.run_recovery_once())

    reconciliation = supervisor.metrics.latest_recovery["globalRiskReservationReconciliation"]
    assert supervisor.metrics.latest_recovery["recoveryStatus"] == "completed"
    assert released == ["reservation-release-1"]
    assert committed == [("reservation-commit-1", "broker-phase31-filled")]
    assert reconciliation["releasedReservations"] == [
        {"orderIntentId": "phase31-rejected", "reservationId": "reservation-release-1", "status": "rejected"}
    ]
    assert reconciliation["committedReservations"] == [
        {"orderIntentId": "phase31-filled", "reservationId": "reservation-commit-1", "status": "filled"}
    ]
    assert repository.table_counts()["regime_orders"] == 0


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


def _supervisor(repository: RegimeRepository, *, paper_gateway) -> RegimeRuntimeSupervisor:
    return RegimeRuntimeSupervisor(
        service=RegimeApplicationService(repository),
        config=RegimeRuntimeSupervisorConfig(
            default_algorithm_instance_id="regime-paper-default",
            default_account_id="paper-account-123",
            default_runtime_mode="paper",
            symbol="SPY",
        ),
        paper_gateway=paper_gateway,
    )


def _checkpoint(identity: dict[str, str]) -> dict[str, object]:
    return {
        **identity,
        "confirmedRegime": "strong_uptrend",
        "candidateRegime": "weak_downtrend",
        "candidateConfirmationCount": 2,
        "regimeDwellBars": 8,
        "lastProcessedBarTimestamp": "2026-07-23T15:30:00Z",
        "lastDecisionId": "phase31-decision-restore",
        "cooldownState": {"remainingBars": 1, "reason": "phase31"},
        "strategyCooldowns": {"trend_pullback": 1},
        "familyCooldowns": {"trend": {"remainingBars": 1}},
        "dailyCounters": {"decisionCount": 4, "orderProposalCount": 1, "tradeCount": 1, "lossCount": 0},
        "sequenceVersion": 3,
    }


def _insert_outbox(
    repository: RegimeRepository,
    identity: dict[str, str],
    *,
    order_intent_id: str,
    status: str,
    reservation_id: str | None,
) -> None:
    created_at = datetime.now(UTC) - timedelta(seconds=10)
    payload = {
        **identity,
        "decisionId": f"decision-{order_intent_id}",
        "orderIntentId": order_intent_id,
        "algorithmVersion": "regime_algorithm_v3_backend_authoritative",
        "settingsVersion": "phase31-settings",
        "profileVersion": "phase31-profile",
        "side": "Buy",
        "positionEffect": "enter_long",
        "quantity": 1,
        "entryPrice": 100.0,
        "limitPrice": 100.0,
        "stopPrice": 99.5,
        "targetPrice": 101.0,
        "riskDollars": 5.0,
        "completedBarFinalized": True,
        "completedBarTimestamp": created_at.replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z"),
        "marketDataValidation": {"passed": True, "complete": True, "current": True},
        "settingsSnapshot": {
            "settingsVersion": "phase31-settings",
            "profileVersion": "phase31-profile",
            "execution": {"orderTimeToLiveSeconds": 300, "orderType": "limit"},
        },
        "createdAt": created_at.isoformat().replace("+00:00", "Z"),
        "expiresAt": (created_at + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    }
    if reservation_id:
        payload["reservationId"] = reservation_id
        payload["globalRiskApproval"] = {"approved": True, "approvedQuantity": 1, "reservationId": reservation_id}
        payload["brokerOrderId"] = f"broker-{order_intent_id}"
    assert repository.insert_order_intent(payload)["inserted"] is True
    assert repository.update_execution_outbox_status(identity, order_intent_id, status=status, payload=payload)["updated"] is True


class _Gateway:
    def __init__(self) -> None:
        self.broker = _Broker()


class _Broker:
    def refresh_positions(self) -> list[dict[str, object]]:
        return []

    def refresh_open_orders(self) -> list[dict[str, object]]:
        return []

    def refresh_fills(self) -> list[dict[str, object]]:
        return []
