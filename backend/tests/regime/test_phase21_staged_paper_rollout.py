from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.algorithms.regime import service as regime_service_module
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.rollout import (
    LIMITED_PAPER_PROMOTION_EVIDENCE,
    REGIME_OPERATIONAL_ROLLOUT_EVIDENCE_SOURCE,
    REGIME_OPERATIONAL_ROLLOUT_STATE_KEY,
    activate_operational_rollout_stage,
    evaluate_operational_rollout_stage,
    read_or_initialize_operational_rollout_stage,
)
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisor, RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.main import app


NOW = datetime(2026, 7, 23, 15, 30, tzinfo=UTC)
TEST_TMP_ROOT = Path(__file__).resolve().parents[2] / ".pytest_regime_phase21_rollout"


def test_phase21_default_operational_stage_is_persisted_decision_shadow() -> None:
    repository, identity = _repository(runtime_mode="shadow")

    snapshot = read_or_initialize_operational_rollout_stage(_Store(repository, identity), recorded_at=NOW)
    persisted = repository.read_runtime_snapshot(identity, REGIME_OPERATIONAL_ROLLOUT_STATE_KEY)

    assert snapshot["stage"] == "decision_shadow"
    assert snapshot["paperOnly"] is True
    assert snapshot["liveTradingEnabled"] is False
    assert persisted is not None
    assert persisted["stage"] == "decision_shadow"
    assert persisted["policy"]["permissions"]["processFinalizedBars"] is True
    assert persisted["policy"]["permissions"]["submitBrokerOrders"] is False


def test_phase21_decision_shadow_records_hypothetical_fill_without_intent_or_outbox(monkeypatch: pytest.MonkeyPatch) -> None:
    repository, identity = _repository(runtime_mode="shadow")
    service = RegimeApplicationService(repository)

    def fake_pipeline(payload: dict) -> dict:
        latest = payload["marketData"]["oneMinuteCandles"][-1]["timestamp"]
        settings = payload["__regime_settings_snapshot"]
        return _stateful_result(identity, latest, settings["settingsVersion"])

    monkeypatch.setattr(regime_service_module, "execute_regime_pipeline", fake_pipeline)

    result = service.evaluate(
        {
            **identity,
            "marketData": _market_data(),
            "__regime_rollout_stage": "decision_shadow",
            "__regime_rollout_source": "backend.app.algorithms.regime.runtime_supervisor",
        }
    )

    counts = repository.table_counts()
    hypothetical = repository.read_owned_records("regime_hypothetical_fills", identity)

    assert result["orderProposal"] is None
    assert result["suppressedOrderProposal"]["orderIntentId"] == "phase21-intent-1"
    assert counts["regime_decisions"] == 1
    assert counts["regime_order_intents"] == 0
    assert counts["regime_execution_outbox"] == 0
    assert len(hypothetical) == 1
    assert hypothetical[0]["inventoryAuthoritative"] is False
    assert "regime.rollout.decision_shadow.hypothetical_fill_recorded" in hypothetical[0]["reasonCodes"]


def test_phase21_simulated_execution_uses_fake_paper_broker_through_real_outbox() -> None:
    repository, identity = _repository(runtime_mode="paper", instance_id="regime-default", account_id="default")
    _record_stage_evidence(repository, identity, requirements=("stable_supervisor_operation", "no_duplicate_orders", "successful_restart_recovery", "acceptable_queue_and_decision_latency", "passing_replay_and_holdout_tests"))
    activation = activate_operational_rollout_stage(
        _Store(repository, identity),
        "simulated_execution",
        actor="rollout-worker",
        reason="phase21 simulated execution test",
        evidence=repository.read_regime_rollout_promotion_evidence(identity),
        activated_at=NOW,
    )
    _insert_intent(repository, identity)
    supervisor = RegimeRuntimeSupervisor(
        service=RegimeApplicationService(repository),
        config=RegimeRuntimeSupervisorConfig(default_runtime_mode="paper", maintenance_interval_seconds=60, heartbeat_interval_seconds=60),
    )
    supervisor.metrics.recovery_succeeded = True
    supervisor.metrics.inventory_reconciled = True

    result = supervisor.process_execution_outbox_once()

    assert activation["activated"] is True
    assert result["processed"] is True
    assert result["status"] == "filled"
    assert supervisor.status()["paperRolloutStage"] == "simulated_execution"
    assert repository.table_counts()["regime_orders"] == 1
    assert repository.table_counts()["regime_fills"] == 1
    assert repository.current_inventory_snapshot(identity)["quantity"] == 1


