import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime import stateful_core
from backend.app.algorithms.regime.configuration import validate_regime_trading_settings_snapshot
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.rollout import (
    LIMITED_PAPER_PROMOTION_EVIDENCE,
    REGIME_OPERATIONAL_ROLLOUT_EVIDENCE_SOURCE,
    REGIME_OPERATIONAL_ROLLOUT_STATE_KEY,
    activate_operational_rollout_stage,
)
from backend.app.algorithms.regime.runtime_health import RegimeRuntimeMetrics
from backend.app.algorithms.regime.runtime_supervisor import (
    RegimeRuntimeSupervisor,
    RegimeRuntimeSupervisorConfig,
    _automatic_entry_submission_blockers,
)
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot
from backend.tests.regime.test_phase22_automatic_paper_readiness import (
    IDENTITY,
    NOW,
    _buy_decision,
    _completed_bar_payload,
    _fresh_account,
    _global_rejection,
    _promotion_evidence,
    _ready_metrics,
    _ready_outbox_record,
    _sizing,
)


TEST_TMP_ROOT = Path(__file__).resolve().parents[2] / ".pytest_regime_phase29_entry_exit_semantics"


def test_phase29_paper_off_blocks_and_cancels_entries_without_shadowing_identity() -> None:
    repository, identity = _repository()
    _promote_to_limited_paper(repository, identity)
    _insert_entry_outbox(repository, identity, order_intent_id="phase29-entry-to-cancel")
    supervisor = _supervisor(repository, identity)
    asyncio.run(supervisor.submit_command("set_automatic_paper", {"enabled": True}, actor="ops"))

    result = asyncio.run(
        supervisor.submit_command(
            "set_automatic_paper",
            {"enabled": False, "reason": "operator_off"},
            actor="operator@example.com",
        )
    )

    control = result["automaticPaperControl"]
    outbox = repository.read_execution_outbox_record(identity, "phase29-entry-to-cancel")
    rollout = repository.read_runtime_snapshot(identity, REGIME_OPERATIONAL_ROLLOUT_STATE_KEY)
    audit = repository.read_runtime_snapshot(identity, "automatic_paper_control")

    assert control["rolloutStage"] == "limited_paper"
    assert rollout["stage"] == "limited_paper"
    assert outbox["processingStatus"] == "cancel_requested"
    assert outbox["cancelReason"] == "regime.runtime.automatic_paper_control_off"
    assert control["paperButtonRequested"] is False
    assert control["paperButtonEffective"] is False
    assert control["operator"] == "operator@example.com"
    assert audit["priorState"]["automaticPaperTradingEnabled"] is True
    assert audit["newState"]["automaticPaperTradingEnabled"] is False
    assert audit["keepsRuntimeIdentityUnchanged"] is True
    assert "regime.runtime.automatic_paper_control_off" in supervisor.metrics.entry_block_reason_codes


def test_phase29_entry_preflight_lists_all_required_operational_blockers() -> None:
    metrics = RegimeRuntimeMetrics()
    record = {
        **_ready_outbox_record(),
        "completedBarFinalized": False,
        "marketDataValidation": {"passed": False, "complete": False, "current": False},
        "localRiskResult": {"passed": False, "approvedQuantity": 0},
        "globalRiskApproval": {"approved": False, "approvedQuantity": 0},
        "quantity": 0,
        "orderIntent": {
            **_ready_outbox_record()["orderIntent"],
            "quantity": 0,
            "localRiskResult": {"passed": False, "approvedQuantity": 0},
            "globalRiskApproval": {"approved": False, "approvedQuantity": 0},
        },
    }

    blockers = _automatic_entry_submission_blockers(
        metrics,
        identity=IDENTITY,
        outbox_record=record,
        rollout_stage="limited_paper",
        rollout_snapshot={"paperButtonRequested": False, "paperButtonEffective": False, "automaticPaperSubmissionEnabled": False},
        promotion_evidence={},
        evaluated_at=datetime(2026, 7, 23, 21, 1, tzinfo=UTC),
    )

    assert "regime.execution.paper_runtime_not_running" in blockers
    assert "regime.execution.paper_button_requested_off" in blockers
    assert "regime.execution.paper_button_effective_off" in blockers
    assert "regime.execution.market_not_regular_session" in blockers
    assert "regime.execution.publisher_unhealthy" in blockers
    assert "regime.execution.database_unhealthy" in blockers
    assert "regime.execution.recovery_incomplete" in blockers
    assert "regime.execution.inventory_not_reconciled" in blockers
    assert "regime.execution.broker_reconciliation_unhealthy" in blockers
    assert "regime.execution.local_risk_missing_or_rejected" in blockers
    assert "regime.execution.global_risk_missing_or_rejected" in blockers
    assert "regime.execution.approved_quantity_required" in blockers


