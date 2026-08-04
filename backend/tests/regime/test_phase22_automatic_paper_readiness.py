from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.app.algorithms.regime import stateful_core
from backend.app.algorithms.regime.configuration import flatten_regime_trading_settings, validate_regime_trading_settings_snapshot
from backend.app.algorithms.regime.contracts import (
    REGIME_ALGORITHM_ID,
    REGIME_ALGORITHM_VERSION,
    REGIME_STRATEGY_CATALOG_VERSION,
    RegimeAxes,
    RegimeClassification,
    RegimeDecision,
    RegimeHysteresisState,
    RegimeSizingResult,
)
from backend.app.algorithms.regime.global_risk_adapter import RegimeGlobalRiskApproval
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot
from backend.app.algorithms.regime.rollout import LIMITED_PAPER_PROMOTION_EVIDENCE
from backend.app.algorithms.regime.runtime_events import RegimeFinalisedBarEvent
from backend.app.algorithms.regime.runtime_health import RegimeRuntimeMetrics
from backend.app.algorithms.regime.runtime_supervisor import (
    RegimeRuntimeSupervisorConfig,
    _automatic_entry_submission_blockers,
)


NOW = datetime(2026, 7, 23, 15, 30, tzinfo=UTC)
IDENTITY = {
    "algorithmId": "regime",
    "algorithmInstanceId": "regime-paper-default",
    "accountId": "paper-account-123",
    "runtimeMode": "paper",
    "symbol": "SPY",
}


def test_phase22_paper_event_defaults_use_explicit_regime_paper_identity(monkeypatch) -> None:
    monkeypatch.setenv("REGIME_ALPACA_PAPER_ACCOUNT_ID", "alpaca-paper-abc")
    payload = _completed_bar_payload()

    event = RegimeFinalisedBarEvent.from_payload(payload)
    config = RegimeRuntimeSupervisorConfig.paper_runtime_from_env()

    assert event.algorithm_id == "regime"
    assert event.algorithm_instance_id == "regime-paper-default"
    assert event.account_id == "alpaca-paper-abc"
    assert event.runtime_mode == "paper"
    assert event.symbol == "SPY"
    assert config.default_algorithm_instance_id == "regime-paper-default"
    assert config.default_account_id == "alpaca-paper-abc"


def test_phase22_global_risk_zero_approval_drops_order_proposal(monkeypatch) -> None:
    settings = validate_regime_trading_settings_snapshot({"identity": IDENTITY}).as_dict()
    monkeypatch.setattr(stateful_core, "calculate_regime_decision", lambda *args, **kwargs: _buy_decision(settings))
    monkeypatch.setattr(stateful_core, "calculate_regime_position_size", lambda *args, **kwargs: _sizing())
    monkeypatch.setattr(stateful_core, "evaluate_regime_global_risk_request", lambda request: _global_rejection(request))

    result = stateful_core.process_completed_bar(
        snapshot=build_regime_market_snapshot(_completed_bar_payload()["marketData"]),
        settings_snapshot=settings,
        previous_state=None,
        inventory_snapshot={**IDENTITY, "quantity": 0, "openOrderQuantity": 0, "reservedCash": 0.0, "inventoryReconciled": True},
        account_snapshot=_fresh_account(),
    )

    assert result["localRiskResult"]["passed"] is True
    assert result["globalRiskApproval"]["rejected"] is True
    assert result["orderProposal"] is None
    assert result["brokerSubmission"] is None
    assert result["persistenceRecords"]["orderIntentId"] is None


def test_phase22_automatic_entry_preflight_requires_all_operational_conditions() -> None:
    metrics = _ready_metrics()
    evidence = _promotion_evidence()
    outbox_record = _ready_outbox_record()

    ready = _automatic_entry_submission_blockers(
        metrics,
        identity=IDENTITY,
        outbox_record=outbox_record,
        rollout_stage="limited_paper",
        rollout_snapshot={"paperButtonRequested": True, "paperButtonEffective": True, "automaticPaperSubmissionEnabled": True},
        promotion_evidence=evidence,
        evaluated_at=NOW,
    )
    blocked = _automatic_entry_submission_blockers(
        metrics,
        identity=IDENTITY,
        outbox_record={**outbox_record, "marketDataValidation": {"passed": False}},
        rollout_stage="limited_paper",
        rollout_snapshot={"paperButtonRequested": False, "paperButtonEffective": False, "automaticPaperSubmissionEnabled": False},
        promotion_evidence=evidence,
        evaluated_at=NOW,
    )
    after_hours = _automatic_entry_submission_blockers(
        metrics,
        identity=IDENTITY,
        outbox_record=outbox_record,
        rollout_stage="limited_paper",
        rollout_snapshot={"paperButtonRequested": True, "paperButtonEffective": True, "automaticPaperSubmissionEnabled": True},
        promotion_evidence=evidence,
        evaluated_at=datetime(2026, 7, 23, 21, 1, tzinfo=UTC),
    )

    assert ready == []
    assert "regime.execution.automatic_paper_control_off" in blocked
    assert "regime.execution.market_data_validation_missing_or_failed" in blocked
    assert "regime.execution.market_not_regular_session" in after_hours