def test_phase21_limited_paper_requires_backend_recorded_evidence_not_static_flags() -> None:
    repository, identity = _repository(runtime_mode="paper")
    missing = evaluate_operational_rollout_stage("limited_paper", current_stage="simulated_execution", evidence={})
    activation = activate_operational_rollout_stage(
        _Store(repository, identity),
        "limited_paper",
        actor="rollout-worker",
        reason="missing evidence should block",
        evidence=repository.read_regime_rollout_promotion_evidence(identity),
        activated_at=NOW,
    )

    _record_stage_evidence(repository, identity, requirements=LIMITED_PAPER_PROMOTION_EVIDENCE)
    simulated = activate_operational_rollout_stage(
        _Store(repository, identity),
        "simulated_execution",
        actor="rollout-worker",
        reason="backend evidence satisfied for simulated execution",
        evidence=repository.read_regime_rollout_promotion_evidence(identity),
        activated_at=NOW + timedelta(seconds=30),
    )
    allowed = activate_operational_rollout_stage(
        _Store(repository, identity),
        "limited_paper",
        actor="rollout-worker",
        reason="backend evidence satisfied",
        evidence=repository.read_regime_rollout_promotion_evidence(identity),
        activated_at=NOW + timedelta(minutes=1),
    )

    assert missing["allowed"] is False
    assert "regime.rollout.evidence_missing:stable_supervisor_operation" in missing["reasonCodes"]
    assert activation["activated"] is False
    assert simulated["activated"] is True
    assert allowed["activated"] is True
    assert allowed["stage"] == "limited_paper"
    assert allowed["automaticPaperSubmissionEnabled"] is True
    assert allowed["liveTradingEnabled"] is False


def test_phase21_rollout_api_rejects_frontend_supplied_promotion_evidence() -> None:
    response = TestClient(app).post(
        "/api/regime/rollout/stage",
        json={
            "stage": "limited_paper",
            "actor": "frontend",
            "reason": "try to promote",
            "promotionEvidence": {"stable_supervisor_operation": True},
        },
    )

    assert response.status_code == 400
    assert "regime.api.rollout_inline_evidence_rejected" in response.json()["detail"]["reasonCodes"]


def test_phase21_global_paper_off_keeps_manual_paper_unaffected_and_runtime_identity_intact() -> None:
    repository, identity = _repository(runtime_mode="paper", instance_id="regime-default", account_id="default")
    _record_stage_evidence(repository, identity, requirements=LIMITED_PAPER_PROMOTION_EVIDENCE)
    activate_operational_rollout_stage(
        _Store(repository, identity),
        "simulated_execution",
        actor="rollout-worker",
        reason="prepare limited paper stage",
        evidence=repository.read_regime_rollout_promotion_evidence(identity),
        activated_at=NOW,
    )
    activate_operational_rollout_stage(
        _Store(repository, identity),
        "limited_paper",
        actor="rollout-worker",
        reason="prepare limited paper stage",
        evidence=repository.read_regime_rollout_promotion_evidence(identity),
        activated_at=NOW + timedelta(seconds=5),
    )
    supervisor = RegimeRuntimeSupervisor(
        service=RegimeApplicationService(repository),
        config=RegimeRuntimeSupervisorConfig(default_runtime_mode="paper", maintenance_interval_seconds=60, heartbeat_interval_seconds=60),
    )

    result = asyncio.run(
        supervisor.submit_command(
            "set_automatic_paper",
            {"enabled": False, "reason": "dashboard.global_paper_toggle_off"},
            actor="dashboard.global_paper_toggle",
        )
    )
    control = result["automaticPaperControl"]

    assert result["immediate"] is True
    assert control["rolloutStage"] == "limited_paper"
    assert control["automaticPaperTradingEnabled"] is False
    assert control["paperButtonRequested"] is False
    assert control["paperButtonEffective"] is False
    assert control["keepsRuntimeIdentityUnchanged"] is True
    assert "regime.runtime.automatic_paper_control_off" in supervisor.metrics.entry_block_reason_codes
    assert control["manualPaperTradingUnaffected"] is True
    assert control["manualPaperTradingWhenMarketOpen"] is True
    assert control["liveTradingEnabled"] is False


