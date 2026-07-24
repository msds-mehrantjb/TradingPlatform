from __future__ import annotations

from datetime import timedelta

from backend.app.algorithms.session import SessionBehavior, SessionConfig, SessionTransitionManager
from session_test_fixtures import NOW, classification_fixture


def test_session_transitions_require_confirmation_before_behavior_change() -> None:
    manager = SessionTransitionManager(config=SessionConfig(transition_confirmation_bars=2, transition_min_dwell_seconds=0))
    state = manager.process(classification_fixture(behavior=SessionBehavior.BALANCED_RANGE))
    candidate = classification_fixture(behavior=SessionBehavior.TREND_UP, confidence=0.95)

    state = manager.process(candidate, state)
    assert state.current_classification.behavior == SessionBehavior.BALANCED_RANGE
    assert state.candidate_behavior == SessionBehavior.TREND_UP

    state = manager.process(classification_fixture(behavior=SessionBehavior.TREND_UP, decision_time=NOW + timedelta(minutes=1), confidence=0.95), state)
    assert state.current_classification.behavior == SessionBehavior.TREND_UP
    assert state.transition_history[-1].transition_reason == "SESSION_TRANSITION_CANDIDATE_CONFIRMED"


def test_session_transitions_safety_deterioration_blocks_immediately_and_recovers_slowly() -> None:
    manager = SessionTransitionManager(config=SessionConfig(transition_recovery_confirmation_bars=2, transition_min_dwell_seconds=0))
    state = manager.process(classification_fixture(behavior=SessionBehavior.TREND_UP))
    emergency = classification_fixture(behavior=SessionBehavior.LIQUIDITY_STRESS, block=True)

    state = manager.process(emergency, state)
    assert state.current_classification.behavior == SessionBehavior.LIQUIDITY_STRESS
    assert state.transition_reason == "SESSION_TRANSITION_EMERGENCY_ACCEPTED"

    recovery = classification_fixture(behavior=SessionBehavior.TREND_UP)
    state = manager.process(recovery, state)
    assert state.current_classification.behavior == SessionBehavior.LIQUIDITY_STRESS
    assert state.transition_reason == "SESSION_TRANSITION_RECOVERY_PENDING"


def test_session_transitions_record_rejected_oscillation_attempts() -> None:
    manager = SessionTransitionManager(config=SessionConfig(transition_oscillation_confirmation_bars=3, transition_min_dwell_seconds=0))
    state = manager.process(classification_fixture(behavior=SessionBehavior.BALANCED_RANGE))

    state = manager.process(classification_fixture(behavior=SessionBehavior.CHOPPY, confidence=0.95), state)

    assert state.current_classification.behavior == SessionBehavior.BALANCED_RANGE
    assert state.transition_history[-1].transition_reason == "SESSION_TRANSITION_OSCILLATION_GUARD"