def test_phase22_preflight_rejects_shadow_namespace_for_real_paper_entries() -> None:
    metrics = _ready_metrics()
    blockers = _automatic_entry_submission_blockers(
        metrics,
        identity={**IDENTITY, "algorithmInstanceId": "regime-default", "accountId": "default"},
        outbox_record={**_ready_outbox_record(), "algorithmInstanceId": "regime-default", "accountId": "default"},
        rollout_stage="limited_paper",
        rollout_snapshot={"paperButtonRequested": True, "paperButtonEffective": True, "automaticPaperSubmissionEnabled": True},
        promotion_evidence=_promotion_evidence(),
        evaluated_at=NOW,
    )

    assert "regime.execution.paper_identity_required" in blockers
    assert "regime.execution.paper_account_identity_required" in blockers


def _ready_metrics() -> RegimeRuntimeMetrics:
    metrics = RegimeRuntimeMetrics()
    metrics.supervisor_started = True
    metrics.recovery_succeeded = True
    metrics.inventory_reconciled = True
    metrics.broker_paper_mode_verified = True
    metrics.broker_connectivity_ok = True
    metrics.latest_reconciliation = {"reconciled": True}
    metrics.persistence_available = True
    metrics.component_health["market_event_publisher"]["status"] = "healthy"
    metrics.component_health["database"]["status"] = "healthy"
    metrics.component_health["paper_broker"]["status"] = "healthy"
    metrics.component_health["broker_connectivity"]["status"] = "healthy"
    return metrics


def _promotion_evidence() -> dict:
    return {
        **{key: True for key in LIMITED_PAPER_PROMOTION_EVIDENCE},
        "persistedEvidenceIds": tuple(LIMITED_PAPER_PROMOTION_EVIDENCE),
    }


def _ready_outbox_record() -> dict:
    return {
        **IDENTITY,
        "orderIntentId": "regime-intent-ready",
        "processingStatus": "created",
        "positionEffect": "enter_long",
        "quantity": 3,
        "completedBarFinalized": True,
        "marketDataValidation": {"passed": True, "complete": True, "current": True},
        "globalRiskApproval": {"approved": True, "approvedQuantity": 3},
        "localRiskResult": {"passed": True, "approvedQuantity": 3},
        "completedBarTimestamp": NOW.isoformat().replace("+00:00", "Z"),
        "orderIntent": {
            **IDENTITY,
            "orderIntentId": "regime-intent-ready",
            "symbol": "SPY",
            "positionEffect": "enter_long",
            "quantity": 3,
        },
    }


def _fresh_account() -> dict:
    return {
        "sourceAuthority": "shared_backend_service",
        "accountId": "paper-account-123",
        "runtimeMode": "paper",
        "equity": 100_000.0,
        "cash": 100_000.0,
        "availableBuyingPower": 100_000.0,
        "buyingPower": 100_000.0,
        "globalRiskCapacityQuantity": 1_000,
        "dailyAccountPnl": 0.0,
        "buyingPowerCurrent": True,
        "accountSnapshotFresh": True,
        "positionsReconciled": True,
        "openOrdersReconciled": True,
        "accountTradingBlocked": False,
        "observedAt": NOW.isoformat().replace("+00:00", "Z"),
        "supervisorStarted": True,
        "automaticPaperTradingEnabled": True,
        "paperButtonRequested": True,
        "paperButtonEffective": True,
        "requireAutomaticPaperControlForEntry": True,
        "rolloutStageAllowsRealPaperExecution": True,
        "requireRealPaperExecutionStage": True,
        "marketRegularSessionOpen": True,
        "finalizedBarCurrent": True,
        "publisherHealthy": True,
        "accountSnapshotCurrent": True,
        "brokerHealthy": True,
        "databaseHealthy": True,
        "marketDataCurrentAndComplete": True,
        "brokerReconciliationHealthy": True,
        "recoverySucceeded": True,
        "inventoryReconciled": True,
        "ordersReconciled": True,
    }