def test_phase29_local_risk_drops_entry_when_paper_button_effective_off(monkeypatch) -> None:
    settings = validate_regime_trading_settings_snapshot({"identity": IDENTITY}).as_dict()
    monkeypatch.setattr(stateful_core, "calculate_regime_decision", lambda *args, **kwargs: _buy_decision(settings))
    monkeypatch.setattr(stateful_core, "calculate_regime_position_size", lambda *args, **kwargs: _sizing())
    monkeypatch.setattr(stateful_core, "evaluate_regime_global_risk_request", lambda request: _global_rejection(request))

    account = {
        **_fresh_account(),
        "paperButtonRequested": False,
        "paperButtonEffective": False,
        "automaticPaperTradingEnabled": False,
        "operationalBlockers": ["regime.runtime.automatic_paper_control_off"],
    }
    result = stateful_core.process_completed_bar(
        snapshot=build_regime_market_snapshot(_completed_bar_payload()["marketData"]),
        settings_snapshot=settings,
        previous_state=None,
        inventory_snapshot={**IDENTITY, "quantity": 0, "openOrderQuantity": 0, "reservedCash": 0.0, "inventoryReconciled": True},
        account_snapshot=account,
    )

    assert result["orderProposal"] is None
    assert result["persistenceRecords"]["orderIntentId"] is None
    assert "regime.local_risk.paper_button_requested_off" in result["localRiskResult"]["blockers"]
    assert "regime.local_risk.paper_button_effective_off" in result["localRiskResult"]["blockers"]


def test_phase29_ready_entry_preflight_stays_empty_when_all_entry_conditions_hold() -> None:
    blockers = _automatic_entry_submission_blockers(
        _ready_metrics(),
        identity=IDENTITY,
        outbox_record=_ready_outbox_record(),
        rollout_stage="limited_paper",
        rollout_snapshot={"paperButtonRequested": True, "paperButtonEffective": True, "automaticPaperSubmissionEnabled": True},
        promotion_evidence=_promotion_evidence(),
        evaluated_at=NOW,
    )

    assert blockers == []


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


def _supervisor(repository: RegimeRepository, identity: dict[str, str]) -> RegimeRuntimeSupervisor:
    return RegimeRuntimeSupervisor(
        service=RegimeApplicationService(repository),
        config=RegimeRuntimeSupervisorConfig(
            default_algorithm_instance_id=identity["algorithmInstanceId"],
            default_account_id=identity["accountId"],
            default_runtime_mode="paper",
            maintenance_interval_seconds=60,
            heartbeat_interval_seconds=60,
        ),
    )


def _promote_to_limited_paper(repository: RegimeRepository, identity: dict[str, str]) -> None:
    evidence = {
        "backendEvidenceSource": REGIME_OPERATIONAL_ROLLOUT_EVIDENCE_SOURCE,
        "evidenceId": f"phase29-evidence-{uuid4().hex}",
        "recordedAt": NOW.isoformat().replace("+00:00", "Z"),
        "persistedEvidenceIds": LIMITED_PAPER_PROMOTION_EVIDENCE,
        **{requirement: True for requirement in LIMITED_PAPER_PROMOTION_EVIDENCE},
    }
    repository.record_regime_rollout_promotion_evidence(identity, evidence)
    activate_operational_rollout_stage(
        _Store(repository, identity),
        "simulated_execution",
        actor="rollout-worker",
        reason="phase29 simulated",
        evidence=repository.read_regime_rollout_promotion_evidence(identity),
        activated_at=NOW,
    )
    activate_operational_rollout_stage(
        _Store(repository, identity),
        "limited_paper",
        actor="rollout-worker",
        reason="phase29 limited",
        evidence=repository.read_regime_rollout_promotion_evidence(identity),
        activated_at=NOW + timedelta(seconds=1),
    )


def _insert_entry_outbox(repository: RegimeRepository, identity: dict[str, str], *, order_intent_id: str) -> None:
    payload = {
        **identity,
        "decisionId": f"{order_intent_id}-decision",
        "orderIntentId": order_intent_id,
        "side": "Buy",
        "positionEffect": "enter_long",
        "quantity": 3,
        "completedBarFinalized": True,
        "marketDataValidation": {"passed": True, "complete": True, "current": True},
        "globalRiskApproval": {"approved": True, "approvedQuantity": 3},
    }
    inserted = repository.insert_execution_outbox_record(identity, payload)
    assert inserted["inserted"] is True


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
        self.repository.write_runtime_snapshot(self.identity, key, snapshot)
