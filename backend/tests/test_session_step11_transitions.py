from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.app.algorithms.session import (
    DataQualityState,
    EventRiskState,
    LiquidityState,
    SessionBehavior,
    SessionClassification,
    SessionConfig,
    SessionPhase,
    SessionTransitionManager,
    SessionTransitionState,
    VolatilityState,
)
from backend.app.algorithms.session.transition import (
    SESSION_TRANSITION_CANDIDATE_CONFIRMED,
    SESSION_TRANSITION_CANDIDATE_NOT_CONFIRMED,
    SESSION_TRANSITION_CONFIDENCE_MARGIN_TOO_SMALL,
    SESSION_TRANSITION_EMERGENCY_ACCEPTED,
    SESSION_TRANSITION_MIN_DWELL_NOT_MET,
    SESSION_TRANSITION_OSCILLATION_GUARD,
    SESSION_TRANSITION_RECOVERY_ACCEPTED,
    SESSION_TRANSITION_RECOVERY_PENDING,
)


START = datetime(2026, 7, 23, 14, 0, tzinfo=UTC)
CONFIG = SessionConfig(
    transition_confirmation_bars=3,
    transition_min_candidate_confidence=0.62,
    transition_min_confidence_improvement=0.05,
    transition_min_dwell_seconds=120,
    transition_recovery_confirmation_bars=3,
    transition_recovery_min_confidence=0.70,
    transition_oscillation_confirmation_bars=4,
)


def test_session_step11_candidate_not_confirmed() -> None:
    manager = SessionTransitionManager(config=CONFIG)
    state = manager.process(_classification(SessionBehavior.BALANCED_RANGE, START, confidence=0.70))

    state = manager.process(_classification(SessionBehavior.TREND_UP, START + timedelta(minutes=3), confidence=0.80), state)

    assert state.current_classification.behavior == SessionBehavior.BALANCED_RANGE
    assert state.candidate_behavior == SessionBehavior.TREND_UP
    assert state.consecutive_confirmation_count == 1
    assert state.transition_history[-1].transition_reason == SESSION_TRANSITION_CANDIDATE_NOT_CONFIRMED


def test_session_step11_confirmation_after_n_finalized_bars() -> None:
    manager = SessionTransitionManager(config=CONFIG)
    state = manager.process(_classification(SessionBehavior.BALANCED_RANGE, START, confidence=0.70))

    for index in range(3):
        state = manager.process(_classification(SessionBehavior.TREND_UP, START + timedelta(minutes=3 + index), confidence=0.80), state)

    assert state.current_classification.behavior == SessionBehavior.TREND_UP
    assert state.candidate_behavior is None
    assert state.transition_history[-1].transition_reason == SESSION_TRANSITION_CANDIDATE_CONFIRMED


def test_session_step11_confidence_margin_blocks_transition() -> None:
    manager = SessionTransitionManager(config=CONFIG)
    state = manager.process(_classification(SessionBehavior.BALANCED_RANGE, START, confidence=0.70))

    state = manager.process(_classification(SessionBehavior.TREND_UP, START + timedelta(minutes=3), confidence=0.73), state)

    assert state.current_classification.behavior == SessionBehavior.BALANCED_RANGE
    assert state.transition_history[-1].transition_reason == SESSION_TRANSITION_CONFIDENCE_MARGIN_TOO_SMALL
    assert SESSION_TRANSITION_CONFIDENCE_MARGIN_TOO_SMALL in state.transition_history[-1].reason_codes


def test_session_step11_minimum_dwell_blocks_early_transition() -> None:
    manager = SessionTransitionManager(config=CONFIG)
    state = manager.process(_classification(SessionBehavior.BALANCED_RANGE, START, confidence=0.70))

    state = manager.process(_classification(SessionBehavior.TREND_UP, START + timedelta(seconds=30), confidence=0.90), state)

    assert state.current_classification.behavior == SessionBehavior.BALANCED_RANGE
    assert state.transition_history[-1].transition_reason == SESSION_TRANSITION_MIN_DWELL_NOT_MET


def test_session_step11_immediate_stale_data_block() -> None:
    manager = SessionTransitionManager(config=CONFIG)
    state = manager.process(_classification(SessionBehavior.TREND_UP, START, confidence=0.80))

    state = manager.process(
        _classification(
            SessionBehavior.UNKNOWN,
            START + timedelta(seconds=30),
            confidence=0.25,
            data_quality=DataQualityState.STALE,
            liquidity=LiquidityState.STALE,
            block=True,
            reason_codes=("SESSION_QUOTE_STALE",),
        ),
        state,
    )

    assert state.current_classification.behavior == SessionBehavior.UNKNOWN
    assert state.current_classification.block_new_entries is True
    assert state.transition_history[-1].transition_reason == SESSION_TRANSITION_EMERGENCY_ACCEPTED


