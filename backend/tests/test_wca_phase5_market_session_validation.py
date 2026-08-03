from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.wca.contracts import WcaCandle, WcaMarketSnapshot, WcaOrderStatus
from backend.app.algorithms.wca.order_validation import validate_wca_final_order
from backend.app.algorithms.wca.paper_broker import WcaDeterministicPaperBroker, WcaPaperBrokerOutboxAdapter, build_wca_paper_broker_request
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.algorithms.wca.runtime_events import WcaFinalizedBarEvent
from backend.app.algorithms.wca.runtime_repository import WcaRuntimeRepository
from backend.app.algorithms.wca.session_validation import WcaBrokerClock, validate_wca_entry_session
from backend.tests.test_wca_phase5_final_order_validation import valid_context, valid_decision
from backend.tests.test_wca_step6_inventory_persistence import temp_db_path


UTC = timezone.utc


def test_runtime_event_acceptance_rejects_background_events_when_calendar_session_is_closed() -> None:
    repository = WcaRuntimeRepository(WcaSqliteRepository(f"sqlite:///{temp_db_path()}"))
    timestamp = datetime(2026, 1, 3, 17, 0, tzinfo=UTC)
    snapshot = _snapshot(timestamp)
    event = WcaFinalizedBarEvent(
        event_id="phase5-market-closed",
        symbol="SPY",
        finalized_candle_timestamp=timestamp,
        data_manifest_hash="phase5-market-closed-manifest",
        publication_timestamp=timestamp + timedelta(seconds=1),
        source="test.background_publisher",
        snapshot=snapshot,
    )

    result = repository.publish_finalized_bar_event(event, now=event.publication_timestamp)

    assert result.accepted is False
    assert result.status == "rejected"
    assert "wca.runtime.event.market_session_blocked" in result.reason_codes
    assert "wca.session.calendar_session_missing" in result.reason_codes


def test_entry_session_validation_requires_open_broker_clock_and_entry_cutoff() -> None:
    closed_clock = WcaBrokerClock(timestamp=datetime(2026, 1, 6, 20, 45, tzinfo=UTC), is_open=False)

    result = validate_wca_entry_session(
        timestamp=datetime(2026, 1, 6, 20, 45, tzinfo=UTC),
        entry_cutoff_minutes=15 * 60 + 30,
        broker_clock=closed_clock,
        require_broker_clock=True,
    )

    assert result.market_is_open is False
    assert result.allowed_session_window is False
    assert "wca.session.broker_clock_closed" in result.reason_codes
    assert "wca.session.entry_cutoff_reached" in result.reason_codes


def test_final_order_validation_blocks_expired_decision_and_runtime_command_deadline() -> None:
    decision = valid_decision()
    context = replace(
        valid_context(decision),
        evaluation_timestamp=decision.decision_timestamp + timedelta(seconds=121),
        decision_expiration_seconds=120,
        command_deadline_at=decision.decision_timestamp + timedelta(seconds=60),
    )

    result = validate_wca_final_order(decision, context)

    assert result.valid is False
    assert "wca.order_validation.decision_expired" in result.reason_codes
    assert "wca.order_validation.runtime_command_deadline_expired" in result.reason_codes


def test_outbox_adapter_pre_submit_check_cancels_without_broker_submission() -> None:
    repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
    decision = valid_decision()
    assert decision.proposed_order is not None
    request = build_wca_paper_broker_request(decision.proposed_order)
    reservation = repository.reserve_decision_order_and_outbox(
        decision,
        run_id="phase5-market-session",
        account_id=decision.proposed_order.account_id,
        idempotency_key=decision.proposed_order.idempotency_key or "phase5-market-key",
        client_order_id=request.client_order_id,
        request_payload=request.model_dump(mode="json"),
        final_validation_context=valid_context(decision),
    )
    broker = WcaDeterministicPaperBroker()

    result = WcaPaperBrokerOutboxAdapter().process_next_outbox(
        repository,
        broker,
        owner_id="phase5-market-session",
        pre_submit_check=lambda _record, _request: (False, ("wca.runtime.pre_submit.market_session_blocked", "wca.session.broker_clock_closed")),
    )

    assert reservation.created is True
    assert result.submitted is False
    assert result.state == WcaOrderStatus.CANCELLED
    assert broker.submit_count == 0
    assert "wca.session.broker_clock_closed" in result.reason_codes


def _snapshot(timestamp: datetime) -> WcaMarketSnapshot:
    candles = tuple(
        WcaCandle(
            timestamp=timestamp - timedelta(minutes=69 - index),
            open=100,
            high=101,
            low=99,
            close=100,
            volume=100_000,
        )
        for index in range(70)
    )
    return WcaMarketSnapshot(
        symbol="SPY",
        data_timestamp=timestamp,
        decision_timestamp=timestamp,
        candles=candles,
        data_ready=True,
    )