def _buy_decision(settings: dict) -> RegimeDecision:
    timestamp = NOW.isoformat().replace("+00:00", "Z")
    flattened = flatten_regime_trading_settings(settings)
    classification = RegimeClassification(
        timestamp=timestamp,
        raw_regime="strong_uptrend",
        axes=RegimeAxes("strong_up", "normal", "trend", "good", "midday", "none"),
        confidence=0.9,
        features={"close": 100.0, "atr": 1.0, "spreadBps": 2.0, "expectedGrossEdgeBps": 100.0},
        evidence={"close": 100.0, "liquidityEvidence": {"spreadBps": 2.0}},
        missing_inputs=(),
        no_trade_reasons=(),
    )
    state = RegimeHysteresisState(
        confirmed_regime="strong_uptrend",
        previous_regime=None,
        candidate_regime=None,
        candidate_confirmation_count=0,
        regime_start_time=timestamp,
        transition_confidence=0.9,
        transition_reason="test",
        regime_confidence=0.9,
    )
    return RegimeDecision(
        algorithm_id=REGIME_ALGORITHM_ID,
        algorithm_version=REGIME_ALGORITHM_VERSION,
        settings_version=str(settings["settingsVersion"]),
        strategy_catalog_version=REGIME_STRATEGY_CATALOG_VERSION,
        profile_version=str(settings["profileVersion"]),
        decision_id="regime-decision-global-risk-zero",
        symbol="SPY",
        signal="Buy",
        aggregate_signal="Buy",
        trade_allowed=True,
        trade_blockers=(),
        raw_classification=classification,
        confirmed_state=state,
        strategy_outputs=(),
        family_scores={"trend": 1.0},
        effective_settings={
            **flattened,
            "minimumNetExpectedEdgeBps": 1.0,
            "maximumTransactionCostBps": 100.0,
            "conservativeCostFallbackApproved": True,
            "maxEntriesPerDay": 10,
            "maxTradesPerDay": 10,
            "maxConsecutiveLosses": 3,
            "maxAllowedShares": 500,
            "maxOrderNotionalDollars": 100_000.0,
            "maxPositionNotionalDollars": 100_000.0,
            "maxParticipationPercent": 1.0,
            "familyAggregation": {
                "activeStrategyCount": 10,
                "activeFamilyCount": 4,
                "winningScore": 1.0,
                "winningEdge": 1.0,
                "expectedGrossEdgeBps": 100.0,
                "familyScores": {"trend": 1.0},
            },
        },
        score=1.0,
        confidence=0.9,
    )


def _sizing() -> RegimeSizingResult:
    return RegimeSizingResult(
        quantity=5,
        risk_dollars=25.0,
        stop_distance=1.0,
        stop_price=99.0,
        target_price=102.0,
        limiting_factor="test",
        quantity_caps=(),
    )


def _global_rejection(request) -> RegimeGlobalRiskApproval:
    return RegimeGlobalRiskApproval(
        algorithm_id="regime",
        decision_id=request.decision_id,
        order_intent_id=request.order_intent_id,
        approved_quantity=0,
        rejected=True,
        reason_codes=("global_risk.failed.test_reject",),
        reservation_id=None,
        expiration_timestamp=NOW.isoformat().replace("+00:00", "Z"),
        account_risk_snapshot_version="test-global-risk",
        status="denied",
        approved_risk_dollars=0.0,
        account_snapshot_id="account-risk-test",
        idempotency_key=request.idempotency_key,
        evaluated_at=NOW.isoformat().replace("+00:00", "Z"),
    )


def _completed_bar_payload() -> dict:
    candles = []
    price = 100.0
    start = datetime(2026, 7, 23, 14, 0, tzinfo=UTC)
    for index in range(40):
        price += 0.04
        candles.append(
            {
                "timestamp": (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
                "open": round(price - 0.02, 4),
                "high": round(price + 0.08, 4),
                "low": round(price - 0.08, 4),
                "close": round(price, 4),
                "volume": 150_000,
            }
        )
    return {
        "runtimeMode": "paper",
        "symbol": "SPY",
        "completedBarTimestamp": candles[-1]["timestamp"],
        "publishedAt": NOW.isoformat().replace("+00:00", "Z"),
        "marketData": {
            "symbol": "SPY",
            "timeframe": "1Min",
            "primaryCandles": candles,
            "oneMinuteCandles": candles,
            "contextFeeds": {
                "quoteFreshness": {"status": "fresh", "ageMs": 100, "bid": 101.58, "ask": 101.60, "spreadBps": 2.0, "expectedFillQuantity": 10_000},
                "scheduledEconomicEvent": {"state": "none", "minutesUntilEvent": 999},
            },
        },
    }
