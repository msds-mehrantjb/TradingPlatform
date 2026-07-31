from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.algorithms.regime.execution_gateway import RegimePaperGatewayStore, process_regime_execution_outbox_once
from backend.app.algorithms.regime.local_gates import evaluate_regime_local_risk
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.execution import PaperGatewayBrokerAck, PaperOrderGateway


NOW = datetime(2026, 7, 23, 15, 30, tzinfo=UTC)
TEST_TMP_ROOT = Path(__file__).resolve().parents[2] / ".pytest_regime_step6_local_risk"


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda c, s: c.update({"completedPrimaryCandle": False}), "regime.local_risk.completed_bar_required"),
        (lambda c, s: c.update({"barAgeSeconds": 120}), "regime.local_risk.completed_bar_stale"),
        (lambda c, s: c["quoteFreshness"].update({"status": "stale"}), "regime.local_risk.quote_stale"),
        (lambda c, s: c["quoteFreshness"].pop("bid"), "regime.local_risk.bid_ask_required"),
        (lambda c, s: c["quoteFreshness"].update({"spreadBps": 500}), "regime.local_risk.spread_too_wide"),
        (lambda c, s: setattr(c["_classification"].axes, "liquidity", "poor"), "regime.local_risk.liquidity_blocked"),
        (lambda c, s: setattr(c["_classification"], "timestamp", "2026-07-25T15:30:00Z"), "regime.local_risk.session_permission"),
        (lambda c, s: setattr(c["_classification"].axes, "event_risk", "blackout"), "regime.local_risk.event_blackout"),
        (lambda c, s: setattr(c["_classification"], "timestamp", "2026-07-23T20:31:00Z"), "regime.local_risk.entry_cutoff"),
        (lambda c, s: c.update({"runtimePaused": True}), "regime.local_risk.runtime_paused"),
        (lambda c, s: c.update({"recoverySucceeded": False}), "regime.local_risk.recovery_incomplete"),
        (lambda c, s: c.update({"inventoryReconciled": False}), "regime.local_risk.reconciliation_incomplete"),
        (lambda c, s: c.update({"openPosition": {"quantity": 1, "notional": 100.0}}), "regime.local_risk.existing_position"),
        (lambda c, s: c.update({"duplicateProposal": True}), "regime.local_risk.duplicate_proposal"),
        (lambda c, s: c.update({"cooldownState": {"remainingBars": 2}}), "regime.local_risk.cooldown"),
        (lambda c, s: c.update({"familyCooldowns": {"trend": {"remainingBars": 1}}}), "regime.local_risk.family_cooldown"),
        (lambda c, s: c["dailyCounters"].update({"strategyTradeCounts": {"moving_average_trend": 1}}), "regime.local_risk.per_strategy_daily_limit"),
        (lambda c, s: (c["dailyCounters"].update({"familyTradeCounts": {"trend": 1}}), s.update({"perFamilyTradeLimits": {"trend": 1}})), "regime.local_risk.per_family_daily_limit"),
        (lambda c, s: c["dailyCounters"].update({"tradeCount": 5}), "regime.local_risk.total_daily_trade_limit"),
        (lambda c, s: c["dailyCounters"].update({"consecutiveLosses": 3}), "regime.local_risk.consecutive_loss_breaker"),
        (lambda c, s: c["dailyCounters"].update({"dailyLossPercent": 0.5}), "regime.local_risk.daily_loss_limit"),
        (lambda c, s: c.update({"decisionAgeSeconds": 120}), "regime.local_risk.decision_age"),
        (lambda c, s: (c.update({"decisionAgeSeconds": 70}), s.update({"orderTimeToLiveSeconds": 60})), "regime.local_risk.order_ttl"),
        (lambda c, s: c.pop("accountSnapshot"), "regime.local_risk.buying_power_unavailable"),
        (lambda c, s: s.update({"maxParticipationPercent": 0.0}), "regime.local_risk.quantity_reduced_to_zero"),
        (lambda c, s: s.update({"maximumTransactionCostBps": 1.0}), "regime.local_risk.transaction_cost_too_high"),
        (lambda c, s: c.update({"expectedGrossEdgeBps": 1.0}), "regime.local_risk.minimum_expected_net_edge"),
    ],
)
def test_local_risk_emits_stable_blocker_reason_codes(mutate, expected) -> None:
    settings = _settings()
    context = _context()
    classification = _classification()
    context["_classification"] = classification
    mutate(context, settings)
    context.pop("_classification", None)

    result = evaluate_regime_local_risk(
        decision_id="regime-decision-risk",
        order_intent_id="regime-intent-risk",
        settings_version="regime-settings-v1",
        requested_quantity=10,
        entry_price=100.0,
        aggregation=_aggregation(),
        classification=classification,
        state=None,
        settings=settings,
        runtime_context=context,
        evaluated_at=NOW,
    )

    assert result.passed is False
    assert expected in result.blockers
    assert expected in result.reasonCodes


