from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import product

from backend.app.algorithms.session import (
    DataQualityState,
    EventRiskState,
    LiquidityState,
    SessionBehavior,
    SessionClassification,
    SessionConfig,
    SessionPhase,
    VolatilityState,
    resolve_session_profile,
    session_route_permissions,
)


NOW = datetime(2026, 7, 23, 15, 0, tzinfo=UTC)


def test_session_step12_profile_mapping_for_every_phase_behavior_combination() -> None:
    for phase, behavior in product(SessionPhase, SessionBehavior):
        profile = resolve_session_profile(_classification(phase=phase, behavior=behavior))

        assert profile.profile_id.startswith("session.profile.")
        assert profile.source_phase == phase.value
        assert profile.source_behavior == behavior.value
        assert profile.reason_codes
        assert isinstance(profile.allowed_strategy_families, tuple)
        assert isinstance(profile.blocked_strategy_families, tuple)


def test_session_step12_no_profile_exceeds_global_risk_ceilings() -> None:
    config = SessionConfig(
        session_profile_global_max_risk_multiplier=0.40,
        session_profile_global_max_position_percentage=0.035,
        maximum_healthy_spread_bps=5.0,
        maximum_quote_age_seconds=0.40,
        maximum_intended_participation_ratio=0.02,
        session_profile_max_concurrent_positions=1,
    )

    for phase, behavior in product(SessionPhase, SessionBehavior):
        profile = resolve_session_profile(_classification(phase=phase, behavior=behavior), config=config)

        assert profile.base_risk_multiplier <= config.session_profile_global_max_risk_multiplier
        assert profile.maximum_position_percentage <= config.session_profile_global_max_position_percentage
        assert profile.maximum_spread_basis_points <= config.maximum_healthy_spread_bps
        assert profile.maximum_quote_age_seconds <= config.maximum_quote_age_seconds
        assert profile.maximum_participation_rate <= config.maximum_intended_participation_ratio
        assert profile.maximum_concurrent_session_originated_positions <= config.session_profile_max_concurrent_positions


def test_session_step12_blocked_states_produce_zero_new_entry_risk() -> None:
    blocked = [
        _classification(behavior=SessionBehavior.EVENT_DRIVEN, event_risk=EventRiskState.BLACKOUT, block=True),
        _classification(behavior=SessionBehavior.LIQUIDITY_STRESS, liquidity=LiquidityState.STRESSED, block=True),
        _classification(behavior=SessionBehavior.BUILDING, data_quality=DataQualityState.WARMING_UP, block=True),
        _classification(phase=SessionPhase.CLOSING_AUCTION, behavior=SessionBehavior.TREND_UP),
    ]

    for classification in blocked:
        profile = resolve_session_profile(classification)

        assert profile.block_new_entries is True
        assert profile.base_risk_multiplier == 0
        assert profile.maximum_position_percentage == 0
        assert profile.maximum_concurrent_session_originated_positions == 0
        assert profile.allowed_order_types == ()


def test_session_step12_choppy_blocks_breakout_chasing() -> None:
    profile = resolve_session_profile(_classification(phase=SessionPhase.MORNING, behavior=SessionBehavior.CHOPPY))

    assert profile.profile_id == "session.profile.choppy"
    assert "breakout" in profile.blocked_strategy_families
    assert "trend" in profile.blocked_strategy_families
    assert "mean_reversion" in profile.allowed_strategy_families
    assert profile.base_risk_multiplier > 0


def test_session_step12_midday_range_allows_mean_reversion_only() -> None:
    profile = resolve_session_profile(_classification(phase=SessionPhase.MIDDAY, behavior=SessionBehavior.BALANCED_RANGE))

    assert profile.profile_id == "session.profile.midday_mean_reverting"
    assert profile.allowed_strategy_families == ("mean_reversion", "reversal", "vwap")
    assert "breakout" in profile.blocked_strategy_families
    assert "trend" in profile.blocked_strategy_families


def test_session_step12_trend_favors_trend_and_pullback_families() -> None:
    profile = resolve_session_profile(_classification(phase=SessionPhase.MORNING, behavior=SessionBehavior.TREND_UP))

    assert profile.profile_id == "session.profile.morning_trend"
    assert "trend" in profile.allowed_strategy_families
    assert "pullback" in profile.allowed_strategy_families
    assert profile.maximum_holding_period_seconds > 900