def test_phase21_global_paper_on_is_blocked_until_backend_rollout_evidence_exists() -> None:
    repository, _identity = _repository(runtime_mode="paper", instance_id="regime-default", account_id="default")
    supervisor = RegimeRuntimeSupervisor(
        service=RegimeApplicationService(repository),
        config=RegimeRuntimeSupervisorConfig(default_runtime_mode="paper", maintenance_interval_seconds=60, heartbeat_interval_seconds=60),
    )

    result = asyncio.run(
        supervisor.submit_command(
            "set_automatic_paper",
            {"enabled": True, "reason": "dashboard.global_paper_toggle_on"},
            actor="dashboard.global_paper_toggle",
        )
    )
    control = result["automaticPaperControl"]

    assert control["requestedAutomaticPaperTradingEnabled"] is True
    assert control["automaticPaperTradingEnabled"] is False
    assert control["rolloutStage"] == "decision_shadow"
    assert "regime.rollout.evidence_missing:stable_supervisor_operation" in control["reasonCodes"]
    assert "regime.runtime.automatic_paper.not_enabled_until_rollout_gate_passes" in control["reasonCodes"]
    assert control["manualPaperTradingUnaffected"] is True


def test_phase21_global_paper_on_advances_to_limited_paper_when_backend_evidence_passes() -> None:
    repository, identity = _repository(runtime_mode="paper", instance_id="regime-default", account_id="default")
    _record_stage_evidence(repository, identity, requirements=LIMITED_PAPER_PROMOTION_EVIDENCE)
    supervisor = RegimeRuntimeSupervisor(
        service=RegimeApplicationService(repository),
        config=RegimeRuntimeSupervisorConfig(default_runtime_mode="paper", maintenance_interval_seconds=60, heartbeat_interval_seconds=60),
    )

    result = asyncio.run(
        supervisor.submit_command(
            "set_automatic_paper",
            {"enabled": True, "reason": "dashboard.global_paper_toggle_on"},
            actor="dashboard.global_paper_toggle",
        )
    )
    control = result["automaticPaperControl"]

    assert control["requestedAutomaticPaperTradingEnabled"] is True
    assert control["paperRequestedOn"] is True
    assert control["paperEffectiveOn"] is False
    assert control["rolloutStage"] == "limited_paper"
    assert [activation["stage"] for activation in control["activations"] if activation.get("activated")] == ["simulated_execution", "limited_paper"]
    assert control["paperEffectiveBlockers"]
    assert control["paperOnly"] is True
    assert control["liveTradingEnabled"] is False


def test_phase21_automatic_paper_api_rejects_frontend_supplied_promotion_evidence() -> None:
    response = TestClient(app).post(
        "/api/regime/rollout/automatic-paper",
        json={
            "enabled": True,
            "actor": "frontend",
            "reason": "try to enable global paper",
            "promotionEvidence": {"stable_supervisor_operation": True},
        },
    )

    assert response.status_code == 400
    assert "regime.api.rollout_inline_evidence_rejected" in response.json()["detail"]["reasonCodes"]


def _repository(*, runtime_mode: str, instance_id: str = "phase21-regime", account_id: str = "phase21-paper") -> tuple[RegimeRepository, dict[str, str]]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    repository = RegimeRepository(f"sqlite:///{TEST_TMP_ROOT / f'{uuid4().hex}.sqlite3'}")
    identity = {
        "algorithmId": "regime",
        "algorithmInstanceId": instance_id,
        "accountId": account_id,
        "runtimeMode": runtime_mode,
        "symbol": "SPY",
    }
    return repository, identity


def _record_stage_evidence(repository: RegimeRepository, identity: dict[str, str], *, requirements: tuple[str, ...]) -> None:
    payload = {
        "backendEvidenceSource": REGIME_OPERATIONAL_ROLLOUT_EVIDENCE_SOURCE,
        "evidenceId": f"phase21-evidence-{uuid4().hex}",
        "recordedAt": NOW.isoformat().replace("+00:00", "Z"),
        "persistedEvidenceIds": requirements,
    }
    payload.update({requirement: True for requirement in requirements})
    result = repository.record_regime_rollout_promotion_evidence(identity, payload)
    assert result["recorded"] is True