@pytest.mark.parametrize(
    ("mutate", "expected_quantity", "expected_reason"),
    [
        (lambda c, s: s.update({"maxAllowedShares": 50}), 50, "regime.local_risk.reduce.maximum_shares"),
        (lambda c, s: s.update({"maxOrderNotionalDollars": 5_000.0}), 50, "regime.local_risk.reduce.maximum_order_notional"),
        (lambda c, s: (s.update({"maxPositionNotionalDollars": 5_000.0, "maxOpenRegimePositions": 2}), c.update({"openPosition": {"notional": 2_500.0}}), s.update({"pyramidingEnabled": True})), 25, "regime.local_risk.reduce.maximum_position_notional"),
        (lambda c, s: (c["quoteFreshness"].update({"expectedFillQuantity": 1_000}), s.update({"maxParticipationPercent": 0.02})), 20, "regime.local_risk.reduce.maximum_participation"),
        (lambda c, s: c["accountSnapshot"].update({"availableBuyingPower": 2_500.0}), 25, "regime.local_risk.reduce.buying_power"),
    ],
)
def test_local_risk_reduces_quantity_with_stable_reason_codes(mutate, expected_quantity, expected_reason) -> None:
    settings = _settings()
    context = _context()
    mutate(context, settings)

    result = evaluate_regime_local_risk(
        decision_id="regime-decision-risk",
        order_intent_id="regime-intent-risk",
        settings_version="regime-settings-v1",
        requested_quantity=100,
        entry_price=100.0,
        aggregation=_aggregation(),
        classification=_classification(),
        state=None,
        settings=settings,
        runtime_context=context,
        evaluated_at=NOW,
    )

    assert result.passed is True
    assert result.approvedQuantity == expected_quantity
    assert any(reduction["reasonCode"] == expected_reason for reduction in result.reductions)


