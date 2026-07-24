from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import shutil
import uuid

from backend.app.algorithms.session import (
    BufferedSessionDecisionWriter,
    DataQualityState,
    EventRiskState,
    LiquidityState,
    SESSION_DECISION_RECORD_SCHEMA_VERSION,
    SESSION_PERSISTENCE_VERSION,
    SessionBehavior,
    SessionClassification,
    SessionDecisionJsonlStore,
    SessionDecisionPersistenceRecord,
    SessionPhase,
    VolatilityState,
    build_session_candidate_decision,
    build_session_decision_record,
    build_session_operational_metrics,
    resolve_session_profile,
)
from backend.app.algorithms.session.persistence import SessionPersistenceStatus
from backend.app.domain.models import Signal


NOW = datetime(2026, 7, 23, 14, 5, tzinfo=UTC)


def test_session_step14_round_trip_serialization() -> None:
    record = _record()

    restored = SessionDecisionPersistenceRecord.model_validate_json(record.model_dump_json())

    assert restored.model_dump(mode="json") == record.model_dump(mode="json")
    assert restored.classificationId == "session-classification-step14"
    assert restored.featureSnapshotId == "session-feature-step14"
    assert restored.outputMode == "shadow"


def test_session_step14_restart_recovery_from_jsonl_store() -> None:
    scratch = _scratch_path()
    store = SessionDecisionJsonlStore(root=scratch / "session")
    record = _record()

    assert store.write_record(record) == "persisted"
    recovered = SessionDecisionJsonlStore(root=scratch / "session").recover_records(symbol="SPY", session_date="2026-07-23")

    assert [item.model_dump(mode="json") for item in recovered] == [record.model_dump(mode="json")]
    shutil.rmtree(scratch, ignore_errors=True)


def test_session_step14_duplicate_persistence_is_idempotent() -> None:
    scratch = _scratch_path()
    store = SessionDecisionJsonlStore(root=scratch / "session")
    record = _record()

    assert store.write_record(record) == "persisted"
    assert store.write_record(record) == "duplicate"

    assert len(store.read_records(symbol="SPY", session_date="2026-07-23")) == 1
    shutil.rmtree(scratch, ignore_errors=True)


def test_session_step14_bounded_queue_overflow_is_explicit() -> None:
    scratch = _scratch_path()
    writer = BufferedSessionDecisionWriter(SessionDecisionJsonlStore(root=scratch / "session"), max_queue_size=1)
    first = _record()
    second = _record(classification_id="session-classification-step14-second")

    accepted = writer.enqueue(first)
    overflow = writer.enqueue(second)

    assert accepted.status == "queued"
    assert overflow.status == "overflow_rejected"
    assert overflow.overflowCount == 1
    assert "session.persistence.queue_overflow_retry_required" in overflow.reasonCodes
    shutil.rmtree(scratch, ignore_errors=True)


def test_session_step14_database_failure_isolated_from_decision_path() -> None:
    writer = BufferedSessionDecisionWriter(_FailingStore(), max_queue_size=2)
    record = _record()

    assert writer.enqueue(record).status == "queued"
    result = writer.flush()

    assert result.failed == 1
    assert result.remainingQueued == 1
    assert result.failureReasons == ("session.persistence.write_failed:RuntimeError",)


def test_session_step14_version_fields_are_present() -> None:
    scratch = _scratch_path()
    store = SessionDecisionJsonlStore(root=scratch / "session")
    record = _record()

    writer = BufferedSessionDecisionWriter(store, max_queue_size=2)
    writer.enqueue(record)
    flush = writer.flush()
    saved = store.read_records(symbol="SPY", session_date="2026-07-23")[0]

    assert flush.persisted == 1
    assert saved.persistenceVersion == SESSION_PERSISTENCE_VERSION
    assert saved.schemaVersion == SESSION_DECISION_RECORD_SCHEMA_VERSION
    assert saved.featureSchemaVersion
    assert saved.classifierVersion
    assert saved.configVersion
    assert saved.profileVersion
    assert saved.baselineVersion == "baseline-v-test"
    shutil.rmtree(scratch, ignore_errors=True)


