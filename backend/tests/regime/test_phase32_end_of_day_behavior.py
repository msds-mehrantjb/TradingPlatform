from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime_health import RegimeRuntimeMetrics
from backend.app.algorithms.regime.runtime_supervisor import (
    RegimeRuntimeSupervisor,
    RegimeRuntimeSupervisorConfig,
    _automatic_entry_submission_blockers,
)
from backend.app.algorithms.regime.service import RegimeApplicationService


ROOT = Path(__file__).resolve().parents[3]
TEST_TMP_ROOT = ROOT / "backend" / ".pytest_regime_phase32_eod"


def test_phase32_entry_submission_is_blocked_after_early_close_effective_cutoff() -> None:
    identity = _identity()
    metrics = _ready_metrics()
    evaluated_at = datetime(2026, 11, 27, 17, 40, tzinfo=UTC)

    blockers = _automatic_entry_submission_blockers(
        metrics,
        identity=identity,
        outbox_record={
            **identity,
            "orderIntentId": "phase32-intent",
            "positionEffect": "enter_long",
            "quantity": 1,
            "completedBarFinalized": True,
            "completedBarTimestamp": evaluated_at.replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z"),
            "marketDataValidation": {"passed": True, "complete": True, "current": True},
            "localRiskResult": {"passed": True, "approvedQuantity": 1},
            "globalRiskApproval": {"approved": True, "approvedQuantity": 1, "reservationId": "phase32-reservation"},
            "settingsSnapshot": {"entry_policy": {"entryCutoffTimeEt": "15:30"}, "exit_policy": {"flattenTimeEt": "15:55"}},
        },
        rollout_stage="limited_paper",
        rollout_snapshot={"automaticPaperSubmissionEnabled": True, "paperButtonRequested": True, "paperButtonEffective": True},
        promotion_evidence={"minShadowDecisions": True, "minSimulatedOrders": True, "brokerSafetyVerified": True},
        evaluated_at=evaluated_at,
    )

    assert "regime.execution.entry_cutoff_reached" in blockers


def test_phase32_end_of_day_flatten_creates_exit_for_owned_quantity_only() -> None:
    repository, identity = _repository()
    repository.record_position_state(identity, _open_position(identity, quantity=3))
    supervisor = _supervisor(repository)

    summary = supervisor.run_end_of_day_once(now=datetime(2026, 7, 23, 19, 56, tzinfo=UTC))

    assert summary["flattenDue"] is True
    assert summary["flattenResult"]["exitIntentsCreated"] == 1
    intent = summary["flattenResult"]["exitIntents"][0]
    assert intent["quantity"] == 3
    assert intent["ownedPositionQuantity"] == 3
    assert intent["exitReason"] == "end_of_day_flatten"


def test_phase32_daily_reset_uses_exchange_session_boundary_not_utc_midnight() -> None:
    repository, identity = _repository()
    repository.write_runtime_checkpoint(
        {
            **identity,
            "lastProcessedBarTimestamp": "2026-07-23T19:55:00Z",
            "dailyCounters": {"tradeCount": 2, "entryCount": 2, "strategyTradeCounts": {"trend": 2}, "familyTradeCounts": {"trend": 2}},
            "cooldownState": {"remainingBars": 4},
            "strategyCooldowns": {"trend_pullback": 4},
            "familyCooldowns": {"trend": {"remainingBars": 4}},
            "sequenceVersion": 1,
        }
    )
    supervisor = _supervisor(repository)

    after_close = supervisor.run_daily_reset_once(now=datetime(2026, 7, 24, 0, 30, tzinfo=UTC))
    assert after_close["reset"] is False
    assert repository.read_runtime_checkpoint(identity)["dailyCounters"]["tradeCount"] == 2

    new_session = supervisor.run_daily_reset_once(now=datetime(2026, 7, 24, 13, 35, tzinfo=UTC))
    checkpoint = repository.read_runtime_checkpoint(identity)
    assert new_session["reset"] is True
    assert checkpoint["dailyCounters"]["sessionDate"] == "2026-07-24"
    assert checkpoint["dailyCounters"]["tradeCount"] == 0
    assert checkpoint["strategyCooldowns"] == {}
    assert checkpoint["familyCooldowns"] == {}


def _repository() -> tuple[RegimeRepository, dict[str, str]]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    repository = RegimeRepository(f"sqlite:///{TEST_TMP_ROOT / f'{uuid4().hex}.sqlite3'}")
    return repository, _identity()


def _identity() -> dict[str, str]:
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-paper-default",
        "accountId": "paper-account-123",
        "runtimeMode": "paper",
        "symbol": "SPY",
    }


def _supervisor(repository: RegimeRepository) -> RegimeRuntimeSupervisor:
    return RegimeRuntimeSupervisor(
        service=RegimeApplicationService(repository),
        config=RegimeRuntimeSupervisorConfig(
            default_algorithm_instance_id="regime-paper-default",
            default_account_id="paper-account-123",
            default_runtime_mode="paper",
            symbol="SPY",
        ),
    )


def _ready_metrics() -> RegimeRuntimeMetrics:
    metrics = RegimeRuntimeMetrics()
    metrics.supervisor_started = True
    metrics.recovery_succeeded = True
    metrics.inventory_reconciled = True
    metrics.risk_reservations_consistent = True
    metrics.broker_paper_mode_verified = True
    metrics.broker_connectivity_ok = True
    metrics.persistence_available = True
    metrics.latest_reconciliation = {"reconciled": True}
    for component in ("market_event_publisher", "database", "paper_broker", "broker_connectivity"):
        metrics.component_health[component]["status"] = "healthy"
    return metrics


def _open_position(identity: dict[str, str], *, quantity: int) -> dict[str, object]:
    return {
        **identity,
        "positionId": "phase32-position-1",
        "tradeId": "phase32-trade-1",
        "decisionId": "phase32-entry-decision",
        "orderIntentId": "phase32-entry-intent",
        "side": "Long",
        "positionStatus": "open",
        "filledQuantity": quantity,
        "quantity": quantity,
        "averageFillPrice": 100.0,
        "stopPrice": 99.0,
        "targetPrice": 102.0,
        "openedAt": "2026-07-23T14:30:00Z",
        "appliedFillIds": ["phase32-fill-1"],
        "authoritativeInventorySnapshot": {"quantity": quantity},
    }