@pytest.mark.parametrize(
    ("risk_payload", "expected_reason"),
    [
        (None, "regime.execution.local_risk_missing"),
        ({"expiresAt": (NOW - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")}, "regime.execution.local_risk_expired"),
        ({"settingsVersion": "regime-settings-v2"}, "regime.execution.local_risk_settings_mismatch"),
        ({"approvedQuantity": 5}, "regime.execution.local_risk_quantity_mismatch"),
        ({"passed": False, "blockers": ["regime.local_risk.daily_loss_limit"]}, "regime.execution.local_risk_failed"),
    ],
)
def test_execution_blocks_missing_stale_or_mismatched_local_risk(risk_payload, expected_reason) -> None:
    repository, identity = _repository()
    _insert_intent(repository, identity)
    if risk_payload is not None:
        _insert_local_risk(repository, identity, **risk_payload)
    broker = _FakeBroker()
    gateway = PaperOrderGateway(broker, RegimePaperGatewayStore(repository, identity))

    result = process_regime_execution_outbox_once(repository=repository, identity=identity, paper_gateway=gateway, evaluated_at=NOW)

    assert result is not None
    assert result.status == "rejected"
    assert broker.submit_count == 0
    assert expected_reason in result.reason_codes
    outbox = repository.read_execution_outbox_record(identity, "regime-intent-1")
    assert expected_reason in outbox["reasonCodes"]


def test_execution_accepts_order_quantity_reduced_below_local_risk_approval_by_global_risk() -> None:
    repository, identity = _repository()
    _insert_intent(repository, identity, quantity=5)
    _insert_local_risk(repository, identity, requestedQuantity=7, approvedQuantity=7)
    broker = _FakeBroker()
    gateway = PaperOrderGateway(broker, RegimePaperGatewayStore(repository, identity))

    result = process_regime_execution_outbox_once(repository=repository, identity=identity, paper_gateway=gateway, evaluated_at=NOW)

    assert result is not None
    assert result.status in {"accepted", "submitted", "acknowledged"}
    assert broker.submit_count == 1


def _settings() -> dict:
    return {
        "settingsVersion": "regime-settings-v1",
        "maxSpreadPercent": 0.03,
        "staleBarToleranceSeconds": 90,
        "quoteAgeToleranceSeconds": 5,
        "pyramidingEnabled": False,
        "maxAllowedShares": 1_000,
        "maxOrderNotionalDollars": 1_000_000.0,
        "maxPositionNotionalDollars": 1_000_000.0,
        "maxParticipationPercent": 1.0,
        "maxTradesPerDay": 5,
        "maxConsecutiveLosses": 3,
        "maxDailyLossPercent": 0.50,
        "perStrategyTradeLimits": {"moving_average_trend": 1},
        "entryCutoffTimeEt": "15:30",
        "orderTimeToLiveSeconds": 60,
        "maximumSlippageBps": 1.0,
        "maximumCostToEdgeRatio": 0.75,
        "conservativeCostFallbackApproved": True,
        "uncertaintyBufferBps": 0.0,
        "estimatedFeesBps": 0.1,
        "estimatedRegulatoryFeesBps": 0.0,
        "marketImpactBps": 0.0,
        "adverseSelectionBufferBps": 0.1,
        "minimumNetExpectedEdgeBps": 5.0,
        "maximumTransactionCostBps": 50.0,
    }


def _context() -> dict:
    return {
        "completedPrimaryCandle": True,
        "barAgeSeconds": 1,
        "decisionAgeSeconds": 1,
        "quoteFreshness": {"status": "fresh", "ageMs": 100, "bid": 99.99, "ask": 100.01, "spreadBps": 2.0, "expectedFillQuantity": 10_000},
        "accountSnapshot": {"sourceAuthority": "shared_backend_service", "equity": 100_000.0, "availableBuyingPower": 100_000.0, "buyingPower": 100_000.0},
        "inventorySnapshot": {"algorithmId": "regime", "symbol": "SPY", "quantity": 0, "openOrderQuantity": 0, "reservedCash": 0.0},
        "inventoryReconciled": True,
        "recoverySucceeded": True,
        "dailyCounters": {"tradeCount": 0, "consecutiveLosses": 0, "dailyLossPercent": 0.0, "strategyTradeCounts": {}, "familyTradeCounts": {}},
        "cooldownState": {"remainingBars": 0},
        "expectedGrossEdgeBps": 100.0,
    }


def _classification():
    return SimpleNamespace(
        timestamp="2026-07-23T15:30:00Z",
        evidence={"liquidityEvidence": {"spreadBps": 2.0}},
        features={"spreadBps": 2.0},
        axes=SimpleNamespace(liquidity="normal", event_risk="none"),
        no_trade_reasons=(),
    )


def _aggregation() -> dict:
    return {
        "winningEdge": 1.0,
        "selectedStrategyByFamily": {"trend": {"strategyId": "moving_average_trend"}},
        "familyScores": {"trend": 0.8},
    }


def _repository() -> tuple[RegimeRepository, dict[str, str]]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    repository = RegimeRepository(f"sqlite:///{TEST_TMP_ROOT / f'{uuid4().hex}.sqlite3'}")
    identity = {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-risk",
        "accountId": "paper-account",
        "runtimeMode": "paper",
        "symbol": "SPY",
    }
    return repository, identity


def _insert_intent(repository: RegimeRepository, identity: dict[str, str], *, quantity: int = 7) -> None:
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
            "settingsSnapshot": {"settingsVersion": "regime-settings-v1", "profileVersion": "regime-profile-v1"},
            "dataManifestHash": "manifest-1",
            "createdAt": NOW.isoformat().replace("+00:00", "Z"),
        }
    )
    assert result["inserted"] is True


def _insert_local_risk(repository: RegimeRepository, identity: dict[str, str], **overrides) -> None:
    payload = {
        **identity,
        "localRiskResultId": f"regime-local-risk-{uuid4().hex}",
        "decisionId": "regime-decision-1",
        "orderIntentId": "regime-intent-1",
        "settingsVersion": "regime-settings-v1",
        "passed": True,
        "requestedQuantity": 7,
        "approvedQuantity": 7,
        "estimatedGrossEdge": 40.0,
        "estimatedTransactionCost": 5.0,
        "estimatedNetEdge": 35.0,
        "blockers": [],
        "reductions": [],
        "evaluatedAt": NOW.isoformat().replace("+00:00", "Z"),
        "expiresAt": (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        **overrides,
    }
    assert repository.record_local_risk_result(identity, payload)["recorded"] is True


class _FakeBroker:
    def __init__(self) -> None:
        self.submit_count = 0

    def verify_paper_account(self) -> bool:
        return True

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        self.submit_count += 1
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=f"broker-{intent.clientOrderId}",
            status="ACCEPTED",
            acceptedAt=NOW,
        )

    def refresh_order(self, client_order_id: str):
        return None

    def cancel_order(self, client_order_id: str) -> bool:
        return True

    def refresh_positions(self) -> list[dict]:
        return []