def test_session_step14_operational_metrics_from_persisted_records() -> None:
    first = _record(behavior=SessionBehavior.TREND_UP, actual={"estimatedCost": 0.02, "realizedCost": 0.03, "paperDecisionHash": "same", "replayDecisionHash": "same"})
    second = _record(
        classification_id="session-classification-step14-second",
        behavior=SessionBehavior.TREND_DOWN,
        liquidity=LiquidityState.STALE,
        data_quality=DataQualityState.STALE,
        block=True,
        reason_codes=("SESSION_QUOTE_STALE", "SESSION_MISSING_BAR"),
        actual={"estimatedCost": 0.04, "realizedCost": 0.01, "paperDecisionHash": "paper", "replayDecisionHash": "replay"},
        decision_offset_seconds=60,
    )

    metrics = build_session_operational_metrics((first, second))

    assert metrics.recordCount == 2
    assert metrics.staleQuoteRate == 0.5
    assert metrics.missingBarRate == 0.5
    assert metrics.transitionCount == 1
    assert metrics.transitionReversalRate == 1.0
    assert metrics.behaviorOccupancy == {"trend_down": 0.5, "trend_up": 0.5}
    assert metrics.blockedEntryCountByReason["SESSION_QUOTE_STALE"] == 1
    assert metrics.estimatedVersusRealizedCosts["sampleCount"] == 2
    assert metrics.paperReplayDivergence["divergenceRate"] == 0.5


def _record(
    *,
    classification_id: str = "session-classification-step14",
    behavior: SessionBehavior = SessionBehavior.TREND_UP,
    liquidity: LiquidityState = LiquidityState.HEALTHY,
    data_quality: DataQualityState = DataQualityState.READY,
    block: bool = False,
    reason_codes: tuple[str, ...] = ("SESSION_TEST_READY",),
    actual: dict | None = None,
    decision_offset_seconds: int = 0,
):
    classification = _classification(
        classification_id=classification_id,
        behavior=behavior,
        liquidity=liquidity,
        data_quality=data_quality,
        block=block,
        reason_codes=reason_codes,
        decision_time=NOW + timedelta(seconds=decision_offset_seconds),
    )
    profile = resolve_session_profile(classification)
    candidate = build_session_candidate_decision(
        classification=classification,
        profile=profile,
        originating_strategy_candidate_id="strategy-candidate-step14",
        side=Signal.BUY,
        order_type="limit",
        desired_quantity=10,
        entry_price=100.0,
        permitted_entry_price_range=(99.95, 100.05),
        expected_gross_edge=0.08,
        spread_estimate=0.005,
        slippage_estimate=0.005,
        fees=0.001,
        market_impact_estimate=0.001,
        adverse_selection_buffer=0.002,
        fill_probability=0.80,
        quantity_cap=10,
        stop_price=99.5,
        target_price=101.0,
        planned_risk_dollars=25.0,
    )
    return build_session_decision_record(
        classification=classification,
        profile=profile,
        candidate=candidate,
        output_mode="shadow",
        transition_state={"currentBehavior": behavior.value, "transitionHistory": []},
        actual_later_outcome=actual,
        persisted_at=classification.decision_time + timedelta(milliseconds=5),
    )


def _classification(
    *,
    classification_id: str,
    behavior: SessionBehavior,
    liquidity: LiquidityState,
    data_quality: DataQualityState,
    block: bool,
    reason_codes: tuple[str, ...],
    decision_time: datetime,
) -> SessionClassification:
    return SessionClassification(
        symbol="SPY",
        session_date="2026-07-23",
        exchange_timezone="America/New_York",
        market_event_time=decision_time - timedelta(milliseconds=120),
        feature_snapshot_time=decision_time - timedelta(milliseconds=40),
        decision_time=decision_time,
        valid_until=decision_time + timedelta(seconds=60),
        phase=SessionPhase.MORNING,
        behavior=behavior,
        volatility_state=VolatilityState.NORMAL,
        liquidity_state=liquidity,
        data_quality_state=data_quality,
        event_risk_state=EventRiskState.CLEAR,
        direction_bias="cash" if block else "long",
        phase_confidence=0.9,
        behavior_confidence=0.8,
        volatility_confidence=0.8,
        liquidity_confidence=0.9,
        data_quality_confidence=0.95,
        overall_confidence=0.8,
        safety_block_confidence=0.9 if block else 0.0,
        reason_codes=reason_codes,
        evidence={
            "classificationId": classification_id,
            "featureSnapshotId": "session-feature-step14",
            "baselineArtifactId": "baseline-artifact-test",
            "baselineVersion": "baseline-v-test",
        },
        allowed_strategy_families=("trend", "pullback", "vwap"),
        blocked_strategy_families=("breakout",) if block else (),
        block_new_entries=block,
    )


class _FailingStore:
    def write_record(self, record) -> SessionPersistenceStatus:
        raise RuntimeError("database unavailable")

    def read_records(self, *, symbol=None, session_date=None):
        return ()


def _scratch_path() -> Path:
    path = Path("backend") / ".test_artifacts" / f"session_step14_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path
