from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.app.algorithms.wca.contracts import (
    WcaBrokerReconciliationResult,
    WcaPaperStabilityValidationRequest,
    WcaPaperStabilityValidationResult,
    WcaPaperValidationDecision,
    WcaPaperValidationEquityPoint,
    WcaPaperValidationExit,
    WcaPaperValidationFill,
    WcaPaperValidationRollbackEvidence,
    WcaSide,
)
from backend.app.algorithms.wca.paper_stability import validate_wca_paper_stability
from backend.app.main import app


def test_stable_paper_validation_requires_full_evidence_before_marking_stable() -> None:
    repository = MemoryPaperStabilityRepository()
    request = stable_request()

    result = validate_wca_paper_stability(request, repository=repository)

    assert result.paper_trading_stable is True
    assert result.rollout_phase_passed is True
    assert result.validation_days == 15.0
    assert result.market_conditions == ("range", "trend", "volatile")
    assert result.decisions == 4
    assert result.rejected_entries == 1
    assert result.fills == 2
    assert result.exits == 2
    assert result.total_pnl == 145.0
    assert result.max_drawdown_percent < 1
    assert result.average_slippage_per_share == 0.02
    assert result.reconciliation_discrepancies == 0
    assert result.duplicate_requests == 2
    assert result.duplicate_preventions == 2
    assert "wca.paper_stability.stable" in result.reason_codes
    assert repository.results == [result]


def test_unstable_paper_validation_blocks_stability_until_all_criteria_pass() -> None:
    now = datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc)
    request = WcaPaperStabilityValidationRequest(
        validation_id="paper-unstable",
        account_id="paper-account",
        started_at=now,
        ended_at=now + timedelta(days=2),
        decisions=(
            WcaPaperValidationDecision(
                decision_id="decision-1",
                timestamp=now,
                market_condition="trend",
                side=WcaSide.BUY,
                quantity=10,
                submitted=True,
            ),
        ),
        equity_curve=(WcaPaperValidationEquityPoint(timestamp=now, equity=100_000),),
    )

    result = validate_wca_paper_stability(request)

    assert result.paper_trading_stable is False
    assert result.rollout_phase_passed is False
    assert "wca.paper_stability.blocked" in result.reason_codes
    assert "wca.paper_stability.period_too_short" in result.blocking_reasons
    assert "wca.paper_stability.insufficient_market_conditions" in result.blocking_reasons
    assert "wca.paper_stability.no_rejected_entries_tracked" in result.blocking_reasons
    assert "wca.paper_stability.no_fills_tracked" in result.blocking_reasons
    assert "wca.paper_stability.no_exits_tracked" in result.blocking_reasons
    assert "wca.paper_stability.no_slippage_tracked" in result.blocking_reasons
    assert "wca.paper_stability.no_reconciliation_results" in result.blocking_reasons
    assert "wca.paper_stability.no_duplicate_prevention_exercised" in result.blocking_reasons
    assert "wca.paper_stability.rollback_not_tested" in result.blocking_reasons
    assert "wca.paper_stability.rollback_safe_state_not_restored" in result.blocking_reasons


def test_paper_stability_api_records_validation_evidence() -> None:
    response = TestClient(app).post(
        "/api/wca/paper/stability/validate",
        json=stable_request().model_dump(mode="json"),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["paper_trading_stable"] is True
    assert body["rollout_phase_passed"] is True
    assert body["decisions"] == 4
    assert body["rejected_entries"] == 1
    assert body["reconciliation_discrepancies"] == 0
    assert "wca.paper_stability.stable" in body["reason_codes"]


def stable_request() -> WcaPaperStabilityValidationRequest:
    started = datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc)
    ended = started + timedelta(days=15)
    decisions = (
        WcaPaperValidationDecision(
            decision_id="decision-trend",
            timestamp=started,
            market_condition="trend",
            side=WcaSide.BUY,
            quantity=50,
            submitted=True,
        ),
        WcaPaperValidationDecision(
            decision_id="decision-range-rejected",
            timestamp=started + timedelta(days=3),
            market_condition="range",
            side=WcaSide.BUY,
            quantity=0,
            rejected=True,
            reason_codes=("wca.local_gate.rejected",),
        ),
        WcaPaperValidationDecision(
            decision_id="decision-volatile",
            timestamp=started + timedelta(days=7),
            market_condition="volatile",
            side=WcaSide.SELL,
            quantity=25,
            submitted=True,
        ),
        WcaPaperValidationDecision(
            decision_id="decision-range-submitted",
            timestamp=started + timedelta(days=10),
            market_condition="range",
            side=WcaSide.BUY,
            quantity=20,
            submitted=True,
        ),
    )
    return WcaPaperStabilityValidationRequest(
        validation_id="paper-stable",
        account_id="paper-account",
        started_at=started,
        ended_at=ended,
        min_validation_days=14,
        min_market_conditions=3,
        max_drawdown_percent=5,
        max_average_slippage_per_share=0.05,
        decisions=decisions,
        fills=(
            WcaPaperValidationFill(
                order_intent_id="intent-trend",
                decision_id="decision-trend",
                timestamp=started + timedelta(minutes=1),
                side=WcaSide.BUY,
                quantity=50,
                expected_price=100,
                fill_price=100.02,
                slippage_per_share=0.02,
            ),
            WcaPaperValidationFill(
                order_intent_id="intent-volatile",
                decision_id="decision-volatile",
                timestamp=started + timedelta(days=7, minutes=1),
                side=WcaSide.SELL,
                quantity=25,
                expected_price=104,
                fill_price=103.98,
                slippage_per_share=0.02,
            ),
        ),
        exits=(
            WcaPaperValidationExit(
                order_intent_id="intent-trend",
                decision_id="decision-trend",
                timestamp=started + timedelta(days=2),
                side=WcaSide.SELL,
                quantity=50,
                exit_price=101.5,
                pnl=75,
            ),
            WcaPaperValidationExit(
                order_intent_id="intent-volatile",
                decision_id="decision-volatile",
                timestamp=started + timedelta(days=9),
                side=WcaSide.BUY,
                quantity=25,
                exit_price=101.2,
                pnl=70,
            ),
        ),
        equity_curve=(
            WcaPaperValidationEquityPoint(timestamp=started, equity=100_000),
            WcaPaperValidationEquityPoint(timestamp=started + timedelta(days=4), equity=99_600),
            WcaPaperValidationEquityPoint(timestamp=started + timedelta(days=10), equity=100_120),
            WcaPaperValidationEquityPoint(timestamp=ended, equity=100_145),
        ),
        reconciliation_results=(
            WcaBrokerReconciliationResult(
                reconciliation_id="recon-clean",
                account_id="paper-account",
                evaluated_at=ended,
                intents_checked=3,
                broker_open_orders_checked=1,
                broker_positions_checked=1,
                discrepancies=(),
                hard_operational_warning=False,
                reason_codes=("wca.broker_reconciliation.clean",),
            ),
        ),
        duplicate_requests=2,
        duplicate_preventions=2,
        rollback=WcaPaperValidationRollbackEvidence(
            tested=True,
            restored_safe_state=True,
            reason_codes=("wca.rollout.rollback_safe",),
        ),
    )


class MemoryPaperStabilityRepository:
    def __init__(self) -> None:
        self.results: list[WcaPaperStabilityValidationResult] = []

    def write_paper_stability_validation(self, result: WcaPaperStabilityValidationResult) -> None:
        self.results.append(result)
