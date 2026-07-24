from __future__ import annotations

from itertools import product

from backend.app.algorithms.session import SessionBehavior, SessionPhase, resolve_session_profile, session_route_permissions
from session_test_fixtures import classification_fixture


def test_session_profiles_cover_every_phase_behavior_pair() -> None:
    for phase, behavior in product(SessionPhase, SessionBehavior):
        profile = resolve_session_profile(classification_fixture(phase=phase, behavior=behavior))
        assert profile.profile_id.startswith("session.profile.")
        assert profile.reason_codes


def test_session_profiles_blocked_states_have_zero_new_entry_risk() -> None:
    for behavior in (SessionBehavior.EVENT_DRIVEN, SessionBehavior.LIQUIDITY_STRESS, SessionBehavior.UNKNOWN):
        profile = resolve_session_profile(classification_fixture(behavior=behavior, block=True))
        assert profile.block_new_entries is True
        assert profile.base_risk_multiplier == 0
        assert profile.maximum_position_percentage == 0
        assert profile.allowed_order_types == ()


def test_session_profiles_are_read_only_and_cannot_bypass_global_gates() -> None:
    classification = classification_fixture(phase=SessionPhase.MIDDAY, behavior=SessionBehavior.COMPRESSION)
    profile = resolve_session_profile(classification)
    route = session_route_permissions(classification)

    assert route["readOnly"] is True
    assert route["cannotBypassGlobalGates"] is True
    assert route["profileHash"] == profile.deterministic_hash()