def test_session_step11_delayed_recovery_from_emergency() -> None:
    manager = SessionTransitionManager(config=CONFIG)
    state = manager.process(_classification(SessionBehavior.TREND_UP, START, confidence=0.80))
    state = manager.process(
        _classification(
            SessionBehavior.LIQUIDITY_STRESS,
            START + timedelta(minutes=1),
            confidence=0.35,
            liquidity=LiquidityState.STRESSED,
            block=True,
        ),
        state,
    )

    state = manager.process(_classification(SessionBehavior.BALANCED_RANGE, START + timedelta(minutes=2), confidence=0.82), state)
    assert state.current_classification.behavior == SessionBehavior.LIQUIDITY_STRESS
    assert state.transition_history[-1].transition_reason == SESSION_TRANSITION_RECOVERY_PENDING

    state = manager.process(_classification(SessionBehavior.BALANCED_RANGE, START + timedelta(minutes=3), confidence=0.82), state)
    state = manager.process(_classification(SessionBehavior.BALANCED_RANGE, START + timedelta(minutes=4), confidence=0.82), state)
    state = manager.process(_classification(SessionBehavior.BALANCED_RANGE, START + timedelta(minutes=5), confidence=0.82), state)

    assert state.current_classification.behavior == SessionBehavior.BALANCED_RANGE
    assert state.current_classification.block_new_entries is False
    assert state.transition_history[-1].transition_reason == SESSION_TRANSITION_RECOVERY_ACCEPTED


def test_session_step11_repeated_flip_attempts_do_not_oscillate() -> None:
    manager = SessionTransitionManager(config=CONFIG)
    state = manager.process(_classification(SessionBehavior.BALANCED_RANGE, START, confidence=0.65))

    for index in range(8):
        behavior = SessionBehavior.MEAN_REVERTING if index % 2 == 0 else SessionBehavior.BALANCED_RANGE
        state = manager.process(_classification(behavior, START + timedelta(minutes=3 + index), confidence=0.82), state)

    assert state.current_classification.behavior == SessionBehavior.BALANCED_RANGE
    assert any(record.transition_reason == SESSION_TRANSITION_OSCILLATION_GUARD for record in state.transition_history)


def test_session_step11_deterministic_replay() -> None:
    stream = [
        _classification(SessionBehavior.BALANCED_RANGE, START, confidence=0.70),
        _classification(SessionBehavior.TREND_UP, START + timedelta(minutes=3), confidence=0.80),
        _classification(SessionBehavior.TREND_UP, START + timedelta(minutes=4), confidence=0.80),
        _classification(SessionBehavior.TREND_UP, START + timedelta(minutes=5), confidence=0.80),
        _classification(SessionBehavior.LIQUIDITY_STRESS, START + timedelta(minutes=6), confidence=0.30, liquidity=LiquidityState.STRESSED, block=True),
    ]

    first = _replay(stream)
    second = _replay(stream)

    assert first.as_dict() == second.as_dict()
    assert first.current_classification.behavior == SessionBehavior.LIQUIDITY_STRESS
    assert len(first.transition_history) == len(second.transition_history)


def _replay(stream: list[SessionClassification]) -> SessionTransitionState:
    manager = SessionTransitionManager(config=CONFIG)
    state: SessionTransitionState | None = None
    for item in stream:
        state = manager.process(item, state)
    assert state is not None
    return state


def _classification(
    behavior: SessionBehavior,
    timestamp: datetime,
    *,
    confidence: float,
    data_quality: DataQualityState = DataQualityState.READY,
    liquidity: LiquidityState = LiquidityState.HEALTHY,
    volatility: VolatilityState = VolatilityState.NORMAL,
    event_risk: EventRiskState = EventRiskState.CLEAR,
    phase: SessionPhase = SessionPhase.MORNING,
    block: bool = False,
    reason_codes: tuple[str, ...] = (),
) -> SessionClassification:
    return SessionClassification(
        symbol="SPY",
        session_date="2026-07-23",
        exchange_timezone="America/New_York",
        market_event_time=timestamp,
        feature_snapshot_time=timestamp,
        decision_time=timestamp,
        valid_until=timestamp + timedelta(seconds=60),
        phase=phase,
        behavior=behavior,
        volatility_state=volatility,
        liquidity_state=liquidity,
        data_quality_state=data_quality,
        event_risk_state=event_risk,
        direction_bias="long" if behavior in {SessionBehavior.TREND_UP, SessionBehavior.BREAKOUT_UP} else "cash" if block else "neutral",
        phase_confidence=0.95,
        behavior_confidence=confidence,
        volatility_confidence=confidence,
        liquidity_confidence=0.30 if liquidity in {LiquidityState.STALE, LiquidityState.UNKNOWN} else confidence,
        data_quality_confidence=0.30 if data_quality in {DataQualityState.STALE, DataQualityState.INVALID} else confidence,
        overall_confidence=confidence,
        safety_block_confidence=0.90 if block else 0.0,
        reason_codes=reason_codes or (f"fixture.{behavior.value}",),
        evidence={"fixture": behavior.value},
        allowed_strategy_families=(),
        blocked_strategy_families=("trend", "breakout") if block else (),
        block_new_entries=block,
    )