def _insert_intent(repository: RegimeRepository, identity: dict[str, str]) -> None:
    created_at = datetime.now(UTC)
    inserted = repository.insert_order_intent(
        {
            **identity,
            "decisionId": "phase21-decision-1",
            "orderIntentId": "phase21-intent-1",
            "algorithmVersion": "regime_algorithm_v3_backend_authoritative",
            "settingsVersion": "phase21-settings",
            "profileVersion": "phase21-profile",
            "side": "Buy",
            "positionEffect": "enter_long",
            "quantity": 1,
            "entryPrice": 100.0,
            "limitPrice": 100.0,
            "stopPrice": 99.5,
            "targetPrice": 101.0,
            "riskDollars": 5.0,
            "settingsSnapshot": {
                "settingsVersion": "phase21-settings",
                "profileVersion": "phase21-profile",
                "execution": {"orderTimeToLiveSeconds": 300, "orderType": "limit", "maximumCancelReplaceAttempts": 1},
            },
            "createdAt": created_at.isoformat().replace("+00:00", "Z"),
        }
    )
    assert inserted["inserted"] is True
    risk = repository.record_local_risk_result(
        identity,
        {
            **identity,
            "localRiskResultId": "phase21-local-risk-1",
            "decisionId": "phase21-decision-1",
            "orderIntentId": "phase21-intent-1",
            "settingsVersion": "phase21-settings",
            "passed": True,
            "requestedQuantity": 1,
            "approvedQuantity": 1,
            "estimatedGrossEdge": 25.0,
            "estimatedTransactionCost": 5.0,
            "estimatedNetEdge": 20.0,
            "blockers": [],
            "reductions": [],
            "evaluatedAt": created_at.isoformat().replace("+00:00", "Z"),
            "expiresAt": (created_at + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        },
    )
    assert risk["recorded"] is True


def _market_data() -> dict:
    candles = []
    price = 100.0
    for index in range(40):
        price += 0.02
        candles.append(
            {
                "timestamp": f"2026-07-23T14:{index:02d}:00Z",
                "open": round(price - 0.02, 4),
                "high": round(price + 0.08, 4),
                "low": round(price - 0.08, 4),
                "close": round(price, 4),
                "volume": 150_000,
            }
        )
    return {
        "symbol": "SPY",
        "primaryCandles": candles,
        "oneMinuteCandles": candles,
        "contextFeeds": {
            "quoteFreshness": {"status": "fresh", "ageMs": 250, "bid": price - 0.01, "ask": price + 0.01, "spreadBps": 2.0, "expectedFillQuantity": 100},
            "scheduledEconomicEvent": {"state": "none", "minutesUntilEvent": 999},
        },
    }


def _stateful_result(identity: dict[str, str], timestamp: str, settings_version: str) -> dict:
    decision_id = "phase21-decision-1"
    order_intent = {
        **identity,
        "decisionId": decision_id,
        "orderIntentId": "phase21-intent-1",
        "symbol": "SPY",
        "side": "Buy",
        "quantity": 1,
        "entryPrice": 100.0,
        "limitPrice": 100.0,
        "stopPrice": 99.5,
        "targetPrice": 101.0,
        "settingsVersion": settings_version,
        "algorithmVersion": "regime_algorithm_v3_backend_authoritative",
        "profileVersion": "phase21-profile",
    }
    return {
        "algorithmId": "regime",
        "algorithmVersion": "regime_algorithm_v3_backend_authoritative",
        "settingsVersion": settings_version,
        "profileVersion": "phase21-profile",
        "dataTimestamp": timestamp,
        "featureTimestamp": timestamp,
        "dataManifestHash": "phase21-manifest",
        "statefulCoreVersion": "phase21-test-core",
        "decision": {
            "algorithm_id": "regime",
            "algorithm_version": "regime_algorithm_v3_backend_authoritative",
            "settings_version": settings_version,
            "strategy_catalog_version": "phase21-strategy",
            "profile_version": "phase21-profile",
            "decision_id": decision_id,
            "symbol": "SPY",
            "signal": "Buy",
            "confirmed_state": {"confirmed_regime": "strong_uptrend"},
            "raw_classification": {"timestamp": timestamp, "raw_regime": "strong_uptrend"},
            "strategy_outputs": [],
            "family_scores": {},
            "effective_settings": {},
            "trade_blockers": [],
        },
        "nextRuntimeState": {"lastProcessedBarTimestamp": timestamp, "stateVersion": 1},
        "strategyOutputs": [],
        "contextOutputs": [],
        "confirmationOutputs": [],
        "safetyOutputs": [],
        "familyAggregation": {},
        "effectiveProfile": {},
        "localRiskResult": {"passed": True},
        "orderProposal": order_intent,
        "persistenceRecords": {},
        "sizing": {},
        "tradeManagement": {},
        "orderIntent": order_intent,
        "orderValidation": {"valid": True},
        "globalRiskApproval": {"approved": True},
        "brokerSubmission": {"submitted": False},
    }


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