def test_session_step12_opening_profiles_use_smaller_risk_and_short_validity() -> None:
    discovery = resolve_session_profile(_classification(phase=SessionPhase.OPENING_DISCOVERY, behavior=SessionBehavior.BALANCED_RANGE))
    drive = resolve_session_profile(_classification(phase=SessionPhase.OPENING_DISCOVERY, behavior=SessionBehavior.OPENING_DRIVE))
    breakout = resolve_session_profile(_classification(phase=SessionPhase.OPENING_RANGE, behavior=SessionBehavior.BREAKOUT_UP))

    assert discovery.profile_id == "session.profile.opening_discovery"
    assert drive.profile_id == "session.profile.confirmed_opening_drive"
    assert breakout.profile_id == "session.profile.opening_range_breakout"
    assert discovery.base_risk_multiplier < 1.0
    assert drive.signal_validity_period_seconds <= 20
    assert breakout.signal_validity_period_seconds <= 30


def test_session_step12_afternoon_power_and_closing_profiles_are_distinct() -> None:
    afternoon = resolve_session_profile(_classification(phase=SessionPhase.AFTERNOON, behavior=SessionBehavior.EXPANSION, volatility=VolatilityState.EXPANDING))
    power = resolve_session_profile(_classification(phase=SessionPhase.POWER_HOUR, behavior=SessionBehavior.TREND_UP))
    closing = resolve_session_profile(_classification(phase=SessionPhase.CLOSING_AUCTION, behavior=SessionBehavior.TREND_UP))

    assert afternoon.profile_id == "session.profile.afternoon_expansion"
    assert power.profile_id == "session.profile.power_hour"
    assert power.flatten_by_time == "16:00"
    assert closing.profile_id == "session.profile.closing_cutoff"
    assert closing.block_new_entries is True


def test_session_step12_deterministic_profile_output_and_read_only_permissions() -> None:
    classification = _classification(phase=SessionPhase.MIDDAY, behavior=SessionBehavior.COMPRESSION, volatility=VolatilityState.COMPRESSED)

    first = resolve_session_profile(classification)
    second = resolve_session_profile(classification)
    route = session_route_permissions(classification)

    assert first.as_dict() == second.as_dict()
    assert first.deterministic_hash() == second.deterministic_hash()
    assert route["readOnly"] is True
    assert route["cannotBypassGlobalGates"] is True
    assert route["canRouteNewEntries"] is True
    assert route["profileHash"] == first.deterministic_hash()


def _classification(
    *,
    phase: SessionPhase = SessionPhase.MORNING,
    behavior: SessionBehavior = SessionBehavior.BALANCED_RANGE,
    volatility: VolatilityState = VolatilityState.NORMAL,
    liquidity: LiquidityState = LiquidityState.HEALTHY,
    data_quality: DataQualityState = DataQualityState.READY,
    event_risk: EventRiskState = EventRiskState.CLEAR,
    block: bool = False,
) -> SessionClassification:
    return SessionClassification(
        symbol="SPY",
        session_date="2026-07-23",
        exchange_timezone="America/New_York",
        market_event_time=NOW,
        feature_snapshot_time=NOW,
        decision_time=NOW,
        valid_until=NOW + timedelta(seconds=60),
        phase=phase,
        behavior=behavior,
        volatility_state=volatility,
        liquidity_state=liquidity,
        data_quality_state=data_quality,
        event_risk_state=event_risk,
        direction_bias="cash" if block else "neutral",
        phase_confidence=0.9,
        behavior_confidence=0.75,
        volatility_confidence=0.8,
        liquidity_confidence=0.8,
        data_quality_confidence=0.9,
        overall_confidence=0.75,
        safety_block_confidence=0.9 if block else 0.0,
        reason_codes=(f"fixture.{phase.value}.{behavior.value}",),
        evidence={"fixture": True},
        allowed_strategy_families=(),
        blocked_strategy_families=(),
        block_new_entries=block,
    )
